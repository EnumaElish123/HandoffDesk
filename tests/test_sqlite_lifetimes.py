"""Database handles must be released without waiting for garbage collection."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from handoffdesk.integration import permission_probe
from handoffdesk.store import Store


CONNECT = sqlite3.connect


class SQLiteLifetimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def capture_connections(self, failure=None):
        connections = []

        class TrackedConnection(sqlite3.Connection):
            closed = False

            def close(self):
                super().close()
                self.closed = True

            def execute(self, sql, parameters=()):
                if failure == 'probe_write' and sql.startswith('INSERT INTO probe'):
                    raise sqlite3.OperationalError('probe write failed')
                return super().execute(sql, parameters)

            def backup(self, target, *args, **kwargs):
                if failure == 'backup':
                    raise sqlite3.OperationalError('backup failed')
                return super().backup(target, *args, **kwargs)

        def connect(*args, **kwargs):
            connection = CONNECT(*args, factory=TrackedConnection, **kwargs)
            connections.append(connection)
            return connection

        # Retain references so the tests also catch leaks on platforms that allow
        # deleting an open database, regardless of garbage collection timing.
        self.addCleanup(lambda: [connection.close() for connection in connections])
        return connect, connections

    def legacy_database(self):
        path = self.root / 'cards.sqlite3'
        with closing(CONNECT(path)) as db, db:
            db.execute('CREATE TABLE legacy_marker(value TEXT)')
            db.execute("INSERT INTO legacy_marker VALUES ('original')")
            db.execute('PRAGMA user_version=4')
        return path

    def test_permission_probe_closes_database_before_removing_temporary_files(self):
        connect, connections = self.capture_connections()
        with patch('handoffdesk.integration.sqlite3.connect', side_effect=connect):
            result = permission_probe(self.root)
        self.assertTrue(result['ok'], result)
        self.assertEqual(len(connections), 1)
        self.assertTrue(all(connection.closed for connection in connections))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_failed_permission_probe_also_closes_database(self):
        connect, connections = self.capture_connections('probe_write')
        with patch('handoffdesk.integration.sqlite3.connect', side_effect=connect):
            result = permission_probe(self.root)
        self.assertFalse(result['ok'])
        self.assertIn('probe write failed', result['message'])
        self.assertEqual(len(connections), 1)
        self.assertTrue(all(connection.closed for connection in connections))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_migration_closes_source_and_backup_connections(self):
        path = self.legacy_database()
        connect, connections = self.capture_connections()
        with patch('handoffdesk.store.sqlite3.connect', side_effect=connect):
            Store(path)
        self.assertEqual(len(connections), 3)
        self.assertTrue(all(connection.closed for connection in connections))
        backups = list((self.root / 'migration-backups').glob('*.sqlite3'))
        self.assertEqual(len(backups), 1)
        with closing(CONNECT(backups[0])) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 4)
            self.assertEqual(db.execute('SELECT value FROM legacy_marker').fetchone()[0], 'original')
        with closing(CONNECT(path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 5)

    def test_failed_migration_also_closes_all_connections(self):
        path = self.legacy_database()
        connect, connections = self.capture_connections('backup')
        with patch('handoffdesk.store.sqlite3.connect', side_effect=connect):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'backup failed'):
                Store(path)
        self.assertEqual(len(connections), 3)
        self.assertTrue(all(connection.closed for connection in connections))
        with closing(CONNECT(path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 4)


if __name__ == '__main__':
    unittest.main()
