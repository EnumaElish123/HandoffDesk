"""User-visible search, safe exports and unambiguous timestamps."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from handoffdesk.agent_records import AgentRecords
from handoffdesk.bootstrap import initialize
from handoffdesk.reader import ReaderModel, format_recorded_at
from handoffdesk.service import Service


class LinuxReaderImprovements(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / 'root'
        self.project.mkdir()
        self.state = self.base / 'state'
        initialize(self.state, project_id='sample', name='独立项目名', root=self.project)
        self.service = Service(self.state)
        self.model = ReaderModel(self.state)
        self.line = self.service.create_workline('sample', '接口超时排查')['id']
        self.file = self.project / '唯一附件名称.csv'
        self.file.write_text('value\n1\n', encoding='utf-8')
        self.record = AgentRecords(self.service).save({
            'schema_version': 1, 'project': 'sample', 'workline': self.line,
            'operation_id': 'seed', 'expected_revision': 0,
            'record': {'title': '完成检查 100%_ready', 'last_done': '数据一致', 'next_action': '再核对'},
            'actor': {'name': 'Agent'}, 'verification': {'status': 'partial', 'summary': '仅核对本地'},
            'files': [{'path': str(self.file), 'purpose': '原始样本用途'}]})

    def test_search_matches_visible_fields_and_literal_punctuation_only(self):
        for query in ('独立项目名', '接口超时', '数据一致', '再核对', '唯一附件', '原始样本', '100%_ready'):
            with self.subTest(query=query):
                self.assertEqual([i['id'] for i in self.model.records(query=query)['items']], [self.record['record_id']])
        for query in ('last_done', 'file_manifest', 'operation_id', str(self.base)):
            with self.subTest(query=query):
                self.assertEqual(self.model.records(query=query)['items'], [])

    def test_search_tracks_current_attachment_overlays_and_removed_task_history(self):
        rid = self.record['record_id']
        item = self.model.detail(rid)['files'][0]
        node = f'record:{self.line}:1'
        self.service.update_node_file('sample', node, item['id'], purpose='新用途覆盖')
        self.assertTrue(self.model.records(query='新用途覆盖')['items'])
        self.assertEqual(self.model.records(query='原始样本用途')['items'], [])
        self.service.remove_node_file('sample', node, item['id'])
        self.assertEqual(self.model.records(query='唯一附件')['items'], [])
        with self.service.store.connection(True) as db:
            db.execute('DELETE FROM worklines WHERE project_id=? AND workline_id=?', ('sample', self.line))
        found = self.model.records(project='sample', workline=self.line)['items']
        self.assertEqual(found[0]['id'], rid)
        self.assertEqual(found[0]['workline_name'], '历史任务')

    def test_export_never_overwrites_an_existing_file_or_symlink(self):
        output = self.base / '中文 导出.md'
        result = self.model.export_file(self.record['record_id'], output)
        content = output.read_text(encoding='utf-8')
        self.assertEqual(result['path'], str(output))
        for text in ('接口超时排查', '原始样本用途', str(self.file), 'partial', '原始时间：', 'UTC'):
            self.assertIn(text, content)
        with self.assertRaisesRegex(ValueError, '已存在'):
            self.model.export_file(self.record['record_id'], output)
        self.assertEqual(output.read_text(encoding='utf-8'), content)
        link = self.base / 'link.md'
        try:
            link.symlink_to(output)
        except OSError:
            return
        with self.assertRaisesRegex(ValueError, '已存在'):
            self.model.export_file(self.record['record_id'], link)
        self.assertEqual(output.read_text(encoding='utf-8'), content)

    def test_fresh_cli_list_returns_empty_without_demo(self):
        state = self.base / 'fresh'
        result = subprocess.run([sys.executable, '-m', 'handoffdesk', '--app-dir', str(state), 'list', '--json'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout), [])
        self.assertFalse((state / 'demo-project').exists())

    def test_named_zone_without_operating_system_database(self):
        import os
        env = dict(os.environ, PYTHONTZPATH='')
        script = "from handoffdesk.reader import format_recorded_at; print(format_recorded_at('2026-10-29T01:11:00Z', 'Asia/Shanghai'))"
        result = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('2026-10-29 09:11', result.stdout)
        self.assertIn('UTC+08:00', result.stdout)

    def test_time_offsets_cross_day_and_dst(self):
        try:
            ZoneInfo('Asia/Shanghai')
            ZoneInfo('America/New_York')
        except ZoneInfoNotFoundError:
            self.skipTest('system IANA timezone database not available')
        self.assertIn('2026-10-30 01:11', format_recorded_at('2026-10-29T17:11:57+00:00', 'Asia/Shanghai'))
        self.assertIn('UTC+08:00', format_recorded_at('2026-10-29T01:11:57+00:00', 'Asia/Shanghai'))
        for instant, offset in [('2026-11-01T05:30:00Z', 'UTC-04:00'), ('2026-11-01T06:30:00Z', 'UTC-05:00')]:
            self.assertIn(offset, format_recorded_at(instant, 'America/New_York'))
        with patch.dict('os.environ', {'HANDOFFDESK_TIMEZONE': 'UTC'}):
            self.assertIn('UTC+00:00', format_recorded_at('2026-10-29T01:11:00+00:00'))
        with self.assertRaisesRegex(ValueError, '时区'):
            format_recorded_at('2026-10-29T01:11:00+00:00', 'Invalid/Zone')
