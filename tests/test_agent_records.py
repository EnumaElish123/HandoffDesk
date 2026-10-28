"""Agent submissions preserve history, ownership, and explicit file evidence."""
import concurrent.futures
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from handoffdesk.agent_records import AgentRecords
from handoffdesk.service import Service
from handoffdesk.store import ConflictError


class AgentRecordTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='agent-record-', dir=Path(__file__).parent)
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.app, self.project, self.attachments = self.base/'工具', self.base/'原项目', self.base/'外部资料'
        for folder in (self.app/'config', self.project, self.attachments):
            folder.mkdir(parents=True)
        self.source = self.project/'来源.txt'
        self.source.write_text('原项目来源，不应被修改。', encoding='utf-8')
        self.report, self.log = self.attachments/'报告 中文.txt', self.attachments/'run.log'
        self.report.write_text('独立试用结果。', encoding='utf-8')
        self.log.write_text('check passed', encoding='utf-8')
        config = {'schema_version': 1, 'projects': [
            {'id': 'alpha', 'name': '项目甲', 'aliases': ['甲'], 'roots': {'main': str(self.project)},
             'sources': [{'id': 'source', 'path': self.source.name, 'format': 'text'}], 'entries': [],
             'worklines': [{'id': 'audit', 'name': '证据检查'}]}]}
        (self.app/'config/projects.json').write_text(json.dumps(config), encoding='utf-8')
        self.service = Service(self.app)
        self.writer = AgentRecords(self.service)
        self.source_before = self.source.read_bytes(), self.source.stat().st_mtime_ns

    def tearDown(self):
        self.assertEqual(self.source_before, (self.source.read_bytes(), self.source.stat().st_mtime_ns))

    def payload(self, **changes):
        result = {
            'schema_version': 1, 'project': 'alpha', 'workline': 'audit',
            'operation_id': 'run-001', 'expected_revision': 0,
            'record': {'title': '检查材料', 'last_done': '核对了报告与日志。', 'next_action': '查看报告中的待解决问题。'},
            'actor': {'name': 'Codex Agent', 'session_id': 'session-001'},
            'verification': {'status': 'partial', 'summary': '日志检查通过，人工结论尚未复核。'},
            'files': [{'path': str(self.report), 'purpose': '本轮结果说明', 'role': 'report'},
                      {'path': str(self.log), 'purpose': '原始检查日志', 'role': 'log'}],
        }
        result.update(changes)
        return result

    def rows(self):
        with self.service.store.connection() as db:
            names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            return {name: [tuple(row) for row in db.execute(f'SELECT * FROM "{name}" ORDER BY rowid')]
                    for name in names}

    def test_schema_available_without_service_or_database(self):
        schema = AgentRecords.schema()
        self.assertEqual(schema['properties']['schema_version']['const'], 1)
        self.assertFalse(schema['additionalProperties'])
        self.assertIn('workline', schema['required'])
        self.assertIn('operation_id', schema['required'])
        self.assertEqual(schema['properties']['files']['maxItems'], 500)

    def test_submission_is_readable_as_existing_history_node_with_distinct_file_purposes(self):
        result = self.writer.save(self.payload())
        self.assertEqual((result['revision'], result['replayed'], result['node_id']), (1, False, 'record:audit:1'))
        saved = self.service.history('alpha', workline_id='audit')[0]
        self.assertEqual(saved['origin'], 'agent_recorded')
        self.assertEqual(saved['actor']['session_id'], 'session-001')
        self.assertEqual(saved['verification']['status'], 'partial')
        self.assertEqual(saved['current_task'], '检查材料')
        files = self.service.get_node_files('alpha', result['node_id'])['files']
        self.assertEqual([item['purpose'] for item in files], ['本轮结果说明', '原始检查日志'])
        self.assertEqual([item['role'] for item in files], ['report', 'log'])
        self.assertTrue(all(item['exists'] and item['status'] == '可用' for item in files))
        self.assertEqual([item['id'] for item in files], [item['id'] for item in result['files']])
        self.assertEqual(self.service.store.node_file_state('alpha', 'current:audit'), {'additions': [], 'hidden': []})

    def test_validation_does_not_save_or_change_human_selection(self):
        self.service.select_workline('alpha', 'default')
        self.service.save_preferences({'geometry': '1080x760', 'last_project': 'alpha'})
        before = self.rows()
        result = self.writer.validate(self.payload())
        self.assertTrue(result['valid'])
        self.assertEqual(result['file_count'], 2)
        self.assertEqual(before, self.rows())
        self.writer.save(self.payload())
        self.assertEqual(self.service.store.selected_workline('alpha'), 'default')
        self.assertEqual(self.service.store.preferences()['geometry'], '1080x760')
        self.assertEqual(self.service.history('alpha', workline_id='default'), [])

    def test_record_manifest_is_exactly_submission_and_can_be_empty(self):
        self.service.add_node_files('alpha', 'current:audit', [self.source], purpose='上一轮资料')
        result = self.writer.save(self.payload(files=[]))
        self.assertEqual(result['record']['file_manifest'], [])
        self.assertEqual(self.service.get_node_files('alpha', result['node_id'])['files'], [])
        self.assertTrue(self.service.get_node_files('alpha', 'current:audit')['files'])

    def test_same_request_retries_after_move_return_original_receipt(self):
        payload = self.payload()
        first = self.writer.save(payload)
        self.report.rename(self.attachments/'报告已归档.txt')
        retry = self.writer.save(payload)
        self.assertTrue(retry['replayed'])
        self.assertEqual(first['record_id'], retry['record_id'])
        self.assertEqual(first['record'], retry['record'])
        self.assertTrue(self.writer.validate(payload)['replayed'])
        self.assertEqual(len(self.service.history('alpha', workline_id='audit')), 1)
        self.assertFalse(self.service.get_node_files('alpha', first['node_id'])['files'][0]['exists'])

    def test_operation_id_reuse_rejects_changed_file_metadata_or_record(self):
        original = self.payload()
        self.writer.save(original)
        before = self.rows()
        for change in ('purpose', 'path', 'verification', 'title'):
            payload = copy.deepcopy(original)
            if change == 'purpose':
                payload['files'][0]['purpose'] = '不同的用途'
            elif change == 'path':
                payload['files'] = [payload['files'][1]]
            elif change == 'verification':
                payload['verification']['status'] = 'passed'
            else:
                payload['record']['title'] = '另一份记录'
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.writer.save(payload)
            self.assertEqual(self.rows(), before)

    def test_operation_id_cannot_replay_legacy_handoff(self):
        self.service.save_handoff('alpha', {'last_done': '人工原记录', 'next_action': '继续核对'},
                                  0, operation_id='run-001', workline_id='audit')
        before = self.rows()
        with self.assertRaises(ValueError):
            self.writer.save(self.payload())
        with self.assertRaises(ValueError):
            self.writer.validate(self.payload())
        self.assertEqual(before, self.rows())

    def test_version_conflict_preserves_original_record_and_all_links(self):
        self.writer.save(self.payload())
        before = self.rows()
        payload = self.payload(operation_id='run-002')
        with self.assertRaises(ConflictError) as caught:
            self.writer.save(payload)
        self.assertEqual(caught.exception.current['revision'], 1)
        with self.assertRaises(ConflictError):
            self.writer.validate(payload)
        self.assertEqual(before, self.rows())

    def test_bad_attachment_fails_entire_submission_without_partial_rows(self):
        before = self.rows()
        payload = self.payload()
        payload['files'].append({'path': str(self.attachments/'不存在.txt'), 'purpose': '缺失资料'})
        with self.assertRaises(ValueError):
            self.writer.save(payload)
        self.assertEqual(before, self.rows())
        with patch.object(self.writer, '_manifest', side_effect=OSError('文件读取失败')):
            with self.assertRaises(OSError):
                self.writer.save(self.payload())
        self.assertEqual(before, self.rows())

    def test_strict_protocol_rejects_unknown_or_dangerous_fields_without_writing(self):
        before = self.rows()
        cases = []
        for key, value in [('file_manifest', []), ('origin', 'user_directed'), ('schema_version', True),
                           ('expected_revision', True), ('workline', ''), ('files', True)]:
            cases.append(self.payload(**{key: value}))
        for location in ('record', 'actor', 'verification'):
            value = self.payload()
            value[location]['unrecognized'] = 'silently dropping this would mislead the caller'
            cases.append(value)
        value = self.payload()
        value['files'][0]['origin'] = 'source'
        cases.append(value)
        value = self.payload()
        del value['workline']
        cases.append(value)
        value = self.payload()
        value['record']['event_kind'] = 'plan'
        cases.append(value)
        for index, payload in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.writer.save(payload)
            self.assertEqual(before, self.rows())

    def test_duplicate_paths_and_oversized_lists_are_rejected(self):
        payload = self.payload()
        payload['files'].append(dict(payload['files'][0]))
        with self.assertRaises(ValueError):
            self.writer.save(payload)
        payload['files'] = [payload['files'][0]] * 501
        with self.assertRaises(ValueError):
            self.writer.save(payload)
        self.assertEqual(self.service.history('alpha', workline_id='audit'), [])

    def test_snapshot_must_belong_to_explicit_workline(self):
        snapshot = self.service.refresh('alpha', workline_id='default')['snapshot']['id']
        with self.assertRaises(ValueError):
            self.writer.save(self.payload(source_snapshot_id=snapshot))
        audit = self.service.refresh('alpha', workline_id='audit')['snapshot']['id']
        result = self.writer.save(self.payload(source_snapshot_id=audit))
        self.assertEqual(result['record']['source_snapshot_id'], audit)

    def test_plan_records_do_not_invent_completed_work(self):
        payload = self.payload()
        payload['record'] = {'event_kind': 'plan', 'title': '下一阶段计划', 'next_action': '先打开原始日志。'}
        result = self.writer.save(payload)
        self.assertEqual(result['record']['event_kind'], 'plan')
        self.assertEqual(result['record']['last_done'], '')

    def test_concurrent_same_operation_has_one_atomic_receipt(self):
        payload = self.payload()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.writer.save(copy.deepcopy(payload)), range(2)))
        self.assertEqual({item['record_id'] for item in results}, {'alpha:audit:1'})
        self.assertEqual(sorted(item['replayed'] for item in results), [False, True])
        self.assertEqual(len(self.service.history('alpha', workline_id='audit')), 1)

    def test_original_record_bytes_and_database_schema_survive_later_agent_save(self):
        self.service.save_handoff('alpha', {'last_done': '原记录', 'next_action': '原下一步'},
                                  0, operation_id='legacy', workline_id='audit')
        with self.service.store.connection() as db:
            before = tuple(db.execute('SELECT * FROM handoffs').fetchone())
        result = self.writer.save(self.payload(expected_revision=1))
        self.assertEqual(result['revision'], 2)
        with self.service.store.connection() as db:
            self.assertEqual(tuple(db.execute('SELECT * FROM handoffs WHERE revision=1').fetchone()), before)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 5)


if __name__ == '__main__':
    unittest.main()
