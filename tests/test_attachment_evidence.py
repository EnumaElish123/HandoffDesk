"""Attachment diagnostics and version evidence through public record/file APIs."""
import copy
import concurrent.futures
import hashlib
import os
from pathlib import Path
import subprocess
import sqlite3
import unittest

import test_agent_records as fixtures
from handoffdesk.service import Service


class AttachmentEvidenceTests(unittest.TestCase):
    setUp = fixtures.AgentRecordTests.setUp
    tearDown = fixtures.AgentRecordTests.tearDown
    payload = fixtures.AgentRecordTests.payload

    def file(self, receipt, index=0):
        return self.service.get_node_files('alpha', receipt['node_id'])['files'][index]

    def test_missing_second_attachment_identifies_input_and_preserves_atomicity(self):
        payload = self.payload()
        missing = self.attachments / '缺失 第二项.log'
        payload['files'][1]['path'] = str(missing)
        with self.assertRaises(ValueError) as caught:
            self.writer.save(payload)
        self.assertEqual(caught.exception.code, 'attachment_missing')
        self.assertEqual(caught.exception.details['file_index'], 1)
        self.assertEqual(caught.exception.details['input_path'], str(missing))
        self.assertEqual(caught.exception.details['resolved_path'], str(missing.resolve()))
        self.assertIn('files[1]', str(caught.exception))
        self.assertEqual(self.service.history('alpha', workline_id='audit'), [])
        missing.write_text('恢复后的实际文件', encoding='utf-8')
        saved = self.writer.save(payload)
        self.assertEqual(saved['revision'], 1)
        self.assertTrue(self.writer.save(payload)['replayed'])

    def test_same_size_and_timestamp_change_detected_without_changing_retry(self):
        self.report.write_bytes(b'before')
        payload = self.payload()
        saved = self.writer.save(payload)
        original_record = copy.deepcopy(saved['record'])
        first = self.file(saved)
        self.assertEqual(first['version_status'], 'unchanged')
        self.assertEqual(first['recorded_metadata']['sha256'], hashlib.sha256(b'before').hexdigest())
        timestamp = self.report.stat()
        self.report.write_bytes(b'AFTER!')
        os.utime(self.report, ns=(timestamp.st_atime_ns, timestamp.st_mtime_ns))
        changed = self.file(saved)
        self.assertEqual(changed['version_status'], 'changed')
        self.assertEqual(changed['recorded_metadata'], first['recorded_metadata'])
        self.assertNotEqual(changed['current_metadata']['sha256'], first['recorded_metadata']['sha256'])
        retry = self.writer.save(payload)
        self.assertTrue(retry['replayed'])
        self.assertEqual(retry['record'], original_record)
        self.assertEqual(len(self.service.history('alpha', workline_id='audit')), 1)

    def test_historical_manifest_without_baseline_is_unknown(self):
        # Store's public history import seam models a pre-metadata record.
        self.service.store.save_handoff('alpha\x1faudit', {
            'last_done': '历史成果', 'next_action': '阅读历史',
            'file_manifest': [{'path': str(self.report), 'origin': 'manual', 'purpose': '旧附件'}]}, 0)
        file = self.service.get_node_files('alpha', 'record:audit:1')['files'][0]
        self.assertEqual(file['version_status'], 'unknown')
        self.assertIsNone(file['recorded_metadata'])
        self.assertEqual(file['current_metadata']['size'], self.report.stat().st_size)

    def test_missing_and_directory_status_do_not_claim_content_unchanged(self):
        payload = self.payload(files=[{'path': str(self.report), 'purpose': '文件'},
                                      {'path': str(self.attachments), 'purpose': '目录'}])
        saved = self.writer.save(payload)
        self.assertEqual(self.file(saved, 1)['version_status'], 'unknown')
        self.report.unlink()
        file = self.file(saved)
        self.assertEqual(file['version_status'], 'missing')
        self.assertIsNotNone(file['recorded_metadata'])
        self.assertIsNone(file['current_metadata'])

    def test_large_file_has_bounded_metadata_and_explicit_comparison_basis(self):
        self.report.write_bytes(b'x' * (1024 * 1024 + 1))
        saved = self.writer.save(self.payload())
        file = self.file(saved)
        self.assertEqual(file['recorded_metadata']['size'], 1024 * 1024 + 1)
        self.assertNotIn('sha256', file['recorded_metadata'])
        self.assertEqual(file['version_basis'], 'metadata')
        self.assertEqual(file['version_status'], 'unchanged')

    def test_explicit_evidence_copy_preserves_source_and_reuses_identical_content(self):
        saved = self.writer.save(self.payload())
        original_manifest = copy.deepcopy(saved['record']['file_manifest'])
        source_stat = self.report.stat()
        original_bytes = self.report.read_bytes()
        first = self.service.node_files.save_evidence('alpha', saved['node_id'], self.file(saved)['id'])
        target = Path(first['file']['path'])
        self.assertTrue(target.is_relative_to(self.service.data_dir / 'evidence'))
        self.assertEqual(target.read_bytes(), original_bytes)
        self.assertEqual(first['file']['evidence']['source_path'], str(self.report))
        self.assertEqual(first['file']['evidence']['version'], 'current_at_save')
        self.assertFalse(first['reused'])
        second = self.service.node_files.save_evidence('alpha', saved['node_id'], self.file(saved)['id'])
        self.assertTrue(second['reused'])
        self.assertEqual(first['file']['id'], second['file']['id'])
        self.assertEqual(len(self.service.get_node_files('alpha', saved['node_id'])['files']), 3)
        self.assertEqual(self.report.read_bytes(), original_bytes)
        self.assertEqual(self.report.stat().st_mtime_ns, source_stat.st_mtime_ns)
        self.assertEqual(self.service.history('alpha', workline_id='audit')[0]['file_manifest'], original_manifest)
        self.report.write_text('保存副本后的新内容', encoding='utf-8')
        self.assertEqual(target.read_bytes(), original_bytes)

    def test_evidence_copy_rejects_directory_missing_and_oversize_files(self):
        saved = self.writer.save(self.payload(files=[{'path': str(self.attachments), 'purpose': '目录'},
                                                    {'path': str(self.report), 'purpose': '文件'}]))
        items = self.service.get_node_files('alpha', saved['node_id'])['files']
        with self.assertRaisesRegex(ValueError, '普通文件'):
            self.service.node_files.save_evidence('alpha', saved['node_id'], items[0]['id'])
        self.report.write_bytes(b'x' * (10 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, '10 MiB'):
            self.service.node_files.save_evidence('alpha', saved['node_id'], items[1]['id'])
        self.report.unlink()
        with self.assertRaisesRegex(ValueError, '缺失|不可用'):
            self.service.node_files.save_evidence('alpha', saved['node_id'], items[1]['id'])
        self.assertFalse((self.service.data_dir / 'evidence').exists())

    def test_evidence_overlay_failure_cleans_only_the_new_copy(self):
        saved = self.writer.save(self.payload())
        source = self.report.read_bytes()
        with self.service.store.connection(True) as db:
            db.execute("""CREATE TRIGGER reject_evidence BEFORE INSERT ON node_file_state
                          BEGIN SELECT RAISE(ABORT, '证据关联写入失败'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, '证据关联写入失败'):
            self.service.node_files.save_evidence('alpha', saved['node_id'], self.file(saved)['id'])
        evidence = self.service.data_dir / 'evidence'
        self.assertEqual(list(evidence.rglob('*.*')) if evidence.exists() else [], [])
        self.assertEqual(self.report.read_bytes(), source)
        self.assertEqual(len(self.service.get_node_files('alpha', saved['node_id'])['files']), 2)

    def test_evidence_directory_redirect_is_rejected_without_writing_destination(self):
        saved = self.writer.save(self.payload())
        link = self.service.data_dir / 'evidence'
        outside = self.base / '不得写入'
        outside.mkdir()
        if os.name == 'nt':
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)],
                                    capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.addCleanup(lambda: os.rmdir(link) if link.exists() else None)
        else:
            link.symlink_to(outside, target_is_directory=True)
            self.addCleanup(link.unlink)
        with self.assertRaisesRegex(ValueError, '重定向|数据目录'):
            self.service.node_files.save_evidence('alpha', saved['node_id'], self.file(saved)['id'])
        self.assertEqual(list(outside.iterdir()), [])

    def test_manual_record_captures_version_at_save_and_relocation_preserves_baseline(self):
        self.service.add_node_files('alpha', 'current:audit', [self.report], purpose='人工材料')
        self.report.write_text('关联后、记录前的实际内容', encoding='utf-8')
        saved = self.service.save_handoff('alpha', {'last_done': '人工检查完成', 'next_action': '继续'},
                                          0, workline_id='audit')
        files = self.service.get_node_files('alpha', 'record:audit:1')['files']
        original = next(item for item in files if item['path'] == str(self.report))
        self.assertEqual(original['version_status'], 'unchanged')
        self.assertEqual(original['recorded_metadata']['sha256'], hashlib.sha256(self.report.read_bytes()).hexdigest())
        moved = self.attachments / '材料已移动.txt'
        self.report.rename(moved)
        changed = self.service.relocate_node_file('alpha', 'record:audit:1', original['id'], moved)
        item = next(item for item in changed['files'] if item['path'] == str(moved))
        self.assertEqual(item['version_status'], 'unchanged')
        self.assertEqual(item['recorded_metadata'], original['recorded_metadata'])
        self.assertEqual(self.service.history('alpha', workline_id='audit')[0]['file_manifest'], saved['file_manifest'])

    def test_concurrent_evidence_requests_reuse_one_committed_copy(self):
        saved = self.writer.save(self.payload())
        identity = self.file(saved)['id']
        def preserve(_):
            return Service(self.app).node_files.save_evidence('alpha', saved['node_id'], identity)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(preserve, range(2)))
        self.assertEqual(sorted(result['reused'] for result in results), [False, True])
        self.assertEqual(len({result['file']['path'] for result in results}), 1)
        self.assertEqual(len(self.service.get_node_files('alpha', saved['node_id'])['files']), 3)

    def test_altered_evidence_copy_is_not_reused_as_matching_content(self):
        saved = self.writer.save(self.payload())
        identity = self.file(saved)['id']
        first = self.service.node_files.save_evidence('alpha', saved['node_id'], identity)
        Path(first['file']['path']).write_bytes(b'changed evidence')
        second = self.service.node_files.save_evidence('alpha', saved['node_id'], identity)
        self.assertFalse(second['reused'])
        self.assertNotEqual(first['file']['path'], second['file']['path'])
        self.assertEqual(Path(second['file']['path']).read_bytes(), self.report.read_bytes())
        original_copy = next(item for item in self.service.get_node_files('alpha', saved['node_id'])['files']
                             if item['id'] == first['file']['id'])
        self.assertEqual(original_copy['version_status'], 'changed')


if __name__ == '__main__':
    unittest.main()
