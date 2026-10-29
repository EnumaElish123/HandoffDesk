"""User journeys from the Linux trial: discovery, stable history and reading."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('textual'), 'optional textual extra is not installed')
class OptimizedTerminalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from handoffdesk.agent_records import AgentRecords
        from handoffdesk.bootstrap import initialize
        from handoffdesk.service import Service
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.project = self.base / '中文 项目'
        self.project.mkdir()
        self.state = self.base / 'state'
        initialize(self.state, project_id='trial', name='试用项目', root=self.project)
        self.note = self.project / '排查报告.md'
        self.note.write_text('# 报告\n' + '\n'.join('正文第%d行' % i for i in range(100)), encoding='utf-8')
        self.api = AgentRecords(Service(self.state))
        self.second = self.project / '输入日志.txt'
        self.second.write_text('只读日志', encoding='utf-8')
        self.revision = 0

    def save(self, title='完成核对'):
        self.revision += 1
        return self.api.save({'schema_version':1,'project':'trial','workline':'default',
            'expected_revision':self.revision-1,'operation_id':f'terminal-{self.revision}',
            'record':{'title':title,'last_done':'已检查日志','next_action':'复查配置'},
            'actor':{'name':'Agent'},'verification':{'status':'partial','summary':'本地核对'},
            'files':[{'path':str(self.note),'purpose':'排查结果'}, {'path':str(self.second),'purpose':'原始日志'}]})

    async def test_search_from_detail_and_task_scope(self):
        from handoffdesk.tui import create_app
        from textual.widgets import Input, OptionList, Select, Static
        self.save()
        app = create_app(self.state)
        async with app.run_test(size=(80,24)) as pilot:
            await pilot.pause()
            row = str(app.query_one('#records',OptionList).get_option_at_index(0).prompt)
            self.assertIn('试用项目 / ', row)
            await pilot.press('enter','/')
            await pilot.press(*list('找不到的词'))
            await pilot.pause()
            self.assertTrue(app.screen.has_class('list-mode'))
            self.assertTrue(app.screen.has_class('empty-mode'))
            self.assertIsNone(app.current)
            self.assertIn('没有符合条件', str(app.query_one('#empty',Static).render()))
            await pilot.press('escape','x')
            await pilot.pause()
            self.assertEqual(app.query_one('#search',Input).value,'')
            self.assertEqual(len(app.items),1)
            app.query_one('#task',Select).value='trial:default'
            await pilot.pause()
            self.assertEqual(len(app.items),1)

    async def test_attachment_full_page_and_missing_file(self):
        from handoffdesk.tui import create_app
        from textual.widgets import TextArea, OptionList
        self.save()
        app=create_app(self.state)
        async with app.run_test(size=(80,24)) as pilot:
            await pilot.pause()
            await pilot.press('enter','f'); await pilot.pause()
            self.assertIn('可用',str(app.screen.query_one('#file-list',OptionList).get_option_at_index(0).prompt))
            await pilot.press('enter');await pilot.pause()
            area=app.screen.query_one('#file-preview',TextArea)
            self.assertEqual(app.focused,area)
            self.assertGreaterEqual(area.content_region.height,10)
            await pilot.press('pagedown');await pilot.pause()
            self.assertGreater(area.scroll_y,0)
            await pilot.resize_terminal(60,18);await pilot.pause()
            self.assertGreaterEqual(area.content_region.height,10)
            await pilot.press('m');await pilot.pause()
            await pilot.press('ctrl+c');await pilot.pause()
            self.assertEqual(app.clipboard,str(self.note))
            await pilot.press('escape','down','ctrl+c');await pilot.pause()
            self.assertEqual(app.clipboard,str(self.second))
            await pilot.press('escape');await pilot.pause()
            self.note.unlink()
            await pilot.press('f');await pilot.pause()
            self.assertIn('缺失',str(app.screen.query_one('#file-list',OptionList).get_option_at_index(0).prompt))
            await pilot.press('enter');await pilot.pause()
            self.assertIn('请让 Agent',app.screen.query_one('#file-preview',TextArea).text)

    async def test_history_anchor_survives_arrivals_and_explicit_refresh(self):
        from handoffdesk.tui import create_app
        from textual.widgets import OptionList
        for i in range(55):self.save('长标题'*15+str(i))
        app=create_app(self.state)
        async with app.run_test(size=(80,24)) as pilot:
            await pilot.pause();await pilot.press('n');await pilot.pause()
            listing=app.query_one('#records',OptionList)
            listing.highlighted=len(app.items)-1
            await pilot.pause()
            identity=app.items[listing.highlighted]['id']
            offset=listing.row_line(listing.highlighted)-listing.scroll_y
            for i in range(10):self.save('后台新记录'+str(i))
            await app.poll();await pilot.pause()
            self.assertEqual(app.items[listing.highlighted]['id'],identity)
            self.assertEqual(listing.row_line(listing.highlighted)-listing.scroll_y,offset)
            self.assertTrue(app.pending_updates)
            await pilot.press('r');await pilot.pause()
            self.assertEqual(app.items[listing.highlighted]['id'],identity)
            self.assertEqual(len(app.items),65)
            self.assertEqual(listing.row_line(listing.highlighted)-listing.scroll_y,offset)

    async def test_export_confirmation_collision_and_cancel(self):
        from handoffdesk.tui import create_app
        from textual.widgets import Input,Static
        self.save()
        destination=self.base/'交接 导出.md'
        app=create_app(self.state)
        async with app.run_test(size=(80,24)) as pilot:
            await pilot.pause();await pilot.press('enter','e');await pilot.pause()
            self.assertFalse(destination.exists())
            app.screen.query_one('#export-path',Input).value=str(destination)
            await pilot.press('enter');await pilot.pause()
            original=destination.read_text()
            self.assertIn('排查结果',original)
            await pilot.press('e');await pilot.pause()
            app.screen.query_one('#export-path',Input).value=str(destination)
            await pilot.press('enter');await pilot.pause()
            self.assertIn('未导出',str(app.screen.query_one('#export-message',Static).render()))
            self.assertEqual(destination.read_text(),original)
            await pilot.press('escape')
            await pilot.press('y');await pilot.pause()
            self.assertIn('trial',app.clipboard)
            self.assertIn('agent sessions',app.clipboard)

    async def test_empty_onboarding_and_diagnostic_modal_poll(self):
        from handoffdesk.tui import create_app
        from textual.widgets import Static,TextArea
        result={'app_dir':str(self.state),'connected':False,'fully_verified':False,'host_trust':'not_verified',
                'message':'尚未接入','issues':[{'code':'not_connected','message':'未配置','next_action':'运行 handoffdesk connect codex','severity':'info'}],
                'layers':[],'next_action':'运行 handoffdesk connect codex'}
        with patch('handoffdesk.integration.doctor',return_value=result):
            app=create_app(self.state)
            async with app.run_test(size=(80,24)) as pilot:
                await pilot.pause()
                self.assertIn('尚未接入',str(app.query_one('#empty',Static).render()))
                await pilot.press('c');await pilot.pause()
                self.assertIn('connect codex',app.screen.query_one('#connection-info',TextArea).text)
                self.save()
                await app.poll();await pilot.pause()
                self.assertEqual(len(app.items),1)
                await pilot.press('escape');await pilot.pause()
                self.assertFalse(app.screen.has_class('empty-mode'))
