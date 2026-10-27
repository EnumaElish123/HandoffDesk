"""End-to-end command tests using only small, isolated fixture projects."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from handoffdesk.cli import main


class V03CLIIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.package = Path(__file__).resolve().parent.parent
        self.temp = tempfile.TemporaryDirectory(prefix='cli-v03-', dir=self.package / 'tests')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.app = self.folder / 'app'
        self.source = self.folder / 'business'
        self.source.mkdir()
        (self.app / 'config').mkdir(parents=True)
        self.state = self.source / 'state.json'
        self.state.write_text('{"status":"before"}', encoding='utf-8')
        self.model = self.source / 'voice.pth'
        self.model.write_text('fixture only', encoding='utf-8')
        configuration = {'schema_version': 1, 'projects': [{
            'id': 'demo', 'name': '演示', 'roots': {'main': str(self.source)},
            'sources': [
                {'id': 'state', 'root': 'main', 'path': 'state.json', 'format': 'json',
                 'fields': {'status': '/status'}},
                {'id': 'model', 'root': 'main', 'path': 'voice.pth', 'format': 'text'}],
            'entries': [
                {'id': 'state-file', 'type': 'open_source', 'source_id': 'state'},
                {'id': 'model-file', 'type': 'open_source', 'source_id': 'model'}],
            'worklines': [{'id': 'alpha', 'name': '甲工作线', 'entry_ids': ['state-file']},
                          {'id': 'beta', 'name': '乙工作线', 'entry_ids': ['model-file']}]}]}
        (self.app / 'config/projects.json').write_text(json.dumps(configuration), encoding='utf-8')

    def command(self, *args):
        return subprocess.run([sys.executable, '-E', '-s', '-B', '-X', 'utf8', '-m',
            'handoffdesk', '--app-dir', str(self.app), '--data-dir', str(self.folder / 'data'),
            *args], cwd=self.package, capture_output=True, text=True, encoding='utf-8', timeout=15)

    def value(self, *args):
        result = self.command(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def refresh(self, line):
        return self.value('refresh', 'demo', '--workline', line, '--json')

    def save_plan(self, line, snapshot, next_action, revision=0):
        payload = self.folder / ('plan-' + line + '.json')
        payload.write_text(json.dumps({'next_action': next_action,
            'source_snapshot_id': snapshot}, ensure_ascii=False), encoding='utf-8')
        return self.value('plan', 'demo', '--workline', line, '--file', str(payload),
                          '--expect-revision', str(revision))

    def test_named_worklines_and_explicit_scope_do_not_follow_active_selection(self):
        lines = self.value('worklines', '演示')
        self.assertTrue({'default', 'alpha', 'beta'}.issubset({line['id'] for line in lines}))
        created = self.value('worklines', 'demo', 'create', '临时复核 工作线')
        self.assertEqual(created['name'], '临时复核 工作线')
        selected = self.value('worklines', 'demo', 'select', created['id'])
        self.assertEqual(selected['workline']['id'], created['id'])
        alpha = self.refresh('alpha')
        beta = self.refresh('beta')
        first = self.save_plan('alpha', alpha['snapshot']['id'], '核对甲资料')
        second = self.save_plan('beta', beta['snapshot']['id'], '核对乙资料')
        self.assertEqual((first['revision'], second['revision']), (1, 1))
        self.assertEqual(self.value('show', 'demo', '--json')['workline']['id'], created['id'])
        self.assertEqual(self.value('show', 'demo', '--workline', 'alpha', '--json')['handoff']['next_action'], '核对甲资料')
        self.assertEqual(len(self.value('history', 'demo', '--workline', 'beta')), 1)

    def test_review_records_do_not_rewrite_original_handoff_and_are_scoped(self):
        first = self.refresh('alpha')
        saved = self.save_plan('alpha', first['snapshot']['id'], '核对原始资料')
        self.state.write_text('{"status":"after"}', encoding='utf-8')
        current = self.refresh('alpha')
        self.assertTrue(self.value('changes', 'demo', '--workline', 'alpha')['changes'])
        reviewed = self.value('review', 'demo', '--workline', 'alpha', '--expect-revision', '1',
                              '--snapshot', str(current['snapshot']['id']))
        self.assertTrue(reviewed)
        state = self.value('show', 'demo', '--workline', 'alpha', '--json')
        self.assertEqual(state['handoff'], saved)
        self.assertEqual(state['source_changes'], [])
        self.assertEqual(len(self.value('reviews', 'demo', '--workline', 'alpha')), 1)
        self.assertEqual(self.value('reviews', 'demo', '--workline', 'beta'), [])
        self.assertEqual(len(self.value('history', 'demo', '--workline', 'alpha')), 1)

    def test_stale_and_cross_scope_review_are_rejected(self):
        alpha = self.refresh('alpha')
        beta = self.refresh('beta')
        self.save_plan('alpha', alpha['snapshot']['id'], '甲计划')
        bad_scope = self.command('review', 'demo', '--workline', 'alpha', '--expect-revision', '1',
                                 '--snapshot', str(beta['snapshot']['id']))
        self.assertEqual(bad_scope.returncode, 2, bad_scope.stdout + bad_scope.stderr)
        stale = self.command('review', 'demo', '--workline', 'alpha', '--expect-revision', '0',
                             '--snapshot', str(alpha['snapshot']['id']))
        self.assertEqual(stale.returncode, 3, stale.stdout + stale.stderr)
        self.assertIn('current', json.loads(stale.stdout))

    def test_failed_source_cannot_be_silenced_by_review(self):
        first = self.refresh('alpha')
        saved = self.save_plan('alpha', first['snapshot']['id'], '确认资料')
        self.state.unlink()
        current = self.refresh('alpha')
        self.assertTrue(current['last_attempt']['errors'])
        rejected = self.command('review', 'demo', '--workline', 'alpha',
                                '--expect-revision', '1', '--snapshot', str(current['snapshot']['id']))
        self.assertEqual(rejected.returncode, 2, rejected.stdout + rejected.stderr)
        card = self.value('show', 'demo', '--workline', 'alpha', '--json')
        self.assertEqual(card['handoff'], saved)
        self.assertTrue(any(item['status'] == 'unavailable' for item in card['source_changes']))
        self.assertEqual(self.value('reviews', 'demo', '--workline', 'alpha'), [])

    def test_source_and_material_copy_return_exact_paths_without_project_writes(self):
        self.refresh('alpha')
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.source.iterdir()}
        source = self.command('source', 'demo', 'state', '--workline', 'alpha', '--action', 'copy')
        material = self.command('open', 'demo', 'model-file', '--workline', 'alpha', '--action', 'copy')
        self.assertEqual(source.returncode, 0, source.stderr)
        self.assertEqual(material.returncode, 0, material.stderr)
        self.assertEqual(source.stdout.strip(), str(self.state))
        self.assertEqual(material.stdout.strip(), str(self.model))
        self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.source.iterdir()})

    @unittest.skipUnless(sys.platform == "win32", "Windows API assertion; Linux dispatch covered separately")
    def test_model_default_action_opens_folder_and_explicit_execute_is_rejected(self):
        self.refresh('alpha')
        prefix = ['--app-dir', str(self.app), '--data-dir', str(self.folder / 'data')]
        stream = StringIO()
        with redirect_stdout(stream), redirect_stderr(stream), patch('os.startfile') as start:
            self.assertEqual(main(prefix + ['open', 'demo', 'model-file', '--workline', 'alpha']), 0)
            start.assert_called_once_with(str(self.source))
            start.reset_mock()
            self.assertEqual(main(prefix + ['open', 'demo', 'model-file', '--workline', 'alpha', '--action', 'open']), 2)
            start.assert_not_called()

    def test_bindings_are_scoped_and_unknown_entries_are_rejected(self):
        self.refresh('alpha')
        self.refresh('beta')
        self.value('worklines', 'demo', 'bind', 'state-file', 'model-file', '--workline', 'alpha')
        alpha = self.value('show', 'demo', '--workline', 'alpha', '--json')
        beta = self.value('show', 'demo', '--workline', 'beta', '--json')
        self.assertEqual(alpha['workline']['entry_ids'], ['state-file', 'model-file'])
        self.assertEqual(beta['workline']['entry_ids'], ['model-file'])
        invalid = self.command('worklines', 'demo', 'bind', 'not-registered', '--workline', 'alpha')
        self.assertEqual(invalid.returncode, 2, invalid.stdout + invalid.stderr)
        missing = self.command('show', 'demo', '--workline', 'not-created', '--json')
        self.assertEqual(missing.returncode, 2, missing.stdout + missing.stderr)


if __name__ == '__main__':
    unittest.main()
