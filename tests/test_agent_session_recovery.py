"""Recovery discovery and compact status through the public agent interfaces."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from handoffdesk.agent_session import AgentSessions, SessionError


class SessionRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.app = self.base / "状态 with space's $"
        self.project = self.base / "项目"
        self.project.mkdir()
        self.api = AgentSessions(self.app)

    def begin(self):
        return self.api.begin(self.project, "修复任务")

    def checkpoint(self, bound, **overrides):
        payload = {"title": "已验证", "actual_done": "真实完成内容。", "next_action": "检查结果。", "files": []}
        payload.update(overrides)
        token = self.api.status(bound["session_ref"])["checkpoint_token"]
        return self.api.checkpoint(bound["session_ref"], token, payload)

    def test_discovery_does_not_register_an_unknown_directory(self):
        value = self.api.sessions(cwd=self.project)
        self.assertEqual(value["sessions"], [])
        self.assertIsNone(value["project"])
        self.assertFalse(self.app.exists())

    def test_same_task_begin_exposes_original_session_without_merging(self):
        first = self.begin()
        saved = self.checkpoint(first)
        second = self.begin()
        self.assertFalse(second["resumed"])
        self.assertNotEqual(first["workline"], second["workline"])
        self.assertEqual(second["recovery_candidates"][0]["session_ref"], first["session_ref"])
        candidates = self.api.sessions(cwd=self.project, task="修复任务")["sessions"]
        self.assertEqual({v["session_ref"] for v in candidates}, {first["session_ref"], second["session_ref"]})
        original = next(v for v in candidates if v["session_ref"] == first["session_ref"])
        self.assertEqual(original["record_count"], 1)
        self.assertEqual(original["latest_revision"], saved["revision"])
        restored = subprocess.run(original["resume_argv"], capture_output=True, encoding="utf-8")
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(json.loads(restored.stdout)["session_ref"], first["session_ref"])
        if os.name == 'nt':
            copied = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', original['resume_command']],
                                    capture_output=True, encoding='utf-8')
            self.assertEqual(copied.returncode, 0, copied.stderr)
            self.assertEqual(json.loads(copied.stdout)['session_ref'], first['session_ref'])
            self.assertIn('汇报', json.loads(copied.stdout)['next_action']['instruction'])
        self.assertEqual(len(self.api.sessions(project=first["project"])["sessions"]), 2)

    def test_discovery_filters_project_and_task_and_rejects_unbounded_limit(self):
        first = self.begin()
        other = self.base / "other"
        other.mkdir()
        other_session = self.api.begin(other, "修复任务")
        self.api.begin(self.project, "不同任务")
        result = self.api.sessions(project=first["project"], task="修复任务")
        self.assertEqual([v["session_ref"] for v in result["sessions"]], [first["session_ref"]])
        self.assertNotIn(other_session["session_ref"], str(result))
        with self.assertRaises(ValueError):
            self.api.sessions(cwd=self.project, limit=101)

    def test_summary_retains_recovery_fields_without_repeating_record_body(self):
        bound = self.begin()
        saved = self.checkpoint(bound, actual_done="大段正文" * 1000)
        full = self.api.status(bound["session_ref"])
        compact = self.api.status(bound["session_ref"], summary=True)
        self.assertIn("history", full)
        self.assertNotIn("history", compact)
        self.assertNotIn("大段正文", json.dumps(compact, ensure_ascii=False))
        self.assertEqual(compact["checkpoint_token"], saved["next_checkpoint_token"])
        self.assertEqual(compact["checkpoint"]["status"], "issued")
        self.assertEqual(compact["latest_revision"], 1)
        self.assertEqual(compact["pending_reports"], [saved["record_id"]])
        self.assertEqual(compact["next_action"]["kind"], "report_pending")
        self.assertLess(len(json.dumps(compact, ensure_ascii=False).encode("utf-8")), 2048)
        self.assertEqual(self.api.resume(bound["session_ref"], summary=True), compact)

    def test_summary_bounds_pending_receipts_and_keeps_the_total(self):
        bound = self.begin()
        for index in range(12):
            self.checkpoint(bound, title=f"阶段 {index}", actual_done="完成内容" * 1000)
        summary = self.api.status(bound["session_ref"], summary=True)
        self.assertEqual(summary["pending_report_count"], 12)
        self.assertEqual(len(summary["pending_reports"]), 5)
        self.assertTrue(summary["pending_reports_more"])
        self.assertEqual(summary["latest_revision"], 12)
        self.assertLess(len(json.dumps(summary, ensure_ascii=False).encode("utf-8")), 3072)

    def test_failed_checkpoint_summary_preserves_retry_action_and_full_proposal(self):
        bound = self.begin()
        payload = {"title": "缺失附件", "actual_done": "已完成检查。", "next_action": "恢复文件", "files": [{"path": "missing.txt", "purpose": "验收"}]}
        with self.assertRaises(SessionError):
            self.api.checkpoint(bound["session_ref"], bound["checkpoint_token"], payload)
        compact = self.api.status(bound["session_ref"], summary=True)
        self.assertEqual(compact["checkpoint"]["status"], "failed")
        self.assertEqual(compact["next_action"]["kind"], "retry_same_payload")
        self.assertEqual(compact["checkpoint"]["error_code"], "attachment_missing")
        full = self.api.status(bound["session_ref"], bound["checkpoint_token"])
        self.assertEqual(full["checkpoints"][0]["request"]["title"], payload["title"])

    def test_conflict_summary_guides_explicit_replacement(self):
        first = self.begin()
        second = self.api.begin(self.project, '另一个任务', workline=first['workline'])
        self.checkpoint(first)
        with self.assertRaises(SessionError) as caught:
            self.checkpoint(second)
        self.assertEqual(caught.exception.code, 'revision_conflict')
        compact = self.api.status(second['session_ref'], summary=True)
        self.assertEqual(compact['checkpoint']['status'], 'conflict')
        self.assertEqual(compact['next_action']['kind'], 'resolve_conflict')
        self.assertIn('--replace-checkpoint', compact['next_action']['instruction'])
        replacement = self.api.resume(second['session_ref'], compact['checkpoint_token'], '已合并现有成果。', summary=True)
        self.assertNotEqual(replacement['checkpoint_token'], compact['checkpoint_token'])
        self.assertEqual(self.checkpoint(second)['revision'], 2)

    def test_missing_attachment_details_keep_original_input_and_durable_failure(self):
        bound = self.begin()
        present = self.project / 'existing.txt'
        present.write_text('现有附件', encoding='utf-8')
        files = [{'path': 'existing.txt', 'purpose': '已存在'},
                 {'path': '缺失 中文.txt', 'purpose': '第二项应定位'}]
        with self.assertRaises(SessionError) as caught:
            self.checkpoint(bound, files=files)
        error = caught.exception
        self.assertEqual(error.code, 'attachment_missing')
        self.assertEqual(error.details['file_index'], 1)
        self.assertEqual(error.details['input_path'], '缺失 中文.txt')
        self.assertEqual(error.details['resolved_path'], str(self.project / '缺失 中文.txt'))
        state = self.api.status(bound['session_ref'])
        self.assertEqual(state['checkpoints'][0]['error']['input_path'], '缺失 中文.txt')
        self.assertFalse(state['history'])
        (self.project / '缺失 中文.txt').write_text('已恢复', encoding='utf-8')
        self.assertEqual(self.checkpoint(bound, files=files)['revision'], 1)

    def test_cli_discovery_and_summary_flags(self):
        bound = self.begin()
        for args in (["sessions", "--cwd", str(self.project)],
                     ["status", "--session", bound["session_ref"], "--summary"],
                     ["resume", "--session", bound["session_ref"], "--summary"]):
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, "-X", "utf8", "-m", "handoffdesk", "--app-dir", str(self.app), "agent", *args], capture_output=True, encoding="utf-8")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(json.loads(result.stdout)["ok"])

    def test_summary_distinguishes_paused_and_interrupted_turns(self):
        paused = self.begin()
        self.api.conclude_turn(paused['turn_ref'], 'paused', reason='暂不记录。')
        self.assertEqual(self.api.status(paused['session_ref'], summary=True)['next_action']['kind'], 'start_new_turn')
        interrupted = self.begin()
        self.api.conclude_turn(interrupted['turn_ref'], 'interrupted', reason='中断后继续。')
        self.assertEqual(self.api.status(interrupted['session_ref'], summary=True)['next_action']['kind'], 'resume_session')
        resumed = self.api.resume(interrupted['session_ref'], summary=True)
        self.assertEqual(resumed['next_action']['kind'], 'ready')
        self.assertNotEqual(resumed['turn_ref'], interrupted['turn_ref'])


if __name__ == "__main__":
    unittest.main()
