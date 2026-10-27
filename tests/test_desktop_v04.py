"""Small-window visual regressions exercised through real Tk user flows.

These checks cover reachable controls and readable states, not exact pixel
positions or theme implementation details. All project inputs are fixtures.
"""
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
from tkinter import font as tkfont, ttk
import unittest

from handoffdesk.desktop import App
from handoffdesk.service import Service


class DesktopV04Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='desktop-v04-', dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.project = base / 'source'
        self.project.mkdir()
        self.long_project = '中文研究项目名称与跨版本原始证据核对' * 3
        self.long_workline = ('跨版本实验结果与原始来源逐项复核工作线' * 6)[:100]
        self.filename = '中文原始证据文件名包含实验版本与来源核验信息' * 2 + '.json'
        self.source = self.project / self.filename
        self.source.write_text('{"status":"paused"}', encoding='utf-8')
        appdir = base / 'app'
        (appdir / 'config').mkdir(parents=True)
        sources = [{'id': 'source-' + str(i), 'path': self.filename, 'format': 'json',
                    'label': self.filename, 'fields': {'recorded_status': '/status'}} for i in range(24)]
        entries = [{'id': 'material-' + str(i), 'type': 'open_source', 'source_id': source['id'],
                    'label': f'第 {i + 1} 份中文证据材料 · ' + self.filename} for i, source in enumerate(sources)]
        config = {'schema_version': 1, 'projects': [
            {'id': 'research', 'name': self.long_project, 'roots': {'main': str(self.project)},
             'worklines': [{'id': 'review', 'name': self.long_workline, 'entry_ids': ['material-0']}],
             'sources': sources, 'entries': entries},
            {'id': 'rvc', 'name': 'RVC 中文音色研究', 'roots': {'main': str(self.project)},
             'sources': [sources[0]], 'entries': [entries[0]]}]}
        (appdir / 'config' / 'projects.json').write_text(json.dumps(config), encoding='utf-8')
        self.service = Service(appdir)
        self.service.save_preferences({'auto_refresh': False})
        self.service.refresh('research', workline_id='review')
        self.service.select_workline('research', 'review')
        self.service.refresh('rvc')
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest('Native Tk unavailable: ' + str(exc))
        self.app = App(self.service, self.root)
        self.root.geometry('840x620+40+40')
        self.root.update()
        self.assertEqual((self.root.winfo_width(), self.root.winfo_height()), (840, 620),
                         'The application no longer permits the supported small window size')

    def tearDown(self):
        if not hasattr(self, 'app') or self.app.closed:
            return
        dialog = self.app.last_dialog
        if dialog and not dialog.closed:
            dialog._destroy()
        for widget in list(self.root.winfo_children()):
            if isinstance(widget, tk.Toplevel):
                widget.destroy()
        self.app.close()

    def pump(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(.01)
        self.fail('The expected user-visible UI state did not appear')

    def assert_inside(self, widget, window):
        window.update_idletasks()
        self.assertTrue(widget.winfo_viewable(), str(widget) + ' is hidden')
        left, top = widget.winfo_rootx(), widget.winfo_rooty()
        right, bottom = left + widget.winfo_width(), top + widget.winfo_height()
        self.assertGreaterEqual(left, window.winfo_rootx() - 1)
        self.assertGreaterEqual(top, window.winfo_rooty() - 1)
        self.assertLessEqual(right, window.winfo_rootx() + window.winfo_width() + 1,
                             str(widget) + ' is clipped at the right edge')
        self.assertLessEqual(bottom, window.winfo_rooty() + window.winfo_height() + 1,
                             str(widget) + ' is clipped at the bottom edge')

    def assert_small_dialog(self, window, *controls):
        window.update()
        self.assertLessEqual(window.winfo_width(), self.root.winfo_width())
        self.assertLessEqual(window.winfo_height(), self.root.winfo_height())
        for control in controls:
            self.assert_inside(control, window)

    def assert_last_text_reachable(self, widget, marker):
        widget.yview_moveto(1)
        widget.update_idletasks()
        self.assertAlmostEqual(widget.yview()[1], 1.0)
        self.assertIn(marker, widget.get('1.0', 'end'))

    def assert_three_line_input(self, widget):
        widget.update_idletasks()
        line_height = tkfont.Font(root=self.root, font=widget.cget('font')).metrics('linespace')
        inset = sum(widget.winfo_pixels(str(widget.cget(key)))
                    for key in ('pady', 'borderwidth', 'highlightthickness'))
        self.assertGreaterEqual(widget.winfo_height(), 3 * line_height + 2 * inset,
                                str(widget) + ' has less than three readable input lines')

    def assert_readable_source(self, widget):
        self.assert_inside(widget, self.root)
        line_height = tkfont.Font(root=self.root, font=widget.cget('font')).metrics('linespace')
        line_height += sum(widget.winfo_pixels(str(widget.cget(key)))
                           for key in ('spacing1', 'spacing3'))
        inset = sum(widget.winfo_pixels(str(widget.cget(key)))
                    for key in ('pady', 'borderwidth', 'highlightthickness'))
        self.assertGreaterEqual(widget.winfo_height(), 2 * line_height + 2 * inset,
                                str(widget) + ' has less than two readable source lines')
        widget.yview_moveto(0)
        widget.update_idletasks()
        self.assertIsNotNone(widget.dlineinfo('1.0'))
        widget.yview_moveto(1)
        widget.update_idletasks()
        self.assertAlmostEqual(widget.yview()[1], 1.0)
        self.assertIsNotNone(widget.dlineinfo('end-2c'), 'The final source line is not reachable')
        widget.yview_moveto(0)

    def button(self, parent, caption):
        pending = list(parent.winfo_children())
        while pending:
            widget = pending.pop(0)
            if isinstance(widget, (tk.Button, ttk.Button)) and str(widget.cget('text')) == caption:
                return widget
            pending.extend(widget.winfo_children())
        self.fail('Missing visible action: ' + caption)

    def test_long_chinese_names_keep_navigation_and_source_actions_reachable(self):
        self.assertEqual(self.app.workline_combo.get(), self.long_workline)
        for widget in (self.app.refresh_button, self.app.workline_combo, self.app.continue_button,
                       self.app.plan_button, self.app.handoff_button):
            self.assert_inside(widget, self.root)
        for widget in self.app.project_buttons.values():
            self.assert_inside(widget, self.root)
        self.app.notebook.select(self.app.source_tab)
        self.root.update()
        self.app.source_tree.selection_set('23')
        self.app.source_tree.see('23')
        self.app._show_source()
        self.root.update()
        row = self.app.source_tree.bbox('23')
        self.assertTrue(row)
        self.assertLessEqual(row[1] + row[3], self.app.source_tree.winfo_height(),
                             'The selected source row is clipped')
        self.assertIn(self.filename, self.app.source_detail.get('1.0', 'end'))
        self.assert_readable_source(self.app.source_detail)
        for caption in ('打开原文', '打开所在文件夹', '复制路径'):
            self.assert_inside(self.button(self.app.source_tab, caption), self.root)
        readable_details = self.app.source_detail.get('1.0', 'end')
        self.app.raw_visible.set(True)
        self.app._toggle_raw()
        self.root.update()
        self.assert_readable_source(self.app.source_raw)
        self.assertFalse(self.app.source_detail.winfo_viewable())
        self.assertIn(self.filename, self.app.source_raw.get('1.0', 'end'))
        for caption in ('打开原文', '打开所在文件夹', '复制路径'):
            self.assert_inside(self.button(self.app.source_tab, caption), self.root)
        self.app.raw_visible.set(False)
        self.app._toggle_raw()
        self.root.update()
        self.assert_readable_source(self.app.source_detail)
        self.assertFalse(self.app.source_raw.winfo_viewable())
        self.assertEqual(self.app.source_detail.get('1.0', 'end'), readable_details)
        for caption in ('打开原文', '打开所在文件夹', '复制路径'):
            self.assert_inside(self.button(self.app.source_tab, caption), self.root)
        self.app.source_action('copy')
        self.assertEqual(self.root.clipboard_get(), str(self.source))

    def test_compact_source_list_reveals_selected_row_after_details_and_keeps_manual_scroll(self):
        self.app.notebook.select(self.app.source_tab)
        self.root.update()
        # A viewport between one and two full rows exposes Tk 8.6's stale
        # see() geometry when selecting a source also updates its details.
        tree = self.app.source_tree
        for minimum in (0, 99):
            self.app.source_tab.rowconfigure(1, weight=0, minsize=minimum)
            self.root.update()
            tree.yview_moveto(0)
            tree.selection_set('23')
            tree.see('23')
            self.app._show_source()
            self.root.update()
            row = tree.bbox('23')
            self.assertTrue(row, 'The selected source must remain reachable')
            self.assertLessEqual(row[1] + row[3], tree.winfo_height(),
                                 'The final selected source must be fully visible')
        self.assertIn(self.filename, self.app.source_detail.get('1.0', 'end'))
        # Browsing earlier rows after that selection must not be pulled back
        # by later idle redraws or an ordinary window resize.
        tree.yview_moveto(0)
        self.root.update()
        self.root.geometry('850x630')
        self.root.update()
        self.assertEqual(tree.yview()[0], 0.0)

    def test_long_editor_content_scrolls_and_keyboard_save_keeps_record_semantics(self):
        for kind in ('plan', 'handoff'):
            with self.subTest(kind=kind):
                editor = self.app.create_handoff_dialog(kind)
                if kind == 'handoff':
                    self.assertTrue(editor.extra_open, 'Inherited blockers should already be expanded')
                if not editor.extra_open:
                    editor.toggle_extra()
                next_action = '\n'.join(f'核对第 {i} 项中文来源和实验条件' for i in range(45))
                editor.next_text.delete('1.0', 'end')
                editor.next_text.insert('1.0', next_action)
                editor.blockers_text.delete('1.0', 'end')
                editor.blockers_text.insert('1.0', '\n'.join(f'待确认条件 {i}' for i in range(40)))
                if kind == 'handoff':
                    editor.last_done_text.insert('1.0', '仅完成来源核对，尚未运行实验。')
                # Long Text contents scroll inside their fixed-height fields.
                # Shrink the outer editor to its supported minimum so this
                # journey also exercises outer scrolling with compact fonts.
                editor.window.geometry('680x470')
                editor.window.update()
                self.assert_small_dialog(editor.window, editor.save_button,
                                         self.button(editor.window, '存草稿并关闭'))
                self.assert_three_line_input(editor.next_text)
                if kind == 'handoff':
                    self.assert_three_line_input(editor.last_done_text)
                canvas = editor.content_canvas
                canvas.yview_moveto(0)
                editor.window.update()
                self.assertLess(canvas.yview()[1], 1.0,
                                'Expanded long editor content has no scrolling route')
                footer_y = editor.footer_frame.winfo_rooty()
                fields = ([editor.last_done_text] if kind == 'handoff' else []) + [
                    editor.next_text, editor.blockers_text, editor.note_entry]
                for field in fields:
                    editor._reveal(field)
                    editor.window.update()
                    self.assert_inside(field, canvas)
                    self.assert_inside(editor.save_button, editor.window)
                    self.assertEqual(editor.footer_frame.winfo_rooty(), footer_y,
                                     'Scrolling content moved the save footer')
                self.assertGreater(canvas.yview()[0], 0,
                                   'The expanded fields never became scroll-reachable')
                self.assert_last_text_reachable(editor.next_text, '核对第 44 项')
                self.assert_last_text_reachable(editor.blockers_text, '待确认条件 39')
                original_snapshot = editor.source_snapshot_id
                editor.next_text.focus_force()
                editor.window.update()
                self.assertEqual(editor.window.focus_get(), editor.next_text)
                editor.next_text.event_generate('<Control-Return>')
                self.pump(lambda: editor.closed)
                saved = self.service.show('research', workline_id='review')['handoff']
                self.assertEqual(saved['event_kind'], kind)
                self.assertEqual(saved['source_snapshot_id'], original_snapshot)
                self.assertEqual(saved['next_action'], next_action)
                self.assertEqual(len(saved['blockers']), 40)
                self.assertEqual(saved['last_done'], '' if kind == 'plan' else '仅完成来源核对，尚未运行实验。')

    def test_material_picker_reaches_last_long_named_item_and_saves_selection(self):
        selected = []
        window = self.app.material_picker(self.app.entries, [], selected.extend)
        tree = self.app.material_picker_tree
        self.assert_small_dialog(window, self.app.material_save_button)
        last = tree.get_children()[-1]
        tree.see(last)
        tree.selection_set(last)
        tree.focus(last)
        window.update()
        self.assertTrue(tree.bbox(last))
        self.app.material_save_button.invoke()
        self.assertEqual(selected, [self.app.entries[int(last)]['id']])
        self.assertFalse(window.winfo_exists())

    def test_import_preview_keeps_actions_visible_and_does_not_publish_without_confirmation(self):
        self.app.import_summary()
        window = self.app.import_window
        self.assert_small_dialog(window, self.app.import_parse_button, self.app.import_save_button)
        self.assertTrue(self.app.import_save_button.instate(['disabled']))
        text = '本次完成：仅核对了资料\n下一步：检查原始来源\n阻塞项：' + '\n'.join(f'阻塞条件 {i}' for i in range(35))
        self.app.import_input.insert('1.0', text)
        window.update()
        self.app.import_parse_button.invoke()
        window.update()
        self.assertFalse(self.app.import_save_button.instate(['disabled']))
        self.assert_last_text_reachable(self.app.import_preview, '阻塞条件 34')
        self.assert_small_dialog(window, self.app.import_parse_button, self.app.import_save_button)
        self.app.import_save_button.invoke()
        self.pump(lambda: self.app.last_dialog is not None)
        editor = self.app.last_dialog
        self.assert_small_dialog(editor.window, editor.save_button)
        self.assertIsNone(self.service.show('research', workline_id='review')['handoff'])
        self.assertEqual(editor.origin, 'assistant_suggested')
        self.assertIsNone(editor.source_snapshot_id)

    def test_rvc_and_new_workline_dialogs_fit_small_parent_and_allow_keyboard_use(self):
        self.app.create_workline()
        window = self.app.workline_window
        self.assert_small_dialog(window, self.app.workline_name_entry, self.app.workline_create_button)
        name = '新的中文工作线与跨阶段原始材料复核'
        self.app.workline_name_entry.insert(0, name)
        self.app.workline_name_entry.focus_force()
        window.update()
        self.app.workline_name_entry.event_generate('<Return>')
        self.pump(lambda: not window.winfo_exists())
        self.assertEqual(self.app.current['workline']['name'], name)
        self.assertIsNone(self.app.current['handoff'])
        self.app.select_project('rvc')
        options = [{'id': f'20261023_120000_{i:010x}', 'label': '任务 ' + str(i),
                    'voice_name': '中文音色名称与补充训练来源记录' * 3, 'kind': 'train',
                    'status': 'completed', 'recorded_at': '2026-10-23T12:00:00'} for i in range(30)]
        self.app._focus_dialog(options)
        window = self.app.focus_window
        self.assert_small_dialog(window, self.app.focus_save_button, self.app.focus_type_combo)
        last = self.app.focus_tree.get_children()[-1]
        self.app.focus_tree.see(last)
        window.update()
        self.assertTrue(self.app.focus_tree.bbox(last))
        self.app.focus_tree.focus_force()
        window.event_generate('<Escape>')
        self.pump(lambda: not window.winfo_exists())

    def test_primary_action_has_visible_focus_and_distinct_disabled_state(self):
        button = self.app.continue_button
        style_name = str(button.cget('style')) or 'TButton'
        style = ttk.Style(self.root)
        properties = ('background', 'foreground', 'bordercolor', 'focuscolor', 'focusthickness')
        normal = tuple(style.lookup(style_name, item, ()) for item in properties)
        focused = tuple(style.lookup(style_name, item, ('focus',)) for item in properties)
        disabled = tuple(style.lookup(style_name, item, ('disabled',)) for item in properties)
        # A ttk focus element can draw only in the focused state without changing
        # its configured color. Accept that standard treatment as well as maps.
        focus_element = 'focus' in str(style.layout(style_name)).lower()
        focus_width = float(style.lookup(style_name, 'focusthickness', ('focus',)) or 0)
        focus_color = style.lookup(style_name, 'focuscolor', ('focus',))
        background = style.lookup(style_name, 'background', ('focus',))
        self.assertTrue(normal != focused or (focus_element and focus_width > 0 and focus_color != background),
                        'Keyboard focus has no visible treatment')
        self.assertNotEqual(normal, disabled, 'Disabled primary action looks enabled')
        button.focus_force()
        self.root.update()
        self.assertEqual(self.root.focus_get(), button)
        button.state(['disabled'])
        self.assertTrue(button.instate(['disabled']))
        self.assertNotEqual(button.tk_focusNext(), button)


if __name__ == '__main__':
    unittest.main()
