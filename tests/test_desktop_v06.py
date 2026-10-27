"""Node-file journeys through the real desktop, using disposable projects only.

The file pickers and OS launch calls are substituted. Node association,
history, search, the clipboard, Tk event handling and SQLite remain real.
"""
import sys
import hashlib
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from handoffdesk.desktop import App
from handoffdesk.service import Service


class DesktopV06Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='desktop-v06-', dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / '研究项目原始资料'
        self.other_source = self.base / '另一个项目'
        self.external = self.base / '跨项目共享资料'
        for folder in (self.source, self.other_source, self.external):
            folder.mkdir()
        self.guide = self.source / '中文原始研究说明.md'
        self.guide.write_text('只读来源。所有测试仅改变 HandoffDesk 中的关联。', encoding='utf-8')
        self.other_guide = self.other_source / '第二项目的记录.md'
        self.other_guide.write_text('用于发现项目切换时串用节点。', encoding='utf-8')
        self.long_folder = self.source / ('详细实验记录与阶段产物目录' * 3)
        self.long_folder.mkdir()
        self.attachments = []
        for index in range(5):
            path = self.long_folder / f'第{index + 1}份阶段成果与原始证据.txt'
            path.write_text(f'原始材料 {index + 1}', encoding='utf-8')
            self.attachments.append(path)
        self.shared_file = self.external / '合作材料的本地副本.txt'
        self.shared_file.write_text('共享目录仅用于关联，不会被复制或移动。', encoding='utf-8')
        self.source_roots = (self.source, self.other_source, self.external)
        self.source_baseline = self.source_state()
        self.appdir = self.base / 'app'
        (self.appdir / 'config').mkdir(parents=True)
        config = {'schema_version': 1, 'projects': [
            {'id': 'research', 'name': '中文研究项目', 'roots': {'main': str(self.source)},
             'sources': [{'id': 'guide', 'path': self.guide.name, 'format': 'text'}],
             'entries': [{'id': 'guide', 'type': 'open_source', 'source_id': 'guide'}],
             'worklines': [{'id': 'audit', 'name': '逐项核验证据', 'entry_ids': ['guide']}]},
            {'id': 'second', 'name': '第二项目', 'roots': {'main': str(self.other_source)},
             'sources': [{'id': 'guide', 'path': self.other_guide.name, 'format': 'text'}],
             'entries': [{'id': 'guide', 'type': 'open_source', 'source_id': 'guide'}]}]}
        (self.appdir / 'config' / 'projects.json').write_text(json.dumps(config), encoding='utf-8')
        self.service = Service(self.appdir)
        self.service.save_preferences({'auto_refresh': False})
        self.service.refresh('research', workline_id='audit')
        self.service.select_workline('research', 'audit')
        self.service.refresh('second')
        self.callback_errors = []
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest('Native Tk unavailable: ' + str(exc))
        self.root.report_callback_exception = lambda *error: self.callback_errors.append(error)
        self.app = App(self.service, self.root)
        self.root.geometry('840x620+50+50')
        self.root.update()
        self.app.tabs.select(4)
        self.root.update()
        self.panel = self.app.files_panel

    def tearDown(self):
        if hasattr(self, 'app') and not self.app.closed:
            self.pump(lambda: self.app.busy_count == 0)
            if self.app.last_dialog and not self.app.last_dialog.closed:
                self.app.last_dialog.close()
            for child in list(self.root.winfo_children()):
                if isinstance(child, tk.Toplevel):
                    child.destroy()
            self.app.close()
        self.assertEqual(self.callback_errors, [], 'Tk reported a callback failure')
        self.assertEqual(self.source_baseline, self.source_state(),
                         'Managing node links changed original project files')

    def source_state(self):
        return {str(path): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
                for folder in self.source_roots for path in folder.rglob('*') if path.is_file()}

    def pump(self, condition, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if condition():
                return
            time.sleep(.01)
        self.fail('Expected user-visible result did not arrive')

    def button(self, parent, caption):
        pending = list(parent.winfo_children())
        while pending:
            widget = pending.pop(0)
            if isinstance(widget, (tk.Button, ttk.Button)) and str(widget.cget('text')) == caption:
                return widget
            pending.extend(widget.winfo_children())
        self.fail('Missing action: ' + caption)

    def attach(self, paths):
        with patch('handoffdesk.file_panel.filedialog.askopenfilenames',
                   return_value=tuple(map(str, paths))) as picker, \
                patch.object(self.panel, '_ask_purpose', return_value='本节点的原始证据'):
            self.panel.add_button.invoke()
        picker.assert_called_once()
        self.root.update()

    def select_file(self, path):
        item = next(row for row in self.panel.last_result['files'] if row['path'] == str(path))
        self.panel.files_tree.selection_set(item['id'])
        self.panel.files_tree.focus(item['id'])
        self.panel.files_tree.event_generate('<<TreeviewSelect>>')
        self.root.update()
        return item

    def select_node(self, node_id):
        if not any(node['id'] == node_id for node in self.panel.nodes):
            self.panel.node_scope_var.set('全部工作线')
            self.panel.node_scope_combo.event_generate('<<ComboboxSelected>>')
            self.root.update()
        index = next(i for i, node in enumerate(self.panel.nodes) if node['id'] == node_id)
        self.panel.node_combo.current(index)
        self.panel.node_combo.event_generate('<<ComboboxSelected>>')
        self.root.update()
        self.assertEqual(self.panel.current_node_id, node_id)

    def new_node(self, title='复核完成的第一个节点', include_current=False):
        self.panel.new_node_button.invoke()
        self.root.update()
        dialog = self.panel.node_dialog
        dialog.title_var.set(title)
        dialog.description_text.insert('1.0', '只整理节点资料，不修改原始文件。')
        dialog.include_current_var.set(include_current)
        dialog.save_button.invoke()
        self.root.update()
        node = self.panel.last_result['node']
        self.assertEqual(node['title'], title)
        return node['id']

    def publish(self, kind='plan', next_action='继续逐项核对原始证据'):
        self.app.tabs.select(0)
        self.root.update()
        (self.app.plan_button if kind == 'plan' else self.app.handoff_button).invoke()
        editor = self.app.last_dialog
        editor.next_text.delete('1.0', 'end')
        editor.next_text.insert('1.0', next_action)
        if kind == 'handoff':
            editor.last_done_text.insert('1.0', '完成了第一批原始材料的核对。')
        editor.save_button.invoke()
        self.pump(lambda: editor.closed)
        self.app.tabs.select(4)
        self.root.update()
        return self.service.show('research', 'audit')['handoff']

    def assert_inside(self, widget):
        self.root.update_idletasks()
        self.assertTrue(widget.winfo_viewable(), str(widget) + ' is hidden')
        left, top = widget.winfo_rootx(), widget.winfo_rooty()
        self.assertGreaterEqual(left, self.root.winfo_rootx() - 1)
        self.assertGreaterEqual(top, self.root.winfo_rooty() - 1)
        self.assertLessEqual(left + widget.winfo_width(), self.root.winfo_rootx() + self.root.winfo_width() + 1,
                             str(widget) + ' is clipped at the right edge')
        self.assertLessEqual(top + widget.winfo_height(), self.root.winfo_rooty() + self.root.winfo_height() + 1,
                             str(widget) + ' is clipped at the bottom edge')

    def test_create_node_attach_many_files_and_directory_then_remove_link(self):
        node_id = self.new_node()
        self.attach(self.attachments + [self.shared_file])
        with patch('handoffdesk.file_panel.filedialog.askdirectory', return_value=str(self.long_folder)), \
                patch.object(self.panel, '_ask_purpose', return_value='阶段成果目录'):
            self.panel.folder_button.invoke()
        self.root.update()
        paths = {row['path'] for row in self.panel.last_result['files']}
        self.assertEqual(paths, set(map(str, self.attachments + [self.shared_file, self.long_folder])))
        self.assertEqual(len(self.panel.files_tree.get_children()), 7)
        self.assertEqual(self.panel.current_node_id, node_id)
        self.select_file(self.long_folder)
        self.assertIn('文件夹', self.panel.selection_var.get())
        with patch('handoffdesk.file_panel.filedialog.askopenfilenames', return_value=()) as picker:
            self.panel.add_button.invoke()
        self.assertEqual(picker.call_args.kwargs['initialdir'], str(self.long_folder))
        self.select_file(self.attachments[0])
        with patch('handoffdesk.file_panel.messagebox.askyesno', return_value=True):
            self.panel.remove_button.invoke()
        self.root.update()
        self.assertNotIn(str(self.attachments[0]), {row['path'] for row in self.panel.last_result['files']})
        self.assertTrue(self.attachments[0].is_file())
        self.assertEqual(self.panel.last_result['node']['file_count'], 6)

    @unittest.skipUnless(sys.platform == "win32", "Windows API assertion; Linux dispatch covered separately")
    def test_search_full_path_open_reveal_and_copy_are_available_from_selected_file(self):
        self.new_node()
        self.attach(self.attachments + [self.shared_file])
        self.panel.search_var.set('第4份')
        self.root.update()
        self.assertEqual(len(self.panel.files_tree.get_children()), 1)
        item = self.select_file(self.attachments[3])
        self.assertEqual(self.panel.path_text.get('1.0', 'end-1c'), str(self.attachments[3]))
        self.panel.path_text.tag_add('sel', '1.0', 'end-1c')
        self.assertEqual(self.panel.path_text.get('sel.first', 'sel.last'), str(self.attachments[3]))
        with patch('os.startfile') as opened:
            self.panel.open_button.invoke()
        opened.assert_called_once_with(str(self.attachments[3]))
        with patch('subprocess.Popen') as launched, patch('os.startfile') as started:
            self.panel.reveal_button.invoke()
        self.assertEqual(launched.call_count + started.call_count, 1)
        calls = repr(launched.call_args_list) + repr(started.call_args_list)
        self.assertIn(self.attachments[3].name, calls)
        self.panel.copy_button.invoke()
        self.assertEqual(self.root.clipboard_get(), item['path'])
        self.panel.search_var.set(str(self.external))
        self.root.update()
        self.assertEqual(len(self.panel.files_tree.get_children()), 1)
        self.select_file(self.shared_file)
        self.panel.search_var.set('完全不存在的文件名')
        self.root.update()
        self.assertEqual(self.panel.files_tree.get_children(), ())
        self.assertIn('disabled', self.panel.open_button.state())
        self.panel.search_var.set('')
        self.root.update()
        self.assertEqual(len(self.panel.files_tree.get_children()), 6)

    @unittest.skipUnless(sys.platform == "win32", "Windows API assertion; Linux dispatch covered separately")
    def test_missing_file_remains_visible_and_can_be_located_or_copied(self):
        self.new_node()
        missing = self.attachments[0]
        self.attach([missing])
        missing.unlink()  # Only this disposable fixture is deleted, never a real project file.
        self.source_baseline.pop(str(missing))
        self.panel.refresh_button.invoke()
        self.root.update()
        item = self.select_file(missing)
        self.assertFalse(item['exists'])
        row = self.panel.files_tree.item(item['id'])
        visible_text = ' '.join(map(str, row.get('values', ()))) + str(row.get('text', ''))
        self.assertTrue(any(word in visible_text for word in ('未找到', '不存在', '缺失', '已移动')),
                        'Missing file status is not visible in its list row: ' + visible_text)
        self.assertIn('disabled', self.panel.open_button.state())
        self.assertNotIn('disabled', self.panel.reveal_button.state())
        with patch('subprocess.Popen') as launched, patch('os.startfile') as started:
            self.panel.reveal_button.invoke()
        self.assertEqual(launched.call_count + started.call_count, 1)
        self.panel.copy_button.invoke()
        self.assertEqual(self.root.clipboard_get(), str(missing))
        self.assertEqual(self.panel.path_text.get('1.0', 'end-1c'), str(missing))

    def test_plan_and_handoff_nodes_keep_their_original_file_sets(self):
        self.select_node('current:audit')
        self.attach([self.attachments[0]])
        first = self.publish('plan')
        self.select_node('current:audit')
        self.select_file(self.attachments[0])
        with patch('handoffdesk.file_panel.messagebox.askyesno', return_value=True):
            self.panel.remove_button.invoke()
        self.attach([self.attachments[1]])
        second = self.publish('handoff')
        self.app.tabs.select(3)
        self.root.update()
        index = next(i for i, record in enumerate(self.app.history_records)
                     if record['revision'] == first['revision'])
        self.app.history_tree.selection_set(str(index))
        self.app.history_files_button.invoke()
        self.root.update()
        self.assertEqual(self.app.notebook.index(self.app.notebook.select()), 4)
        self.assertEqual(self.panel.current_node_id, f'record:audit:{first["revision"]}')
        first_paths = {row['path'] for row in self.panel.last_result['files']}
        self.assertIn(str(self.attachments[0]), first_paths)
        self.assertNotIn(str(self.attachments[1]), first_paths)
        self.select_node(f'record:audit:{second["revision"]}')
        second_paths = {row['path'] for row in self.panel.last_result['files']}
        self.assertIn(str(self.attachments[1]), second_paths)
        self.assertNotIn(str(self.attachments[0]), second_paths)
        self.assertEqual(self.app.workline_id, 'audit')
        self.app.tabs.select(0)
        self.root.update()
        self.button(self.app.card_tab, '节点文件').invoke()
        self.root.update()
        self.assertEqual(self.panel.current_node_id, 'current:audit')
        self.assertEqual(self.app.notebook.index(self.app.notebook.select()), 4)

    def test_project_and_workline_switches_do_not_mix_nodes_or_paths(self):
        manual_id = self.new_node()
        self.attach([self.attachments[0]])
        for unused in range(3):
            self.app.project_buttons['second'].invoke()
            self.app.tabs.select(4)
            self.root.update()
            self.assertEqual(self.panel.current_node_id, 'current:default')
            self.assertEqual(self.panel.last_result['node']['workline_id'], 'default')
            self.assertNotIn(str(self.attachments[0]), {row['path'] for row in self.panel.last_result['files']})
            self.app.project_buttons['research'].invoke()
            self.app.tabs.select(4)
            self.root.update()
            self.assertEqual(self.panel.last_result['node']['workline_id'], 'audit')
            default_index = next(i for i, line in enumerate(self.app.worklines) if line['id'] == 'default')
            self.app.workline_combo.current(default_index)
            self.app.workline_combo.event_generate('<<ComboboxSelected>>')
            self.root.update()
            self.assertEqual(self.panel.current_node_id, 'current:default')
            self.select_node(manual_id)
            self.assertEqual(self.panel.last_result['node']['workline_id'], 'audit')
            self.assertEqual(self.app.workline_id, 'default')
            audit_index = next(i for i, line in enumerate(self.app.worklines) if line['id'] == 'audit')
            self.app.workline_combo.current(audit_index)
            self.app.workline_combo.event_generate('<<ComboboxSelected>>')
            self.root.update()
            self.assertEqual(self.panel.current_node_id, 'current:audit')

    def test_small_window_keeps_node_picker_paths_and_actions_reachable(self):
        self.new_node('原始结果核验完成后保留的阶段文件节点' * 3)
        self.attach(self.attachments)
        self.select_file(self.attachments[4])
        self.assertEqual((self.root.winfo_width(), self.root.winfo_height()), (840, 620))
        self.assertEqual(self.app.notebook.index(self.app.files_tab), 4)
        self.assertEqual(self.app.notebook.index(self.app.history_tab), 3)
        for control in (self.panel.node_combo, self.panel.files_tree, self.panel.path_text,
                        self.panel.new_node_button, self.panel.add_button, self.panel.folder_button,
                        self.panel.open_button, self.panel.reveal_button, self.panel.copy_button,
                        self.panel.remove_button, self.panel.refresh_button, self.panel.info_button):
            self.assert_inside(control)
        self.assertGreaterEqual(self.panel.files_tree.winfo_height(), 75)
        self.panel.path_text.tag_add('sel', '1.0', 'end-1c')
        self.assertEqual(self.panel.path_text.get('sel.first', 'sel.last'), str(self.attachments[4]))
        before = self.panel.path_text.get('1.0', 'end-1c')
        self.panel.path_text.insert('end', '不应写入路径')
        self.assertEqual(self.panel.path_text.get('1.0', 'end-1c'), before)
        self.panel.info_button.invoke()
        self.root.update()
        dialog = self.panel.node_info_dialog
        explanation = dialog.detail_text.get('1.0', 'end-1c')
        self.assertIn(self.panel.last_result['node']['title'], explanation)
        self.assertIn(self.panel.last_result['node']['description'], explanation)
        self.assertIn(self.panel.last_result['notice'], explanation)
        self.assertIn(str(self.attachments[4]), explanation)
        self.assertIn('本节点的原始证据', explanation)
        self.assert_inside(dialog.detail_text)
        self.button(dialog, '关闭').invoke()

    def test_cancelled_file_picker_and_node_dialog_leave_no_records(self):
        before = self.service.list_file_nodes('research')
        with patch('handoffdesk.file_panel.filedialog.askopenfilenames', return_value=()), \
                patch.object(self.panel, '_ask_purpose') as purpose:
            self.panel.add_button.invoke()
        purpose.assert_not_called()
        self.panel.new_node_button.invoke()
        self.root.update()
        dialog = self.panel.node_dialog
        dialog.title_var.set('应取消的阶段节点')
        window = getattr(dialog, 'window', dialog)
        self.button(window, '取消').invoke()
        self.root.update()
        self.assertEqual(self.service.list_file_nodes('research'), before)

    def test_purpose_dialog_save_cancel_and_search_use_the_real_form(self):
        self.new_node()

        def accept_purpose():
            dialog = self.panel.purpose_dialog
            dialog.purpose_var.set('本阶段待复核的原始结果')
            dialog.save_button.invoke()

        self.root.after(50, accept_purpose)
        with patch('handoffdesk.file_panel.filedialog.askopenfilenames',
                   return_value=(str(self.attachments[0]),)):
            self.panel.add_button.invoke()
        self.root.update()
        item = self.select_file(self.attachments[0])
        self.assertEqual(item['purpose'], '本阶段待复核的原始结果')
        self.panel.search_var.set('待复核')
        self.root.update()
        self.assertEqual(len(self.panel.files_tree.get_children()), 1)
        self.panel.search_var.set('')

        def cancel_purpose():
            dialog = self.panel.purpose_dialog
            dialog.purpose_var.set('取消后不得写入')
            self.button(dialog, '取消').invoke()

        self.root.after(50, cancel_purpose)
        with patch('handoffdesk.file_panel.filedialog.askopenfilenames',
                   return_value=(str(self.attachments[1]),)):
            self.panel.add_button.invoke()
        self.root.update()
        self.assertEqual({row['path'] for row in self.panel.last_result['files']}, {str(self.attachments[0])})


if __name__ == '__main__':
    unittest.main()
