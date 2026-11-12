"""Independent nested projects and their records through the public CLI."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from handoffdesk.cli import main


class NestedProjectCLITests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        base = Path(folder.name)
        self.state = base / 'state'
        parent = base / 'JMLR'
        child = parent / '子项目'
        self.roots = {'parent': parent, 'child': child}
        self.runs = child / 'runs'
        self.runs.mkdir(parents=True)
        self.ordinary = parent / 'other'
        self.ordinary.mkdir()

    def command(self, *args, payload=None):
        out, err = StringIO(), StringIO()
        with patch('sys.stdin', StringIO(json.dumps(payload))), redirect_stdout(out), redirect_stderr(err):
            code = main(['--app-dir', str(self.state), *map(str, args)])
        self.assertEqual(code, 0, out.getvalue() + err.getvalue())
        return json.loads(out.getvalue())

    def assert_independent_records(self, first, second):
        self.command('init', '--project', first, '--root', self.roots[first])
        inherited = self.command('agent', 'inspect', '--cwd', self.runs)
        self.assertEqual(inherited['project']['id'], first)
        self.command('project', 'add', second, '--root', self.roots[second])

        expected_ids = {}
        for project, cwd in [('parent', self.ordinary), ('child', self.runs)]:
            match = self.command('agent', 'inspect', '--cwd', cwd)
            self.assertEqual((match['project']['id'], match['root']),
                             (project, str(self.roots[project].resolve())))
            session = self.command('agent', 'begin', '--cwd', cwd, '--task', '同名任务', '--actor', 'Test')
            self.assertEqual(session['project'], project)
            self.assertFalse(session['workspace_added'])
            saved = self.command('agent', 'checkpoint', '--session', session['session_ref'],
                                 '--checkpoint', session['checkpoint_token'], '--stdin',
                                 payload={'kind': 'handoff', 'title': project, 'actual_done': '已执行',
                                          'next_action': '核对结果'})
            expected_ids[project] = saved['record_id']

        self.assertCountEqual([p['id'] for p in self.command('list', '--json')], self.roots)
        for project, record_id in expected_ids.items():
            items = self.command('feed', '--project', project)['items']
            self.assertEqual([(i['id'], i['title'], i['workline_name']) for i in items],
                             [(record_id, project, '同名任务')])
        entries = self.command('feed')['items']
        self.assertCountEqual([(i['project_id'], i['id']) for i in entries], expected_ids.items())

    def test_parent_then_child(self):
        self.assert_independent_records('parent', 'child')

    def test_child_then_parent(self):
        self.assert_independent_records('child', 'parent')


if __name__ == '__main__':
    unittest.main()
