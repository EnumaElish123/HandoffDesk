"""Real reader journeys across navigation, read state, and contextual actions."""
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('textual'), 'optional textual extra')
class ReadingJourneys(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from handoffdesk.agent_records import AgentRecords
        from handoffdesk.bootstrap import initialize
        from handoffdesk.reader import ReaderModel
        from handoffdesk.service import Service

        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        project = Path(workspace.name) / '对照'
        project.mkdir()
        self.state = Path(workspace.name) / 'state'
        initialize(self.state, project_id='review', name='对照项目', root=project)
        records = AgentRecords(Service(self.state))
        self.ids = []
        for revision, title in enumerate(('先前记录', '当前记录')):
            receipt = records.save({
                'schema_version':1, 'project':'review', 'workline':'default',
                'expected_revision':revision, 'operation_id':f'reading-{revision}',
                'record':{'title':title, 'last_done':title + '已完成', 'next_action':title + '下一步'},
                'actor':{'name':'test'}, 'verification':{'status':'passed', 'summary':'完成核验'},
                'files':[]})
            self.ids.append(receipt['record_id'])
        self.model = ReaderModel(self.state)

    async def test_export_continue_and_unread_follow_open_record(self):
        from handoffdesk.tui import create_app
        from handoffdesk.tui_reading import ActionMenu, CopyScreen, HelpScreen
        from textual.widgets import OptionList, TextArea

        app = create_app(self.state)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press('enter', 'enter')
            await pilot.pause()
            self.assertEqual(app.items[0]['id'], self.ids[1])
            await pilot.press('enter', 'y')
            self.assertIsInstance(app.screen, CopyScreen)
            self.assertIn(self.ids[1], app.clipboard)
            await pilot.press('escape', 'escape', 'down')
            self.assertIsNone(app.current)
            self.assertFalse(self.model.detail(self.ids[0])['read'])
            await pilot.press('e', 'y')
            self.assertIs(app.screen, app.reader_screen)
            await pilot.press('enter', 'e')
            self.assertEqual(app.screen.record['id'], self.ids[0])
            await pilot.press('escape', 'y')
            self.assertIn(self.ids[0], app.clipboard)
            self.assertIn('先前记录下一步', app.screen.query_one(TextArea).text)
            await pilot.press('escape', 'ctrl+p')
            self.assertIsInstance(app.screen, ActionMenu)
            self.assertGreater(app.screen.query_one(OptionList).option_count, 0)
            await pilot.press('escape', '?')
            self.assertIsInstance(app.screen, HelpScreen)
            await pilot.press('escape')
            self.assertEqual(app.current['id'], self.ids[0])
            await app.action_mark_unread()
            self.assertFalse(self.model.detail(self.ids[0])['read'])
            self.assertTrue(self.model.detail(self.ids[1])['read'])

    async def test_ack_failure_keeps_readable_detail(self):
        from handoffdesk.tui import create_app
        from textual.widgets import Static, TextArea

        app = create_app(self.state)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            await pilot.press('enter','enter')
            await pilot.pause()
            with patch('handoffdesk.reader.ReaderModel.ack', side_effect=sqlite3.OperationalError('busy')):
                await pilot.press('enter')
                await pilot.pause()
            self.assertIn('当前记录下一步', app.widget('#detail', TextArea).text)
            self.assertIn('已读', str(app.widget('#status', Static).render()))
            self.assertFalse(self.model.detail(self.ids[1])['read'])
