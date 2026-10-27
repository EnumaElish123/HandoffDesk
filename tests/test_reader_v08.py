"""Agent writes from a second service; a human reader keeps their context."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from tkinter import ttk

import test_desktop_v06 as previous
from handoffdesk.agent_records import AgentRecords
from handoffdesk.desktop import _wheel_direction
from handoffdesk.file_panel import NodeFilesPanel
from handoffdesk.presentation import record_attribution
from handoffdesk.service import Service


class ReaderPresentationTests(unittest.TestCase):
    def test_agent_attribution_separates_reported_checks_and_handles_legacy_metadata(self):
        self.assertEqual(record_attribution({'origin': 'user_directed'}), '')
        self.assertIn('未提供', record_attribution({'origin': 'agent_recorded'}))
        for status, label in [('passed', '通过'), ('partial', '部分验证'),
                              ('not_run', '未验证'), ('failed', '失败')]:
            text = record_attribution({'origin': 'agent_recorded',
                'actor': {'name': 'Codex', 'session_id': 'session-1'},
                'verification': {'status': status, 'summary': '只核对了日志'}}, detailed=True)
            self.assertIn('Agent 记录 · Codex', text)
            self.assertIn('报告验证：' + label, text)
            self.assertIn('来源会话：session-1', text)
            self.assertIn('验证说明（由记录者报告）：只核对了日志', text)

    def test_linux_and_windows_wheel_direction(self):
        self.assertEqual(_wheel_direction(SimpleNamespace(num=4, delta=0)), -1)
        self.assertEqual(_wheel_direction(SimpleNamespace(num=5, delta=0)), 1)
        self.assertEqual(_wheel_direction(SimpleNamespace(delta=120)), -1)
        self.assertEqual(_wheel_direction(SimpleNamespace(delta=-120)), 1)


class ReaderV08Journeys(unittest.TestCase):
    setUp = previous.DesktopV06Tests.setUp
    tearDown = previous.DesktopV06Tests.tearDown
    source_state = previous.DesktopV06Tests.source_state
    pump = previous.DesktopV06Tests.pump
    button = previous.DesktopV06Tests.button
    select_file = previous.DesktopV06Tests.select_file

    def submit(self, *, line='audit', project='research', files=None):
        writer = Service(self.appdir)
        current = writer.show(project, workline_id=line)['handoff'] or {}
        revision = current.get('revision', 0)
        chosen = self.attachments[:2] if files is None else files
        return AgentRecords(writer).save({
            'schema_version': 1, 'project': project, 'workline': line,
            'expected_revision': revision, 'operation_id': f'native-reader-{project}-{line}-{revision}',
            'record': {'title': f'第 {revision + 1} 次实验核对',
                       'last_done': '已检查原始日志；尚未重新运行完整实验。',
                       'next_action': '打开结果表，逐项核对异常。'},
            'actor': {'name': 'Codex', 'session_id': f'trial-session-{revision + 1}'},
            'verification': {'status': 'partial', 'summary': '仅执行了日志核对。'},
            'files': [{'path': str(path), 'purpose': f'本次产物 {i + 1}', 'role': '实验日志'}
                      for i, path in enumerate(chosen)]})

    def check_external(self):
        self.app._check_external_progress()
        self.root.update()

    def test_external_record_notifies_then_home_links_to_exact_record_files(self):
        self.app.tabs.select(0)
        self.root.update()
        prior = copy.deepcopy(self.app.current)
        selection = self.service.store.selected_workline('research')
        result = self.submit()
        with patch.object(self.service, 'show', wraps=self.service.show) as showed:
            self.check_external()
            showed.assert_not_called()
        self.assertTrue(self.app.external_progress_pending)
        self.assertTrue(self.app.external_update_button.winfo_ismapped())
        self.assertEqual(self.app.current, prior)
        with patch.object(self.service, 'refresh') as refreshed:
            self.app.external_update_button.invoke()
            self.root.update()
            refreshed.assert_not_called()
        self.assertFalse(self.app.external_progress_pending)
        self.assertEqual(self.app.current['handoff']['revision'], result['revision'])
        self.assertEqual(self.service.store.selected_workline('research'), selection)
        self.assertIn('Agent 记录 · Codex', self.app.task_var.get())
        self.assertIn('报告验证：部分验证', self.app.task_var.get())
        self.assertEqual(str(self.app.latest_files_button.cget('text')), '本次文件 2 项')
        self.app.latest_files_button.invoke()
        self.root.update()
        self.assertEqual(self.panel.current_node_id, result['node_id'])
        self.assertEqual({x['path'] for x in self.panel.last_result['files']},
                         {str(p) for p in self.attachments[:2]})
        self.app.tabs.select(0)
        self.app.continue_button.invoke()
        self.root.update()
        self.assertEqual(self.panel.current_node_id, result['node_id'])
        self.panel.show_node_info()
        self.root.update()
        info = self.panel.node_info_dialog.detail_text.get('1.0', 'end-1c')
        self.assertIn('trial-session-1', info)
        self.assertIn('仅执行了日志核对', info)
        self.assertIn('文件角色：实验日志', info)
        self.panel.node_info_dialog.destroy()

    def test_external_update_preserves_open_editor_text_draft_and_base_revision(self):
        self.app.tabs.select(0)
        dialog = self.app.create_plan_dialog()
        dialog.next_text.delete('1.0', 'end')
        dialog.next_text.insert('1.0', '人类正在编辑的下一步，不得被 Agent 覆盖。')
        self.root.update()
        self.assertTrue(dialog.flush_draft(force=True))
        draft = copy.deepcopy(self.service.load_draft('research', event_kind='plan', workline_id='audit'))
        base_revision = dialog.expected_revision
        self.submit()
        self.check_external()
        self.app.reload_saved_progress()
        self.root.update()
        self.assertFalse(dialog.closed)
        self.assertEqual(dialog.next_text.get('1.0', 'end-1c'), '人类正在编辑的下一步，不得被 Agent 覆盖。')
        self.assertEqual(dialog.expected_revision, base_revision)
        self.assertEqual(self.service.load_draft('research', event_kind='plan', workline_id='audit'), draft)
        self.assertFalse(dialog.save())
        self.assertEqual(dialog.conflicting_revision, 1)
        self.assertIn('已保留', dialog.error_var.get())

    def test_new_progress_preserves_historical_node_file_view_and_history_selection(self):
        first = self.submit(files=self.attachments)
        self.check_external()
        self.app.reload_saved_progress()
        self.app.history_tree.selection_set('0')
        self.app._show_history()
        self.app.show_latest_files()
        self.root.update()
        selected = self.select_file(self.attachments[-1])
        self.panel.files_tree.yview_moveto(1)
        self.root.update()
        old_y = self.panel.files_tree.yview()
        before = copy.deepcopy(self.service.history('research', 'audit')[0])
        self.submit(files=[self.attachments[0]])
        self.check_external()
        self.app.reload_saved_progress()
        self.root.update()
        self.assertEqual(self.app.notebook.select(), str(self.app.files_tab))
        self.assertEqual(self.panel.current_node_id, first['node_id'])
        self.assertEqual(self.panel.files_tree.selection(), (selected['id'],))
        self.assertEqual(self.panel.files_tree.yview(), old_y)
        history_selection = self.app.history_tree.selection()[0]
        self.assertEqual(self.app.history_records[int(history_selection)]['revision'], first['revision'])
        self.assertIn('trial-session-1', self.app.history_detail.get('1.0', 'end-1c'))
        self.assertEqual(self.service.history('research', 'audit')[-1], before)

    def test_other_workline_progress_updates_sidebar_without_switching_reader(self):
        before_project, before_line = self.app.project_id, self.app.workline_id
        before_node = self.panel.current_node_id
        self.submit(line='default')
        self.check_external()
        self.assertTrue(self.app.external_progress_pending)
        self.app.reload_saved_progress()
        self.root.update()
        self.assertEqual((self.app.project_id, self.app.workline_id), (before_project, before_line))
        self.assertEqual(self.panel.current_node_id, before_node)
        self.assertIsNone(self.app.current['handoff'])
        self.assertEqual(self.service.store.selected_workline('research'), 'audit')
        self.assertFalse(self.app.external_progress_pending)
        self.assertIn('中文研究项目 / 原有工作线', self.app.status_var.get())

    def test_non_record_writes_do_not_prompt_and_unchanged_poll_is_lightweight(self):
        writer = Service(self.appdir)
        writer.save_preferences({'reduced_motion': True})
        self.check_external()
        self.assertFalse(self.app.external_progress_pending)
        with patch.object(self.app, '_record_revisions', wraps=self.app._record_revisions) as revisions:
            for _ in range(4):
                self.app._check_external_progress()
            revisions.assert_not_called()

    def test_x11_wheel_bindings_reach_card_and_editor_scroll_containers(self):
        self.assertTrue(self.app.card_canvas.bind('<Button-4>'))
        self.assertTrue(self.app.card_canvas.bind('<Button-5>'))
        dialog = self.app.create_plan_dialog()
        self.assertTrue(dialog.next_text.bind('<Button-4>'))
        self.assertTrue(dialog.next_text.bind('<Button-5>'))

    def test_ubuntu_file_panel_uses_native_file_manager_wording(self):
        frame = ttk.Frame(self.root)
        with patch('handoffdesk.file_panel.platform_io.is_windows', return_value=False):
            panel = NodeFilesPanel(self.app, frame)
        self.assertEqual(str(panel.reveal_button.cget('text')), '文件管理器')
        panel.refresh('research', preferred_node_id='current:audit')
        with patch.object(self.service, 'open_node_file', return_value=str(self.guide)):
            panel.open_selected('reveal')
        self.assertIn('在文件管理器中显示', self.app.status_var.get())
        frame.destroy()


if __name__ == '__main__':
    unittest.main()
