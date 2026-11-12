"""Onboarding uses isolated state and preserves registered project files."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from handoffdesk.bootstrap import initialize, add_project
from handoffdesk.cli import main
from handoffdesk.agent_cli import configure_roots
from handoffdesk.registry import Registry
from handoffdesk.workspace import Workspace
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

    @patch('handoffdesk.workspace._git_identity', return_value=None)
    def test_explicit_nested_registration_in_both_orders(self, _git_identity):
        child = self.project / 'nested'
        runs = child / 'runs'
        runs.mkdir(parents=True)
        roots = {'parent': self.project, 'child': child}
        for first, second in [('parent', 'child'), ('child', 'parent')]:
            with self.subTest(first=first):
                app = self.base / first
                workspace = Workspace(app)
                initialize(app, project_id=first, root=roots[first])
                if first == 'parent':
                    inherited = workspace.connect(runs)
                    self.assertEqual(inherited['project']['id'], 'parent')
                    self.assertFalse(inherited['added'])
                else:
                    with self.assertRaisesRegex(ValueError, '包含已登记项目'):
                        workspace.connect(self.project)
                add_project(app, second, root=roots[second])
                self.assertEqual(workspace.inspect(self.project)['project']['id'], 'parent')
                self.assertEqual(workspace.inspect(runs)['project']['id'], 'child')

    def test_add_rejects_duplicate_canonical_or_unsafe_root_without_changes(self):
        initialize(self.app, project_id='sample', root=self.project)
        nested = self.project / 'nested'
        nested.mkdir()
        config = self.app / 'config/projects.json'
        before = config.read_bytes()
        for root in [self.project, nested / '..', self.base, self.app / 'config']:
            with self.subTest(root=root), self.assertRaises(ValueError):
                add_project(self.app, 'other', root=root)
            self.assertEqual(config.read_bytes(), before)

    def test_named_root_mapping_and_relocation_share_effective_ownership(self):
        child = self.project / 'child'
        other = self.base / 'other'
        child.mkdir()
        other.mkdir()
        initialize(self.app, project_id='parent', root=self.project)
        add_project(self.app, 'child', root=other)
        config = self.app / 'config/projects.json'
        base = json.loads(config.read_text(encoding='utf-8'))
        base['projects'][1]['roots']['docs'] = str(other / 'docs')
        config.write_text(json.dumps(base), encoding='utf-8')
        workspace = Workspace(self.app)
        workspace.relocate('child', child)
        self.assertEqual(workspace.inspect(child)['project']['id'], 'child')
        with self.assertRaisesRegex(ValueError, '同一根目录'):
            workspace.relocate('parent', child)
        self.assertEqual(workspace.inspect(self.project)['project']['id'], 'parent')

        candidate = self.base / 'roots.input.json'
        target = config.with_name('roots.local.json')
        mapped = {'schema_version': 1, 'projects': {'child': {'docs': str(self.project / 'docs')}}}
        (self.project / 'docs').mkdir()
        candidate.write_text(json.dumps(mapped), encoding='utf-8')
        configure_roots(self.app, candidate)
        self.assertEqual(workspace.inspect(self.project / 'docs')['project']['id'], 'child')
        original = target.read_bytes()
        with self.assertRaisesRegex(ValueError, '同一根目录'):
            workspace.relocate('parent', self.project / 'docs')
        self.assertEqual(target.read_bytes(), original)
        mapped['projects']['child']['docs'] = str(child / '..')
        candidate.write_text(json.dumps(mapped), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '同一根目录'):
            configure_roots(self.app, candidate)
        self.assertEqual(target.read_bytes(), original)
        # A previously written conflicting local mapping is rejected on load too.
        target.write_bytes(candidate.read_bytes())
        with self.assertRaisesRegex(ValueError, '同一根目录'):
            Registry(config)
        mapped['projects']['child']['docs'] = str(other / 'docs')
        candidate.write_text(json.dumps(mapped), encoding='utf-8')
        configure_roots(self.app, candidate)
        self.assertEqual(Registry(config).get('child')['roots']['docs'], str(other / 'docs'))

    def test_new_business_bindings_cannot_be_inside_state(self):
        hidden = self.app / 'private'
        hidden.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, '状态目录'):
            initialize(self.app, project_id='private', root=hidden)
        initialize(self.app, project_id='outside', root=self.project)
        with self.assertRaisesRegex(ValueError, '状态目录'):
            add_project(self.app, 'private', root=hidden)
        with self.assertRaisesRegex(ValueError, '状态目录'):
            Workspace(self.app).relocate('outside', hidden)
        candidate = self.base / 'roots.input.json'
        candidate.write_text(json.dumps({'schema_version': 1, 'projects': {
            'outside': {'main': str(hidden)}}}), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '状态目录'):
            configure_roots(self.app, candidate)
        self.assertFalse((self.app / 'config/roots.local.json').exists())
        self.assertEqual(Workspace(self.app).inspect(self.project)['project']['id'], 'outside')

    def test_builtin_example_can_restore_its_original_mapping(self):
        initialize(self.app)
        original = self.app / 'examples/demo-project'
        candidate = self.base / 'roots.input.json'
        for root in (self.project, original):
            candidate.write_text(json.dumps({'schema_version': 1, 'projects': {
                'demo': {'main': str(root)}}}), encoding='utf-8')
            self.assertEqual(configure_roots(self.app, candidate)['projects']['demo']['main'], str(root))
        self.assertEqual(Workspace(self.app).inspect(original)['project']['id'], 'demo')

    def test_mapping_rechecks_ownership_under_registration_lock(self):
        from handoffdesk.bootstrap import _publish, configuration_lock
        from handoffdesk.paths import validate_configuration_writes

        initialize(self.app, project_id='first', root=self.project)
        claimed = self.base / 'claimed'
        claimed.mkdir()
        candidate = self.base / 'roots.input.json'
        candidate.write_text(json.dumps({'schema_version': 1, 'projects': {
            'first': {'main': str(claimed)}}}), encoding='utf-8')
        config = self.app / 'config/projects.json'
        preflight = threading.Event()

        def checked(*args):
            validate_configuration_writes(*args)
            preflight.set()

        with ThreadPoolExecutor(max_workers=1) as pool, patch(
                'handoffdesk.paths.validate_configuration_writes', side_effect=checked):
            with configuration_lock(self.app):
                update = pool.submit(configure_roots, self.app, candidate)
                self.assertTrue(preflight.wait(5))
                projects = json.loads(config.read_text(encoding='utf-8'))
                projects['projects'].append({'id': 'second', 'name': 'second', 'roots': {'main': str(claimed)}})
                _publish(self.app, config, projects, create=False)
            with self.assertRaisesRegex(ValueError, '同一根目录'):
                update.result(timeout=5)
        self.assertFalse(config.with_name('roots.local.json').exists())
        self.assertEqual(Workspace(self.app).inspect(claimed)['project']['id'], 'second')

    @unittest.skipIf(os.name == 'nt', 'Symlink privileges vary on Windows')
    def test_symlink_alias_cannot_occupy_another_projects_root(self):
        initialize(self.app, project_id='parent', root=self.project)
        child = self.project / 'child'
        child.mkdir()
        alias = self.base / 'alias'
        alias.symlink_to(child, target_is_directory=True)
        add_project(self.app, 'child', root=alias)
        self.assertEqual(Workspace(self.app).inspect(alias)['project']['id'], 'child')
        with self.assertRaisesRegex(ValueError, '同一根目录'):
            add_project(self.app, 'duplicate', root=child)

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

    @patch('handoffdesk.integration.registered_app_directory', return_value=None)
    def test_windows_and_linux_platform_defaults(self, registered_directory):
        # Platform defaults apply only when no installation has registered state.
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.base)}, clear=True), patch('handoffdesk.paths.sys.platform', 'win32'):
            self.assertEqual(app_directory(), self.base / 'HandoffDesk')
        with patch.dict(os.environ, {'XDG_DATA_HOME': str(self.base)}, clear=True), patch('handoffdesk.paths.sys.platform', 'linux'):
            self.assertEqual(app_directory(), self.base / 'handoffdesk')
        with patch.dict(os.environ, {'XDG_DATA_HOME': 'relative'}, clear=True), patch('handoffdesk.paths.sys.platform', 'linux'), patch('handoffdesk.paths.Path.home', return_value=self.base):
            self.assertEqual(app_directory(), self.base / '.local/share/handoffdesk')
        self.assertEqual(registered_directory.call_count, 3)

    def test_headless_schema_and_init_do_not_import_tk_or_write_package(self):
        code = """import json, sys
from handoffdesk.cli import main
from handoffdesk.agent_cli import configure_roots
from handoffdesk.registry import Registry
from handoffdesk.workspace import Workspace
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
