import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from handoffdesk.tui import ensure_terminal


class TerminalGuardTests(unittest.TestCase):
    def test_noninteractive_rejected_without_creating_state(self):
        with patch('sys.stdin.isatty', return_value=False), patch('sys.stdout.isatty', return_value=False):
            with self.assertRaisesRegex(ValueError, 'TTY'):
                ensure_terminal()


@unittest.skipUnless(importlib.util.find_spec('textual'), 'optional textual extra is not installed')
class TerminalInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_search_files_and_resize(self):
        from handoffdesk.agent_records import AgentRecords
        from handoffdesk.bootstrap import initialize
        from handoffdesk.service import Service
        from handoffdesk.tui import create_app
        from textual.widgets import Input, TextArea
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / 'project'
            project.mkdir()
            note = project / '说明.txt'
            note.write_text('中文报告正文\n第二行', encoding='utf-8')
            initialize(base / 'state', project_id='test', root=project)
            AgentRecords(Service(base / 'state')).save({'schema_version': 1, 'project': 'test', 'workline': 'default',
                'expected_revision': 0, 'operation_id': 'tui-test', 'record': {'title': '界面试用', 'last_done': '试用中文。', 'next_action': '阅读附件。'},
                'actor': {'name': '测试 Agent'}, 'verification': {'status': 'not_run', 'summary': '交互测试中。'},
                'files': [{'path': str(note), 'purpose': '中文报告'}]})
            app = create_app(base / 'state')
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                self.assertEqual(len(app.items), 1)
                await pilot.press('enter')
                await pilot.pause()
                self.assertIsNotNone(app.current)
                self.assertIn('试用中文', app.query_one('#detail', TextArea).text)
                self.assertTrue(app.screen.has_class('detail-mode'))
                await pilot.press('f')
                await pilot.pause()
                await pilot.press('enter')
                await pilot.pause()
                self.assertIn('中文报告正文', app.screen.query_one('#file-preview', TextArea).text)
                await pilot.press('escape', 'escape', '/')
                await pilot.press('q', 'p', 'r')
                self.assertEqual(app.query_one('#search', Input).value, 'qpr')
                await pilot.resize_terminal(120, 40)
                await pilot.pause()
                self.assertFalse(app.screen.has_class('narrow'))


if __name__ == '__main__':
    unittest.main()
