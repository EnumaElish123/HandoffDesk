"""Historical cross-system locations remain visible without becoming openable."""
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import tempfile
import unittest
from unittest.mock import patch

from handoffdesk.node_files import _foreign_history_path, merge_files, path_key
from handoffdesk.service import Service


class ForeignHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='foreign-history-', dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.root = base / '业务 中文'; self.root.mkdir()
        app = base / '工具'; (app / 'config').mkdir(parents=True)
        config = {'schema_version': 1, 'projects': [
            {'id': 'alpha', 'name': '甲', 'roots': {'main': str(self.root)}, 'sources': [], 'entries': []}]}
        (app / 'config' / 'projects.json').write_text(json.dumps(config), encoding='utf-8')
        self.service = Service(app)

    def legacy(self, raw):
        store = self.service.store
        generation = store.begin_refresh('alpha')
        sid = store.finish_refresh('alpha', generation, {'observations': [
            {'source_id': 'old', 'source_ref': raw, 'status': 'ok'}], 'entries': []})
        store.save_handoff('alpha', {'current_task': '历史记录', 'last_done': '已保存',
            'next_action': '检查文件', 'blockers': [], 'origin': 'user_directed', 'source_snapshot_id': sid}, 0)
        return sid

    def check_history(self, raw, windows, pure):
        self.legacy(raw)
        with patch('handoffdesk.platform_io.is_windows', return_value=windows), \
             patch('handoffdesk.platform_io.open_document') as opened, \
             patch('handoffdesk.platform_io.reveal_path') as revealed:
            files = self.service.get_node_files('alpha', 'record:default:1')['files']
            self.assertEqual(len(files), 1)
            item = files[0]
            self.assertEqual(item['path'], raw)
            self.assertEqual(item['name'], pure.name)
            self.assertEqual(item['parent'], str(pure.parent))
            self.assertEqual(item['status'], '位置不可访问')
            self.assertFalse(item['exists'])
            self.assertEqual(self.service.open_node_file('alpha', 'record:default:1', item['id'], 'copy'), raw)
            for action in ('open', 'reveal'):
                with self.assertRaises(ValueError):
                    self.service.open_node_file('alpha', 'record:default:1', item['id'], action)
            restored = self.service.restore_handoff('alpha', 1, 1)
            self.assertEqual(restored['file_manifest'][0]['path'], raw)
            self.assertEqual(self.service.get_node_files('alpha', 'record:default:2')['files'][0]['path'], raw)
            opened.assert_not_called(); revealed.assert_not_called()

    def test_windows_history_on_linux_branch_is_visible_copyable_and_restorable(self):
        raw = 'D:/原项目/结果 报告.txt'
        self.check_history(raw, False, PureWindowsPath(raw))

    def test_linux_history_on_windows_branch_is_visible_copyable_and_restorable(self):
        raw = '/home/user/原项目/结果 报告.txt'
        self.check_history(raw, True, PurePosixPath(raw))

    def test_relocation_preserves_original_history_and_snapshot(self):
        raw = '/home/user/历史/结果.txt' if os.name == 'nt' else r'D:\历史\结果.txt'
        sid = self.legacy(raw)
        record_before = self.service.store.history('alpha')
        snapshot_before = self.service.store.snapshot(sid)
        item = self.service.get_node_files('alpha', 'record:default:1')['files'][0]
        target = self.root / '已迁移 结果.txt'; target.write_text('真实文件', encoding='utf-8')
        result = self.service.relocate_node_file('alpha', 'record:default:1', item['id'], target)
        self.assertEqual(len(result['files']), 1)
        self.assertEqual(result['files'][0]['path'], str(target))
        self.assertEqual(result['files'][0]['relinked_from'], raw)
        self.assertEqual(result['files'][0]['status'], '可用')
        self.assertEqual(self.service.store.history('alpha'), record_before)
        self.assertEqual(self.service.store.snapshot(sid), snapshot_before)

    def test_new_associations_and_current_sources_still_require_native_paths(self):
        raw = '/home/user/历史/结果.txt' if os.name == 'nt' else r'D:\历史\结果.txt'
        self.legacy(raw)
        self.assertEqual(self.service.get_node_files('alpha', 'current:default')['files'], [])
        with self.assertRaises(ValueError):
            self.service.add_node_files('alpha', 'current:default', [raw])

    def test_foreign_history_does_not_accept_urls_unc_devices_or_invalid_windows_paths(self):
        for windows in (False, True):
            with patch('handoffdesk.platform_io.is_windows', return_value=windows):
                for raw in ('https://example.org/a', 'file:///tmp/a', '//server/share/a',
                            r'\\server\share\a', r'\\?\C:\secret', '/tmp/a\nfile', 'relative/file'):
                    with self.subTest(windows=windows, path=raw):
                        self.assertIsNone(_foreign_history_path(raw))
                        self.assertIsNone(self.service.node_files._candidate('alpha', raw, 'source', strict=False))
        with patch('handoffdesk.platform_io.is_windows', return_value=False):
            for raw in (r'C:\temp\NUL.txt', r'C:\temp\file:stream', r'C:\temp\file?', r'C:relative'):
                self.assertIsNone(_foreign_history_path(raw))
                self.assertIsNone(self.service.node_files._candidate('alpha', raw, 'source', strict=False))

    def test_overlay_keys_keep_windows_case_rules_and_posix_case_sensitivity(self):
        self.assertEqual(path_key('D:/Archive/RESULT.txt'), r'd:\archive\result.txt')
        self.assertNotEqual(path_key('/Archive/RESULT.txt'), path_key('/archive/result.txt'))
        self.assertEqual(merge_files([{'path': 'D:/Archive/RESULT.txt'}],
                                    {'hidden': [r'd:\archive\result.txt']}), [])


if __name__ == '__main__':
    unittest.main()
