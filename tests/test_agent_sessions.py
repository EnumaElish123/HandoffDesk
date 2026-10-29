"""Recovery, transaction boundaries and truthful reporting of agent checkpoints."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from handoffdesk.agent_records import AgentRecords
from handoffdesk.agent_session import AgentSessions, SessionError
from handoffdesk.bootstrap import initialize, add_project
from handoffdesk.registry import Registry
from handoffdesk.service import Service
from handoffdesk.store import Store
from handoffdesk.workspace import Workspace


class AgentSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.app = self.base / 'state'
        self.project = self.base / '项目 中文'
        self.project.mkdir()
        self.file = self.project / 'result.md'
        self.file.write_text('# Result', encoding='utf-8')
        self.api = AgentSessions(self.app)

    def begin(self, **options):
        return self.api.begin(self.project, '完成阅读优化', **options)

    def payload(self, **options):
        return dict({'title': '已完成阶段', 'actual_done': '已实现并保存。', 'next_action': '复核实际效果。',
                     'files': [{'path': 'result.md', 'purpose': '本阶段结果'}]}, **options)

    def rows(self, table):
        with closing(sqlite3.connect(self.app / 'data/cards.sqlite3')) as db:
            return db.execute('SELECT * FROM ' + table).fetchall()

    def test_begin_auto_connect_is_fixed_and_host_restore_is_idempotent(self):
        one = self.begin(host='codex', host_session_id='actual-host-session')
        again = self.begin(host='codex', host_session_id='actual-host-session')
        self.assertEqual(one['session_ref'], again['session_ref'])
        self.assertEqual(one['checkpoint_token'], again['checkpoint_token'])
        service = Service(self.app)
        service.select_workline(one['project'], 'default')
        saved = self.api.checkpoint(one['session_ref'], one['checkpoint_token'], self.payload())
        self.assertEqual(saved['workline'], one['workline'])
        self.assertIsNone(service.store.current_handoff(one['project']))
        self.assertEqual(len(Registry(self.app / 'config/projects.json').projects), 1)
        self.assertEqual(len(self.rows('agent_sessions')), 1)
        self.assertEqual(self.file.read_text(encoding='utf-8'), '# Result')

    def test_plain_question_creates_no_project_and_has_one_recovery_budget(self):
        observed = self.api.observe_turn('codex', 's1', 't1')
        self.assertFalse((self.app / 'config/projects.json').exists())
        repeated = self.api.observe_turn('codex', 's1', 't1')
        self.assertEqual(observed['turn_ref'], repeated['turn_ref'])
        self.api.conclude_turn(observed['turn_ref'], 'no_record', reason='仅解释现有设置。')
        self.assertTrue(self.api.claim_remediation(observed['turn_ref']))
        continuation = self.api.observe_turn('codex', 's1', 't2', resume_of=observed['turn_ref'])
        self.assertFalse(self.api.claim_remediation(continuation['turn_ref']))
        self.assertEqual(len(self.rows('handoffs')), 0)

    def test_unknown_host_does_not_fabricate_actor_session(self):
        bound = self.begin()
        saved = self.api.checkpoint(bound['session_ref'], bound['checkpoint_token'], self.payload())
        self.assertEqual(bound['identity_source'], 'handoffdesk')
        self.assertNotIn('session_id', saved['record']['actor'])
        self.assertEqual(saved['record']['verification']['status'], 'not_run')
        self.assertEqual(saved['file_count'], 1)
        self.assertEqual(Path(saved['files'][0]['path']), self.file)

    def test_replay_after_file_move_keeps_original_receipt_and_rejects_different_content(self):
        bound = self.begin()
        saved = self.api.checkpoint(bound['session_ref'], bound['checkpoint_token'], self.payload())
        self.file.rename(self.project / 'moved.md')
        again = self.api.checkpoint(bound['session_ref'], bound['checkpoint_token'], self.payload())
        self.assertTrue(again['replayed'])
        self.assertIn('已确认先前记录', again['report_text'])
        self.assertEqual(saved['record_id'], again['record_id'])
        self.assertEqual(saved['next_checkpoint_token'], again['next_checkpoint_token'])
        self.assertEqual(len(self.rows('handoffs')), 1)
        self.assertEqual(len(self.rows('progress_events')), 1)
        with self.assertRaises(SessionError) as caught:
            self.api.checkpoint(bound['session_ref'], bound['checkpoint_token'], self.payload(title='different'))
        self.assertEqual(caught.exception.code, 'operation_mismatch')

    def test_record_receipt_event_are_atomic_if_response_construction_fails(self):
        bound = self.begin()
        with patch.object(self.api, '_report', side_effect=RuntimeError('simulated interruption')):
            with self.assertRaises(RuntimeError):
                self.api.checkpoint(bound['session_ref'], bound['checkpoint_token'], self.payload())
        self.assertEqual(len(self.rows('handoffs')), 0)
        self.assertEqual(len(self.rows('progress_events')), 0)
        pending = self.api.status(bound['session_ref'], bound['checkpoint_token'])['checkpoints'][0]
        self.assertEqual(pending['status'], 'pending')
        self.assertIsNotNone(pending['request'])
        saved = self.api.checkpoint(bound['session_ref'], bound['checkpoint_token'], self.payload())
        self.assertEqual(saved['revision'], 1)

    def test_lost_success_response_is_recovered_without_another_record(self):
        bound = self.begin()
        original = Store.commit_checkpoint
        def lost_response(store, *args):
            original(store, *args)
            raise RuntimeError('transport interrupted after commit')
        with patch.object(Store, 'commit_checkpoint', lost_response):
            with self.assertRaises(RuntimeError):
                self.api.checkpoint(bound['session_ref'], bound['checkpoint_token'], self.payload())
        status = self.api.status(bound['session_ref'])
        self.assertEqual(len(status['pending_reports']), 1)
        saved = self.api.checkpoint(bound['session_ref'], bound['checkpoint_token'], self.payload())
        self.assertTrue(saved['replayed'])
        self.assertEqual(len(self.rows('handoffs')), 1)

    def test_parallel_identical_submissions_create_one_record(self):
        bound = self.begin()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.api.checkpoint, bound['session_ref'], bound['checkpoint_token'], self.payload()) for _ in range(2)]
            results = [future.result() for future in futures]
        self.assertEqual({value['record_id'] for value in results}, {results[0]['record_id']})
        self.assertEqual(sorted(value['replayed'] for value in results), [False, True])
        self.assertEqual(len(self.rows('handoffs')), 1)
        self.assertEqual(len(self.rows('agent_checkpoints')), 2)

    def test_conflict_requires_explicit_replacement_and_keeps_proposal(self):
        initialize(self.app, project_id='fixture', root=self.project)
        bound = self.begin(workline='default')
        service = Service(self.app)
        service.store.save_handoff('fixture', {'event_kind': 'handoff', 'current_task': '另一位提交',
                                             'last_done': '现有内容', 'next_action': '审查', 'blockers': []}, 0)
        with self.assertRaises(SessionError) as caught:
            self.api.checkpoint(bound['session_ref'], bound['checkpoint_token'], self.payload())
        self.assertEqual(caught.exception.code, 'revision_conflict')
        status = self.api.resume(bound['session_ref'])
        self.assertEqual(status['checkpoint_token'], bound['checkpoint_token'])
        self.assertEqual(status['checkpoints'][0]['request']['title'], '已完成阶段')
        resumed = self.api.resume(bound['session_ref'], bound['checkpoint_token'], '已阅读并合并另一位的内容。')
        saved = self.api.checkpoint(bound['session_ref'], resumed['checkpoint_token'], self.payload(actual_done='合并后的内容。'))
        self.assertEqual(saved['revision'], 2)
        self.assertEqual(len(service.store.history('fixture')), 2)

    def test_missing_attachment_retains_request_and_can_retry_same_token(self):
        turn = self.api.observe_turn('codex', 's', 't')
        bound = self.begin(turn_ref=turn['turn_ref'])
        self.file.unlink()
        with self.assertRaises(SessionError):
            self.api.checkpoint(bound['session_ref'], bound['checkpoint_token'], self.payload())
        self.assertEqual(len(self.rows('handoffs')), 0)
        state = self.api.turn_status(turn['turn_ref'])
        self.assertEqual(len(state['pending_checkpoints']), 1)
        with self.assertRaises(SessionError):
            self.api.conclude_turn(turn['turn_ref'], 'no_record', reason='无结果')
        self.file.write_text('Recovered', encoding='utf-8')
        saved = self.api.checkpoint(bound['session_ref'], bound['checkpoint_token'], self.payload())
        self.assertEqual(saved['revision'], 1)
        self.assertFalse(self.api.turn_status(turn['turn_ref'])['pending_checkpoints'])

    def test_receipt_reporting_is_distinct_from_save_and_user_read(self):
        turn = self.api.observe_turn('codex', 's', 't')
        bound = self.begin(turn_ref=turn['turn_ref'])
        saved = self.api.checkpoint(bound['session_ref'], bound['checkpoint_token'], self.payload())
        state = self.api.conclude_turn(turn['turn_ref'], 'recorded', [saved['record_id']])
        self.assertEqual(len(state['pending_reports']), 1)
        state = self.api.ack_report(turn['turn_ref'], saved['record_id'], 'agent_acknowledged', '发送了即时汇报。')
        self.assertEqual(state['receipts'][0]['report_status'], 'agent_acknowledged')
        self.assertFalse(state['delivery_verified'])
        state = self.api.ack_report(turn['turn_ref'], saved['record_id'], 'observed_in_final', 'message-id-real')
        self.assertEqual(state['receipts'][0]['report_status'], 'observed_in_final')
        other = self.api.observe_turn('codex', 's', 'other')
        with self.assertRaises(SessionError):
            self.api.ack_report(other['turn_ref'], saved['record_id'], 'agent_acknowledged', 'wrong turn')
        self.assertEqual(len(self.rows('reader_reads')), 0)

    def test_recovery_turn_can_report_original_receipt_without_rewriting_disposition(self):
        turn = self.api.observe_turn('codex', 's', 't')
        bound = self.begin(turn_ref=turn['turn_ref'])
        saved = self.api.checkpoint(bound['session_ref'], bound['checkpoint_token'], self.payload())
        self.api.conclude_turn(turn['turn_ref'], 'interrupted', reason='用户中断')
        resumed = self.api.observe_turn('codex', 's', 't2', resume_of=turn['turn_ref'])
        self.assertEqual(len(resumed['pending_reports']), 1)
        self.api.ack_report(resumed['turn_ref'], saved['record_id'], 'observed_in_final', 'new message')
        concluded = self.api.conclude_turn(resumed['turn_ref'], 'recorded', [saved['record_id']])
        self.assertEqual(concluded['disposition'], 'recorded')
        with self.assertRaises(SessionError):
            self.api.conclude_turn(turn['turn_ref'], 'no_record', reason='改口')

    def test_skill_only_resume_after_interrupt_uses_new_recovery_turn(self):
        bound = self.begin()
        saved = self.api.checkpoint(bound['session_ref'], bound['checkpoint_token'], self.payload())
        self.api.claim_remediation(bound['turn_ref'])
        self.api.conclude_turn(bound['turn_ref'], 'interrupted', reason='中断')
        resumed = self.api.resume(bound['session_ref'])
        self.assertNotEqual(resumed['turn_ref'], bound['turn_ref'])
        self.assertFalse(self.api.claim_remediation(resumed['turn_ref']))
        self.api.ack_report(resumed['turn_ref'], saved['record_id'], 'agent_acknowledged', '恢复后已补报')
        result = self.api.conclude_turn(resumed['turn_ref'], 'recorded', [saved['record_id']])
        self.assertEqual(result['disposition'], 'recorded')
        self.assertEqual(len(self.rows('handoffs')), 1)

    def test_host_task_cannot_silently_rebind_checkout(self):
        bound = self.begin(host='codex', host_session_id='real-host')
        nested = self.project / 'nested'
        nested.mkdir()
        with self.assertRaises(SessionError) as caught:
            self.api.begin(nested, '完成阅读优化', host='codex', host_session_id='real-host')
        self.assertEqual(caught.exception.code, 'session_binding_conflict')
        self.assertEqual(self.api.status(bound['session_ref'])['cwd'], str(self.project))
        self.assertEqual(len(self.rows('agent_sessions')), 1)

    def test_plan_cannot_claim_actual_done(self):
        bound = self.begin()
        with self.assertRaises(ValueError):
            self.api.checkpoint(bound['session_ref'], bound['checkpoint_token'], self.payload(kind='plan'))
        saved = self.api.checkpoint(bound['session_ref'], bound['checkpoint_token'], self.payload(kind='plan', actual_done=''))
        self.assertEqual(saved['record']['event_kind'], 'plan')
        self.assertEqual(saved['record']['last_done'], '')

    def test_v4_migration_preserves_raw_payload_and_consistent_backup(self):
        bound = self.begin()
        saved = self.api.checkpoint(bound['session_ref'], bound['checkpoint_token'], self.payload())
        path = self.app / 'data/cards.sqlite3'
        with closing(sqlite3.connect(path)) as db, db:
            raw = db.execute('SELECT payload FROM handoffs').fetchone()[0]
            db.execute('PRAGMA user_version=4')
        Service(self.app)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 5)
            self.assertEqual(db.execute('SELECT payload FROM handoffs').fetchone()[0], raw)
        backups = list((self.app / 'data/migration-backups').glob('*.sqlite3'))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 4)
            self.assertEqual(db.execute('SELECT payload FROM handoffs').fetchone()[0], raw)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(len(list(backups[0].parent.glob('*-projects.json'))), 1)


class WorkspaceConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.app = self.base / 'state'
        self.first = self.base / 'one'; self.first.mkdir()
        self.second = self.base / 'two'; self.second.mkdir()

    def test_parallel_first_registration_merges_both_projects(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(lambda path: Workspace(self.app).connect(path), [self.first, self.second]))
        registered = Registry(self.app / 'config/projects.json').projects
        self.assertEqual({item['id'] for item in registered}, {item['project']['id'] for item in values})
        self.assertEqual(len(registered), 2)
        self.assertFalse((self.first / '.handoffdesk').exists())

    def test_parallel_same_project_registration_deduplicates(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(lambda _: Workspace(self.app).connect(self.first), range(2)))
        self.assertEqual(values[0]['project']['id'], values[1]['project']['id'])
        self.assertEqual(len(Registry(self.app / 'config/projects.json').projects), 1)

    def test_parallel_manual_add_merges_latest_config(self):
        initialize(self.app)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(add_project, self.app, name, root=path)
                       for name, path in [('one', self.first), ('two', self.second)]]
            [future.result() for future in futures]
        self.assertEqual({item['id'] for item in Registry(self.app / 'config/projects.json').projects}, {'demo', 'one', 'two'})

    def test_git_worktree_uses_common_dir_and_retains_checkout(self):
        def git(*args):
            result = subprocess.run(['git', '-C', str(self.first), *args], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        git('init')
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-m', 'fixture')
        checkout = self.base / 'checkout'
        git('worktree', 'add', '-b', 'fixture-checkout', str(checkout))
        workspace = Workspace(self.app)
        original = workspace.connect(self.first)
        linked = workspace.connect(checkout)
        self.assertEqual(original['project']['id'], linked['project']['id'])
        self.assertEqual(linked['match'], 'git_common_dir')
        self.assertEqual(linked['checkout']['root'], str(checkout))
        self.assertEqual(len(Registry(self.app / 'config/projects.json').projects), 1)

    def test_registered_git_subdirectories_do_not_claim_enclosing_repo_or_worktrees(self):
        def git(*args):
            result = subprocess.run(['git', '-C', str(self.first), *args], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        git('init')
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-m', 'fixture')
        first_child = self.first / 'placeholders' / 'one'
        second_child = self.first / 'placeholders' / 'two'
        first_child.mkdir(parents=True)
        second_child.mkdir()
        initialize(self.app, project_id='first-child', root=first_child)
        add_project(self.app, 'second-child', root=second_child)
        workspace = Workspace(self.app)
        outer = workspace.inspect(self.first)
        self.assertFalse(outer['connected'])
        self.assertEqual(outer['root'], str(self.first))
        self.assertEqual(workspace.inspect(first_child)['project']['id'], 'first-child')
        checkout = self.base / 'checkout'
        git('worktree', 'add', '-b', 'fixture-checkout', str(checkout))
        self.assertFalse(workspace.inspect(checkout)['connected'])
        self.assertEqual(len(Registry(self.app / 'config/projects.json').projects), 2)

    def test_relocation_to_git_subdirectory_drops_enclosing_repo_identity(self):
        result = subprocess.run(['git', '-C', str(self.first), 'init'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        child = self.first / 'component'
        child.mkdir()
        workspace = Workspace(self.app)
        original = workspace.connect(self.second)
        workspace.relocate(original['project']['id'], child)
        self.assertFalse(workspace.inspect(self.first)['connected'])
        self.assertEqual(workspace.inspect(child)['project']['id'], original['project']['id'])

    def test_explicit_relocation_keeps_project_id(self):
        workspace = Workspace(self.app)
        original = workspace.connect(self.first)
        workspace.relocate(original['project']['id'], self.second)
        current = workspace.inspect(self.second)
        self.assertEqual(current['project']['id'], original['project']['id'])
        self.assertFalse(workspace.inspect(self.first)['connected'])


if __name__ == '__main__':
    unittest.main()
