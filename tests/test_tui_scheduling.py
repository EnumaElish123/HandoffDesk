"""Reader work should follow the current input, even when storage stalls."""
import asyncio
import importlib.util
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch


async def until(predicate):
    async def check():
        while not predicate():
            await asyncio.sleep(.01)
    await asyncio.wait_for(check(), timeout=8)


@unittest.skipUnless(importlib.util.find_spec('textual'), 'optional textual extra')
class TerminalSchedulingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from handoffdesk.agent_records import AgentRecords
        from handoffdesk.bootstrap import add_project, initialize
        from handoffdesk.service import Service

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name) / 'state'
        for key, titles in [('one', ['first result', 'last result']), ('two', ['first elsewhere'])]:
            folder = Path(tmp.name) / key
            folder.mkdir()
            if key == 'one':
                initialize(self.state, project_id=key, root=folder)
            else:
                add_project(self.state, key, root=folder)
            writer = AgentRecords(Service(self.state))
            for revision, title in enumerate(titles):
                writer.save({'schema_version': 1, 'project': key, 'workline': 'default',
                             'operation_id': f'{key}-{revision}', 'expected_revision': revision,
                             'record': {'title': title, 'last_done': title, 'next_action': 'Verify'},
                             'actor': {'name': 'Test'}, 'verification': {'status': 'not_run', 'summary': 'Pending'},
                             'files': []})

    async def open(self, app):
        app.next_poll = float('inf')  # These tests schedule storage I/O explicitly.
        app.open_feed('one', 'default')
        await until(lambda: app.feed_ready and len(app.items) == 2)

    async def test_only_latest_input_runs_after_inflight_search(self):
        from handoffdesk.reader import ReaderModel
        from handoffdesk.tui import create_app
        from textual.widgets import Input

        app = create_app(self.state)
        entered = {q: threading.Event() for q in ('first', 'last')}
        release = {q: threading.Event() for q in entered}
        queries = []
        records = ReaderModel.records

        def gated(model, project, line, limit, cursor, unread, query):
            if query:
                queries.append(query)
            if query in entered:
                entered[query].set()
                if not release[query].wait(10):
                    raise AssertionError(f'Query was never released: {query}')
            return records(model, project, line, limit, cursor, unread, query)

        async with app.run_test() as pilot:
            await self.open(app)
            original_ids = [item['id'] for item in app.items]
            await pilot.press('/')
            field = app.widget('#search', Input)
            with patch.object(ReaderModel, 'records', gated):
                try:
                    field.value = 'first'
                    await until(entered['first'].is_set)
                    field.value = 'f'
                    field.value = 'la'
                    field.value = 'last'
                    await until(lambda: app.search_queued)
                    self.assertEqual(queries, ['first'])
                    release['first'].set()
                    await until(entered['last'].is_set)
                    self.assertEqual([item['id'] for item in app.items], original_ids)
                    release['last'].set()
                    await until(lambda: not app.search_busy)
                    self.assertEqual(queries, ['first', 'last'])
                    self.assertEqual([item['title'] for item in app.items], ['last result'])
                finally:
                    for gate in release.values():
                        gate.set()

    async def test_escape_restores_task_unread_filter_and_anchor_before_old_result_arrives(self):
        from handoffdesk.reader import ReaderModel
        from handoffdesk.tui import create_app
        from textual.widgets import Input, OptionList

        app = create_app(self.state)
        entered, release = threading.Event(), threading.Event()
        records = ReaderModel.records

        def gated(model, project, line, limit, cursor, unread, query):
            if project is None and query == 'elsewhere':
                entered.set()
                if not release.wait(10):
                    raise AssertionError('Cross-project query was never released')
            return records(model, project, line, limit, cursor, unread, query)

        async with app.run_test() as pilot:
            await self.open(app)
            await app.action_unread()
            listing = app.widget('#records', OptionList)
            listing.highlighted = 1
            anchor = app.items[1]['id']
            await pilot.press('/')
            await app.choose_scope('*')
            with patch.object(ReaderModel, 'records', gated):
                try:
                    app.widget('#search', Input).value = 'elsewhere'
                    await until(entered.is_set)
                    await pilot.press('escape')
                    await until(lambda: app.search_origin is None and len(app.items) == 2)
                    release.set()
                    await until(lambda: not app.search_busy)
                    self.assertEqual(app.scope(), ('one', 'default'))
                    self.assertTrue(app.unread_only)
                    self.assertEqual(app.items[listing.highlighted]['id'], anchor)
                    self.assertEqual({item['project_id'] for item in app.items}, {'one'})
                    self.assertIs(app.focused, listing)
                finally:
                    release.set()

    async def test_help_during_slow_resume_preserves_requested_record(self):
        from handoffdesk.tui import create_app
        from handoffdesk.tui_reading import HelpScreen

        app = create_app(self.state)
        entered, release = asyncio.Event(), asyncio.Event()
        changes = app.changes

        async def delayed(cursor):
            entered.set()
            await release.wait()
            return await changes(cursor)

        async with app.run_test() as pilot:
            app.next_poll = float('inf')
            with patch.object(app, 'changes', delayed):
                try:
                    app.open_feed('one', 'default', 'one:default:2')
                    await asyncio.wait_for(entered.wait(), timeout=8)
                    await pilot.press('?')
                    self.assertIsInstance(app.screen, HelpScreen)
                    release.set()
                    await until(lambda: app.feed_ready and bool(app.items))
                    self.assertIsInstance(app.screen, HelpScreen)
                    await pilot.press('escape')
                    await until(lambda: app.current is not None)
                    self.assertEqual(app.current['id'], 'one:default:2')
                    self.assertIs(app.screen, app.reader_screen)
                finally:
                    release.set()
