"""The two readers share compatibility, incremental and bounded-file behavior."""
import concurrent.futures
import json
from pathlib import Path
import tempfile
import unittest

from handoffdesk.agent_records import AgentRecords
from handoffdesk.bootstrap import initialize, add_project
from handoffdesk.reader import ReaderModel
from handoffdesk.service import Service


class ProgressReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.app, self.project = self.base / 'state', self.base / '项目 中文'
        self.project.mkdir()
        self.note = self.project / '长文件名与阅读报告.txt'
        self.note.write_text('报告正文\n', encoding='utf-8')
        initialize(self.app, project_id='example', name='项目 中文', root=self.project)
        self.service = Service(self.app)
        self.model = ReaderModel(self.app)

    def save(self, revision=0, title='本轮检查', workline='default'):
        return AgentRecords(self.service).save({'schema_version': 1, 'project': 'example', 'workline': workline,
            'operation_id': f'{workline}-{revision}', 'expected_revision': revision,
            'record': {'title': title, 'last_done': '确认了正文和文件。', 'next_action': '阅读结果。'},
            'actor': {'name': '测试 Agent'}, 'verification': {'status': 'partial', 'summary': '只验证了文本。'},
            'files': [{'path': str(self.note), 'purpose': '可阅读的检查报告'}]})

    def test_detail_export_and_reading_do_not_refresh_sources(self):
        saved = self.save()
        with self.service.store.connection() as db:
            before = db.execute('SELECT count(*) FROM snapshots').fetchone()[0]
        detail = self.model.detail(saved['record_id'])
        self.assertEqual(detail['files'][0]['name'], self.note.name)
        self.assertFalse(detail['read'])
        text = self.model.export(saved['record_id'])
        for expected in ('测试 Agent', '只验证了文本', str(self.note), '可阅读的检查报告'):
            self.assertIn(expected, text)
        with self.service.store.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM snapshots').fetchone()[0], before)

    def test_live_configuration_and_event_feed(self):
        cursor = self.model.changes()['cursor']
        self.assertFalse(self.model.changes(cursor)['changed'])
        saved = self.save()
        self.assertTrue(self.model.changes(cursor)['changed'])
        cursor = self.model.changes()['cursor']
        second = self.base / 'second'
        second.mkdir()
        add_project(self.app, 'second', name='新项目', root=second)
        self.assertTrue(self.model.changes(cursor)['changed'])
        self.assertEqual(len(self.model.projects()), 2)
        self.assertEqual(self.model.records()['items'][0]['id'], saved['record_id'])

    def test_unread_is_per_record_and_concurrent_union(self):
        records = [self.save(index)['record_id'] for index in range(4)]
        with concurrent.futures.ThreadPoolExecutor() as pool:
            list(pool.map(lambda identity: self.model.ack([identity]), [records[0], records[2]]))
        self.assertEqual({item['id'] for item in self.model.records(unread_only=True)['items']}, {records[1], records[3]})
        self.assertEqual(self.model.projects()[0]['unread'], 2)
        self.model.ack([records[0]])
        self.assertEqual(self.model.projects()[0]['unread'], 2)
        with self.assertRaises(ValueError):
            self.model.ack([records[1], 'example:default:999'])
        self.assertFalse(self.model.detail(records[1])['read'])

    def test_paging_is_stable_when_new_record_arrives(self):
        records = [self.save(index)['record_id'] for index in range(4)]
        page = self.model.records(limit=2)
        self.save(4)
        tail = self.model.records(limit=2, cursor=page['next_cursor'])
        self.assertEqual([item['id'] for item in page['items'] + tail['items']], records[::-1])
        self.assertIsNone(tail['next_cursor'])
        self.assertEqual(len(self.model.records(query='本轮')['items']), 5)
        self.assertEqual(self.model.records(query='%')['items'], [])

    def test_bounded_preview_escapes_control_characters_and_does_not_execute(self):
        saved = self.save()
        file_id = self.model.detail(saved['record_id'])['files'][0]['id']
        self.note.write_text('\x1b]52;c;evil\x07\n' + '很长的正文\n' * 2200, encoding='utf-8')
        preview = self.model.preview(saved['record_id'], file_id)
        self.assertTrue(preview['truncated'])
        self.assertNotIn('\x1b', preview['text'])
        self.assertIn('\\u001b', preview['text'])
        self.assertLessEqual(len(preview['text'].splitlines()), 2000)
        self.note.write_bytes(b'\x00\x01binary')
        self.assertFalse(self.model.preview(saved['record_id'], file_id)['previewable'])

    def test_attachment_redirect_and_unknown_identity_are_rejected(self):
        saved = self.save()
        file_id = self.model.detail(saved['record_id'])['files'][0]['id']
        self.note.unlink()
        other = self.base / 'secret.txt'
        other.write_text('not the recorded file', encoding='utf-8')
        try:
            self.note.symlink_to(other)
        except OSError:
            self.skipTest('symlink privileges unavailable')
        with self.assertRaises(ValueError):
            self.model.preview(saved['record_id'], file_id)
        with self.assertRaises(ValueError):
            self.model.preview(saved['record_id'], 'unknown-file')

    def test_workline_specific_counts_and_filters(self):
        self.service.create_workline('example', '独立任务')
        line = self.service.list_worklines('example')[-1]['id']
        first = self.save()
        second = self.save(workline=line)
        self.model.ack([second['record_id']])
        self.assertEqual(self.model.projects()[0]['unread'], 1)
        self.assertEqual(self.model.records(project='example', workline=line)['items'][0]['id'], second['record_id'])
        self.assertEqual(self.model.records(unread_only=True)['items'][0]['id'], first['record_id'])

    def test_new_reader_has_empty_state_without_a_demo_project(self):
        reader = ReaderModel(self.base / 'empty-state')
        self.assertEqual(reader.projects(), [])
        self.assertEqual(reader.records()['items'], [])
        self.assertFalse((self.base / 'empty-state/demo-project').exists())

    def test_removed_task_label_does_not_hide_immutable_history(self):
        line = self.service.create_workline('example', '历史任务')['id']
        saved = self.save(workline=line)
        with self.service.store.connection(True) as db:
            db.execute('DELETE FROM worklines WHERE project_id=? AND workline_id=?', ('example', line))
        self.assertEqual(self.model.detail(saved['record_id'])['title'], '本轮检查')
        self.model.ack([saved['record_id']])
        self.assertTrue(self.model.detail(saved['record_id'])['read'])


if __name__ == '__main__':
    unittest.main()
