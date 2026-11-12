"""Keyboard journeys for project registration, navigation and reading history."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

from handoffdesk.reader import ReaderModel


@unittest.skipUnless(importlib.util.find_spec('textual'), 'optional textual extra is not installed')
class ProjectNavigationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from handoffdesk.tui_projects import make_project_screens
        from textual.app import App
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.project = self.base / '中文 项目'
        self.project.mkdir()
        self.state = self.base / 'state'
        self.model = ReaderModel(self.state)
        home = make_project_screens(self.model)

        class Host(App):
            opened = None

            def on_mount(self):
                self.push_screen(home())

            def open_feed(self, project, line, record_id=None, *, search=False):
                self.opened = (project, line, record_id, search)

        self.app = Host()

    async def test_register_validate_retry_create_task_and_remember_focus(self):
        from textual.widgets import Input, OptionList
        async with self.app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            await pilot.press('enter')
            self.app.screen.query_one('#project-root', Input).value = str(self.base / 'missing')
            await pilot.press('enter', 'enter')
            await pilot.pause()
            self.assertIn('绝对目录', str(self.app.screen.query_one('#entry-status').render()))
            self.app.screen.query_one('#project-root', Input).value = str(self.project)
            await pilot.press('enter', 'enter')
            await pilot.pause()
            home = self.app.screen
            self.assertIn(self.project.name, str(home.query_one('#nav-title').render()))
            rows = home.query_one('#nav-list', OptionList)
            self.assertEqual(rows.get_option_at_index(rows.highlighted).id, 'add')
            self.assertEqual(self.model.records()['items'], [])
            await pilot.press('enter')
            self.app.screen.query_one('#task-name', Input).value = '实验复核'
            await pilot.press('enter')
            await pilot.pause()
            pid, line, _, _ = self.app.opened
            self.assertEqual(pid, home.project_id)
            self.assertIn('实验复核', [x['name'] for x in self.model.projects()[0]['worklines'] if x['id'] == line])
            await pilot.press('escape')
            await pilot.pause()
            rows = self.app.screen.query_one('#nav-list', OptionList)
            self.assertEqual(rows.get_option_at_index(rows.highlighted).id, 'project:' + pid)

    async def test_cancel_does_not_register_and_search_has_scope(self):
        from textual.widgets import Input
        async with self.app.run_test(size=(60, 18)) as pilot:
            await pilot.pause()
            await pilot.press('a')
            self.app.screen.query_one('#project-root', Input).value = str(self.project)
            await pilot.press('escape', '/')
            self.assertEqual(self.app.opened, (None, None, None, True))
            self.assertEqual(self.model.registered_projects(), [])
            self.assertEqual(list(self.project.iterdir()), [])

    async def test_resume_and_exact_duplicate_use_stable_ids(self):
        from textual.widgets import OptionList
        from handoffdesk.tui_projects import _register, save_reading_position
        p, created = _register(self.model, str(self.project), '样例')
        self.assertTrue(created)
        same, created = _register(self.model, str(self.project), '改名')
        self.assertFalse(created)
        self.assertEqual(same['id'], p['id'])
        self.assertEqual(same['name'], '样例')
        nested = self.project / '子目录'
        nested.mkdir()
        with self.assertRaisesRegex(ValueError, '重叠'):
            _register(self.model, str(nested), '子项目')
        rid = p['id'] + ':default:1'
        save_reading_position(self.model, dict(id=rid, project_id=p['id'], workline_id='default', title='上次阅读'))
        async with self.app.run_test() as pilot:
            await pilot.pause()
            rows = self.app.screen.query_one('#nav-list', OptionList)
            rows.highlighted = rows.get_option_index('resume')
            await pilot.press('enter')
            self.assertEqual(self.app.opened, (p['id'], 'default', rid, False))

    def test_concurrent_registration_reuses_one_project(self):
        from concurrent.futures import ThreadPoolExecutor
        from handoffdesk.tui_projects import _register
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_register, self.model, str(self.project), '登记') for _ in range(2)]
            results = [future.result(timeout=10) for future in futures]
        self.assertEqual(len({project['id'] for project, _ in results}), 1)
        self.assertEqual(sum(created for _, created in results), 1)

    async def test_project_lists_latest_title_and_ignores_incomplete_resume_preference(self):
        from handoffdesk.agent_records import AgentRecords
        from handoffdesk.tui_projects import _register
        from textual.widgets import OptionList
        project, _ = _register(self.model, str(self.project), '已登记')
        service = self.model._service()
        service.store.save_preferences({'tui_reading_position': {'project_id': project['id']}})
        record = {'schema_version': 1, 'project': project['id'], 'workline': 'default',
                  'operation_id': 'project-card-test', 'expected_revision': 0,
                  'record': {'title': '最近一条记录', 'last_done': '已完成', 'next_action': '复核'},
                  'actor': {'name': '测试'}, 'verification': {'status': 'passed', 'summary': '已检查'},
                  'files': []}
        AgentRecords(service).save(record)
        async with self.app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            rows = self.app.screen.query_one('#nav-list', OptionList)
            self.assertNotIn('resume', [option.id for option in rows.options])
            await pilot.press('enter')
            await pilot.pause()
            task = self.app.screen.query_one('#nav-list', OptionList).get_option('task:default')
            self.assertIn('最近一条记录', str(task.prompt))


if __name__ == '__main__':
    unittest.main()
