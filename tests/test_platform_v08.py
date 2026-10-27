import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

from handoffdesk import platform_io
from handoffdesk.registry import Registry, RegistryError
from handoffdesk.service import Service


class PlatformIOTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='平台 中文 空格 ', dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.path = self.folder / '阶段报告, 中文空格.txt'
        self.path.write_text('文档内容', encoding='utf-8')
        self.linux = patch('handoffdesk.platform_io.is_windows', return_value=False)
        self.linux.start(); self.addCleanup(self.linux.stop)

    def test_linux_document_arguments_preserve_unicode_and_spaces(self):
        with patch.dict(os.environ, {'DISPLAY': ':99'}, clear=True), \
             patch('handoffdesk.platform_io.shutil.which', return_value='/usr/bin/xdg-open'), \
             patch('handoffdesk.platform_io.subprocess.Popen') as opened:
            platform_io.open_document(self.path)
        opened.assert_called_once_with(['/usr/bin/xdg-open', str(self.path.absolute())], shell=False)

    def test_linux_gio_is_available_without_xdg_open(self):
        with patch.dict(os.environ, {'WAYLAND_DISPLAY': 'wayland-0'}, clear=True), \
             patch('handoffdesk.platform_io.shutil.which', side_effect=lambda name: '/usr/bin/gio' if name == 'gio' else None), \
             patch('handoffdesk.platform_io.subprocess.Popen') as opened:
            platform_io.open_document(self.path)
        self.assertEqual(opened.call_args.args[0], ['/usr/bin/gio', 'open', str(self.path.absolute())])

    def test_reveal_uses_file_manager_dbus_with_encoded_uri(self):
        with patch.dict(os.environ, {'DISPLAY': ':99'}, clear=True), \
             patch('handoffdesk.platform_io.shutil.which', return_value='/usr/bin/gdbus'), \
             patch('handoffdesk.platform_io.subprocess.run', return_value=Mock(returncode=0)) as dbus, \
             patch('handoffdesk.platform_io.subprocess.Popen') as opened:
            platform_io.reveal_path(self.path)
        args = dbus.call_args.args[0]
        self.assertIn('org.freedesktop.FileManager1.ShowItems', args)
        self.assertEqual(json.loads(args[-2]), [self.path.absolute().as_uri()])
        self.assertFalse(dbus.call_args.kwargs['shell'])
        opened.assert_not_called()

    def test_dbus_unavailable_and_timeout_fall_back_to_parent(self):
        for outcome in (Mock(returncode=1), subprocess.TimeoutExpired('gdbus', 3)):
            with self.subTest(outcome=type(outcome).__name__), \
                 patch.dict(os.environ, {'DISPLAY': ':99'}, clear=True), \
                 patch('handoffdesk.platform_io.shutil.which', return_value='/usr/bin/gdbus'), \
                 patch('handoffdesk.platform_io.subprocess.run', **({'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome})), \
                 patch('handoffdesk.platform_io.open_document') as opened:
                platform_io.reveal_path(self.path)
                opened.assert_called_once_with(self.folder)

    def test_missing_nested_path_finds_existing_parent(self):
        with patch('handoffdesk.platform_io.open_document') as opened:
            platform_io.reveal_path(self.folder / '不存在目录' / '原记录.txt')
        opened.assert_called_once_with(self.folder)

    def test_headless_never_launches_external_apps(self):
        with patch.dict(os.environ, {}, clear=True), patch('handoffdesk.platform_io.subprocess.Popen') as opened:
            for method in (platform_io.open_document, platform_io.open_text, platform_io.reveal_path):
                with self.subTest(method=method.__name__), self.assertRaisesRegex(ValueError, '服务器'):
                    method(self.path)
        opened.assert_not_called()

    def test_script_uses_only_internal_read_only_viewer(self):
        script = self.folder / '不要执行.sh'
        script.write_text('touch SHOULD_NOT_EXIST', encoding='utf-8')
        with patch.dict(os.environ, {'DISPLAY': ':99'}, clear=True), \
             patch('handoffdesk.platform_io.importlib.util.find_spec', return_value=object()), \
             patch('handoffdesk.platform_io.subprocess.Popen') as opened:
            platform_io.open_text(script)
        args = opened.call_args.args[0]
        self.assertEqual(args[0], sys.executable)
        self.assertEqual(Path(args[-2]).name, 'text_viewer.py')
        self.assertEqual(args[-1], str(script.absolute()))
        self.assertFalse(opened.call_args.kwargs['shell'])
        self.assertFalse((self.folder / 'SHOULD_NOT_EXIST').exists())

    def test_missing_tk_or_desktop_opener_reports_actionable_message(self):
        with patch.dict(os.environ, {'DISPLAY': ':99'}, clear=True), \
             patch('handoffdesk.platform_io.importlib.util.find_spec', return_value=None):
            with self.assertRaisesRegex(ValueError, 'python3-tk'):
                platform_io.open_text(self.path)
            with self.assertRaisesRegex(ValueError, 'python3-tk'):
                platform_io.ensure_desktop(tk_required=True)
        with patch.dict(os.environ, {'DISPLAY': ':99'}, clear=True), \
             patch('handoffdesk.platform_io.shutil.which', return_value=None):
            with self.assertRaisesRegex(ValueError, 'xdg-utils'):
                platform_io.open_document(self.path)


class LocalRootMappingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='本机目录 ', dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.app = Path(self.temporary.name)
        self.config_dir = self.app / 'config'; self.config_dir.mkdir()
        self.old_root = self.app / '旧业务'; self.old_root.mkdir()
        self.new_root = self.app / '新业务 中文'; self.new_root.mkdir()
        self.config = self.config_dir / 'projects.json'
        self.payload = {'schema_version': 1, 'projects': [{'id': 'alpha', 'name': '项目', 'roots': {'main': str(self.old_root)},
            'sources': [{'id': 'notes', 'root': 'main', 'path': '记录.txt', 'format': 'text'}], 'entries': []}]}
        self.config.write_text(json.dumps(self.payload, ensure_ascii=False), encoding='utf-8')
        self.original = self.config.read_bytes()
        self.mapping = {'schema_version': 1, 'projects': {'alpha': {'main': str(self.new_root)}}}

    def test_mapping_applies_without_modifying_shared_config(self):
        before = Registry(self.config)
        (self.config_dir / 'roots.local.json').write_text(json.dumps(self.mapping), encoding='utf-8')
        after = Registry(self.config)
        self.assertEqual(after.get('alpha')['roots']['main'], str(self.new_root))
        self.assertEqual(after.source_path(after.get('alpha'), after.get('alpha')['sources'][0]), self.new_root / '记录.txt')
        self.assertNotEqual(before.fingerprint, after.fingerprint)
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertEqual(before.fingerprint, hashlib.sha256(self.original).hexdigest())

    def test_preview_mapping_does_not_write_file(self):
        registry = Registry(self.config, roots_override=self.mapping)
        self.assertEqual(registry.get('alpha')['roots']['main'], str(self.new_root))
        self.assertFalse((self.config_dir / 'roots.local.json').exists())

    def test_mapping_preview_and_disk_share_size_limit(self):
        oversized = {'schema_version': 1, 'projects': {'alpha': {'main': str(self.new_root) + '字' * 100000}}}
        with self.assertRaisesRegex(RegistryError, '256 KiB'):
            Registry(self.config, roots_override=oversized)
        (self.config_dir / 'roots.local.json').write_text(json.dumps(oversized, ensure_ascii=False), encoding='utf-8')
        with self.assertRaisesRegex(RegistryError, '256 KiB'):
            Registry(self.config)

    def test_unknown_project_root_relative_and_wrong_schema_rejected(self):
        candidates = [
            {'schema_version': True, 'projects': {}},
            {'schema_version': 1, 'projects': {'unknown': {'main': str(self.new_root)}}},
            {'schema_version': 1, 'projects': {'alpha': {'unknown': str(self.new_root)}}},
            {'schema_version': 1, 'projects': {'alpha': {'main': '../relative'}}},
            {'schema_version': 1, 'projects': {'alpha': {'main': str(self.new_root)}}, 'extra': True},
        ]
        for value in candidates:
            with self.subTest(value=value), self.assertRaises(RegistryError):
                Registry(self.config, roots_override=value)

    def test_mapping_cannot_redirect_data_into_project(self):
        mapping = {'schema_version': 1, 'projects': {'alpha': {'main': str(self.app)}}}
        (self.config_dir / 'roots.local.json').write_text(json.dumps(mapping), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '数据目录'):
            Service(self.app)
        self.assertFalse((self.app / 'data').exists())

    def test_mapping_cannot_turn_config_or_actual_data_into_business_root(self):
        from handoffdesk.agent_cli import configure_roots
        candidate = self.app / 'mapping-input.json'
        target = self.config_dir / 'roots.local.json'
        configure_roots(self.app, self._mapping_file(candidate, self.new_root))
        previous = target.read_bytes(), target.stat().st_mtime_ns
        for root, actual_data in [(self.config_dir, None), (self.config_dir / 'nested', None),
                                  (self.new_root, self.new_root / 'tool-data')]:
            with self.subTest(root=root):
                self._mapping_file(candidate, root)
                with self.assertRaisesRegex(ValueError, '配置或数据目录'):
                    configure_roots(self.app, candidate, actual_data)
                self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), previous)
                self.assertEqual(self.config.read_bytes(), self.original)
        self.assertFalse((self.new_root / 'tool-data').exists())

    def _mapping_file(self, path, root):
        path.write_text(json.dumps({'schema_version': 1, 'projects': {'alpha': {'main': str(root)}}}), encoding='utf-8')
        return path

    def test_server_can_list_and_copy_without_tk_import(self):
        notes = self.old_root / '记录.txt'; notes.write_text('只读来源', encoding='utf-8')
        code = """import sys
from pathlib import Path
from handoffdesk.service import Service
s=Service(Path(sys.argv[1]),Path(sys.argv[1])/'data')
assert s.list_projects()[0]['id']=='alpha'
node=s.get_node_files('alpha','current:default')
f=node['files'][0]
assert s.open_node_file('alpha','current:default',f['id'],'copy')==str(Path(sys.argv[1])/'旧业务'/'记录.txt')
assert 'tkinter' not in sys.modules
print('headless passed')
"""
        env = dict(os.environ); env.pop('DISPLAY', None); env.pop('WAYLAND_DISPLAY', None)
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', code, str(self.app)],
                                capture_output=True, text=True, encoding='utf-8', env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('headless passed', result.stdout)

    @unittest.skipIf(os.name == 'nt', 'Foreign Windows-root rejection is exercised on Linux')
    def test_windows_roots_need_mapping_on_linux(self):
        self.payload['projects'][0]['roots']['main'] = r'D:\项目\alpha'
        self.config.write_text(json.dumps(self.payload), encoding='utf-8')
        with self.assertRaisesRegex(RegistryError, 'configure-roots'):
            Registry(self.config)
        mapped = Registry(self.config, roots_override=self.mapping)
        self.assertEqual(mapped.get('alpha')['roots']['main'], str(self.new_root))


if __name__ == '__main__':
    unittest.main()
