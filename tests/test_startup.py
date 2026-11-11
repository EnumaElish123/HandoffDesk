"""Cold CLI queries and current-schema readers must stay lightweight."""
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from handoffdesk.store import Store


class StartupTests(unittest.TestCase):
    def test_version_with_explicit_state_avoids_backend_and_ui_imports(self):
        script = """
import sys
from handoffdesk.cli import main
try:
    main(['--app-dir', sys.argv[1], '--version'])
except SystemExit as exc:
    assert exc.code == 0
else:
    raise AssertionError('version action did not exit')
for name in ('handoffdesk.service', 'handoffdesk.agent_session', 'handoffdesk.integration',
             'handoffdesk.store', 'textual', 'tkinter'):
    assert name not in sys.modules, name
"""
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'new state'
            result = subprocess.run([sys.executable, '-c', script, str(state)],
                                    cwd=Path(__file__).resolve().parents[1],
                                    capture_output=True, text=True, check=True)
            self.assertIn('HandoffDesk ', result.stdout)
            self.assertFalse(state.exists())

    def test_current_reader_can_open_while_writer_is_active(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'cards.sqlite3'
            Store(path)
            with closing(sqlite3.connect(path)) as writer:
                writer.execute('BEGIN IMMEDIATE')
                with Store(path).connection() as reader:
                    self.assertEqual(reader.execute('SELECT count(*) FROM handoffs').fetchone()[0], 0)
