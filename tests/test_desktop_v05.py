"""User journeys through the rounded controls and motion-enabled desktop.

Everything here uses disposable local projects and a real SQLite database.
Only OS file launching and confirmation dialogs are replaced; reads, writes,
background collection, drafts, worklines and saved preferences remain real.
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
from handoffdesk.presentation import display_value
from handoffdesk.service import Service


class DesktopV05Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='desktop-v05-', dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / '项目资料'
        self.source.mkdir()
        self.state_file = self.source / 'state.json'
        self.state_file.write_text('{"status":"paused"}', encoding='utf-8')
        self.guide = self.source / '中文执行计划.md'
        self.guide.write_text('只用于回归验证的执行计划。', encoding='utf-8')
        self.weight = self.source / 'model.pth'
        self.weight.write_bytes(b'fixture, not an executable or model')
        self.job_ids = ('20261023_120000_aaaaaaaaaa', '20261023_120100_bbbbbbbbbb')
        for job_id, kind, voice in zip(self.job_ids, ('train', 'infer'), ('试听甲', '试听乙')):
            folder = self.source / 'workflows' / 'jobs' / job_id
            folder.mkdir(parents=True)
            (folder / 'job.json').write_text(json.dumps({
                'kind': kind, 'voice_name': voice, 'created_at': '2026-10-23T12:00:00'}), encoding='utf-8')
            (folder / 'state.json').write_text(json.dumps({
                'status': 'completed', 'updated_at': '2026-10-23T12:05:00'}), encoding='utf-8')
        sources = [
            {'id': 'state', 'path': 'state.json', 'format': 'json', 'fields': {'recorded_status': '/status'}},
            {'id': 'guide', 'path': self.guide.name, 'format': 'text'},
            {'id': 'weight', 'path': self.weight.name, 'format': 'binary_entry_only'}]
        entries = [{'id': name, 'type': 'open_source', 'source_id': name}
                   for name in ('state', 'guide', 'weight')]
        self.appdir = self.base / 'app'
        (self.appdir / 'config').mkdir(parents=True)
        config = {'schema_version': 1, 'projects': [
            {'id': 'research', 'name': '中文研究项目', 'aliases': ['研究'],
             'roots': {'main': str(self.source)}, 'sources': sources, 'entries': entries,
             'worklines': [{'id': 'audit', 'name': '证据核验', 'entry_ids': ['guide']}]},
            {'id': 'rvc', 'name': 'RVC 音色项目',
             'roots': {'main': str(self.source), 'applio': str(self.source)},
             'sources': sources[:1], 'entries': entries[:1]}]}
        (self.appdir / 'config' / 'projects.json').write_text(json.dumps(config), encoding='utf-8')
        self.service = Service(self.appdir)
        self.service.save_preferences({'auto_refresh': False})
        self.service.refresh('research', workline_id='audit')
        self.service.select_workline('research', 'audit')
        self.callback_errors = []
        self.make_window()

    def make_window(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest('Native Tk unavailable: ' + str(exc))
        self.root.report_callback_exception = lambda *error: self.callback_errors.append(error)
        self.app = App(self.service, self.root)
        self.root.geometry('840x620+50+50')
        self.root.update()

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

    def edit_next(self, editor, text):
        editor.next_text.delete('1.0', 'end')
        editor.next_text.insert('1.0', text)

    def publish_plan(self, text='核对第一份原始证据'):
        self.app.plan_button.invoke()
        editor = self.app.last_dialog
        self.edit_next(editor, text)
        editor.save_button.invoke()
        self.pump(lambda: editor.closed)
        return self.service.show('research', workline_id='audit')['handoff']

    def test_complete_work_session_review_restore_copy_and_export(self):
        planned = self.publish_plan()
        self.assertEqual(planned['event_kind'], 'plan')
        self.assertEqual(planned['last_done'], '')
        self.app.handoff_button.invoke()
        editor = self.app.last_dialog
        editor.last_done_text.insert('1.0', '已完成原始来源核对，未运行实验。')
        self.edit_next(editor, '继续核对第二份证据')
        editor.save_button.invoke()
        self.pump(lambda: editor.closed)
        completed = self.service.show('research', 'audit')['handoff']
        self.assertEqual(completed['revision'], 2)
        self.assertEqual(completed['event_kind'], 'handoff')
        self.state_file.write_text('{"status":"completed"}', encoding='utf-8')
        self.app.refresh_button.invoke()
        self.pump(lambda: self.app.busy_count == 0)
        self.assertTrue(self.app.source_changes)
        self.app.tabs.select(2)
        self.root.update()
        self.app.review_button.invoke()
        self.assertFalse(self.app.source_changes)
        self.assertEqual(self.service.show('research', 'audit')['handoff'], completed)
        self.app.tabs.select(3)
        self.root.update()
        index = next(i for i, record in enumerate(self.app.history_records) if record['revision'] == 1)
        self.app.history_tree.selection_set(str(index))
        with patch('handoffdesk.desktop.messagebox.askyesno', return_value=True):
            self.button(self.root, '恢复所选内容为新版本').invoke()
        restored = self.service.show('research', 'audit')['handoff']
        self.assertEqual(restored['revision'], 3)
        self.assertEqual(restored['next_action'], planned['next_action'])
        self.assertEqual(len(self.service.history('research', 'audit')), 3)
        self.app.copy_summary()
        self.assertIn(planned['next_action'], self.root.clipboard_get())
        with patch('handoffdesk.desktop.messagebox.showinfo') as info:
            self.app.export()
        info.assert_called_once()
        export = Path(info.call_args.args[1])
        self.assertTrue(export.is_relative_to(self.appdir / 'data' / 'exports'))
        self.assertIn(planned['next_action'], export.read_text('utf-8'))

    @unittest.skipUnless(sys.platform == "win32", "Windows API assertion; Linux dispatch covered separately")
    def test_material_binding_opening_and_source_actions_preserve_files(self):
        baseline = {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                    for p in self.source.rglob('*') if p.is_file()}
        self.app.bind_materials()
        tree = self.app.material_picker_tree
        ids = [str(i) for i, item in enumerate(self.app.entries) if item['id'] in ('guide', 'weight')]
        tree.selection_set(ids)
        self.app.material_save_button.invoke()
        self.assertEqual({entry['id'] for entry in self.app._bound_entries()}, {'guide', 'weight'})
        with patch('os.startfile') as opened:
            self.app.continue_button.invoke()
        self.assertEqual({call.args[0] for call in opened.call_args_list}, {str(self.guide), str(self.source)})
        self.app.tabs.select(1)
        index = next(i for i, obs in enumerate(self.app.observations) if obs['source_id'] == 'guide')
        self.app.source_tree.selection_set(str(index))
        self.app._show_source()
        self.button(self.app.source_tab, '复制路径').invoke()
        self.assertEqual(self.root.clipboard_get(), str(self.guide))
        with patch('os.startfile') as opened:
            self.button(self.app.source_tab, '打开原文').invoke()
            self.button(self.app.source_tab, '打开所在文件夹').invoke()
        self.assertEqual([call.args[0] for call in opened.call_args_list], [str(self.guide), str(self.source)])
        self.assertEqual(baseline, {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                                    for p in self.source.rglob('*') if p.is_file()})

    def test_import_preview_survives_draft_close_and_reopens_for_confirmation(self):
        self.app.import_summary()
        self.app.import_input.insert('1.0', '本次完成：助手整理了证据索引\n下一步：人工核对第二项\n阻塞项：尚需原始日志')
        self.root.update()
        self.app.import_parse_button.invoke()
        self.app.import_save_button.invoke()
        editor = self.app.last_dialog
        self.assertEqual(editor.origin, 'assistant_suggested')
        self.assertIsNone(self.service.show('research', 'audit')['handoff'])
        self.edit_next(editor, '人工核对第三项')
        self.pump(lambda: (self.service.load_draft('research', workline_id='audit') or {}).get('next_action') == '人工核对第三项')
        editor.close()
        self.app.draft_buttons['handoff'].invoke()
        restored = self.app.last_dialog
        self.assertIn('人工核对第三项', restored.next_text.get('1.0', 'end'))
        self.assertEqual(restored.origin, 'assistant_suggested')
        self.assertIsNone(restored.source_snapshot_id)
        restored.save_button.invoke()
        self.pump(lambda: restored.closed)
        record = self.service.show('research', 'audit')['handoff']
        self.assertEqual(record['origin'], 'assistant_suggested')
        self.assertIsNone(record['source_snapshot_id'])
        self.assertEqual(record['next_action'], '人工核对第三项')

    def test_rvc_filter_focus_and_background_refresh_use_real_task_metadata(self):
        self.assertEqual(display_value('infer'), '换声')
        self.assertEqual(display_value('inference'), '换声')
        baseline = {p: p.read_bytes() for p in (self.source / 'workflows').rglob('*.json')}
        self.app.search_var.set('RVC')
        self.root.update()
        self.assertFalse(self.app.project_buttons['research'].winfo_manager())
        self.app.project_buttons['rvc'].invoke()
        self.app.focus_button.invoke()
        self.pump(lambda: self.app.busy_count == 0 and hasattr(self.app, 'focus_window'))
        self.app.focus_query.set('试听乙')
        self.app.focus_kind.set(display_value('infer'))
        self.root.update()
        self.assertEqual([row['id'] for row in self.app.focus_filtered], [self.job_ids[1]])
        self.app.focus_tree.selection_set('0')
        self.app.focus_save_button.invoke()
        self.pump(lambda: self.app.busy_count == 0)
        state = self.service.show('rvc')
        self.assertEqual(state['project']['focus_job'], self.job_ids[1])
        self.assertEqual(state['workline']['id'], 'job-' + self.job_ids[1])
        self.assertTrue(state['last_attempt']['usable'])
        self.assertIsNone(state['handoff'])
        self.assertEqual(baseline, {p: p.read_bytes() for p in (self.source / 'workflows').rglob('*.json')})

    def test_segmented_navigation_keyboard_fast_switches_and_resize_stay_in_sync(self):
        for size in ('840x620', '1080x760'):
            self.root.geometry(size)
            self.root.update()
            for index in (3, 1, 2, 0, 2, 3):
                self.app.tabs.select(index)
                self.assertEqual(self.app.notebook.index(self.app.notebook.select()), index)
            self.app.tabs.focus_force()
            self.root.update()
            for key, expected in (('<Home>', 0), ('<Right>', 1), ('<End>', len(self.app.notebook.tabs())-1), ('<Left>', len(self.app.notebook.tabs())-2)):
                self.app.tabs.event_generate(key)
                self.root.update()
                self.assertEqual(self.app.tabs.selected_index, expected)
                self.assertEqual(self.app.notebook.index(self.app.notebook.select()), expected)
            self.app.notebook.select(self.app.source_tab)
            self.root.update()
            self.assertEqual(self.app.tabs.selected_index, 1)
            self.app.tabs.select(0)
            self.root.update()
            for widget in (self.app.tabs, self.app.continue_button, self.app.plan_button, self.app.handoff_button):
                self.assertTrue(widget.winfo_viewable())
                self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), self.root.winfo_rootx() + self.root.winfo_width())
                self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), self.root.winfo_rooty() + self.root.winfo_height())
        self.pump(lambda: self.app.motion.active_count == 0)
        self.assertEqual(self.callback_errors, [])

    def test_reduce_motion_finishes_effects_and_survives_restart(self):
        self.app.tabs.select(3)
        self.root.update()
        self.app.reduce_motion_var.set(True)
        self.app._motion_changed()
        self.assertEqual(self.app.motion.active_count, 0)
        self.assertTrue(Service(self.appdir).preferences()['reduced_motion'])
        for index in (1, 2, 0):
            self.app.tabs.select(index)
            self.root.update()
            self.assertEqual(self.app.motion.active_count, 0)
            self.assertEqual(self.app.tabs.selected_index, index)
        self.app.close()
        self.make_window()
        self.assertTrue(self.app.reduce_motion_var.get())
        self.assertFalse(self.app.auto_refresh_var.get())
        self.assertEqual(self.app.workline_id, 'audit')
        editor = self.app.create_plan_dialog()
        self.root.update()
        self.assertEqual(float(editor.window.attributes('-alpha')), 1.0)
        editor.close()
        self.app.reduce_motion_var.set(False)
        self.app._motion_changed()
        self.assertFalse(Service(self.appdir).preferences()['reduced_motion'])
        with self.assertRaises(ValueError):
            self.service.save_preferences({'reduced_motion': 'true'})

    def test_close_immediately_during_dialog_and_navigation_motion(self):
        for _ in range(4):
            editor = self.app.create_plan_dialog()
            editor.close()
            self.root.update()
        for index in (3, 1, 0, 2):
            self.app.tabs.select(index)
        self.assertTrue(self.app.close())
        self.assertEqual(self.app.motion.active_count, 0)
        self.assertEqual(self.callback_errors, [])


if __name__ == '__main__':
    unittest.main()
