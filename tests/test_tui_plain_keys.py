"""Unmodified keys work in browsers and stay literal in every text entry."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('textual'), 'Textual optional dependency')
class PlainKeyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from handoffdesk.agent_records import AgentRecords
        from handoffdesk.bootstrap import initialize
        from handoffdesk.service import Service
        from handoffdesk.tui import create_app

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        project = root / 'project'
        project.mkdir()
        state = root / 'state'
        initialize(state, project_id='sample', name='样例项目', root=project)
        AgentRecords(Service(state)).save({
            'schema_version': 1, 'project': 'sample', 'workline': 'default',
            'expected_revision': 0, 'operation_id': 'plain-keys',
            'record': {'title': '记录', 'last_done': '已执行', 'next_action': '核查'},
            'actor': {'name': 'test'}, 'verification': {'status': 'passed', 'summary': '已核查'}, 'files': []})
        self.app = create_app(state)

    def footer_key(self, key):
        buttons = [item for item in self.app.screen.query('FooterKey') if item.key == key]
        self.assertEqual(len(buttons), 1)
        button = buttons[0]
        self.assertGreaterEqual(button.region.x, 0)
        self.assertLessEqual(button.region.right, self.app.size.width)
        return button

    async def test_plain_keys_visible_on_browsing_screens_and_clickable(self):
        from handoffdesk.tui_reading import HelpScreen
        from textual.widgets import Input, OptionList

        async with self.app.run_test(size=(60, 18)) as pilot:
            for screen in ('home', 'project', 'feed', 'detail'):
                if screen != 'home':
                    await pilot.press('enter')
                await pilot.pause()
                self.footer_key('q')
                self.assertFalse(any(k.key == 'ctrl+p' for k in self.app.screen.query('FooterKey')))
                menu_key = self.footer_key('full_stop')
                origin = self.app.screen
                if screen == 'home':
                    self.assertTrue(await pilot.click(menu_key))
                else:
                    await pilot.press('.')
                self.assertIsInstance(self.app.screen, HelpScreen)
                choices = self.app.screen.query_one(OptionList)
                self.assertIn('quit', [row.id for row in choices.options])
                self.assertIs(self.app.focused, choices)
                self.assertFalse(self.app.screen.query(Input))
                self.assertIn('handoffdesk doctor --help', choices.options[-1].prompt.plain)
                await pilot.press('escape')
                self.assertIs(self.app.screen, origin)
            self.assertIsNotNone(self.app.current)
            with patch.object(self.app, 'exit', wraps=self.app.exit) as exit_app:
                self.assertTrue(await pilot.click(self.footer_key('q')))
                exit_app.assert_called_once()

    async def test_form_and_search_keep_literal_keys_then_q_quits_listing(self):
        from textual.widgets import Input

        async with self.app.run_test(size=(80, 24)) as pilot:
            await pilot.press('a', '.', 'q')
            self.assertEqual(self.app.screen.query_one('#project-root', Input).value, '.q')
            await pilot.press('tab', 'q', 'ctrl+q')
            self.assertIsNotNone(self.app.screen.query_one('#entry-box'))
            await pilot.press('escape', '/', '.', 'q')
            self.assertEqual(self.app.widget('#search', Input).value, '.q')
            await pilot.press('escape', 'escape')
            with patch.object(self.app, 'notify') as notify:
                await pilot.press('ctrl+c')
                notify.assert_called_once_with('按 q 退出阅读器。')
            with patch.object(self.app, 'exit', wraps=self.app.exit) as exit_app:
                await pilot.press('q')
                exit_app.assert_called_once()

    async def test_explicit_quit_from_menu_opened_over_search(self):
        from textual.widgets import Input

        async with self.app.run_test(size=(80, 24)) as pilot:
            await pilot.press('/', 'q', '.', 'ctrl+p')
            self.app.screen.query_one('#action-query', Input).value = '退出'
            await pilot.pause()
            with patch.object(self.app, 'exit', wraps=self.app.exit) as exit_app:
                await pilot.press('enter')
                exit_app.assert_called_once()

    async def test_read_only_help_can_exit_and_ctrl_p_still_opens_menu(self):
        from handoffdesk.tui_reading import ActionMenu, HelpScreen
        from textual.widgets import OptionList

        async with self.app.run_test(size=(60, 18)) as pilot:
            await pilot.press('ctrl+p')
            self.assertIsInstance(self.app.screen, ActionMenu)
            await pilot.press('escape', '?')
            self.assertIsInstance(self.app.screen, HelpScreen)
            self.assertEqual(self.app.screen.query_one(OptionList).highlighted_option.id, 'shortcuts')
            self.footer_key('q')
            with patch.object(self.app, 'exit', wraps=self.app.exit) as exit_app:
                await pilot.press('q')
                exit_app.assert_called_once()

    async def test_detach_choice_only_on_managed_reader_and_error_keeps_it_running(self):
        from textual.widgets import OptionList

        async with self.app.run_test() as pilot:
            with patch('handoffdesk.terminal_session.is_managed_reader', return_value=False):
                await pilot.press('.')
                choices = self.app.screen.query_one(OptionList)
                self.assertNotIn('detach', [row.id for row in choices.options])
                await pilot.press('escape')
            with patch('handoffdesk.terminal_session.is_managed_reader', return_value=True):
                origin = self.app.screen
                await pilot.press('.')
                choices = self.app.screen.query_one(OptionList)
                choices.highlighted = choices.get_option_index('detach')
                with patch('handoffdesk.terminal_session.detach_reader', side_effect=ValueError('连接已改变')) as detach, \
                        patch.object(self.app, 'notify') as notify:
                    await pilot.press('enter')
                    detach.assert_called_once()
                    notify.assert_called_once_with('连接已改变', severity='warning')
                    self.assertIs(self.app.screen, origin)
