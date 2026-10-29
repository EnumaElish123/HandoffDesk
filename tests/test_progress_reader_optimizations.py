"""Regressions from the Windows handoff/readback trial, through reader APIs."""
from pathlib import Path
import re
import tempfile
import unittest

from handoffdesk.agent_records import AgentRecords
from handoffdesk.bootstrap import initialize
from handoffdesk.reader import ReaderModel
from handoffdesk.service import Service


class ReaderOptimizationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.project = base / '项目 中文'
        self.project.mkdir()
        self.note = self.project / '报告 100%_完成.txt'
        self.note.write_text('现有正文', encoding='utf-8')
        self.app = base / 'state'
        initialize(self.app, project_id='sample', name='中文研究', root=self.project)
        self.service = Service(self.app)
        self.reader = ReaderModel(self.app)

    def save(self, revision=0, title='Windows 修复交接', workline='default'):
        return AgentRecords(self.service).save({
            'schema_version': 1, 'project': 'sample', 'workline': workline,
            'operation_id': f'{workline}-{revision}', 'expected_revision': revision,
            'record': {'title': title, 'last_done': '修复 SQLite 连接。', 'next_action': '复查导出。'},
            'actor': {'name': '试用 Agent'},
            'verification': {'status': 'partial', 'summary': '文本预览已验证。'},
            'files': [{'path': str(self.note), 'purpose': '本轮验证证据'}]})

    def test_search_user_fields_receipt_and_literal_paths_excludes_protocol_keys(self):
        saved = self.save()
        for query in (saved['record_id'], str(self.note), str(self.note).replace('\\', '/'),
                      'sqlite', '中文研究', '本轮验证证据', '100%_完成', 'partial'):
            with self.subTest(query=query):
                self.assertEqual([v['id'] for v in self.reader.records(query=query)['items']], [saved['record_id']])
        for query in ('file_manifest', 'agent_request_hash', 'schema_version', '没有这个词'):
            with self.subTest(query=query):
                self.assertEqual(self.reader.records(query=query)['items'], [])
        explicit = self.save(1, title='正文确实提到 file_manifest')
        self.assertEqual([v['id'] for v in self.reader.records(query='file_manifest')['items']], [explicit['record_id']])

    def test_mark_unread_is_atomic_persistent_and_changes_cursor_even_when_count_is_equal(self):
        first = self.save()['record_id']
        second = self.save(1)['record_id']
        self.reader.ack([first])
        cursor = self.reader.changes()['cursor']
        self.reader.ack([second])
        self.reader.mark_unread([first])
        self.assertTrue(self.reader.changes(cursor)['changed'])
        self.assertFalse(ReaderModel(self.app).detail(first)['read'])
        self.assertTrue(self.reader.detail(second)['read'])
        with self.assertRaises(ValueError):
            self.reader.mark_unread([second, 'sample:default:999'])
        self.assertTrue(self.reader.detail(second)['read'])

    def test_project_worklines_show_record_counts_and_empty_tasks(self):
        line = self.service.create_workline('sample', '同名任务')['id']
        first = self.save()
        lines = {v['id']: v for v in self.reader.projects()[0]['worklines']}
        self.assertEqual(lines['default']['record_count'], 1)
        self.assertEqual(lines['default']['latest_recorded_at'], first['record']['recorded_at'])
        self.assertEqual(lines[line]['record_count'], 0)
        self.assertIsNone(lines[line]['latest_recorded_at'])

    def test_export_names_are_chinese_safe_bounded_and_distinguish_records(self):
        first = self.save(title='CON: 中文/报告? ' + '长' * 95)
        second = self.save(1, title='CON: 中文/报告? ' + '长' * 95)
        names = [self.reader.export_filename(value['record_id']) for value in (first, second)]
        for name in names:
            self.assertIn('中文研究', name)
            self.assertIn('中文', name)
            self.assertTrue(name.endswith('.md'))
            self.assertFalse(re.search(r'[<>:"/\\|?*\x00-\x1f]', name))
            self.assertLessEqual(len(name.encode('utf-16-le')) // 2, 180)
        self.assertNotEqual(*names)
        self.assertEqual(names[0], self.reader.export_filename(first['record_id']))

    def test_search_paging_is_stable_across_sparse_matches_and_new_records(self):
        first = self.save(title='命中较早记录')['record_id']
        self.save(1, title='无关一')
        last = self.save(2, title='命中较新记录')['record_id']
        self.save(3, title='无关二')
        page = self.reader.records(query='命中', limit=1)
        self.assertEqual([v['id'] for v in page['items']], [last])
        self.save(4, title='命中新到记录')
        tail = self.reader.records(query='命中', limit=1, cursor=page['next_cursor'])
        self.assertEqual([v['id'] for v in tail['items']], [first])
        self.assertIsNone(tail['next_cursor'])

    def test_current_evidence_copy_updates_reader_counts_search_feed_and_export(self):
        saved = self.save()
        file_id = self.reader.detail(saved['record_id'])['files'][0]['id']
        cursor = self.reader.changes()['cursor']
        result = self.reader.freeze_file(saved['record_id'], file_id)
        self.assertTrue(result['saved'])
        self.assertEqual(self.reader.records()['items'][0]['file_count'], 2)
        self.assertTrue(self.reader.changes(cursor)['changed'])
        self.assertEqual(self.reader.records(query=result['file']['path'])['items'][0]['id'], saved['record_id'])
        exported = self.reader.export(saved['record_id'])
        self.assertIn('记录者报告', exported)
        self.assertIn(result['frozen_at'], exported)
        self.assertIn('不是原记录时的快照', exported)
        self.note.write_text('后来改动的正文', encoding='utf-8')
        self.assertEqual(self.reader.preview(saved['record_id'], result['file']['id'])['text'], '现有正文')
        self.assertIn('与记录时相比已变化', self.reader.export(saved['record_id']))


if __name__ == '__main__':
    unittest.main()
