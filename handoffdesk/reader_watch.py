"""Connection-local SQLite change notifications for the terminal's polling worker."""
import sqlite3
from uuid import uuid4

from .paths import app_directory, configuration_path, validate_configuration_writes, _resolve_path, _is_within
from .registry import Registry


class ReaderChangeMonitor:
    """Use and close on a single thread. Never hold a read transaction between polls."""

    def __init__(self, model):
        self.model = model
        self._db = None
        self._identity = None
        self._epoch = None

    def close(self):
        if self._db is not None:
            self._db.close()
            self._db = None
        self._identity = None

    def _source(self, *, opening=False):
        app = app_directory(self.model.app_dir)
        registry = Registry(configuration_path(app))
        folder = _resolve_path(self.model.data_dir or app / 'data')
        path = _resolve_path(folder / 'cards.sqlite3')
        if opening:
            validate_configuration_writes(app, registry.projects, folder)
        if not _is_within(path, folder):
            raise ValueError('工具数据库重定向到了数据目录之外。')
        # SQLite can open sidecars while servicing even a read-only connection.
        for suffix in ('-wal', '-shm', '-journal'):
            sidecar = path.with_name(path.name + suffix)
            if sidecar.is_symlink() and not _is_within(_resolve_path(sidecar), folder):
                raise ValueError('数据库辅助文件重定向到了数据目录之外。')
        info = path.stat()
        return path, info.st_dev, info.st_ino, registry.fingerprint

    def changes(self, cursor=None):
        # Service bootstraps fresh state and validates project/config/data boundaries.
        try:
            if self._db is None:
                self.model._service()
            identity = self._source(opening=self._db is None)
            if identity != self._identity:
                if self._db is not None:
                    self.close()
                    self.model._service()
                    identity = self._source(opening=True)
                self._db = sqlite3.connect(identity[0].as_uri() + '?mode=ro', uri=True,
                                           isolation_level=None, timeout=2)
                self._identity = identity
                # SQLite data_version values only compare on the same connection.
                self._epoch = uuid4().hex
            version = self._db.execute('PRAGMA data_version').fetchone()[0]
        except (OSError, ValueError, sqlite3.Error):
            self.close()
            raise
        value = f'{self._epoch}:{version}'
        return {'cursor': value, 'changed': value != cursor}
