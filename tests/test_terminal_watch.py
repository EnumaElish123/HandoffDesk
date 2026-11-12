import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from handoffdesk.bootstrap import initialize, add_project
from handoffdesk.reader import ReaderModel


class ChangeMonitorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.project = self.base / 'project'
        self.project.mkdir()
        self.state = self.base / 'state'
        initialize(self.state, project_id='first', name='研究', root=self.project)
        self.model = ReaderModel(self.state)
        self.watch = self.model.change_monitor()
        self.addCleanup(self.watch.close)
        self.cursor = self.watch.changes()['cursor']
        self.db = self.state / 'data' / 'cards.sqlite3'

    def test_idle_observer_only_requests_data_version_from_sqlite(self):
        queries = []
        self.watch._db.set_trace_callback(queries.append)
        with patch.object(self.model, '_service', side_effect=AssertionError('reopened service')):
            self.assertFalse(self.watch.changes(self.cursor)['changed'])
        self.assertEqual([sql.lower() for sql in queries], ['pragma data_version'])

    def test_existing_writer_is_detected_at_commit_only(self):
        with closing(sqlite3.connect(self.db)) as writer:
            for sql in [
                    "INSERT INTO worklines VALUES('first','revision','{}')",
                    "UPDATE worklines SET payload='{}' || ' ' WHERE workline_id='revision'",
                    "INSERT INTO reader_reads VALUES('local','first:default:1','time')",
                    "DELETE FROM reader_reads WHERE record_id='first:default:1'",
                    "INSERT INTO node_file_state VALUES('first','record:default:1','{}')",
                    "UPDATE node_file_state SET payload='{}' || ' ' WHERE project_id='first'",
                    "INSERT INTO handoffs(project_id,revision,recorded_at,payload) VALUES('first',1,'time','{}')"]:
                writer.execute(sql)
                self.assertFalse(self.watch.changes(self.cursor)['changed'])
                writer.rollback()
                self.assertFalse(self.watch.changes(self.cursor)['changed'])
                writer.execute(sql)
                writer.commit()
                value = self.watch.changes(self.cursor)
                self.assertTrue(value['changed'])
                self.cursor = value['cursor']
                self.assertFalse(self.watch.changes(self.cursor)['changed'])

    def test_configuration_and_root_overrides_invalidate_observer(self):
        second = self.base / 'second'
        second.mkdir()
        add_project(self.state, 'second', name='其他项目', root=second)
        value = self.watch.changes(self.cursor)
        self.assertTrue(value['changed'])
        override = self.state / 'config' / 'roots.local.json'
        override.write_text(json.dumps({'schema_version': 1, 'projects': {'first': {'main': str(self.project)}}}))
        self.assertTrue(self.watch.changes(value['cursor'])['changed'])

    def test_reopen_uses_a_new_cursor_even_for_same_database(self):
        self.watch.close()
        self.assertTrue(self.watch.changes(self.cursor)['changed'])

    @unittest.skipIf(os.name == 'nt', 'Windows locks open database files')
    def test_replaced_database_is_reopened(self):
        self.db.parent.rename(self.state / 'old-data')
        # Recreate state using the configured source through the regular reader.
        self.model.records()
        value = self.watch.changes(self.cursor)
        self.assertTrue(value['changed'])
        self.assertFalse(self.watch.changes(value['cursor'])['changed'])

    def test_redirected_sidecar_is_rejected_without_writing_outside_state(self):
        outside = self.project / 'outside'
        outside.write_bytes(b'preserve')
        sidecar = self.db.with_name(self.db.name + '-journal')
        try:
            sidecar.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest('symlinks unavailable')
        with self.assertRaisesRegex(ValueError, '重定向'):
            self.watch.changes(self.cursor)
        sidecar.unlink()
        self.assertEqual(outside.read_bytes(), b'preserve')
        self.assertTrue(self.watch.changes(self.cursor)['changed'])


class SearchExcerptTests(unittest.TestCase):
    def test_folded_text_and_joined_overlay_supply_matching_context(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project = base / 'project'
            project.mkdir()
            app = base / 'state'
            initialize(app, project_id='first', name='研究', root=project)
            model = ReaderModel(app)
            store = model._service().store
            body = '长正文' * 80 + 'Ｓｉｇｎａｌ结束'
            payload = {'current_task': '检查', 'last_done': body, 'file_manifest': []}
            added = {'path': str(project / '文件.txt'), 'purpose': '初次核验'}
            with store.connection(True) as db:
                db.execute("INSERT INTO handoffs(project_id,revision,recorded_at,payload) VALUES('first',1,'now',?)",
                           (json.dumps(payload),))
                db.execute("INSERT INTO node_file_state VALUES('first','record:default:1',?)",
                           (json.dumps({'additions': [added]}),))
            item = model.records(query='signal')['items'][0]
            self.assertTrue(item['match_excerpt'].startswith('实际完成 · …'))
            self.assertIn('Ｓｉｇｎａｌ', item['match_excerpt'])
            self.assertLess(len(item['match_excerpt']), 100)
            self.assertEqual(model.records(query='核验')['items'][0]['match_excerpt'], '附件用途 · 初次核验')
            with store.connection(True) as db:
                db.execute("UPDATE node_file_state SET payload=?", (json.dumps({'hidden': [added['path']]}),))
            self.assertFalse(model.records(query='核验')['items'])
            self.assertNotIn('match_excerpt', model.records()['items'][0])


class DetailMetadataTests(unittest.TestCase):
    def test_legacy_snapshot_and_overlay_preserve_scope_history_and_access_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / 'business'
            root.mkdir()
            first = root / '报告.md'
            first.write_text('完成复核')
            outside = base / 'outside.md'
            outside.write_text('不可打开')
            app = base / 'state'
            initialize(app, project_id='p', root=root)
            model = ReaderModel(app)
            service = model._service()
            scope = 'p\x1faudit'
            with service.store.connection(True) as db:
                snapshot = {'observations': [{'source_ref': str(first), 'source_id': '历史报告'}]}
                sid = db.execute('INSERT INTO snapshots(project_id,generation,observed_at,payload) VALUES(?,1,?,?)',
                                 (scope, 'now', json.dumps(snapshot))).lastrowid
                for target in (scope, 'p'):
                    db.execute('INSERT INTO handoffs(project_id,revision,recorded_at,payload) VALUES(?,1,?,?)',
                               (target, 'now', json.dumps({'source_snapshot_id': sid, 'current_task': '核对'})))
                db.execute("INSERT INTO node_file_state VALUES('p','record:audit:1',?)",
                           (json.dumps({'additions': [{'path': str(outside), 'purpose': '外部旧路径'}]}),))
            with patch.object(model, '_service', return_value=service), \
                    patch.object(service.store, 'connection', wraps=service.store.connection) as opened:
                detail = model.detail('p:audit:1')
                self.assertEqual(opened.call_count, 1)
            self.assertEqual(detail['workline_name'], '历史任务')
            self.assertEqual(detail['file_count'], 2)
            files = {row['path']: row for row in detail['files']}
            self.assertEqual(files[str(first)]['purpose'], '历史报告')
            self.assertNotEqual(files[str(outside)]['status'], '可用')
            with self.assertRaises(ValueError):
                model.preview('p:audit:1', files[str(outside)]['id'])
            self.assertEqual(model.detail('p:default:1')['file_count'], 0)
