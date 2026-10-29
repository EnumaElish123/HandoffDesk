"""Onboarding uses isolated state and preserves registered project files."""
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from handoffdesk.bootstrap import initialize, add_project
from handoffdesk.cli import main
from handoffdesk.paths import app_directory
from handoffdesk.service import Service


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='handoffdesk-onboarding-', dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.app = self.base / 'state 中文'
        self.project = self.base / 'project 中文'
        self.project.mkdir()
        self.source = self.project / 'README.md'
        self.source.write_text('# Original\n', encoding='utf-8')

    def cli(self, *args):
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = main(['--app-dir', str(self.app), *args])
        return code, output.getvalue(), errors.getvalue()

    def test_default_initialization_is_idempotently_refused_without_database(self):
        result = initialize(self.app)
        self.assertEqual(result['project']['id'], 'demo')
        self.assertTrue(Path(result['project']['roots']['main']).is_relative_to(self.app))
        self.assertFalse((self.app / 'data').exists())
        config = self.app / 'config/projects.json'
        before = config.read_bytes()
        with self.assertRaisesRegex(ValueError, '不会覆盖'):
            initialize(self.app)
        self.assertEqual(config.read_bytes(), before)
        service = Service(self.app)
        self.assertEqual(service.show('demo')['project']['id'], 'demo')

    def test_existing_project_initialization_preserves_files_and_cli_context(self):
        before = self.source.read_bytes(), self.source.stat().st_mtime_ns
        code, output, _ = self.cli('init', '--project', 'sample', '--name', 'Sample', '--root', str(self.project))
        self.assertEqual(code, 0, output)
        self.assertTrue(json.loads(output)['initialized'])
        code, output, _ = self.cli('record', 'context', 'sample', '--workline', 'default')
        self.assertEqual(code, 0, output)
        context = json.loads(output)
        self.assertEqual(context['current_revision'], 0)
        self.assertEqual(context['roots']['main'], str(self.project))
        self.assertEqual((self.source.read_bytes(), self.source.stat().st_mtime_ns), before)

    def test_add_preserves_existing_config_mapping_and_database_records(self):
        initialize(self.app)
        service = Service(self.app)
        service.store.save_preferences({'fixture': 'original'})
        mapping = self.app / 'config/roots.local.json'
        mapping.write_text(json.dumps({'schema_version': 1, 'projects': {'demo': {'main': str(self.app / 'examples/demo-project')}}}), encoding='utf-8')
        config = self.app / 'config/projects.json'
        old_project = json.loads(config.read_text(encoding='utf-8'))['projects'][0]
        before_mapping = mapping.read_bytes()
        before_db = (self.app / 'data/cards.sqlite3').read_bytes()
        added = add_project(self.app, 'sample', name='Sample', root=self.project)
        self.assertTrue(added['added'])
        self.assertEqual(json.loads(config.read_text(encoding='utf-8'))['projects'][0], old_project)
        self.assertEqual(mapping.read_bytes(), before_mapping)
        self.assertEqual((self.app / 'data/cards.sqlite3').read_bytes(), before_db)
        code, output, _ = self.cli('project', 'add', 'sample', '--root', str(self.project))
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(output)['ok'])

    def test_bad_roots_are_rejected_before_creating_configuration(self):
        for root in (Path('relative'), self.base / 'missing', self.source, self.base):
            with self.subTest(root=root):
                with self.assertRaises(ValueError):
                    initialize(self.app, project_id='sample', root=root)
                self.assertFalse((self.app / 'config/projects.json').exists())
        with self.assertRaises(ValueError):
            initialize(self.app, project_id='sample', root=self.project, data_dir=self.project / 'tool-data')
        self.assertEqual(sorted(p.name for p in self.project.iterdir()), ['README.md'])

    def test_add_rejects_overlapping_roots_and_leaves_original_unchanged(self):
        initialize(self.app, project_id='sample', root=self.project)
        nested = self.project / 'nested'; nested.mkdir()
        config = self.app / 'config/projects.json'
        before = config.read_bytes()
        for project_id, root in [('other', self.project), ('other', nested), ('other', self.base)]:
            with self.subTest(root=root):
                with self.assertRaises(ValueError):
                    add_project(self.app, project_id, root=root)
                self.assertEqual(config.read_bytes(), before)

    def test_missing_configuration_has_actionable_message(self):
        code, _, errors = self.cli('show', 'sample')
        self.assertEqual(code, 2)
        self.assertIn('handoffdesk init', errors)
        self.assertFalse(self.app.exists())

    def test_busy_configuration_cannot_overwrite_existing_projects(self):
        initialize(self.app)
        config = self.app / 'config/projects.json'
        before = config.read_bytes()
        lock = self.app / 'config/.projects.lock'
        lock.mkdir()
        try:
            with self.assertRaisesRegex(ValueError, '正在被更新'):
                add_project(self.app, 'sample', root=self.project)
            self.assertEqual(config.read_bytes(), before)
        finally:
            lock.rmdir()

    def test_add_rejects_unsafe_existing_mapping_before_any_configuration_write(self):
        initialize(self.app, project_id='sample', root=self.project)
        other = self.base / 'another-project'; other.mkdir()
        mapping = self.app / 'config/roots.local.json'
        config = self.app / 'config/projects.json'
        for old_root, actual_data in [(self.app / 'config', None), (self.project, self.project / 'tool-data')]:
            mapping.write_text(json.dumps({'schema_version': 1, 'projects': {'sample': {'main': str(old_root)}}}), encoding='utf-8')
            before = config.read_bytes(), config.stat().st_mtime_ns, mapping.read_bytes()
            with self.subTest(root=old_root), patch('handoffdesk.bootstrap.tempfile.NamedTemporaryFile', side_effect=AssertionError('must reject before temporary writes')):
                with self.assertRaisesRegex(ValueError, '配置或数据目录'):
                    add_project(self.app, 'other', root=other, data_dir=actual_data)
            self.assertEqual((config.read_bytes(), config.stat().st_mtime_ns, mapping.read_bytes()), before)
            self.assertFalse((self.app / 'config/.projects.lock').exists())
        self.assertFalse((self.project / 'tool-data').exists())

    def test_cli_mapping_validates_actual_data_directory_before_replacement(self):
        initialize(self.app, project_id='sample', root=self.project)
        config = self.app / 'config/projects.json'
        mapping = self.base / 'roots.json'
        mapping.write_text(json.dumps({'schema_version': 1, 'projects': {'sample': {'main': str(self.project)}}}), encoding='utf-8')
        original = config.read_bytes(), config.stat().st_mtime_ns
        code, output, _ = self.cli('--data-dir', str(self.project / 'tool-data'), 'configure-roots', '--file', str(mapping))
        self.assertEqual(code, 2, output)
        self.assertIn('配置或数据目录', json.loads(output)['error']['message'])
        self.assertFalse((self.app / 'config/roots.local.json').exists())
        self.assertFalse((self.project / 'tool-data').exists())
        self.assertEqual((config.read_bytes(), config.stat().st_mtime_ns), original)

    def test_explicit_and_environment_state_directory_precedence(self):
        with patch.dict(os.environ, {'HANDOFFDESK_HOME': str(self.app)}, clear=True):
            self.assertEqual(app_directory(), self.app)
            self.assertEqual(app_directory(self.project), self.project)
        with patch.dict(os.environ, {'HANDOFFDESK_HOME': 'relative'}, clear=True):
            with self.assertRaises(ValueError):
                app_directory()

    def test_windows_and_linux_platform_defaults(self):
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.base)}, clear=True), patch('handoffdesk.paths.sys.platform', 'win32'):
            self.assertEqual(app_directory(), self.base / 'HandoffDesk')
        with patch.dict(os.environ, {'XDG_DATA_HOME': str(self.base)}, clear=True), patch('handoffdesk.paths.sys.platform', 'linux'):
            self.assertEqual(app_directory(), self.base / 'handoffdesk')
        with patch.dict(os.environ, {'XDG_DATA_HOME': 'relative'}, clear=True), patch('handoffdesk.paths.sys.platform', 'linux'), patch('handoffdesk.paths.Path.home', return_value=self.base):
            self.assertEqual(app_directory(), self.base / '.local/share/handoffdesk')

    def test_headless_schema_and_init_do_not_import_tk_or_write_package(self):
        code = """import json, sys
from handoffdesk.cli import main
assert main(['--app-dir', sys.argv[1], 'record', 'schema']) == 0
assert 'tkinter' not in sys.modules
assert main(['--app-dir', sys.argv[1], 'init']) == 0
assert 'tkinter' not in sys.modules
"""
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', code, str(self.app)],
                                cwd=Path(__file__).resolve().parent.parent, capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.app / 'data').exists())


if __name__ == '__main__':
    unittest.main()
