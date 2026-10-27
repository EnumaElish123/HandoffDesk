"""Native Tk callback tests; no project files or real open actions are touched."""
import copy
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from handoffdesk.desktop import App


class ConflictError(ValueError):
    def __init__(self, current, proposed):
        super().__init__("version conflict")
        self.current = current
        self.proposed = proposed


class FakeService:
    def __init__(self):
        self.calls = []
        self.release = None
        self.records = []
        self.states = {}
        self.drafts = {}
        self._preferences = {"auto_refresh": False}
        for project in self.list_projects():
            self.states[project["id"]] = {
                "project": project,
                "handoff": None,
                "snapshot": {
                    "id": 11, "observed_at": "2026-10-22T01:00:00+08:00",
                    "facts": ["文件记录：历史任务已完成；实时运行未检查"],
                    "warnings": [],
                    "entries": [{"id": "plan", "label": "中文计划", "type": "file", "target": "D:/只读项目/plan.md"}],
                    "observations": [{"source_id": "saved_status", "status": "ok", "value": {"status": "completed"}, "source_recorded_at": "2026-09-25"}],
                },
                "warnings": [],
            }

    def list_projects(self):
        return [
            {"id": "alpha", "name": "Alpha 工程", "aliases": ["engineering", "工程"]},
            {"id": "beta", "name": "Beta 研究", "aliases": ["research", "研究"]},
            {"id": "rvc", "name": "RVC 音色", "aliases": ["音色"]},
        ]

    def preferences(self):
        return copy.deepcopy(self._preferences)

    def save_preferences(self, patch):
        if "entries" in patch:
            self._preferences["entries"] = dict(self._preferences.get("entries", {}), **copy.deepcopy(patch["entries"]))
        self._preferences.update(copy.deepcopy({k: v for k, v in patch.items() if k != "entries"}))
        return self.preferences()

    def load_draft(self, project_id, event_kind="handoff", workline_id=None):
        return copy.deepcopy(self.drafts.get((project_id, event_kind)))

    def save_draft(self, project_id, payload, expected_draft_revision, workline_id=None):
        key = (project_id, payload.get("event_kind", "handoff"))
        current = self.drafts.get(key) or {}
        if current.get("revision", 0) != expected_draft_revision or (current and current.get("draft_id") != payload.get("draft_id")):
            raise ConflictError(copy.deepcopy(current), payload)
        if current and any(current.get(k) != payload.get(k) for k in ("base_revision", "source_snapshot_id", "event_kind")):
            raise ValueError("Draft basis is immutable")
        saved = dict(copy.deepcopy(payload), revision=expected_draft_revision + 1, updated_at="2026-10-22T01:01:00+08:00")
        self.drafts[key] = saved
        return copy.deepcopy(saved)

    def delete_draft(self, project_id, draft_id, expected_revision, event_kind=None, workline_id=None):
        key = next((k for k,v in self.drafts.items() if k[0] == project_id and v["draft_id"] == draft_id), None)
        current = self.drafts.get(key)
        if current and current["draft_id"] == draft_id and current["revision"] == expected_revision:
            del self.drafts[key]
            return True
        return False

    def show(self, project_id, workline_id=None):
        self.calls.append(("show", project_id))
        result = copy.deepcopy(self.states[project_id])
        result["drafts"] = {kind:self.load_draft(project_id,kind) for kind in ("plan","handoff")}
        return result

    def refresh(self, project_id, workline_id=None):
        self.calls.append(("refresh", project_id))
        if self.release:
            self.release.wait(3)
        if self.states[project_id]["snapshot"] is None:
            self.states[project_id]["snapshot"] = {"id": 20, "entries": [{"id": "fresh", "label": "新读取材料"}], "observations": [], "facts": []}
        else:
            self.states[project_id]["snapshot"]["id"] += 1
        return self.show(project_id)

    def history(self, project_id, workline_id=None):
        return [x for x in self.records if x["project_id"] == project_id]

    def save_handoff(self, project_id, payload, expected_revision, operation_id=None, workline_id=None):
        self.calls.append(("save", project_id, copy.deepcopy(payload), expected_revision, operation_id))
        current = self.states[project_id]["handoff"] or {}
        if expected_revision != current.get("revision", 0):
            raise ConflictError(current, payload)
        result = dict(payload, revision=expected_revision + 1, project_id=project_id, recorded_at="2026-10-22T01:02:00+08:00")
        self.states[project_id]["handoff"] = result
        if result.get("last_done") and result.get("event_kind") != "plan":
            self.states[project_id]["last_completion"] = result
        self.records.append(result)
        return result

    def save_plan(self, project_id, payload, expected_revision, operation_id=None, workline_id=None):
        self.assert_plan = payload.get("last_done") == "" and payload.get("event_kind") == "plan"
        return self.save_handoff(project_id, payload, expected_revision, operation_id)

    def restore_handoff(self, project_id, revision, expected_revision, workline_id=None):
        original = next(x for x in self.records if x["project_id"] == project_id and x["revision"] == revision)
        self.calls.append(("restore", project_id, revision, expected_revision))
        return self.save_handoff(project_id, original, expected_revision)

    def summary(self, project_id, workline_id=None):
        return "项目摘要：" + project_id

    def open_entry(self, project_id, entry_id, action=None, workline_id=None):
        self.calls.append(("open", project_id, entry_id))

    def export(self, project_id, mode="local", workline_id=None):
        self.calls.append(("export", project_id, mode))
        return Path("C:/测试工具/导出.md")

    def focus_options(self, project_id):
        return [{"id": "job-1", "label": "历史训练 job-1", "kind": "train", "voice_name": "音色甲", "status": "completed", "recorded_at": "2026-10-12"},
                {"id": "job-2", "label": "历史换声 job-2", "kind": "convert", "voice_name": "音色乙", "status": "paused", "recorded_at": "2026-10-13"}]

    def set_focus(self, project_id, focus_id):
        self.calls.append(("focus", project_id, focus_id))
        self.states[project_id]["project"]["focus_label"] = focus_id


class DesktopTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest("Native Tk unavailable: " + str(exc))
        self.root.withdraw()
        self.service = FakeService()
        self.app = App(self.service, self.root)
        self.root.update_idletasks()

    def tearDown(self):
        if hasattr(self, "app"):
            if self.app.last_dialog and not self.app.last_dialog.closed:
                self.app.last_dialog._destroy()
            self.root.update()
            self.app.close()

    def pump(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if predicate():
                return
            time.sleep(.01)
        self.fail("Tk callback did not finish")

    def test_initial_cache_has_no_fabricated_handoff_or_automatic_refresh(self):
        self.assertIn("还没有下一步", self.app.next_text.get("1.0", "end"))
        self.assertFalse(any(x[0] == "refresh" for x in self.service.calls))
        self.assertEqual(self.app.source_tree.get_children(), ("0",))

    def test_alias_search_does_not_refresh_or_execute_query(self):
        self.app.search_var.set("研究")
        self.app._search_enter()
        self.assertEqual(self.app.project_id, "beta")
        self.app.search_var.set("$(malicious) & ..")
        self.assertEqual(self.app.visible_projects, [])
        self.assertFalse(any(x[0] in ("refresh", "open") for x in self.service.calls))

    def test_refresh_runs_off_ui_thread_and_does_not_replace_selected_project(self):
        self.service.release = threading.Event()
        self.app.refresh()
        self.assertEqual(str(self.app.refresh_button["state"]), "disabled")
        self.app.select_project("rvc")
        self.service.release.set()
        self.pump(lambda: self.app.busy_count == 0)
        self.assertEqual(self.app.project_id, "rvc")
        self.assertEqual(self.app.current["project"]["id"], "rvc")
        self.assertEqual(str(self.app.refresh_button["state"]), "normal")

    def test_handoff_binds_snapshot_at_open_and_validates_required_fields(self):
        dialog = self.app.create_handoff_dialog()
        self.assertFalse(dialog.save())
        self.assertIn("请填写", dialog.error_var.get())
        self.service.states["alpha"]["snapshot"]["id"] = 99
        self.app.load_current()
        dialog.last_done_text.insert("1.0", "核对计划入口；未运行训练")
        dialog.next_text.insert("1.0", "阅读提案并记录决定")
        self.assertTrue(dialog.save())
        saved = self.service.states["alpha"]["handoff"]
        self.assertEqual(saved["source_snapshot_id"], 11)
        self.assertEqual(saved["origin"], "user_directed")
        self.assertIn("阅读提案", self.app.next_text.get("1.0", "end"))

    def test_conflict_preserves_proposed_text_and_requires_explicit_resubmit(self):
        dialog = self.app.create_handoff_dialog()
        dialog.last_done_text.insert("1.0", "我的实际完成内容")
        dialog.next_text.insert("1.0", "我的下一步")
        self.service.states["alpha"]["handoff"] = {"revision": 1, "last_done": "另一会话完成项", "next_action": "另一会话下一步"}
        self.assertFalse(dialog.save())
        self.assertEqual(dialog.last_done_text.get("1.0", "end-1c"), "我的实际完成内容")
        self.assertIn("另一会话下一步", dialog.conflict_text.get("1.0", "end"))
        self.assertEqual(str(dialog.save_button["state"]), "disabled")
        self.assertTrue(dialog.accept_latest_base())
        self.assertEqual(self.service.states["alpha"]["handoff"]["revision"], 2)

    def test_source_detail_open_material_copy_export(self):
        self.app.source_tree.selection_set("0")
        self.app._show_source()
        self.assertIn("2026-09-25", self.app.source_raw.get("1.0", "end"))
        self.assertIn("记录状态", self.app.source_detail.get("1.0", "end"))
        self.app.open_selected_entry()
        self.assertIn(("open", "alpha", "plan"), self.service.calls)
        self.app.copy_summary()
        self.assertEqual(self.root.clipboard_get(), "项目摘要：alpha")
        with patch("handoffdesk.desktop.messagebox.showinfo"):
            self.app.export("local")
        self.assertIn(("export", "alpha", "local"), self.service.calls)

    def test_failed_source_exposes_previous_success_as_historical(self):
        self.service.states["alpha"]["snapshot"]["observations"] = [{
            "source_id": "saved_status", "status": "error", "error": "无法读取",
            "last_success": {"observed_at": "2026-10-21", "value": {"status": "completed"}},
        }]
        self.app.load_current()
        self.app.source_tree.selection_set("0")
        self.app._show_source()
        text = self.app.source_detail.get("1.0", "end")
        self.assertIn("本次读取失败", text)
        self.assertIn("上次成功读取：2026-10-21", text)
        self.assertIn("已完成", text)

    def test_total_failed_attempt_overrides_sources_but_keeps_card_snapshot(self):
        state = self.service.states["alpha"]
        state["latest_attempt_snapshot"] = {
            "id": 12, "observed_at": "2026-10-22T02:00:00+08:00",
            "observations": [{"source_id": "failed_latest", "status": "error", "error": "文件已移走",
                "last_success": {"observed_at": "2026-10-22T01:00:00+08:00", "value": {"status": "completed"}}}],
        }
        self.app.load_current()
        self.assertEqual(self.app.current["snapshot"]["id"], 11)
        self.assertEqual(self.app.observations[0]["source_id"], "failed_latest")
        self.app.source_tree.selection_set("0")
        self.app._show_source()
        self.assertIn("文件已移走", self.app.source_detail.get("1.0", "end"))
        self.assertIn("上次成功读取：2026-10-22T01:00:00+08:00", self.app.source_detail.get("1.0", "end"))

    def test_total_collector_failure_without_observations_is_visible(self):
        self.service.states["alpha"]["latest_attempt_snapshot"] = {
            "id": 12, "observed_at": "2026-10-22T02:00:00+08:00", "observations": [],
            "error": "无法打开来源目录",
        }
        self.app.load_current()
        self.assertEqual(self.app.source_tree.get_children(), ())
        self.assertIn("最近一次整体读取失败：无法打开来源目录", self.app.source_detail.get("1.0", "end"))
        self.assertIn("2026-10-22T02:00:00+08:00", self.app.source_detail.get("1.0", "end"))

    def test_history_restore_creates_new_revision(self):
        self.service.save_handoff("alpha", {"last_done": "一", "next_action": "二", "origin": "user_directed"}, 0)
        self.service.save_handoff("alpha", {"last_done": "三", "next_action": "四", "origin": "user_directed"}, 1)
        self.app.load_current()
        self.app.history_tree.selection_set("0")
        self.app._show_history()
        with patch("handoffdesk.desktop.messagebox.askyesno", return_value=True):
            self.app.restore_selected()
        self.assertEqual(self.service.states["alpha"]["handoff"]["revision"], 3)
        self.assertEqual(self.service.states["alpha"]["handoff"]["next_action"], "二")

    def test_focus_button_only_for_rvc(self):
        self.assertEqual(self.app.focus_button.grid_info(), {})
        self.app.select_project("rvc")
        self.assertTrue(self.app.focus_button.grid_info())

    def test_plan_preserves_latest_actual_completion(self):
        self.service.save_handoff("alpha", {"last_done": "已完成证据核对", "next_action": "原下一步", "origin": "user_directed"}, 0)
        self.app.load_current()
        dialog = self.app.create_plan_dialog()
        dialog.next_text.delete("1.0", "end")
        dialog.next_text.insert("1.0", "新下一步")
        self.assertTrue(dialog.save())
        self.assertTrue(self.service.assert_plan)
        self.assertEqual(self.service.states["alpha"]["handoff"]["event_kind"], "plan")
        self.assertIn("已完成证据核对", self.app.last_text.get("1.0", "end"))
        self.assertEqual(self.service.load_draft("alpha"), None)

    def test_draft_autosaves_and_restores_original_snapshot_and_base(self):
        dialog = self.app.create_handoff_dialog()
        dialog.last_done_text.insert("1.0", "填写到一半的完成项")
        self.pump(lambda: self.service.load_draft("alpha") is not None, timeout=2)
        self.assertTrue(dialog.close())
        self.service.states["alpha"]["snapshot"]["id"] = 99
        self.service.states["alpha"]["handoff"] = {"revision": 3, "next_action": "后来记录", "event_kind": "plan"}
        self.app.load_current()
        separate = self.app.create_plan_dialog()
        self.assertEqual(separate.event_kind, "plan")
        self.assertEqual(separate.source_snapshot_id, 99)
        separate.close()
        recovered = self.app.create_handoff_dialog()
        self.assertEqual(recovered.event_kind, "handoff")
        self.assertEqual(recovered.source_snapshot_id, 11)
        self.assertEqual(recovered.expected_revision, 0)
        self.assertIn("填写到一半", recovered.last_done_text.get("1.0", "end"))

    def test_unchanged_draft_still_checks_revision_before_publication(self):
        dialog = self.app.create_plan_dialog()
        dialog.next_text.insert("1.0", "原草稿下一步")
        self.assertTrue(dialog.flush_draft(force=True))
        self.assertTrue(dialog.close())
        recovered = self.app.create_plan_dialog()
        current = self.service.load_draft("alpha", "plan")
        updated = dict(current, next_action="另一窗口改好的下一步")
        self.service.save_draft("alpha", updated, current["revision"])
        self.assertFalse(recovered.save())
        self.assertFalse(recovered.closed)
        self.assertIsNone(self.service.states["alpha"]["handoff"])
        self.assertIn("另一窗口改好的下一步", recovered.conflict_text.get("1.0", "end"))

    def test_draft_conflict_stays_open_and_explicit_retry_preserves_basis(self):
        dialog = self.app.create_plan_dialog()
        dialog.next_text.insert("1.0", "我的下一步")
        self.assertTrue(dialog.flush_draft(force=True))
        current = self.service.load_draft("alpha", "plan")
        self.service.save_draft("alpha", dict(current, next_action="另一窗口下一步"), current["revision"])
        dialog.next_text.insert("end", "，补充条件")
        self.assertFalse(dialog.close())
        self.assertFalse(dialog.closed)
        self.assertTrue(dialog.retry_draft())
        saved = self.service.load_draft("alpha", "plan")
        self.assertEqual(saved["base_revision"], 0)
        self.assertEqual(saved["source_snapshot_id"], 11)
        self.assertIn("补充条件", saved["next_action"])

    def test_replaced_draft_is_not_overwritten_or_deleted(self):
        dialog = self.app.create_plan_dialog()
        dialog.next_text.insert("1.0", "旧窗口输入")
        self.assertTrue(dialog.flush_draft(force=True))
        old = self.service.load_draft("alpha", "plan")
        self.service.delete_draft("alpha", old["draft_id"], old["revision"])
        replacement = dict(old, draft_id="other-id", next_action="另一份草稿")
        self.service.save_draft("alpha", replacement, 0)
        self.assertFalse(dialog.save())
        self.assertFalse(dialog.retry_draft())
        with patch("handoffdesk.desktop.messagebox.askyesno", return_value=True):
            self.assertTrue(dialog.discard())
        self.assertEqual(self.service.load_draft("alpha", "plan")["draft_id"], "other-id")

    def test_preferences_remember_project_and_material_without_losing_others(self):
        self.service._preferences["entries"] = {"rvc": "rvc-entry"}
        self.app.preferences_data = self.service.preferences()
        self.service.save_preferences({"entries": {"rvc": "newer-rvc-entry"}})
        self.app.open_selected_entry()
        self.assertEqual(self.service.preferences()["entries"], {"rvc": "newer-rvc-entry", "alpha": "plan"})
        self.app.select_project("beta")
        self.assertEqual(self.service.preferences()["last_project"], "beta")
        self.root.update()
        self.app.close()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.service, self.root)
        self.assertEqual(self.app.project_id, "beta")

    def test_continue_without_cache_refreshes_then_opens_one_entry(self):
        self.service.states["alpha"]["snapshot"] = None
        self.app.load_current()
        self.app.continue_work()
        self.pump(lambda: self.app.busy_count == 0)
        opens = [call for call in self.service.calls if call[0] == "open"]
        self.assertEqual(opens, [("open", "alpha", "fresh")])

    def test_changes_are_human_readable_and_raw_source_is_collapsed(self):
        self.service.states["alpha"]["source_changes"] = [{
            "source_id": "alpha.decision", "label": "decision.json", "status": "changed", "path": "D:/只读项目/decision.json",
            "fields": [{"field": "accepted_option", "before": {"missing": True}, "after": None}],
        }]
        self.service.states["alpha"]["change_comparison"] = "handoff"
        self.app.load_current()
        self.app.changes_tree.selection_set("0")
        self.app._show_change()
        text = self.app.changes_detail.get("1.0", "end")
        self.assertIn("decision.json", text)
        self.assertIn("未提供此字段", text)
        self.assertIn("明确为空", text)
        self.assertIn("当前交接", self.app.change_var.get())
        self.assertFalse(self.app.raw_visible.get())
        self.assertEqual(self.app.raw_frame.grid_info(), {})

    def test_rvc_search_type_filter_and_preselection_exclude_empty_default_id(self):
        self.service.states["rvc"]["project"]["focus_label"] = "job-1"
        self.app.select_project("rvc")
        options = [{"id": "", "label": "未指定", "kind": "", "status": ""}] + self.service.focus_options("rvc")
        self.app._focus_dialog(options)
        selected = self.app.focus_tree.selection()
        self.assertEqual(self.app.focus_filtered[int(selected[0])]["id"], "job-1")
        self.app.focus_query.set("音色乙")
        self.assertEqual([o["id"] for o in self.app.focus_filtered], ["job-2"])
        self.app.focus_query.set("")
        self.app.focus_kind.set("训练")
        self.assertEqual([o["id"] for o in self.app.focus_filtered], ["job-1"])
        self.app.focus_window.destroy()

    def test_action_area_fits_target_window_sizes(self):
        self.root.deiconify()
        for width, height in ((1080, 760), (840, 620)):
            self.root.geometry(f"{width}x{height}")
            for _ in range(4):
                self.root.update()
                time.sleep(.02)
            bottom = self.app.handoff_button.winfo_rooty() + self.app.handoff_button.winfo_height() - self.root.winfo_rooty()
            self.assertLess(bottom, height)
            self.assertGreaterEqual(self.app.next_text.winfo_height(), 36)
            self.assertLessEqual(self.app.continue_button.winfo_rootx() + self.app.continue_button.winfo_width(), self.root.winfo_rootx() + width)


if __name__ == "__main__":
    unittest.main()
