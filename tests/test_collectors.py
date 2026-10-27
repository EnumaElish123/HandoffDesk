import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from handoffdesk.collectors import SourceReadError, collect_project, json_pointer, read_json, _read_stable
from handoffdesk.registry import Registry, RegistryError


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="续接测试 空格 ", dir=Path(__file__).resolve().parent)
        self.root = Path(self.temp.name)
        self.project = {"id": "test", "name": "研究测试", "aliases": ["别名", "ALIAS"],
                        "roots": {"main": str(self.root), "applio": str(self.root)}, "sources": [], "entries": [], "adapters": []}

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def registry(self, defaults=None):
        path = self.root / "registry.json"
        path.write_text(json.dumps({"schema_version": 1, "projects": [self.project], "defaults": defaults or {}}, ensure_ascii=False), encoding="utf-8")
        return Registry(path)

    def source(self, id="test.status", path="state.json", fields=None, **extra):
        source = {"id": id, "root": "main", "path": path, "format": "json", **extra}
        if fields is not None:
            source["fields"] = fields
        self.project["sources"].append(source)
        return source

    def test_aliases_and_unicode_root(self):
        registry = self.registry()
        self.assertEqual(registry.get("alias")["id"], "test")
        self.assertEqual(registry.get("别名")["id"], "test")
        self.assertEqual(registry.get("研究测试")["id"], "test")
        self.assertEqual(registry.resolve("test", "main", "中文 资料.txt"), self.root / "中文 资料.txt")

    def test_reject_escape_and_windows_absolute_or_ads(self):
        registry = self.registry()
        for path in ["../escape", "nested/../../escape", "C:\\Windows\\win.ini", "C:relative", "\\\\server\\share", "/tmp/file", "safe.txt:stream", "\\Windows\\win.ini"]:
            with self.subTest(path=path), self.assertRaises(RegistryError):
                registry.resolve("test", "main", path)

    def test_symlink_escape(self):
        registry = self.registry()
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as elsewhere:
            try:
                (self.root / "outside").symlink_to(elsewhere, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"系统未授予符号链接权限：{exc}")
            with self.assertRaises(RegistryError):
                registry.resolve("test", "main", "outside/file.json")

    def test_resolved_junction_outside_root_is_rejected(self):
        registry = self.registry()
        inside = self.root / "junction" / "file.json"
        outside = self.root.parent / "outside-file.json"
        original_resolve = Path.resolve
        def simulated_resolve(path, *args, **kwargs):
            if path == inside:
                return outside
            return original_resolve(path, *args, **kwargs)
        with patch.object(Path, "resolve", simulated_resolve):
            with self.assertRaises(RegistryError):
                registry.resolve("test", "main", "junction/file.json")

    def test_permission_denied_is_source_error(self):
        self.write("state.json", {"status": "running"})
        self.source(fields={"recorded_status": "/status"})
        registry = self.registry()
        with patch("handoffdesk.collectors._read_stable", side_effect=PermissionError("拒绝读取")):
            result = collect_project(registry, "test")
        self.assertEqual(result["observations"][0]["status"], "error")
        self.assertIn("拒绝读取", result["observations"][0]["error"])

    def test_json_pointer_null_missing_and_escaping(self):
        doc = {"accepted": None, "a/b": {"~field": ["yes"]}}
        self.assertIsNone(json_pointer(doc, "/accepted"))
        self.assertEqual(json_pointer(doc, "/a~1b/~0field/0"), "yes")
        for pointer in ["/missing", "/a~1b/~0field/01", "/a~1b/~0field/-", "a", "/a~2b"]:
            with self.assertRaises(KeyError):
                json_pointer(doc, pointer)

    def test_json_missing_is_error_null_is_valid(self):
        self.write("state.json", {"status": None})
        self.source(fields={"null_value": "/status", "missing": "/missing"})
        result = collect_project(self.registry(), "test")
        obs = result["observations"][0]
        self.assertEqual(obs["status"], "error")
        self.assertIsNone(obs["value"]["null_value"])
        self.assertNotIn("missing", obs["value"])
        self.assertTrue(obs["fingerprint"])
        self.assertIn("missing", obs["error"])

    def test_optional_missing_does_not_fail(self):
        self.write("state.json", {"status": "running", "updated_at": "2020-01-01T00:00:00Z", "pid": 1234})
        self.source(fields={"recorded_status": "/status", "resumed_by": "/resumed_by", "source_recorded_at": "/updated_at"}, optional_fields=["resumed_by"])
        result = collect_project(self.registry(), "test")
        obs = result["observations"][0]
        self.assertEqual(obs["status"], "ok")
        self.assertEqual(obs["source_recorded_at"], "2020-01-01T00:00:00Z")
        self.assertNotEqual(obs["source_recorded_at"], obs["observed_at"])
        self.assertIn("当前运行未检查", result["facts"][0])
        self.assertNotIn("pid", obs["value"])

    def test_half_written_and_oversized_json(self):
        (self.root / "state.json").write_text('{"status":', encoding="utf-8")
        self.source()
        result = collect_project(self.registry(), "test")
        self.assertEqual(result["observations"][0]["status"], "error")
        self.assertIn("JSON 无效", result["observations"][0]["error"])
        self.write("state.json", {"large": "x" * 100})
        result = collect_project(self.registry({"max_json_bytes": 30}), "test")
        self.assertIn("超过读取上限", result["observations"][0]["error"])
        with self.assertRaises(SourceReadError):
            read_json(self.root / "state.json", max_bytes=10)

    def test_log_tail_is_bounded(self):
        (self.root / "worker.log").write_bytes(b"first" + b"x" * 20000 + b"THE END")
        self.source(path="worker.log", format="log")
        obs = collect_project(self.registry({"log_tail_bytes": 32}), "test")["observations"][0]
        self.assertEqual(obs["status"], "ok")
        self.assertEqual(len(obs["value"]["excerpt"]), 32)
        self.assertTrue(obs["value"]["excerpt"].endswith("THE END"))
        self.assertTrue(obs["value"]["truncated"])

    def test_source_bytes_and_mtime_unchanged(self):
        file = self.write("state.json", {"status": "completed"})
        self.source(fields={"recorded_status": "/status"})
        registry = self.registry()
        before = (file.read_bytes(), file.stat().st_mtime_ns)
        collect_project(registry, "test")
        self.assertEqual(before, (file.read_bytes(), file.stat().st_mtime_ns))

    def test_stable_read_retries_and_fails_if_continuously_changed(self):
        file = self.write("state.json", {"ok": 1})
        real_signature = __import__("handoffdesk.collectors", fromlist=["_signature"])._signature
        counter = {"n": 0}
        def unstable_signature(stat):
            counter["n"] += 1
            return (*real_signature(stat), counter["n"])
        with patch("handoffdesk.collectors._signature", side_effect=unstable_signature):
            with self.assertRaisesRegex(SourceReadError, "unstable"):
                _read_stable(file, retries=2)
        self.assertEqual(counter["n"], 6)

    def test_frozen_protocol_and_completed_execution_are_distinct(self):
        self.write("protocol.json", {"status": "FROZEN_BEFORE_CONFIRMATION", "latest_report_execution_summary": {"status": "COMPLETE_REQUIRED_SCHEDULE"}})
        self.source(path="protocol.json", fields={"protocol_status": "/status", "execution_status": "/latest_report_execution_summary/status"})
        result = collect_project(self.registry(), "test")
        self.assertTrue(any("FROZEN_BEFORE_CONFIRMATION" in f for f in result["facts"]))
        self.assertTrue(any("COMPLETE_REQUIRED_SCHEDULE" in f for f in result["facts"]))
        self.assertFalse(any("不一致" in w for w in result["warnings"]))

    def setup_rvc(self):
        self.project["id"] = "rvc"
        self.project["adapters"] = ["rvc_job_chain"]
        self.source(id="rvc.previous_state", path="workflows/jobs/old/state.json", fields={"recorded_status": "/status", "resumed_by": "/resumed_by"})

    def test_rvc_followup_and_missing_target(self):
        self.setup_rvc()
        self.write("workflows/jobs/old/state.json", {"status": "paused", "resumed_by": "new"})
        registry = self.registry()
        result = collect_project(registry, "rvc")
        self.assertTrue(any("续接目标 new 无法核实" in w for w in result["warnings"]))
        self.write("workflows/jobs/new/state.json", {"status": "completed", "updated_at": "2020-01-01T00:00:00Z"})
        self.write("workflows/jobs/new/result.json", {"status": "completed", "outputs": [], "finished_at": "2020-01-01T00:00:00Z"})
        result = collect_project(registry, "rvc")
        self.assertTrue(any("old（paused）→ new（completed）" in f for f in result["facts"]))
        self.assertFalse(any("续接目标" in w for w in result["warnings"]))

    def test_rvc_cycle_and_result_conflict(self):
        self.setup_rvc()
        self.write("workflows/jobs/old/state.json", {"status": "paused", "resumed_by": "new"})
        self.write("workflows/jobs/new/state.json", {"status": "running", "updated_at": "2020-01-01T00:00:00Z", "resumed_by": "old"})
        self.write("workflows/jobs/new/result.json", {"status": "completed", "outputs": [], "finished_at": "2020-01-01T00:00:00Z"})
        result = collect_project(self.registry(), "rvc")
        self.assertTrue(any("存在环" in w for w in result["warnings"]))
        self.assertTrue(any("state=running，result=completed" in w for w in result["warnings"]))

    def test_rvc_output_escape_rejected(self):
        self.project["id"] = "rvc"
        self.write("result.json", {"outputs": ["../outside.pth", "C:\\Windows\\calc.exe", "safe/result.txt"]})
        self.source(id="rvc.result", path="result.json", fields={"outputs": "/outputs"})
        result = collect_project(self.registry(), "rvc")
        self.assertEqual(len(result["entries"]), 1)
        self.assertEqual(result["entries"][0]["target"], str(self.root / "safe" / "result.txt"))
        self.assertEqual(sum("结果入口被拒绝" in w for w in result["warnings"]), 2)

    def test_missing_source_and_invalid_url(self):
        self.source(id="rvc.server_record", fields={"url": "/url"})
        result = collect_project(self.registry(), "test")
        self.assertEqual(result["observations"][0]["status"], "error")
        self.write("state.json", {"url": "file:///C:/Windows/calc.exe"})
        result = collect_project(self.registry(), "test")
        self.assertFalse(result["entries"])
        self.assertTrue(any("URL 无效" in w for w in result["warnings"]))

    def test_git_failure_is_not_clean(self):
        self.project["adapters"] = ["git_readonly"]
        with patch("handoffdesk.collectors.shutil.which", return_value=None), patch.dict(os.environ, {}, clear=True):
            result = collect_project(self.registry(), "test")
        self.assertEqual(result["observations"][0]["status"], "error")
        self.assertFalse(any("clean" in fact for fact in result["facts"]))
        self.assertTrue(any("未找到 Git" in warning for warning in result["warnings"]))

    def test_git_rename_original_name_not_counted(self):
        self.project["adapters"] = ["git_readonly"]
        def fake_run(command, stdout, **kwargs):
            self.assertFalse(kwargs["shell"])
            self.assertEqual(kwargs["env"]["GIT_OPTIONAL_LOCKS"], "0")
            self.assertNotIn("GIT_DIR", kwargs["env"])
            stdout.write(b"# branch.oid abc\x00# branch.head main\x002 R. N... 100644 100644 100644 a b R100 new\x00? fake-old-name\x00? real-untracked\x00")
            return subprocess.CompletedProcess(command, 0, stderr=b"")
        with patch("handoffdesk.collectors.subprocess.run", side_effect=fake_run), patch.dict(os.environ, {"HANDOFFDESK_GIT": "git", "GIT_DIR": "evil"}):
            result = collect_project(self.registry(), "test")
        value = result["observations"][0]["value"]
        self.assertEqual(value["tracked_changes"], 1)
        self.assertEqual(value["untracked_entries"], 1)


if __name__ == "__main__":
    unittest.main()
