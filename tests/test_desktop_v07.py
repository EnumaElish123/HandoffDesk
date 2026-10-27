"""User journeys for editing and finding node files, with disposable data."""
import sys
import copy
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

import test_desktop_v06 as previous


class DesktopV07Journeys(unittest.TestCase):
    # Reuse fixture helpers without inheriting or rediscovering the v0.6 tests.
    setUp = previous.DesktopV06Tests.setUp
    tearDown = previous.DesktopV06Tests.tearDown
    source_state = previous.DesktopV06Tests.source_state
    pump = previous.DesktopV06Tests.pump
    button = previous.DesktopV06Tests.button
    attach = previous.DesktopV06Tests.attach
    select_file = previous.DesktopV06Tests.select_file
    new_node = previous.DesktopV06Tests.new_node
    publish = previous.DesktopV06Tests.publish
    assert_inside = previous.DesktopV06Tests.assert_inside

    def select_node(self, node_id):
        self.panel.show_node(node_id)
        self.root.update()
        self.assertEqual(self.panel.current_node_id, node_id)

    def more(self, label_part):
        menu = self.panel.more_menu
        index = next(index for index in range(menu.index('end') + 1)
                     if menu.type(index) != 'separator' and label_part in menu.entrycget(index, 'label'))
        self.assertNotEqual(menu.entrycget(index, 'state'), 'disabled', label_part)
        menu.invoke(index)
        self.root.update()

    def labels(self, parent):
        values = []
        for widget in parent.winfo_children():
            if isinstance(widget, (tk.Label, ttk.Label, ttk.Checkbutton)):
                values.append(str(widget.cget('text')))
            values.extend(self.labels(widget))
        return values

    def switch_workline(self, workline_id):
        index = next(i for i, line in enumerate(self.app.worklines) if line['id'] == workline_id)
        self.app.workline_combo.current(index)
        self.app.workline_combo.event_generate('<<ComboboxSelected>>')
        self.root.update()
        self.assertEqual(self.app.workline_id, workline_id)

    def test_scope_filter_and_cross_line_browsing_make_new_node_ownership_clear(self):
        audit_node = self.new_node('属于证据核验的节点')
        self.switch_workline('default')
        self.assertEqual(self.panel.node_scope_var.get(), '当前工作线')
        self.assertTrue(all(node['workline_id'] == 'default' for node in self.panel.nodes))
        self.assertNotIn(audit_node, {node['id'] for node in self.panel.nodes})
        self.select_node(audit_node)
        self.assertEqual(self.panel.node_scope_var.get(), '全部工作线')
        self.assertEqual(self.app.workline_id, 'default')
        self.panel.new_node_button.invoke()
        self.root.update()
        dialog = self.panel.node_dialog
        text = '\n'.join(self.labels(dialog))
        active_name = next(line['name'] for line in self.app.worklines if line['id'] == 'default')
        self.assertIn('归属', text)
        self.assertIn(active_name, text)
        dialog.title_var.set('明确留给当前工作线的新阶段')
        dialog.include_current_var.set(False)
        dialog.save_button.invoke()
        self.root.update()
        self.assertEqual(self.panel.last_result['node']['workline_id'], 'default')
        self.assertEqual(self.app.workline_id, 'default')
        self.switch_workline('audit')
        self.assertEqual(self.panel.node_scope_var.get(), '当前工作线')
        self.assertEqual(self.panel.current_node_id, 'current:audit')

    def test_edit_purpose_cancel_search_duplicate_and_explicit_clear(self):
        node = self.new_node()
        self.attach([self.attachments[0]])
        original = self.select_file(self.attachments[0])
        self.more('修改用途')
        dialog = self.panel.edit_purpose_dialog
        self.assertEqual(dialog.purpose_var.get(), original['purpose'])
        dialog.purpose_var.set('取消的说明不能写入')
        self.button(dialog, '取消').invoke()
        self.root.update()
        self.assertEqual(self.service.get_node_files('research', node)['files'][0]['purpose'], original['purpose'])
        self.more('修改用途')
        dialog = self.panel.edit_purpose_dialog
        dialog.purpose_var.set('核验完成，待合入阶段报告')
        dialog.save_button.invoke()
        self.root.update()
        self.panel.search_var.set('待合入')
        self.root.update()
        self.assertEqual(len(self.panel.files_tree.get_children()), 1)
        self.select_file(self.attachments[0])
        with patch('handoffdesk.file_panel.filedialog.askopenfilenames', return_value=(str(self.attachments[0]),)), \
                patch.object(self.panel, '_ask_purpose', return_value=''):
            self.panel.add_button.invoke()
        self.root.update()
        self.assertEqual(self.panel.last_result['files'][0]['purpose'], '核验完成，待合入阶段报告')
        self.select_file(self.attachments[0])
        self.more('修改用途')
        dialog = self.panel.edit_purpose_dialog
        dialog.purpose_var.set('')
        dialog.save_button.invoke()
        self.root.update()
        self.assertEqual(self.service.get_node_files('research', node)['files'][0]['purpose'], '')

    def test_rename_custom_node_preserves_id_line_files_and_cancelled_input(self):
        node = self.new_node('待完善的阶段名称')
        self.attach([self.attachments[0]])
        self.more('修改节点名称')
        dialog = self.panel.rename_dialog
        self.assertEqual(dialog.title_var.get(), '待完善的阶段名称')
        dialog.title_var.set('第一阶段证据复核完成')
        dialog.description_text.delete('1.0', 'end')
        dialog.description_text.insert('1.0', '结果已核对，文件仍保留在原目录。')
        dialog.save_button.invoke()
        self.root.update()
        saved = self.service.get_node_files('research', node)
        self.assertEqual(saved['node']['title'], '第一阶段证据复核完成')
        self.assertEqual(saved['node']['description'], '结果已核对，文件仍保留在原目录。')
        self.assertEqual(saved['node']['workline_id'], 'audit')
        self.assertEqual(self.panel.current_node_id, node)
        self.assertIn('第一阶段证据复核完成', self.panel.node_combo.get())
        self.more('修改节点名称')
        dialog = self.panel.rename_dialog
        dialog.title_var.set('')
        dialog.save_button.invoke()
        self.root.update()
        self.assertTrue(dialog.winfo_exists())
        self.assertTrue(dialog.error_var.get())
        self.button(dialog, '取消').invoke()
        self.root.update()
        self.assertEqual(self.service.get_node_files('research', node), saved)

    def test_missing_file_relocation_cancel_then_success_keeps_historical_paths(self):
        self.select_node('current:audit')
        self.attach([self.attachments[0]])
        first = self.publish('handoff')
        old_record = copy.deepcopy(first)
        self.select_node('current:audit')
        missing = self.attachments[0]
        missing.unlink()  # Delete only this disposable fixture to model a moved file.
        self.source_baseline.pop(str(missing))
        self.panel.refresh_button.invoke()
        self.root.update()
        original = self.select_file(missing)
        with patch('handoffdesk.file_panel.filedialog.askopenfilename', return_value=''), \
                patch.object(self.service, 'relocate_node_file', wraps=self.service.relocate_node_file) as relocated:
            self.more('重新定位')
        relocated.assert_not_called()
        self.assertEqual(self.panel.path_text.get('1.0', 'end-1c'), str(missing))
        replacement = self.attachments[1]
        with patch('handoffdesk.file_panel.filedialog.askopenfilename', return_value=str(replacement)):
            self.more('重新定位')
        self.root.update()
        current = self.service.get_node_files('research', 'current:audit')
        paths = {item['path'] for item in current['files']}
        self.assertIn(str(replacement), paths)
        self.assertNotIn(str(missing), paths)
        changed = next(item for item in current['files'] if item['path'] == str(replacement))
        self.assertEqual(changed['purpose'], original['purpose'])
        self.assertEqual(changed['relinked_from'], str(missing))
        self.assertEqual(self.panel.path_text.get('1.0', 'end-1c'), str(replacement))
        self.assertTrue(changed['exists'])
        self.select_node(f'record:audit:{first["revision"]}')
        historical_paths = {item['path'] for item in self.panel.last_result['files']}
        self.assertIn(str(missing), historical_paths)
        self.assertNotIn(str(replacement), historical_paths)
        saved_record = next(item for item in self.service.history('research', 'audit')
                            if item['revision'] == first['revision'])
        self.assertEqual(saved_record, old_record)

    def test_relocating_to_already_linked_path_retains_selection_and_data(self):
        node = self.new_node()
        self.attach(self.attachments[:2])
        original = self.select_file(self.attachments[0])
        before = copy.deepcopy(self.service.get_node_files('research', node))
        with patch('handoffdesk.file_panel.filedialog.askopenfilename', return_value=str(self.attachments[1])), \
                patch('handoffdesk.file_panel.messagebox.showerror') as error:
            self.more('重新定位')
        error.assert_called_once()
        self.assertEqual(self.service.get_node_files('research', node), before)
        self.assertEqual(self.panel.files_tree.selection(), (original['id'],))
        self.assertEqual(self.panel.path_text.get('1.0', 'end-1c'), str(self.attachments[0]))

    def test_relocation_from_one_filtered_missing_file_selects_visible_replacement(self):
        paths = []
        for index in range(24):
            path = self.long_folder / f'本阶段参考材料_{index:02}.txt'
            path.write_text(f'原始参考材料 {index}', encoding='utf-8')
            paths.append(path)
        missing = self.long_folder / '旧位置待定位日志.log'
        missing.write_text('原位置的临时日志', encoding='utf-8')
        self.source_baseline = self.source_state()
        self.new_node('在很多文件中修复一个旧位置')
        self.attach(paths + [missing])
        missing.unlink()
        self.source_baseline.pop(str(missing))
        self.panel.refresh_button.invoke()
        self.panel.search_var.set('旧位置')
        self.root.update()
        original = self.select_file(missing)
        self.assertEqual(len(self.panel.files_tree.get_children()), 1)
        replacement = self.attachments[1]
        with patch('handoffdesk.file_panel.filedialog.askopenfilename', return_value=str(replacement)):
            self.more('重新定位')
        changed = next(item for item in self.panel.last_result['files'] if item['path'] == str(replacement))
        self.assertEqual(changed['purpose'], original['purpose'])
        self.assertEqual(self.panel.search_var.get(), '')
        tree = self.panel.files_tree
        for refresh in (False, True):
            if refresh:
                self.panel.refresh_button.invoke()
                self.root.update()
            self.assertEqual(tree.selection(), (changed['id'],))
            self.assertEqual(self.panel.path_text.get('1.0', 'end-1c'), str(replacement))
            bounds = tree.bbox(changed['id'])
            self.assertTrue(bounds, 'The newly linked file must be visible after clearing the filter')
            self.assertGreaterEqual(bounds[1], 0)
            self.assertLessEqual(bounds[1] + bounds[3], tree.winfo_height())
            self.assertGreater(tree.yview()[0], 0, 'The view must follow the replacement at the end of the list')

    def test_refresh_preserves_manual_scroll_and_selection_in_same_node(self):
        self.new_node()
        tree = self.panel.files_tree
        # The shared fixture has only five files, which can all fit with a
        # compact Linux font. Build enough actual associations for the current
        # viewport before testing preservation of a nonzero scroll position.
        self.root.update()
        row_height = int(ttk.Style(self.root).lookup('Files.Treeview', 'rowheight'))
        count = tree.winfo_height() // row_height + 5
        paths = []
        for index in range(count):
            path = self.long_folder / f'滚动位置验收材料_{index:03}.txt'
            path.write_text(f'原始滚动验收材料 {index}', encoding='utf-8')
            paths.append(path)
        self.source_baseline = self.source_state()
        self.attach(paths)
        chosen = self.select_file(paths[0])
        tree.column('location', width=900, stretch=False)
        self.root.update()
        tree.yview_moveto(1)
        tree.xview_moveto(.35)
        self.root.update()
        old_y, old_x = tree.yview()[0], tree.xview()[0]
        self.assertGreater(old_y, 0)
        self.assertGreater(old_x, 0)
        self.panel.refresh_button.invoke()
        self.root.update()
        self.assertEqual(tree.selection(), (chosen['id'],))
        self.assertAlmostEqual(tree.yview()[0], old_y, delta=.01)
        self.assertAlmostEqual(tree.xview()[0], old_x, delta=.01)

    def test_relocation_keeps_new_row_visible_when_details_resize_the_table(self):
        paths = []
        for index in range(24):
            path = self.long_folder / f'布局变化时的原始材料_{index:02}.txt'
            path.write_text(f'原始材料 {index}', encoding='utf-8')
            paths.append(path)
        missing = self.attachments[0]
        self.source_baseline = self.source_state()
        self.new_node('文件详情变高后仍能看到新位置')
        self.attach(paths + [missing])
        missing.unlink()
        self.source_baseline.pop(str(missing))
        self.panel.refresh_button.invoke()
        self.panel.search_var.set(missing.name)
        self.root.update()
        self.select_file(missing)
        original_details = self.panel._file_selected
        resized = []
        def larger_details(event=None):
            original_details(event)
            if not resized:
                # Model a font/desktop-metric change while the new selection's
                # details are rendered, after an early Treeview.see would run.
                self.panel.path_text.configure(font=('TkDefaultFont', 20))
                resized.append(True)
        replacement = self.attachments[1]
        with patch('handoffdesk.file_panel.filedialog.askopenfilename', return_value=str(replacement)), \
                patch.object(self.panel, '_file_selected', side_effect=larger_details):
            self.more('重新定位')
        self.root.update()
        changed = next(row for row in self.panel.last_result['files'] if row['path'] == str(replacement))
        tree = self.panel.files_tree
        self.assertTrue(resized)
        self.assertEqual(tree.selection(), (changed['id'],))
        bounds = tree.bbox(changed['id'])
        self.assertTrue(bounds, 'The relocated row must remain visible after details change the viewport')
        self.assertGreaterEqual(bounds[1], 0)
        self.assertLessEqual(bounds[1] + bounds[3], tree.winfo_height())

    def test_read_failure_retains_context_disables_changes_and_retry_recovers(self):
        node = self.new_node('需要保留浏览上下文的阶段')
        self.attach(self.attachments)
        self.panel.search_var.set('第2份')
        self.root.update()
        chosen = self.select_file(self.attachments[1])
        before_paths = {item['path'] for item in self.panel.last_result['files']}
        with patch.object(self.service, 'get_node_files', side_effect=RuntimeError('模拟临时读取失败')):
            self.panel.refresh_button.invoke()
            self.root.update()
        self.assertEqual(self.panel.current_node_id, node)
        self.assertEqual(self.panel.search_var.get(), '第2份')
        self.assertEqual({item['path'] for item in self.panel.last_result['files']}, before_paths)
        self.assertEqual(self.panel.files_tree.selection(), (chosen['id'],))
        self.assertIn('disabled', self.panel.add_button.state())
        self.assertIn('disabled', self.panel.remove_button.state())
        self.assertNotIn('disabled', self.panel.refresh_button.state())
        self.assertIn('失败', self.app.status_var.get())
        self.panel.refresh_button.invoke()
        self.root.update()
        self.assertEqual(self.panel.current_node_id, node)
        self.assertEqual(self.panel.search_var.get(), '第2份')
        self.assertEqual(self.panel.files_tree.selection(), (chosen['id'],))
        self.assertNotIn('disabled', self.panel.add_button.state())

    def test_failed_read_after_project_switch_never_shows_previous_project_files(self):
        self.new_node('旧项目私有阶段')
        self.attach([self.attachments[0]])
        self.panel.search_var.set('第1份')
        self.root.update()
        with patch.object(self.service, 'get_node_files', side_effect=RuntimeError('新项目暂不可读')):
            self.app.project_buttons['second'].invoke()
            self.app.tabs.select(4)
            self.root.update()
        self.assertEqual(self.app.project_id, 'second')
        self.assertEqual(self.panel.project_id, 'second')
        self.assertEqual(self.panel.last_result['files'], [])
        self.assertEqual(self.panel.files_tree.get_children(), ())
        self.assertIn('disabled', self.panel.add_button.state())
        self.panel.refresh_button.invoke()
        self.root.update()
        self.assertEqual(self.panel.current_node_id, 'current:default')
        self.assertEqual(self.panel.search_var.get(), '')
        self.assertEqual({item['path'] for item in self.panel.last_result['files']}, {str(self.other_guide)})
        self.assertNotIn(str(self.attachments[0]), self.panel.path_text.get('1.0', 'end-1c'))

    @unittest.skipUnless(sys.platform == "win32", "Windows API assertion; Linux dispatch covered separately")
    def test_failed_read_blocks_enter_and_double_click_open_but_allows_path_copy(self):
        node = self.new_node('读取失败时只查看旧清单')
        self.attach([self.attachments[0]])
        chosen = self.select_file(self.attachments[0])
        with patch.object(self.service, 'get_node_files', side_effect=RuntimeError('临时读取失败')):
            self.panel.refresh_button.invoke()
            self.root.update()
        tree = self.panel.files_tree
        tree.focus_force()
        self.root.update()
        bounds = tree.bbox(chosen['id'])
        self.assertTrue(bounds)
        x, y = bounds[0] + 10, bounds[1] + bounds[3] // 2
        with patch.object(self.panel, 'open_selected', wraps=self.panel.open_selected) as request, \
                patch.object(self.service, 'open_node_file', return_value=chosen['path']) as backend, \
                patch('os.startfile') as opened, patch('subprocess.Popen') as launched:
            tree.event_generate('<Return>')
            self.root.update()
            self.assertEqual(request.call_count, 1)
            for event, stamp in (('<ButtonPress-1>', 10000), ('<ButtonRelease-1>', 10020),
                                 ('<ButtonPress-1>', 10100), ('<ButtonRelease-1>', 10120)):
                tree.event_generate(event, x=x, y=y, time=stamp)
                self.root.update()
            self.assertGreaterEqual(request.call_count, 2, 'A real Tk double-click must reach the handler')
            backend.assert_not_called()
            opened.assert_not_called()
            launched.assert_not_called()
            self.assertIn('刷新', self.app.status_var.get())
            self.panel.copy_button.invoke()
            self.root.update()
            backend.assert_not_called()
            self.assertEqual(self.root.clipboard_get(), chosen['path'])

    def test_persistent_backend_failure_copies_cached_path_by_button_and_keyboard(self):
        self.new_node('后端暂不可读时仍能复制已知位置')
        self.attach([self.attachments[0]])
        chosen = self.select_file(self.attachments[0])
        with patch.object(self.service.node_files, 'get', side_effect=RuntimeError('持续的数据库读取故障')) as unreadable, \
                patch.object(self.service, 'open_node_file', wraps=self.service.open_node_file) as backend, \
                patch('handoffdesk.file_panel.messagebox.showerror') as error:
            self.panel.refresh_button.invoke()
            self.root.update()
            self.assertTrue(self.panel._read_failed)
            failed_reads = unreadable.call_count
            self.assertGreater(failed_reads, 0)
            self.root.clipboard_clear()
            self.root.clipboard_append('复制前的占位内容')
            self.panel.copy_button.invoke()
            self.root.update()
            self.assertEqual(self.root.clipboard_get(), chosen['path'])
            self.assertIn('上次清单', self.app.status_var.get())
            self.root.clipboard_clear()
            self.root.clipboard_append('键盘复制前的占位内容')
            self.panel.files_tree.focus_force()
            self.root.update()
            self.panel.files_tree.event_generate('<Control-c>')
            self.root.update()
            self.assertEqual(self.root.clipboard_get(), chosen['path'])
            self.assertIn('刷新', self.app.status_var.get())
            self.assertEqual(unreadable.call_count, failed_reads, 'Copying a cached path must not retry the database')
            backend.assert_not_called()
            error.assert_not_called()

    def test_failed_node_switch_preserves_query_and_selection_until_successful_read(self):
        first = self.new_node('保留查找上下文的旧节点')
        self.attach([self.attachments[0]])
        second = self.new_node('暂时读取失败的新节点')
        self.attach([self.attachments[1]])
        self.select_node(first)
        self.panel.search_var.set('第1份')
        self.root.update()
        chosen = self.select_file(self.attachments[0])
        target_index = next(i for i, node in enumerate(self.panel.nodes) if node['id'] == second)
        with patch.object(self.service, 'get_node_files', side_effect=RuntimeError('新节点暂时不可读')):
            self.panel.node_combo.current(target_index)
            self.panel.node_combo.event_generate('<<ComboboxSelected>>')
            self.root.update()
        self.assertEqual(self.panel.current_node_id, first)
        self.assertEqual(self.panel.search_var.get(), '第1份')
        self.assertEqual(self.panel.files_tree.selection(), (chosen['id'],))
        self.assertEqual(self.panel.path_text.get('1.0', 'end-1c'), chosen['path'])
        self.assertIn('disabled', self.panel.add_button.state())
        target_index = next(i for i, node in enumerate(self.panel.nodes) if node['id'] == second)
        self.panel.node_combo.current(target_index)
        self.panel.node_combo.event_generate('<<ComboboxSelected>>')
        self.root.update()
        self.assertEqual(self.panel.current_node_id, second)
        self.assertEqual(self.panel.search_var.get(), '')
        self.assertEqual({item['path'] for item in self.panel.last_result['files']}, {str(self.attachments[1])})
        self.assertNotIn('disabled', self.panel.add_button.state())

    @unittest.skipUnless(sys.platform == "win32", "Windows API assertion; Linux dispatch covered separately")
    def test_small_window_details_and_keyboard_keep_files_accessible(self):
        self.new_node('原始结果核验完成后保留的阶段文件节点' * 3)
        self.attach(self.attachments)
        chosen = self.select_file(self.attachments[0])
        for widget in (self.panel.node_scope_combo, self.panel.node_combo, self.panel.more_button,
                       self.panel.open_button, self.panel.reveal_button, self.panel.copy_button,
                       self.panel.add_button, self.panel.folder_button, self.panel.remove_button):
            self.assert_inside(widget)
        tree = self.panel.files_tree
        tree.yview_moveto(0)
        self.root.update()
        for item in tree.get_children()[:3]:
            bounds = tree.bbox(item)
            self.assertTrue(bounds, 'At least three file rows should fit in the small window')
            self.assertLessEqual(bounds[1] + bounds[3], tree.winfo_height())
        self.more('查看文件详情')
        dialog = self.panel.file_info_dialog
        details = dialog.detail_text.get('1.0', 'end-1c')
        self.assertIn(chosen['name'], details)
        self.assertIn(chosen['path'], details)
        self.assertIn(chosen['purpose'], details)
        self.button(dialog, '关闭').invoke()
        self.root.update()
        tree.focus_force()
        tree.event_generate('<Control-f>')
        self.root.update()
        self.assertEqual(self.root.focus_get(), self.panel.search_entry)
        self.panel.search_var.set('完全没有结果')
        self.root.update()
        self.panel.search_entry.event_generate('<Escape>')
        self.root.update()
        self.assertEqual(self.panel.search_var.get(), '')
        self.select_file(self.attachments[0])
        tree.focus_force()
        with patch('os.startfile') as opened:
            tree.event_generate('<Return>')
            self.root.update()
        opened.assert_called_once_with(chosen['path'])

    def test_first_jump_from_home_to_many_node_files_keeps_first_selected_row_visible(self):
        paths = []
        for index in range(24):
            path = self.long_folder / f'新阶段文件_{index:02}.txt'
            path.write_text(f'阶段原始材料 {index}', encoding='utf-8')
            paths.append(str(path))
        self.source_baseline = self.source_state()
        node = self.service.create_file_node('research', '从工作起点首次进入的大节点',
                                             workline_id='audit', include_current=False)
        self.service.add_node_files('research', node['id'], paths, purpose='首次进入时需完整可见')
        self.app.close()
        self.root = tk.Tk()
        self.root.report_callback_exception = lambda *error: self.callback_errors.append(error)
        self.app = previous.App(self.service, self.root)
        self.root.geometry('840x620+50+50')
        self.root.update()
        self.panel = self.app.files_panel
        self.assertEqual(self.app.notebook.select(), str(self.app.card_tab))
        self.assertFalse(self.panel.files_tree.winfo_viewable())
        self.app.show_node_files(node['id'])
        self.root.update()
        tree = self.panel.files_tree
        first = self.panel.last_result['files'][0]['id']
        self.assertEqual(tree.get_children()[0], first)
        self.assertEqual(tree.selection(), (first,))
        bounds = tree.bbox(first)
        self.assertTrue(bounds, 'The selected first file must be visible on the first visit')
        self.assertGreaterEqual(bounds[1], 0)
        self.assertLessEqual(bounds[1] + bounds[3], tree.winfo_height())
        self.assertAlmostEqual(tree.yview()[0], 0, delta=.001)


if __name__ == '__main__':
    unittest.main()
