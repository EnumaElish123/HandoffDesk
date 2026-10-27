"""A local desktop for returning to work and leaving the next small step."""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone
import queue
import re
import sqlite3
import threading
import tkinter as tk
import uuid
from tkinter import messagebox, ttk

from .presentation import (ORIGINS, CHANGE_LABELS, display_value, field_label,
                           human_time, observation_text, source_label, relative_time, short_material, short_workline, rvc_summary, record_attribution)

from .theme import (BG, WHITE, GREEN, INK, MUTED, BORDER, SIDEBAR,
                    ACCENT_SOFT, WARNING_BG, WARNING_INK, FONT, apply_theme, style_text)
from .motion import MotionController
from .components import SegmentedTabs
from .file_panel import NodeFilesPanel


def _text(parent, height=4, **kwargs):
    widget = tk.Text(parent, height=height, wrap="word", relief="flat", bd=0,
                     padx=12, pady=8, font=(FONT, 10), background=WHITE, foreground=INK)
    style_text(widget)
    widget.configure(**kwargs)
    return widget


def _set_text(widget, value):
    widget.configure(state="normal", highlightthickness=0)
    widget.delete("1.0", "end")
    widget.insert("1.0", str(value or ""))
    widget.configure(state="disabled")


def _lines(value):
    return "\n".join(map(str, value)) if isinstance(value, list) else str(value or "")


def _wheel_direction(event):
    """Tk reports wheel buttons on X11 and a signed delta on Windows."""
    return -1 if getattr(event, 'num', None) == 4 or getattr(event, 'delta', 0) > 0 else 1


class AutoScrollbar(ttk.Scrollbar):
    """Hide scrollbars only when their entire content is already visible."""
    def set(self, low, high):
        self.grid_remove() if getattr(self, "suppressed", False) or (float(low) <= 0 and float(high) >= 1) else self.grid()
        super().set(low, high)


def _center(window, parent, width, height):
    window.update_idletasks()
    width = min(width, max(400, parent.winfo_width() - 32), window.winfo_screenwidth() - 40)
    height = min(height, max(260, parent.winfo_height() - 40), window.winfo_screenheight() - 100)
    x = max(0, parent.winfo_rootx() + (parent.winfo_width() - width) // 2)
    y = max(0, parent.winfo_rooty() + (parent.winfo_height() - height) // 2)
    window.geometry(f"{width}x{height}+{x}+{y}")
    motion = getattr(window, "_resume_motion", None)
    if motion and not getattr(window, "_appearance_shown", False):
        window._appearance_shown = True
        motion.fade_in(window)


def _dialog(parent, title):
    window = tk.Toplevel(parent)
    window.title(title)
    window.transient(parent)
    window.configure(background=WHITE)
    window.columnconfigure(0, weight=1)
    window._resume_motion = getattr(parent.winfo_toplevel(), "_resume_motion", None)
    return window


def _dialog_footer(parent, row):
    footer = ttk.Frame(parent, style="Card.TFrame", padding=(20, 12))
    footer.grid(row=row, column=0, columnspan=2, sticky="ew")
    return footer


def _editor_height(parent, preferred):
    parent.update_idletasks()
    available = parent.winfo_height()
    return min(preferred, max(470, available - 40)) if available > 1 else preferred


class HandoffDialog:
    """An editor bound to one workline, one event kind and its original evidence."""
    def __init__(self, app, event_kind="handoff", imported=None, entry_ids=None):
        self.app = app
        self.project_id = app.project_id
        self.workline_id = app.workline_id
        self.closed = self.saved = self.dirty = False
        self.autosave_id = None
        self.draft_conflict = self.conflicting_revision = None
        self.operation_id = str(uuid.uuid4())
        state = app.current or {}
        prior = state.get("handoff") or {}
        draft = app.service.load_draft(self.project_id, event_kind=event_kind, workline_id=self.workline_id)
        if imported and draft:
            raise ValueError("这条工作线已有同类型草稿。请先继续并保存该草稿，再导入新内容；原草稿仍保留。")
        self.event_kind = event_kind
        self.expected_revision = int((draft or {}).get("base_revision", prior.get("revision", 0)))
        self.draft_base_revision = self.expected_revision
        self.source_snapshot_id = draft.get("source_snapshot_id") if draft else imported.get("source_snapshot_id") if imported is not None else (state.get("snapshot") or {}).get("id")
        self.draft_id = (draft or {}).get("draft_id") or str(uuid.uuid4())
        self.draft_revision = int((draft or {}).get("revision", 0))
        initial = draft or imported or {"current_task": prior.get("current_task", ""), "last_done": "",
            "next_action": prior.get("next_action", ""), "blockers": prior.get("blockers", []),
            "entry_ids": state["bound_entry_ids"] if "bound_entry_ids" in state else prior.get("entry_ids") or (state.get("workline") or {}).get("entry_ids") or []}
        self.origin = initial.get("origin", "user_directed") if (draft or imported) else "user_directed"
        self.entry_ids = list(initial.get("entry_ids") or []) if entry_ids is None else list(entry_ids)
        self.available_entries = list(state.get("entries") or (state.get("snapshot") or {}).get("entries") or [])
        self.window = _dialog(app.root, "")
        title = "设置下一步" if event_kind == "plan" else "留下交接"
        self.window.title(title + " · " + state.get("project", {}).get("name", self.project_id))
        self.window.transient(app.root)
        self.window.configure(bg=WHITE)
        self.window.minsize(620, 470)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)
        viewport = ttk.Frame(self.window, style="Card.TFrame")
        viewport.grid(row=0, column=0, sticky="nsew")
        viewport.columnconfigure(0, weight=1); viewport.rowconfigure(0, weight=1)
        self.content_canvas = tk.Canvas(viewport, background=WHITE, highlightthickness=0, bd=0)
        self.content_canvas.grid(row=0, column=0, sticky="nsew")
        self.content_scroll = AutoScrollbar(viewport, command=self.content_canvas.yview)
        self.content_scroll.grid(row=0, column=1, sticky="ns")
        self.content_canvas.configure(yscrollcommand=self.content_scroll.set)
        frame = self.content_frame = ttk.Frame(self.content_canvas, style="Card.TFrame", padding=(20,20,20,12))
        self.content_window = self.content_canvas.create_window((0,0), window=frame, anchor="nw")
        frame.bind("<Configure>", lambda _e:self.content_canvas.configure(scrollregion=self.content_canvas.bbox("all")))
        self.content_canvas.bind("<Configure>", self._resize_content)
        self.content_canvas.bind("<MouseWheel>", self._wheel)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="留下下一步" if event_kind == "plan" else "收好这次进展", style="Title.TLabel", background=WHITE).grid(row=0, sticky="w")
        workline_name = (state.get("workline") or {}).get("name") or "原有工作线"
        ttk.Label(frame, text="工作线：" + workline_name, style="CardMuted.TLabel", wraplength=630).grid(row=1, sticky="ew", pady=(3, 4))
        status = "已恢复这份草稿，保留原版本与来源依据。" if draft else "输入后自动保存草稿，正常关闭后可继续。"
        if self.origin == "assistant_suggested":
            status = "助手建议 · 待你确认。请核对实际完成、下一步及来源依据。"
        self.draft_status = tk.StringVar(value=status)
        ttk.Label(frame, textvariable=self.draft_status, style="CardMuted.TLabel", wraplength=630).grid(row=2, sticky="ew", pady=(0, 10))
        self.current_task = tk.StringVar(value=initial.get("current_task") or "")
        # Retain the contextual note without requiring users to re-enter the workline.
        self.current_entry = ttk.Entry(frame, textvariable=self.current_task, style='Card.TEntry')
        self.last_label = ttk.Label(frame, text="本次完成 · 写下实际进展", style="Card.TLabel")
        self.last_label.grid(row=3, sticky="w")
        self.last_done_text = _text(frame, height=4, width=45)
        self.last_done_text.insert("1.0", initial.get("last_done") or "")
        self.last_done_text.grid(row=4, column=0, sticky="nsew", pady=(4, 8))
        self.last_scroll = app._scroll(frame, self.last_done_text, 4)
        if event_kind == "plan":
            self.last_scroll.suppressed = True
            self.last_label.grid_remove(); self.last_done_text.grid_remove(); self.last_scroll.grid_remove()
        ttk.Label(frame, text="下次第一步 · 一个具体动作", style="Card.TLabel").grid(row=5, sticky="w")
        self.next_text = _text(frame, height=4, width=45)
        self.next_text.insert("1.0", initial.get("next_action") or "")
        self.next_text.grid(row=6, column=0, sticky="nsew", pady=(4, 8))
        app._scroll(frame, self.next_text, 6)
        self.material_var = tk.StringVar()
        material_row = ttk.Frame(frame, style="Card.TFrame")
        material_row.grid(row=7, sticky="ew", pady=(1, 5))
        material_row.columnconfigure(0, weight=1)
        ttk.Label(material_row, textvariable=self.material_var, style="CardMuted.TLabel", wraplength=490).grid(row=0, column=0, sticky="ew")
        ttk.Button(material_row, text="选择材料", style="Card.Ghost.TButton", command=self.choose_materials).grid(row=0, column=1)
        self._material_label()
        self.extra_open = bool(initial.get("blockers") or initial.get("current_task"))
        self.extra_button = ttk.Button(frame, text="阻塞与补充说明 ▾" if self.extra_open else "阻塞与补充说明 ▸", style="Card.Ghost.TButton", command=self.toggle_extra)
        self.extra_button.grid(row=8, sticky="w", pady=(2, 4))
        self.extra_frame = ttk.Frame(frame, style="Card.TFrame")
        self.extra_frame.grid(row=9, sticky="ew")
        self.extra_frame.columnconfigure(0, weight=1)
        ttk.Label(self.extra_frame, text="阻塞项 · 每行一项", style="CardMuted.TLabel").grid(row=0, sticky="w")
        self.blockers_text = _text(self.extra_frame, height=4, width=45)
        self.blockers_text.insert("1.0", _lines(initial.get("blockers")))
        self.blockers_text.grid(row=1, column=0, sticky="ew", pady=(3, 5))
        app._scroll(self.extra_frame, self.blockers_text, 1)
        ttk.Label(self.extra_frame, text="本次主题补充 · 选填", style="CardMuted.TLabel").grid(row=2, sticky="w")
        self.note_entry = ttk.Entry(self.extra_frame, textvariable=self.current_task, style='Card.TEntry')
        self.note_entry.grid(row=3, sticky="ew", pady=(2, 5))
        if not self.extra_open: self.extra_frame.grid_remove()
        self.error_var = tk.StringVar()
        self.conflict_text = _text(frame, height=3, width=45, background=WARNING_BG)
        self.conflict_text.grid(row=11, sticky="ew", pady=4); self.conflict_text.grid_remove()
        conflict_row = ttk.Frame(frame, style="Card.TFrame"); conflict_row.grid(row=12, sticky="ew")
        self.retry_button = ttk.Button(conflict_row, text="按最新交接版本提交以上内容", command=self.accept_latest_base)
        self.draft_retry_button = ttk.Button(conflict_row, text="用我的输入更新这份草稿", command=self.retry_draft)
        self.copy_button = ttk.Button(conflict_row, text="复制当前输入", command=self.copy_input)
        self.footer_frame = ttk.Frame(self.window, style="Card.TFrame", padding=(20,10,20,14))
        self.footer_frame.grid(row=1, column=0, sticky="ew"); self.footer_frame.columnconfigure(0, weight=1)
        self.error_label = ttk.Label(self.footer_frame, textvariable=self.error_var, foreground=WARNING_INK, style="CardMuted.TLabel", wraplength=630)
        self.error_label.grid(row=0, column=0, sticky="ew", pady=(0,8)); self.error_label.grid_remove()
        self.error_var.trace_add("write", lambda *_a:self.error_label.grid() if self.error_var.get() else self.error_label.grid_remove())
        row = ttk.Frame(self.footer_frame, style="Card.TFrame"); row.grid(row=1, column=0, sticky="ew")
        ttk.Button(row, text="放弃草稿", style="Danger.TButton", command=self.discard).pack(side="left")
        ttk.Button(row, text="存草稿并关闭", style="Card.Ghost.TButton", command=self.close).pack(side="right", padx=(8, 0))
        self.save_button = ttk.Button(row, text="确认并保存" if self.origin == "assistant_suggested" else "保存下一步" if event_kind == "plan" else "保存交接", command=self.save, style="Primary.TButton")
        self.save_button.pack(side="right")
        for widget in (self.last_done_text, self.next_text, self.blockers_text):
            widget.edit_modified(False); widget.bind("<<Modified>>", self._modified)
        self.current_task.trace_add("write", self._changed)
        self._bind_scrolling(frame)
        self.window.bind("<Escape>", lambda _event: self.close())
        self.window.bind("<Control-Return>", lambda _event: self.save())
        self._last_draft = self._serialized_draft() if draft else None
        self._initial_draft = self._serialized_draft()
        _center(self.window, app.root, 720, _editor_height(app.root, 650 if event_kind == "handoff" or self.extra_open else 510))
        (self.next_text if event_kind == "plan" else self.last_done_text).focus_set()
        self.window.grab_set()
        if imported is not None:
            self.dirty = True
            self.flush_draft(force=True)

    def _resize_content(self, event):
        self.content_canvas.itemconfigure(self.content_window, width=event.width)
        pending = list(self.content_frame.winfo_children())
        while pending:
            widget = pending.pop()
            if isinstance(widget, ttk.Label) and int(widget.cget("wraplength") or 0):
                width = max(240, event.width - (160 if widget.master.master is self.content_frame else 44))
                if int(widget.cget("wraplength")) != width:widget.configure(wraplength=width)
            pending.extend(widget.winfo_children())
        if hasattr(self,"error_label"):self.error_label.configure(wraplength=max(240,event.width-40))

    def _bind_scrolling(self, widget):
        widget.bind("<MouseWheel>", self._wheel)
        widget.bind("<Button-4>", self._wheel)
        widget.bind("<Button-5>", self._wheel)
        if isinstance(widget, (tk.Text, ttk.Entry, ttk.Button)):
            widget.bind("<FocusIn>", lambda _e,w=widget:self._reveal(w), add="+")
        for child in widget.winfo_children():self._bind_scrolling(child)

    def _wheel(self, event):
        direction = _wheel_direction(event)
        if isinstance(event.widget, tk.Text):
            low, high = event.widget.yview()
            if (direction < 0 and low > 0) or (direction > 0 and high < 1):return
        self.content_canvas.yview_scroll(direction, "units")
        return "break"

    def _reveal(self, widget):
        if self.closed or not widget.winfo_exists():return
        self.window.update_idletasks()
        top = widget.winfo_rooty() - self.content_frame.winfo_rooty()
        bottom = top + widget.winfo_height()
        view_top = self.content_canvas.canvasy(0)
        height = self.content_canvas.winfo_height()
        total = max(1,self.content_frame.winfo_height())
        if top < view_top + 8:self.content_canvas.yview_moveto(max(0,top-8)/total)
        elif bottom > view_top + height - 8:self.content_canvas.yview_moveto(max(0,bottom-height+8)/total)

    def _material_label(self):
        labels = [short_material(e) for e in self.available_entries if e["id"] in self.entry_ids]
        self.material_var.set("工作材料：" + (" / ".join(labels) if labels else "尚未绑定材料，可选择 1–3 份"))

    def choose_materials(self):
        self.app.material_picker(self.available_entries, self.entry_ids, self._accept_materials, self.window)

    def _accept_materials(self, ids):
        self.entry_ids = list(ids); self._material_label(); self._changed()

    def toggle_extra(self):
        self.extra_open = not self.extra_open
        self.extra_frame.grid() if self.extra_open else self.extra_frame.grid_remove()
        self.extra_button.configure(text="阻塞与补充说明 ▾" if self.extra_open else "阻塞与补充说明 ▸")
        if self.extra_open:
            _center(self.window, self.app.root, max(680, self.window.winfo_width()), _editor_height(self.app.root, 650))
            self._reveal(self.blockers_text)

    def payload(self):
        return {"current_task": self.current_task.get().strip(),
                "last_done": "" if self.event_kind == "plan" else self.last_done_text.get("1.0", "end-1c").strip(),
                "next_action": self.next_text.get("1.0", "end-1c").strip(),
                "blockers": [x.strip() for x in self.blockers_text.get("1.0", "end-1c").splitlines() if x.strip()],
                "origin": self.origin, "event_kind": self.event_kind, "entry_ids": self.entry_ids,
                "source_snapshot_id": self.source_snapshot_id}

    def draft_payload(self):
        payload = self.payload()
        payload.update(draft_id=self.draft_id, base_revision=self.draft_base_revision)
        return payload

    def _serialized_draft(self):
        return json.dumps(self.draft_payload(), ensure_ascii=False, sort_keys=True)

    def _modified(self, event):
        if event.widget.edit_modified():
            event.widget.edit_modified(False)
            self._changed()

    def _changed(self, *_args):
        if self.closed:
            return
        self.dirty = True
        if self.autosave_id:
            self.window.after_cancel(self.autosave_id)
        self.draft_status.set("正在编辑 · 稍后自动保存草稿")
        self.autosave_id = self.window.after(700, self.flush_draft)

    def flush_draft(self, force=False):
        if self.closed:
            return True
        if self.autosave_id:
            self.window.after_cancel(self.autosave_id)
            self.autosave_id = None
        serialized = self._serialized_draft()
        # Read the widgets as well: a close can precede delivery of <<Modified>>.
        if not force and (serialized == self._last_draft or (not self.dirty and serialized == self._initial_draft)):
            return True
        try:
            saved = self.app.service.save_draft(self.project_id, self.draft_payload(), self.draft_revision, workline_id=self.workline_id)
        except Exception as exc:
            if hasattr(exc, "current"):
                self.draft_conflict = getattr(exc, "current") or {}
                self.error_var.set("草稿已由另一个窗口更新。你的输入保留在这里；先比较，再决定如何保存。")
                _set_text(self.conflict_text, "现有草稿的下一步：\n" + str(self.draft_conflict.get("next_action") or "未填写"))
                self.conflict_text.grid()
                if self.draft_conflict.get("draft_id") == self.draft_id:
                    self.draft_retry_button.pack(side="left", pady=4)
                else:
                    self.error_var.set("另一份草稿已取代原草稿。请复制当前输入留存，或明确放弃本窗口输入；另一份草稿会保留。")
                self.copy_button.pack(side="left", padx=8, pady=4)
            else:
                self.error_var.set("草稿未保存：" + str(exc))
            self.draft_status.set("草稿尚未保存 · 当前输入保留在窗口中")
            return False
        self.draft_revision = int(saved["revision"])
        self._last_draft = serialized
        self.dirty = False
        self.draft_conflict = None
        self.draft_retry_button.pack_forget()
        self.draft_status.set(("助手建议 · 待确认 · " if self.origin == "assistant_suggested" else "") + "草稿已保存 · " + human_time(saved.get("updated_at")))
        self.app.refresh_overviews()
        return True

    def retry_draft(self):
        if not self.draft_conflict or self.draft_conflict.get("draft_id") != self.draft_id:
            return False
        self.draft_revision = int(self.draft_conflict.get("revision", 0))
        result = self.flush_draft(force=True)
        if result:
            self.error_var.set("")
            self.conflict_text.grid_remove()
            self.copy_button.pack_forget()
        return result

    def copy_input(self):
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(json.dumps(self.draft_payload(), ensure_ascii=False, indent=2))
        self.draft_status.set("当前输入已复制，可粘贴到文件或协作聊天中留存。")

    def save(self):
        payload = self.payload()
        if not payload["next_action"] or (self.event_kind != "plan" and not payload["last_done"]):
            self.error_var.set("请填写下一步。" if self.event_kind == "plan" else "请填写本次完成和下一步。")
            return False
        # Publication must CAS-check even unchanged restored drafts: a second
        # editor may have updated this draft without publishing a new event.
        if not self.flush_draft(force=True):
            return False
        try:
            method = self.app.service.save_plan if self.event_kind == "plan" else self.app.service.save_handoff
            method(self.project_id, payload, self.expected_revision, operation_id=self.operation_id, workline_id=self.workline_id)
        except Exception as exc:
            current = getattr(exc, "current", None)
            if isinstance(current, dict):
                self.conflicting_revision = int(current.get("revision", 0))
                self.error_var.set("其他会话已保存新记录。你的文本和草稿已保留；比较后可以明确提交新版本。")
                _set_text(self.conflict_text, f"现有版本 {self.conflicting_revision}\n本次完成：{current.get('last_done', '')}\n下一步：{current.get('next_action', '')}")
                self.conflict_text.grid()
                self.retry_button.pack(side="left", pady=4)
                self.save_button.configure(state="disabled")
            else:
                self.error_var.set(str(exc))
            return False
        cleanup_note = ""
        try:
            deleted = self.app.service.delete_draft(self.project_id, self.draft_id, self.draft_revision, event_kind=self.event_kind, workline_id=self.workline_id)
            if deleted is False:
                cleanup_note = "另一个窗口的草稿仍保留。"
        except Exception:
            cleanup_note = "草稿已被其他窗口更新，仍保留。"
        self.saved = True
        self._destroy()
        if (self.app.project_id, self.app.workline_id) == (self.project_id, self.workline_id):
            self.app.load_current()
        self.app.status_var.set(("下一步已保存。" if self.event_kind == "plan" else "交接已保存。") + cleanup_note)
        return True

    def accept_latest_base(self):
        if self.conflicting_revision is None:
            return False
        self.expected_revision = self.conflicting_revision
        # draft_base_revision intentionally stays the original judgment baseline.
        self.operation_id = str(uuid.uuid4())
        self.save_button.configure(state="normal")
        return self.save()

    def discard(self):
        if not messagebox.askyesno("放弃当前草稿", "放弃本窗口未提交的输入？匹配的草稿会删除；已保存交接会保留。", parent=self.window):
            return False
        try:
            self.app.service.delete_draft(self.project_id, self.draft_id, self.draft_revision, event_kind=self.event_kind, workline_id=self.workline_id)
        except Exception as exc:
            self.error_var.set("未能删除草稿，当前输入仍保留：" + str(exc))
            return False
        self._destroy()
        self.app.status_var.set("已放弃本窗口的草稿输入。")
        return True

    def close(self):
        if not self.flush_draft():
            return False
        self._destroy()
        return True

    def _destroy(self):
        if self.closed:
            return
        self.closed = True
        if self.autosave_id:
            self.window.after_cancel(self.autosave_id)
            self.autosave_id = None
        self.window.destroy()
        if not self.app.closed:
            self.app.load_current()


class App:
    def __init__(self, service, root=None):
        self.service = service
        self.root = root or tk.Tk()
        self.root.title("HandoffDesk")
        try:
            self.app_icon = tk.PhotoImage(file=str(Path(__file__).parent / "assets" / "app.png"))
            self.root.iconphoto(True, self.app_icon)
        except (tk.TclError, OSError):
            self.app_icon = None
        self.root.minsize(840, 620)
        self.root.configure(background=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.closed = False
        self.project_id = None
        self.workline_id = None
        self.auto_pending = None
        self.current = None
        self.last_dialog = None
        self.jobs = queue.Queue()
        self.busy_count = 0
        self._external_db = None
        self._external_poll_id = None
        self._external_version = None
        self._seen_record_revisions = {}
        self._latest_record_revisions = {}
        self.external_progress_pending = False
        self.projects = list(service.list_projects())
        self.visible_projects = list(self.projects)
        self.preferences_data = service.preferences() or {}
        geometry = str(self.preferences_data.get("geometry") or "1080x760")
        self.root.geometry(geometry if re.fullmatch(r"\d{3,4}x\d{3,4}(?:[+-]\d+){0,2}", geometry) else "1080x760")
        self.status_var = tk.StringVar(value="本地保存交接 · 只读接入项目")
        self._styles()
        self.motion = MotionController(self.root, reduced=bool(self.preferences_data.get("reduced_motion", False)))
        self.root._resume_motion = self.motion
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)
        self._build_sidebar()
        self.body = ttk.Frame(self.root, padding=(24, 20, 24, 12))
        self.body.grid(row=0, column=1, sticky="nsew")
        self.body.columnconfigure(0, weight=1)
        self.body.rowconfigure(2, weight=1)
        self._build_header()
        self._build_body()
        self.status_bar = ttk.Frame(self.body)
        self.status_bar.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self.status_bar.columnconfigure(1, weight=1)
        self.status_dot = tk.Canvas(self.status_bar,width=12,height=16,bg=BG,highlightthickness=0)
        self.status_dot.grid(row=0,column=0,padx=(0,6))
        self.status_dot.create_oval(3,5,9,11,fill=MUTED,outline='',tags='dot')
        self.status_label = ttk.Label(self.status_bar, textvariable=self.status_var, style="Status.TLabel", wraplength=800)
        self.status_label.grid(row=0, column=1, sticky="ew")
        self.external_update_button = ttk.Button(self.status_bar, text="有新进度，点击更新",
            style="Ghost.TButton", command=self.reload_saved_progress)
        self.external_update_button.grid(row=0, column=2, sticky="e", padx=(8, 0))
        self.external_update_button.grid_remove()
        self.status_var.trace_add("write", self._status_changed)
        self.poll_id = self.root.after(80, self._poll)
        self.root.bind("<Configure>", self._resize)
        last = self.preferences_data.get("last_project")
        if self.projects:
            self.select_project(last if any(p["id"] == last for p in self.projects) else self.projects[0]["id"])
        self._start_external_watch()

    def _record_revisions(self):
        return {row[0]: row[1] for row in self._external_db.execute(
            'SELECT project_id, MAX(revision) FROM handoffs GROUP BY project_id')}

    def _start_external_watch(self):
        if self.closed:
            return
        self._external_poll_id = None
        path = getattr(getattr(self.service, 'store', None), 'path', None)
        if path is None:
            return
        try:
            self._external_db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro',
                                                uri=True, timeout=.05)
            self._external_version = self._external_db.execute('PRAGMA data_version').fetchone()[0]
            self._latest_record_revisions = self._record_revisions()
            displayed = dict(self._seen_record_revisions)
            self._seen_record_revisions = dict(self._latest_record_revisions)
            self._seen_record_revisions.update(displayed)
            self._show_external_update()
        except (OSError, sqlite3.Error):
            if self._external_db is not None:
                self._external_db.close()
            self._external_db = None
            self._external_poll_id = self.root.after(1500, self._start_external_watch)
            return
        self._external_poll_id = self.root.after(1500, self._poll_external_progress)

    def _show_external_update(self):
        self.external_progress_pending = any(revision > self._seen_record_revisions.get(scope, 0)
            for scope, revision in self._latest_record_revisions.items())
        if self.external_progress_pending:
            self.external_update_button.grid()
        else:
            self.external_update_button.grid_remove()
        self.status_label.configure(wraplength=max(160, self.body.winfo_width() -
            (250 if self.external_progress_pending else 70)))

    def _check_external_progress(self):
        if self.closed or self._external_db is None:
            return
        try:
            version = self._external_db.execute('PRAGMA data_version').fetchone()[0]
            if version == self._external_version:
                return
            revisions = self._record_revisions()
        except sqlite3.Error:
            # A short busy period must not interrupt the current reading/editing.
            return
        self._external_version = version
        self._latest_record_revisions = revisions
        self._show_external_update()

    def _poll_external_progress(self):
        self._external_poll_id = None
        if self.closed:
            return
        self._check_external_progress()
        self._external_poll_id = self.root.after(1500, self._poll_external_progress)

    def reload_saved_progress(self):
        """Read saved progress without refreshing sources or disturbing editors."""
        if self.project_id is None:
            return
        old_y = self.card_canvas.yview()
        try:
            # Capture notification state before rendering. A later concurrent
            # commit remains observable by the next data_version check.
            acknowledged = self._record_revisions() if self._external_db is not None else {}
            changed_scopes = [scope for scope, revision in acknowledged.items()
                              if revision > self._seen_record_revisions.get(scope, 0)]
            changed_labels = []
            for scope in changed_scopes[:3]:
                project_id, _, workline_id = scope.partition('\x1f')
                project = next((p for p in self.projects if p['id'] == project_id), {})
                line = next((item for item in self.service.list_worklines(project_id)
                             if item['id'] == (workline_id or 'default')), {})
                changed_labels.append(str(project.get('name') or project_id) + ' / ' +
                                      str(line.get('name') or workline_id or '原有工作线'))
            self._render(self.service.show(self.project_id, workline_id=self.workline_id))
            self.root.update_idletasks()
            if old_y:
                self.card_canvas.yview_moveto(old_y[0])
            self._seen_record_revisions.update(acknowledged)
            self._show_external_update()
            if changed_labels:
                suffix = f'等 {len(changed_scopes)} 条工作线' if len(changed_scopes) > 3 else ''
                self.status_var.set('新进度来自：' + '、'.join(changed_labels) + suffix + '。查看位置和草稿已保留。')
            else:
                self.status_var.set('已更新本地进度；当前项目、工作线和编辑草稿已保留。')
        except Exception as exc:
            self.status_var.set('读取新进度失败：' + str(exc))

    def _styles(self):
        self.style = apply_theme(self.root)

    def _status_changed(self, *_args):
        if self.closed:return
        text = self.status_var.get()
        initial = WARNING_INK if any(word in text for word in ('失败','未完成','无法')) else GREEN
        start = tuple(int(initial[i:i+2],16) for i in (1,3,5))
        end = tuple(int(MUTED[i:i+2],16) for i in (1,3,5))
        def update(progress):
            color = '#'+''.join(f'{round(a+(b-a)*progress):02x}' for a,b in zip(start,end))
            self.status_dot.itemconfigure('dot',fill=color)
        self.motion.run('status-feedback',update,duration=420)

    def _motion_changed(self):
        reduced = bool(self.reduce_motion_var.get())
        if not self._save_preferences({'reduced_motion':reduced}):
            self.reduce_motion_var.set(self.motion.reduced)
            return
        self.motion.set_reduced(reduced)
        self._busy_visual()
        self.status_var.set('已减少动态效果。' if reduced else '已启用轻量动态效果。')

    def _busy_visual(self):
        self.refresh_progress.stop()
        if not self.busy_count:
            self.refresh_progress.grid_remove()
            return
        self.refresh_progress.grid()
        if self.motion.reduced:
            self.refresh_progress.configure(mode='determinate',value=50)
        else:
            self.refresh_progress.configure(mode='indeterminate')
            self.refresh_progress.start(24)

    def _build_sidebar(self):
        self.sidebar = tk.Frame(self.root, bg=SIDEBAR, width=180)
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.sidebar.grid_propagate(False)
        self.sidebar.columnconfigure(0, weight=1)
        self.brand_icon = self.app_icon.subsample(4, 4) if self.app_icon else None
        tk.Label(self.sidebar, text=" HandoffDesk", image=self.brand_icon, compound="left", bg=SIDEBAR, fg=INK, font=(FONT, 14, "bold")).grid(sticky="w", padx=14, pady=(22, 4))
        tk.Label(self.sidebar, text="回到工作，从容开始", bg=SIDEBAR, fg=MUTED, font=(FONT, 9)).grid(sticky="w", padx=18, pady=(0, 18))
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(self.sidebar, textvariable=self.search_var, width=17, style='Sidebar.TEntry')
        self.search_entry.grid(sticky="ew", padx=14)
        self.search_entry.bind("<Return>", self._search_enter)
        tk.Label(self.sidebar, text="搜索项目", bg=SIDEBAR, fg=MUTED, font=(FONT, 9)).grid(sticky="w", padx=16, pady=(3, 9))
        self.project_buttons = {}
        self.project_rows = {}
        self.overviews = {}
        for index, project in enumerate(self.projects):
            name = project["name"]
            button = ttk.Button(self.sidebar, text=name, width=1, style="Project.TButton",
                takefocus=True, cursor="hand2", command=lambda pid=project["id"]: self.select_project(pid))
            button.grid(row=4+index, sticky="ew", padx=10, pady=4)
            self.project_buttons[project["id"]] = button
            self.project_rows[project["id"]] = 4+index
        self.no_results = tk.Label(self.sidebar, text="没有匹配的项目", bg=SIDEBAR, fg=MUTED, font=(FONT, 9))
        self.no_results.grid(row=5+len(self.projects), sticky="w", padx=15)
        self.no_results.grid_remove()
        self.sidebar.rowconfigure(7+len(self.projects), weight=1)
        tk.Label(self.sidebar, text="本机保存 · 项目只读", bg=SIDEBAR, fg=MUTED, font=(FONT, 9)).grid(row=8+len(self.projects), sticky="sw", padx=16, pady=18)
        self.project_combo = ttk.Combobox(self.sidebar, state="readonly")
        self.project_combo.bind("<<ComboboxSelected>>", self._project_selected)
        self.search_var.trace_add("write", self._filter_projects)
        self._update_project_values()

    def _build_header(self):
        header = ttk.Frame(self.body)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        header.columnconfigure(0, weight=1)
        self.project_title = ttk.Label(header, style="Title.TLabel", wraplength=600)
        self.project_title.grid(row=0, column=0, sticky="w")
        self.refresh_button = ttk.Button(header, text="刷新材料", command=self.refresh, style="Ghost.TButton")
        self.refresh_button.grid(row=0, column=1, sticky="e")
        self.meta_var = tk.StringVar()
        self.meta_label = ttk.Label(header, textvariable=self.meta_var, style="Muted.TLabel")
        self.meta_label.grid(row=1, column=0, sticky="ew", pady=(3, 5))
        self.refresh_progress = ttk.Progressbar(header,length=80,mode='indeterminate')
        self.refresh_progress.grid(row=1,column=1,sticky='e',padx=8)
        self.refresh_progress.grid_remove()
        scope = ttk.Frame(header)
        scope.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(3, 1))
        scope.columnconfigure(1, weight=1)
        ttk.Label(scope, text="工作线", style="Muted.TLabel").grid(row=0, column=0, padx=(0, 7))
        self.workline_combo = ttk.Combobox(scope, state="readonly", width=22)
        self.workline_combo.grid(row=0, column=1, sticky="ew")
        self.workline_combo.bind("<<ComboboxSelected>>", self._workline_selected)
        ttk.Button(scope, text="新建", command=self.create_workline, style="Ghost.TButton").grid(row=0, column=2, padx=(6, 0))
        self.focus_button = ttk.Button(scope, text="选择任务", command=self.choose_focus, style="Ghost.TButton")
        self.focus_button.grid(row=0, column=3, padx=(6, 0))

    def _build_body(self):
        self.style.layout('Pages.TNotebook.Tab', [])
        self.style.configure('Pages.TNotebook',tabmargins=(0,0,0,0))
        self.notebook = ttk.Notebook(self.body, style='Pages.TNotebook', takefocus=False)
        self.notebook.grid(row=2, column=0, sticky="nsew")
        card, sources, changes, history, files = [ttk.Frame(self.notebook, style="Surface.TFrame", padding=(16, 16)) for _ in range(5)]
        for frame, title in ((card,"工作起点"),(sources,"来源"),(changes,"变化"),(history,"交接历史"),(files,"节点文件")):
            self.notebook.add(frame, text=title)
        self.tabs = SegmentedTabs(self.body,self.notebook,self.motion,("工作起点","来源","变化","交接历史","节点文件"))
        self.tabs.grid(row=1,column=0,sticky='ew',pady=(7,12))
        self.card_tab,self.source_tab,self.changes_tab = card,sources,changes
        self.files_tab = files
        self.history_tab = history
        self._files_scope = None
        card.columnconfigure(0, weight=1); card.rowconfigure(0, weight=1)
        self.card_canvas = tk.Canvas(card, bg=WHITE, highlightthickness=0, bd=0)
        self.card_canvas.grid(row=0,column=0,sticky="nsew")
        self.card_scroll = AutoScrollbar(card, command=self.card_canvas.yview)
        self.card_scroll.grid(row=0,column=1,sticky="ns")
        self.card_canvas.configure(yscrollcommand=self.card_scroll.set)
        content = self.card_content = ttk.Frame(self.card_canvas, style="Card.TFrame")
        content.columnconfigure(0, weight=1)
        self.card_content_id = self.card_canvas.create_window((0,0),window=content,anchor="nw")
        content.bind("<Configure>", lambda _e:self.card_canvas.configure(scrollregion=self.card_canvas.bbox("all")))
        self.card_canvas.bind("<Configure>",lambda e:self.card_canvas.itemconfigure(self.card_content_id,width=e.width))
        self.card_canvas.bind("<MouseWheel>",self._card_wheel)
        self.card_canvas.bind("<Button-4>",self._card_wheel)
        self.card_canvas.bind("<Button-5>",self._card_wheel)
        self.guide_frame=ttk.Frame(content,style="Card.TFrame",padding=(0,2))
        self.guide_frame.grid(row=0,sticky="ew",pady=(0,7));self.guide_frame.columnconfigure(0,weight=1)
        self.guide_label=ttk.Label(self.guide_frame,text="选择工作线  →  查看材料  →  写下第一步",style="CardMuted.TLabel",wraplength=700)
        self.guide_label.grid(sticky="ew")
        self.task_var=tk.StringVar();self.task_label=ttk.Label(content,textvariable=self.task_var,style="CardMuted.TLabel",wraplength=700)
        self.task_label.grid(row=1,sticky="ew",pady=(0,5))
        self.draft_bar=ttk.Frame(content,style="Card.TFrame")
        self.draft_bar.grid(row=2,sticky="ew",pady=(0,6))
        self.draft_buttons={}
        for kind,label in (("plan","继续下一步草稿"),("handoff","继续交接草稿")):
            button=ttk.Button(self.draft_bar,text=label,style="Card.Ghost.TButton",command=lambda k=kind:self.create_handoff_dialog(k))
            button.pack(side="left",padx=(0,7));self.draft_buttons[kind]=button
        self.next_frame=tk.Frame(content,bg=WHITE,highlightthickness=0)
        self.next_frame.grid(row=3,sticky="ew",pady=(0,7));self.next_frame.columnconfigure(1,weight=1)
        tk.Frame(self.next_frame,bg=GREEN,width=2).grid(row=0,column=0,rowspan=2,sticky="ns",padx=(0,12),pady=7)
        tk.Label(self.next_frame,text="下一步",bg=WHITE,fg=INK,font=(FONT,11,"bold"),padx=0,pady=6).grid(row=0,column=1,sticky="w")
        self.next_text=_text(self.next_frame,height=2,width=28,background=WHITE,padx=0,pady=3,highlightthickness=0)
        self.next_text.grid(row=1,column=1,sticky="ew",pady=(0,8));self._scroll(self.next_frame,self.next_text,1,column=2)
        self.last_frame=ttk.Frame(content,style="Card.TFrame",padding=(0,5))
        self.last_frame.grid(row=4,sticky="ew",pady=(0,7));self.last_frame.columnconfigure(0,weight=1)
        self.completion_label=ttk.Label(self.last_frame,text="最近完成",style="CardMuted.TLabel")
        self.completion_label.grid(sticky="w")
        self.last_text=_text(self.last_frame,height=1,width=25,padx=0,pady=3,highlightthickness=0)
        self.last_text.grid(row=1,column=0,sticky="ew");self._scroll(self.last_frame,self.last_text,1)
        self.blocker_frame=ttk.Frame(content,style="Card.TFrame",padding=(0,6))
        self.blocker_frame.grid(row=5,sticky="ew",pady=(0,5));self.blocker_frame.columnconfigure(0,weight=1)
        self.blocker_var=tk.StringVar();self.blocker_label=ttk.Label(self.blocker_frame,textvariable=self.blocker_var,style="CardSection.TLabel",foreground=WARNING_INK)
        self.blocker_label.grid(row=0,column=0,sticky="w")
        self.blocker_toggle=ttk.Button(self.blocker_frame,text="展开全部",style="Card.Ghost.TButton",command=self.toggle_blockers)
        self.blocker_toggle.grid(row=0,column=1,sticky="e")
        self.blocker_text=_text(self.blocker_frame,height=3,width=30,padx=2,pady=3,background=WARNING_BG,foreground=WARNING_INK,highlightthickness=0)
        self.blocker_text.grid(row=1,column=0,sticky="ew");self._scroll(self.blocker_frame,self.blocker_text,1)
        self.blockers_expanded=False
        self.alert_button=ttk.Button(content,text="",style="Alert.TButton",command=lambda:self.notebook.select(self.source_tab))
        self.alert_button.grid(row=6,sticky="ew",pady=(0,6));self.alert_button.grid_remove()
        notices=ttk.Frame(content,style="Card.TFrame");notices.grid(row=7,sticky="ew",pady=(0,7))
        self.change_shortcut=ttk.Button(notices,text="查看变化",style="Card.Ghost.TButton",command=lambda:self.notebook.select(self.changes_tab))
        self.change_shortcut.pack(side="left")
        self.fact_button=ttk.Button(notices,text="来源记录",style="Card.Ghost.TButton",command=lambda:self.notebook.select(self.source_tab))
        self.fact_button.pack(side="left",padx=(7,0))
        self.warning_text=_text(content,height=2)  # Stable inspection surface; detailed warnings live in Sources.
        self.facts_text=_text(content,height=2)
        material=ttk.Frame(content,style="Card.TFrame");material.grid(row=8,sticky="ew");material.columnconfigure(0,weight=1)
        material_heading=ttk.Frame(material,style="Card.TFrame")
        material_heading.grid(row=0,sticky="ew",pady=(2,4))
        ttk.Label(material_heading,text="工作材料",style="CardSection.TLabel").pack(side="left")
        self.latest_files_button = ttk.Button(material_heading,text="节点文件",style="Card.Ghost.TButton",command=self.show_latest_files)
        self.latest_files_button.pack(side="right")
        self.material_tree=self._tree(material,[("name","材料",340),("action","操作",120)],3)
        self.material_tree.grid(row=1,column=0,sticky="ew");self._scroll(material,self.material_tree,1)
        self.material_tree.bind("<<TreeviewSelect>>",self._material_selected)
        self.material_tree.bind("<Double-1>",lambda _e:self.open_selected_entry())
        material_actions=ttk.Frame(material,style="Card.TFrame");material_actions.grid(row=2,column=0,sticky="ew",pady=(4,2))
        self.open_button=ttk.Button(material_actions,text="打开所选材料",command=self.open_selected_entry)
        self.open_button.pack(side="left")
        ttk.Button(material_actions,text="复制路径",style="Card.Ghost.TButton",command=lambda:self.open_selected_entry("copy")).pack(side="left",padx=6)
        ttk.Button(material_actions,text="绑定到下一步",style="Card.Ghost.TButton",command=self.bind_materials).pack(side="left")
        self.entry_hint=ttk.Label(material,text="",style="CardMuted.TLabel",wraplength=700)
        self.entry_hint.grid(row=3,column=0,sticky="ew",pady=(3,4))
        # Hidden compatibility selector; the visible surface is one material list.
        self.entry_combo=ttk.Combobox(material,state="readonly")
        self.entry_combo.bind("<<ComboboxSelected>>",self._entry_selected)
        self.quick_entries=ttk.Frame(material)
        actions=ttk.Frame(card,style="Card.TFrame");actions.grid(row=1,column=0,columnspan=2,sticky="ew",pady=(12,0))
        self.continue_button=ttk.Button(actions,text="打开工作材料",command=self.continue_work,style="Primary.TButton")
        self.continue_button.pack(side="left")
        self.plan_button=ttk.Button(actions,text="设置下一步",command=self.create_plan_dialog)
        self.plan_button.pack(side="left",padx=6)
        self.handoff_button=ttk.Button(actions,text="留下交接",command=self.create_handoff_dialog)
        self.handoff_button.pack(side="left")
        extra=ttk.Menubutton(actions,text="更多",style="Ghost.TMenubutton")
        menu=tk.Menu(extra,tearoff=False,font=(FONT,10),background=WHITE,foreground=INK,activebackground=ACCENT_SOFT,activeforeground=GREEN,bd=0)
        menu.add_command(label="导入助手总结为草稿…",command=self.import_summary)
        menu.add_command(label="复制本机摘要",command=self.copy_summary)
        menu.add_command(label="导出本机交接",command=self.export)
        self.auto_refresh_var=tk.BooleanVar(value=self.preferences_data.get("auto_refresh",True))
        menu.add_checkbutton(label="进入项目时后台刷新",variable=self.auto_refresh_var,command=self._auto_refresh_changed)
        menu.add_separator()
        self.reduce_motion_var=tk.BooleanVar(value=self.motion.reduced)
        menu.add_checkbutton(label="减少动态效果",variable=self.reduce_motion_var,command=self._motion_changed)
        self.more_menu = menu
        extra.configure(menu=menu);extra.pack(side="right")
        self._build_sources(sources);self._build_changes(changes);self._build_history(history)
        self.files_panel = NodeFilesPanel(self, files)
        self.notebook.bind("<<NotebookTabChanged>>", self._files_tab_changed, add="+")
        self._bind_card_wheel(content)

    def _files_tab_changed(self, _event=None):
        if self.notebook.select() == str(self.files_tab) and hasattr(self.service, "list_file_nodes"):
            self.files_panel.refresh(self.project_id)

    def show_node_files(self, node_id=None):
        self.files_panel.refresh(self.project_id, preferred_node_id=node_id or "current:" + str(self.workline_id or "default"))
        self.notebook.select(self.files_tab)

    def show_latest_files(self):
        record = (self.current or {}).get('handoff') or {}
        if record.get('origin') == 'agent_recorded' and record.get('revision'):
            self.show_node_files(f"record:{self.workline_id}:{record['revision']}")
        else:
            self.show_node_files()

    def _history_node_files(self):
        selected = self.history_tree.selection()
        if not selected:
            self.status_var.set("请先选择一条交接或下一步记录。")
            return
        record = self.history_records[int(selected[0])]
        self.show_node_files(f"record:{self.workline_id}:{record['revision']}")

    def _card_wheel(self,event):
        self.card_canvas.yview_scroll(_wheel_direction(event),"units")
        return "break"

    def _bind_card_wheel(self,widget):
        if not isinstance(widget,(tk.Text,ttk.Treeview)):
            widget.bind("<MouseWheel>",self._card_wheel)
            widget.bind("<Button-4>",self._card_wheel)
            widget.bind("<Button-5>",self._card_wheel)
        for child in widget.winfo_children(): self._bind_card_wheel(child)

    def toggle_blockers(self):
        self.blockers_expanded=not self.blockers_expanded
        count=len(((self.current or {}).get("handoff") or {}).get("blockers") or [])
        self.blocker_text.configure(height=min(12,count) if self.blockers_expanded else min(3,count))
        self.blocker_toggle.configure(text="收起" if self.blockers_expanded else "展开全部")

    @staticmethod
    def _scroll(parent, text, row, column=1):
        scroll = AutoScrollbar(parent, command=text.yview)
        scroll.grid(row=row, column=column, sticky="ns")
        text.configure(yscrollcommand=scroll.set)
        return scroll

    def _tree(self, parent, columns, height=5):
        tree = ttk.Treeview(parent, columns=[x[0] for x in columns], show="headings", height=height, selectmode="browse")
        for key, label, width in columns:
            tree.heading(key, text=label, anchor="w")
            tree.column(key, width=width, minwidth=65, stretch=True, anchor="w")
        return tree

    def _build_sources(self, frame):
        frame.columnconfigure(0, weight=1)
        # Both panes share available height.  A fixed three-row source list
        # otherwise squeezes the details to zero with taller CJK fonts.
        frame.rowconfigure(1, weight=1)
        frame.rowconfigure(2, weight=3)
        ttk.Label(frame, text="选择来源查看依据 · 保存状态不代表当前运行状态", style="CardMuted.TLabel").grid(sticky="w")
        self.source_tree = self._tree(frame, [("source", "来源", 250), ("status", "读取结果", 120), ("time", "记录 / 文件时间", 170)], 1)
        self.source_tree.grid(row=1, column=0, sticky="nsew", pady=(6, 4))
        self._scroll(frame, self.source_tree, 1)
        self.source_tree.bind("<<TreeviewSelect>>", self._show_source)
        self.source_detail = _text(frame, height=2, width=30)
        self.source_detail.grid(row=2, column=0, sticky="nsew")
        self.source_detail_scroll = self._scroll(frame, self.source_detail, 2)
        self.raw_visible = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="查看原始记录", style="Card.TCheckbutton", variable=self.raw_visible, command=self._toggle_raw).grid(row=3, column=0, sticky="w", pady=6)
        self.raw_frame = ttk.Frame(frame, style="Card.TFrame")
        self.raw_frame.grid(row=2, column=0, columnspan=2, sticky="nsew")
        self.raw_frame.columnconfigure(0, weight=1)
        self.raw_frame.rowconfigure(0, weight=1)
        self.source_raw = _text(self.raw_frame, height=2, width=30, font=(FONT, 9))
        self.source_raw.grid(sticky="nsew")
        self._scroll(self.raw_frame, self.source_raw, 0)
        self.raw_frame.grid_remove()
        source_actions=ttk.Frame(frame, style="Card.TFrame")
        source_actions.grid(row=5,column=0,sticky="ew",pady=4)
        for label,action in (("打开原文","open"),("打开所在文件夹","folder"),("复制路径","copy")):
            ttk.Button(source_actions,text=label,style="Card.Ghost.TButton",command=lambda a=action:self.source_action(a)).pack(side="left",padx=(0,6))
        self.source_tree.bind("<Double-1>",lambda _e:self.source_action("open"))

    def _build_changes(self, frame):
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        self.change_var = tk.StringVar()
        self.change_label = ttk.Label(frame, textvariable=self.change_var, style="CardMuted.TLabel", wraplength=700)
        self.change_label.grid(sticky="ew", pady=(0, 8))
        self.changes_tree = self._tree(frame, [("source", "来源", 280), ("status", "变化", 150)], 3)
        self.changes_tree.grid(row=1, column=0, sticky="ew")
        self._scroll(frame, self.changes_tree, 1)
        self.changes_tree.bind("<<TreeviewSelect>>", self._show_change)
        self.changes_detail = _text(frame, height=10, width=30)
        self.changes_detail.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        self._scroll(frame, self.changes_detail, 2)
        source_actions=ttk.Frame(frame, style="Card.TFrame");source_actions.grid(row=3,column=0,sticky="ew",pady=6)
        for label,action in (("打开原文","open"),("打开所在文件夹","folder"),("复制路径","copy")):
            ttk.Button(source_actions,text=label,style="Card.Ghost.TButton",command=lambda a=action:self.source_action(a,changed=True)).pack(side="left",padx=(0,6))
        review_actions=ttk.Frame(frame, style="Card.TFrame");review_actions.grid(row=4,column=0,sticky="ew",pady=(2,5))
        self.review_button=ttk.Button(review_actions,text="已复核，沿用下一步",command=self.acknowledge_changes,style="Primary.TButton")
        self.review_button.pack(side="left")
        ttk.Button(review_actions,text="根据变化调整下一步",command=self.create_plan_dialog).pack(side="left",padx=7)
        self.review_var=tk.StringVar()
        ttk.Label(frame,textvariable=self.review_var,style="CardMuted.TLabel",wraplength=650).grid(row=5,column=0,sticky="ew")

    def _build_history(self, frame):
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        self.history_tree = self._tree(frame, [("revision", "版本", 60), ("kind", "记录类型", 120), ("time", "时间", 180), ("origin", "来源", 120)], 4)
        self.history_tree.grid(sticky="ew")
        self._scroll(frame, self.history_tree, 0)
        self.history_tree.bind("<<TreeviewSelect>>", self._show_history)
        self.history_detail = _text(frame, height=10, width=30)
        self.history_detail.grid(row=1, column=0, sticky="nsew", pady=8)
        self._scroll(frame, self.history_detail, 1)
        footer = ttk.Frame(frame, style="Card.TFrame")
        footer.grid(row=2, column=0, sticky="ew")
        self.history_files_button = ttk.Button(footer, text="查看节点文件", command=self._history_node_files)
        self.history_files_button.pack(side="left")
        ttk.Button(footer, text="恢复所选内容为新版本", command=self.restore_selected).pack(side="right")

    def _resize(self, event):
        if event.widget != self.root: return
        width=max(330,event.width-265)
        self.project_title.configure(wraplength=max(280,width-95))
        for label in (self.meta_label,self.task_label,self.entry_hint,self.status_label,self.change_label,self.guide_label):
            label.configure(wraplength=width)
        if self.external_progress_pending:
            self.status_label.configure(wraplength=max(160,width-180))

    def refresh_overviews(self):
        if not hasattr(self.service,"project_overviews"): return
        try:
            self.overviews={p["id"]:p for p in self.service.project_overviews()}
            self._update_project_values()
        except Exception: pass

    def _save_preferences(self, patch):
        try:
            self.preferences_data = self.service.save_preferences(patch)
            return True
        except Exception as exc:
            self.status_var.set("偏好未保存：" + str(exc))
            return False

    def _update_project_values(self):
        self.project_combo["values"]=[p["name"] for p in self.visible_projects]
        self.project_combo.set("")
        for index,project in enumerate(self.visible_projects):
            if project["id"]==self.project_id: self.project_combo.current(index)
        matched={p["id"] for p in self.visible_projects}
        for project in self.projects:
            pid=project["id"];button=self.project_buttons[pid];selected=pid==self.project_id
            overview=self.overviews.get(pid,{})
            name=project["name"]
            line=str(overview.get("workline_name") or "选择工作线")
            details=[]
            if overview.get("draft_count"):details.append(f"草稿 {overview['draft_count']}")
            if overview.get("blocker_count"):details.append(f"阻塞 {overview['blocker_count']}")
            if not details and overview.get("updated_at"):details.append(relative_time(overview["updated_at"]))
            button.configure(text=name+"\n"+short_workline(line)+("\n"+" · ".join(details) if details else ""))
            button.state(['selected'] if selected else ['!selected'])
            button.grid() if pid in matched else button.grid_remove()
        self.no_results.grid_remove() if matched else self.no_results.grid()

    def _filter_projects(self, *_args):
        query = self.search_var.get().strip().casefold()
        self.visible_projects = [p for p in self.projects if query in " ".join([p["id"], p["name"]] + list(p.get("aliases") or [])).casefold()]
        self._update_project_values()

    def _search_enter(self, _event=None):
        if self.visible_projects:
            self.select_project(self.visible_projects[0]["id"])
        else:
            self.status_var.set("没有匹配的项目。")

    def _project_selected(self, _event=None):
        index = self.project_combo.current()
        if 0 <= index < len(self.visible_projects):
            self.select_project(self.visible_projects[index]["id"])

    def select_project(self, project_id):
        if self.last_dialog and not self.last_dialog.closed and not self.last_dialog.close(): return
        self.project_id=project_id;self.workline_id=None
        self.status_var.set("已切换项目。")
        self._save_preferences({"last_project":project_id})
        self.load_current()
        self.notebook.select(self.card_tab)
        self._maybe_refresh()

    def load_current(self):
        if self.project_id is None:return
        try:self._render(self.service.show(self.project_id,workline_id=self.workline_id))
        except Exception as exc:self.status_var.set("无法读取本地卡片："+str(exc))

    def _workline_selected(self,_event=None):
        index=self.workline_combo.current()
        if not 0<=index<len(self.worklines):return
        if self.last_dialog and not self.last_dialog.closed and not self.last_dialog.close():return
        try:
            state=self.service.select_workline(self.project_id,self.worklines[index]["id"])
            self._render(state)
            self.card_canvas.yview_moveto(0)
            self.status_var.set("已切换工作线；下一步、草稿和材料已同步。")
            self._maybe_refresh()
        except Exception as exc:messagebox.showerror("无法切换工作线",str(exc),parent=self.root)

    def create_workline(self):
        project_id = self.project_id
        window = self.workline_window = _dialog(self.root, "新建工作线")
        content = ttk.Frame(window, style="Card.TFrame", padding=(20,18,20,8))
        content.grid(sticky="nsew");content.columnconfigure(0,weight=1)
        ttk.Label(content,text="新建工作线",style="Title.TLabel",background=WHITE).grid(sticky="w")
        ttk.Label(content,text="给这项工作一个清楚的名字",style="CardMuted.TLabel").grid(sticky="w",pady=(5,14))
        name = tk.StringVar()
        self.workline_name_entry = ttk.Entry(content,textvariable=name,width=40,style='Card.TEntry')
        self.workline_name_entry.grid(sticky="ew")
        error=tk.StringVar(value="例如：V7 结论复核")
        error_label=ttk.Label(content,textvariable=error,style="CardMuted.TLabel",wraplength=450)
        error_label.grid(sticky="ew",pady=(7,0))
        footer=_dialog_footer(window,1)
        def save():
            if not name.get().strip():error.set("请填写工作线名称。");self.workline_name_entry.focus_set();return
            try:
                line=self.service.create_workline(project_id,name.get().strip())
                state=self.service.select_workline(project_id,line["id"])
                window.destroy()
                if self.project_id==project_id:self._render(state);self._maybe_refresh()
                self.status_var.set("工作线已建立，先查看材料，再写下第一步。")
            except Exception as exc:error.set(str(exc));error_label.configure(foreground=WARNING_INK)
        ttk.Button(footer,text="取消",style="Card.Ghost.TButton",command=window.destroy).pack(side="left")
        self.workline_create_button=ttk.Button(footer,text="创建工作线",style="Primary.TButton",command=save)
        self.workline_create_button.pack(side="right")
        window.bind("<Return>",lambda _e:save());window.bind("<Escape>",lambda _e:window.destroy())
        _center(window,self.root,540,275);window.grab_set();self.workline_name_entry.focus_set()
        return window

    def _auto_refresh_changed(self):
        self._save_preferences({"auto_refresh":bool(self.auto_refresh_var.get())})
        if self.auto_refresh_var.get():self._maybe_refresh()

    def _maybe_refresh(self):
        if not self.auto_refresh_var.get() or not self.current:return
        snapshot=self.current.get("snapshot")
        stamp=(snapshot or {}).get("observed_at")
        try:
            parsed=datetime.fromisoformat(str(stamp).replace("Z","+00:00"))
            if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=timezone.utc)
            stale=(datetime.now(timezone.utc)-parsed).total_seconds()>300
        except (ValueError,TypeError):stale=True
        if not snapshot or stale:
            scope=(self.project_id,self.workline_id)
            if self.busy_count:self.auto_pending=scope
            else:self.refresh()

    def _render(self,state):
        self.current=state
        self.workline_id=(state.get("workline") or {}).get("id","default")
        self.worklines=list(state.get("worklines") or [{"id":"default","name":"原有工作线"}])
        self.workline_combo["values"]=[w["name"] for w in self.worklines]
        self.workline_combo.current(next((i for i,w in enumerate(self.worklines) if w["id"]==self.workline_id),0))
        project,handoff,snapshot=state.get("project") or {},state.get("handoff") or {},state.get("snapshot") or {}
        self.latest_files_button.configure(text=(f"本次文件 {len(handoff.get('file_manifest') or [])} 项"
            if handoff.get('origin') == 'agent_recorded' else '节点文件'))
        completion=state.get("last_completion")
        if "last_completion" not in state and handoff.get("event_kind")!="plan":completion=handoff
        completion=completion or {}
        self.project_title.configure(text=project.get("name",self.project_id))
        self.focus_button.grid() if self.project_id=="rvc" else self.focus_button.grid_remove()
        metadata="材料读取于 "+relative_time(snapshot.get("observed_at")) if snapshot else "首次进入 · 读取材料后开始"
        if handoff:metadata+="  ·  续接更新 "+relative_time(handoff.get("recorded_at"))
        self.meta_var.set(metadata)
        next_action=handoff.get("next_action")
        _set_text(self.next_text,next_action or "还没有下一步。查看工作材料，再写下第一步。")
        self.next_text.configure(height=min(5,max(2,len(str(next_action or ""))//42+str(next_action or "").count("\n")+1)))
        self.guide_frame.grid_remove() if handoff else self.guide_frame.grid()
        self.plan_button.configure(text="调整下一步" if handoff else "设置下一步")
        _set_text(self.last_text,completion.get("last_done") or "")
        self.last_text.configure(height=min(3,max(1,len(str(completion.get("last_done") or ""))//65+1)))
        completion_caption="最近完成 · "+human_time(completion.get("recorded_at"))
        if completion.get('revision') != handoff.get('revision') and record_attribution(completion):
            completion_caption += '\n' + record_attribution(completion)
        self.completion_label.configure(text=completion_caption,wraplength=max(300,self.body.winfo_width()-80))
        self.last_frame.grid() if completion else self.last_frame.grid_remove()
        context=rvc_summary(snapshot) if self.project_id=="rvc" else handoff.get("current_task") or ""
        attribution=record_attribution(handoff)
        if attribution:context += ('\n' if context else '') + attribution
        self.task_var.set(context)
        self.task_label.grid() if context else self.task_label.grid_remove()
        drafts=state.get("drafts") or {}
        visible=False
        for kind,button in self.draft_buttons.items():
            if drafts.get(kind):button.pack(side="left",padx=(0,7));visible=True
            else:button.pack_forget()
        self.draft_bar.grid() if visible else self.draft_bar.grid_remove()
        blockers=list(handoff.get("blockers") or [])
        self.blocker_var.set(f"当前阻塞 · {len(blockers)} 项")
        _set_text(self.blocker_text,"\n".join(f"{i+1}. {value}" for i,value in enumerate(blockers)))
        self.blocker_text.configure(height=min(12 if self.blockers_expanded else 3,max(1,len(blockers))))
        self.blocker_frame.grid() if blockers else self.blocker_frame.grid_remove()
        self.blocker_toggle.grid() if len(blockers)>3 else self.blocker_toggle.grid_remove()
        facts=snapshot.get("facts") or [];_set_text(self.facts_text,"\n\n".join(map(str,facts)))
        warnings=list(dict.fromkeys(map(str,list(state.get("warnings") or [])+list(snapshot.get("warnings") or []))))
        _set_text(self.warning_text,"\n".join(warnings))
        self.fact_button.configure(text=f"来源记录 · {len(warnings)} 条说明" if warnings else "查看来源记录")
        changes=state.get("source_changes") or []
        self.change_shortcut.configure(text=f"{len(changes)} 项变化待复核" if changes else "来源暂无新变化")
        self.entries=list(state.get("entries") or snapshot.get("entries") or [])
        self.entry_combo["values"]=[str(e.get("label") or e["id"]) for e in self.entries]
        binding=state.get("bound_entry_ids") if "bound_entry_ids" in state else handoff.get("entry_ids") or (state.get("workline") or {}).get("entry_ids") or []
        preferred=binding[0] if binding else (self.preferences_data.get("entries") or {}).get(self.project_id)
        selected=next((i for i,e in enumerate(self.entries) if e["id"]==preferred),0)
        self.material_tree.delete(*self.material_tree.get_children())
        for i,entry in enumerate(self.entries):
            bound="★ " if entry["id"] in binding else ""
            self.material_tree.insert("","end",iid=str(i),values=(bound+short_material(entry),entry.get("action_label") or "打开"))
        if self.entries:
            self.entry_combo.current(selected);self.material_tree.selection_set(str(selected));self.open_button.configure(state="normal")
        else:self.entry_combo.set("");self.open_button.configure(state="disabled")
        self._entry_selected(persist=False)
        self._update_continue_label()
        attempted=state.get("latest_attempt_snapshot")
        source_snapshot=attempted if isinstance(attempted,dict) else snapshot
        self.observations=list(source_snapshot.get("observations") or [])
        failed=sum(obs.get("status")!="ok" for obs in self.observations)
        alerts=[]
        if failed:alerts.append(f"{failed} 项来源读取失败")
        if source_snapshot.get("error"):alerts.append("本次读取未完成")
        if state.get("missing_entry_ids"):alerts.append(f"{len(state['missing_entry_ids'])} 份绑定材料暂不可用")
        if any("接入配置已变化" in warning for warning in warnings):alerts.append("接入配置已变化，请刷新")
        self.alert_button.configure(text=" · ".join(alerts)+" · 查看详情")
        self.alert_button.grid() if alerts else self.alert_button.grid_remove()
        self.source_tree.delete(*self.source_tree.get_children())
        for index,obs in enumerate(self.observations):
            status="已读取" if obs.get("status")=="ok" else "失败 · 有历史" if obs.get("last_success") else "读取失败"
            stamp=human_time(obs.get("source_recorded_at")) if obs.get("source_recorded_at") else "文件修改 "+human_time(obs.get("source_mtime"))
            self.source_tree.insert("","end",iid=str(index),values=(source_label(obs.get("source_id"),obs.get("label")),status,stamp))
        error=source_snapshot.get("error")
        details=("最近一次整体读取失败："+str(error)+"\n读取时间："+str(source_snapshot.get("observed_at") or "未提供")) if error else "选择来源查看字段、时间，并直接打开原文。\n\n"+"\n".join(map(str,facts))
        if warnings:details+="\n\n需要留意\n"+"\n".join(warnings)
        _set_text(self.source_detail,details);_set_text(self.source_raw,"选择来源后可展开原始记录。")
        self._load_changes(state);self._load_history();self.refresh_overviews()
        scope = (self.project_id, self.workline_id)
        if hasattr(self.service, "list_file_nodes"):
            if self._files_scope != scope:
                self.files_panel.refresh(self.project_id, preferred_node_id="current:" + self.workline_id)
            elif self.notebook.select() == str(self.files_tab):
                self.files_panel.refresh(self.project_id)
        self._files_scope = scope
        storage_scope = self.project_id if self.workline_id == 'default' else self.project_id + '\x1f' + self.workline_id
        self._seen_record_revisions[storage_scope] = handoff.get('revision', 0)
        self._show_external_update()

    def _load_changes(self, state):
        self.source_changes = list(state.get("source_changes") or [])
        comparison = state.get("change_comparison")
        self.change_var.set("来源内容变化会保留旧下一步，请结合变化复核。" if self.source_changes else "暂无可展示的来源变化；首次读取会建立比较基线。")
        if isinstance(comparison, dict):
            label = comparison.get("description") or comparison.get("label") or comparison.get("message")
            if label:
                self.change_var.set(str(label))
        elif isinstance(comparison, str):
            self.change_var.set({"handoff": "与当前交接所依据的材料比较；原下一步保持不变。",
                                 "review": "与最近一次复核的材料比较；原交接依据保留。",
                                 "previous_refresh": "与上次有效读取比较；首次出现的来源会单独列出。",
                                 "none": "尚无比较基线，先刷新一次材料。"}.get(comparison, "来源变化按已保存的比较基线展示。"))
        self.changes_tree.delete(*self.changes_tree.get_children())
        for index, item in enumerate(self.source_changes):
            self.changes_tree.insert("", "end", iid=str(index), values=(source_label(item.get("source_id"), item.get("label")), CHANGE_LABELS.get(item.get("status"), item.get("status", "内容变化"))))
        _set_text(self.changes_detail, "选择一项变化，查看前后字段。" if self.source_changes else "尚无变化明细。")
        review=state.get("review") or {}
        self.review_var.set("最近复核："+human_time(review.get("recorded_at"))+" · 原交接及依据保持原样。" if review else "复核会单独留痕，不改写原交接依据。")
        ready=bool(state.get("handoff") and state.get("snapshot") and self.source_changes and not any(c.get("status") in {"unavailable","error"} for c in self.source_changes))
        self.review_button.configure(state="normal" if ready else "disabled")

    def _load_history(self):
        selected = self.history_tree.selection()
        old_records = getattr(self, 'history_records', [])
        previous_revision = (old_records[int(selected[0])].get('revision')
            if selected and int(selected[0]) < len(old_records) and
               getattr(self, '_history_scope', None) == (self.project_id, self.workline_id) else None)
        old_y, old_x = self.history_tree.yview(), self.history_tree.xview()
        old_detail_y = self.history_detail.yview()
        self.history_tree.delete(*self.history_tree.get_children())
        self.history_records = []
        self._history_scope = (self.project_id, self.workline_id)
        try:
            self.history_records = list(self.service.history(self.project_id, workline_id=self.workline_id))
            for index, item in enumerate(self.history_records):
                self.history_tree.insert("", "end", iid=str(index), values=(item.get("revision"), "调整下一步" if item.get("event_kind") == "plan" else "工作交接", human_time(item.get("recorded_at")), ORIGINS.get(item.get("origin"), item.get("origin"))))
            _set_text(self.history_detail, "选择一条记录查看详情。" if self.history_records else "尚无交接记录。可先设置下一步，开始工作。")
            index = next((i for i, item in enumerate(self.history_records)
                          if item.get('revision') == previous_revision), None)
            if index is not None:
                self.history_tree.selection_set(str(index))
                self.history_tree.focus(str(index))
                self._show_history()
                if old_y:self.history_tree.yview_moveto(old_y[0])
                if old_x:self.history_tree.xview_moveto(old_x[0])
                if old_detail_y:self.history_detail.yview_moveto(old_detail_y[0])
        except Exception as exc:
            _set_text(self.history_detail, "历史读取失败：" + str(exc))

    def _show_source(self, _event=None):
        selected = self.source_tree.selection()
        if selected:
            observation = self.observations[int(selected[0])]
            _set_text(self.source_detail, observation_text(observation))
            _set_text(self.source_raw, json.dumps(observation, ensure_ascii=False, indent=2))

    def _toggle_raw(self):
        raw = self.raw_visible.get()
        self.source_detail_scroll.suppressed = raw
        if raw:
            self.source_detail.grid_remove(); self.source_detail_scroll.grid_remove()
            self.raw_frame.grid()
        else:
            self.raw_frame.grid_remove(); self.source_detail.grid()
            self.source_detail_scroll.set(*self.source_detail.yview())

    def _show_change(self, _event=None):
        selected = self.changes_tree.selection()
        if selected:
            change = self.source_changes[int(selected[0])]
            lines = [source_label(change.get("source_id"), change.get("label")), str(change.get("path") or ""), ""]
            for field in change.get("fields") or []:
                lines += [field_label(field.get("field")), "之前：" + display_value(field.get("before")), "现在：" + display_value(field.get("after")), ""]
            if not change.get("fields"):
                lines += [CHANGE_LABELS.get(change.get("status"), str(change.get("status") or "内容变化")), str(change.get("message") or "可使用下方按钮打开原文核对内容。")]
            _set_text(self.changes_detail, "\n".join(lines))

    def _show_history(self, _event=None):
        selected = self.history_tree.selection()
        if selected:
            item = self.history_records[int(selected[0])]
            lines = ["工作线：" + str((self.current.get("workline") or {}).get("name") or "原有工作线")]
            attribution = record_attribution(item, detailed=True)
            if attribution:lines += [attribution]
            if item.get("current_task"):
                lines += ["本次主题补充：" + str(item["current_task"])]
            if item.get("event_kind") != "plan":
                lines += ["\n本次完成：\n" + str(item.get("last_done") or "未填写")]
            lines += ["\n下一步：\n" + str(item.get("next_action") or "未填写"), "\n阻塞项：\n" + (_lines(item.get("blockers")) or "无"), "\n依据快照：" + str(item.get("source_snapshot_id") or "未绑定"), "\n保存时间：" + str(item.get("recorded_at") or "")]
            _set_text(self.history_detail, "\n".join(lines))

    def _entry_selected(self,_event=None,persist=True):
        index=self.entry_combo.current()
        if 0<=index<len(getattr(self,"entries",[])):
            entry=self.entries[index]
            self.entry_hint.configure(text=str(entry.get("target") or entry.get("label") or ""))
            self.open_button.configure(text=entry.get("action_label") or "打开所选材料")
            if self.material_tree.selection()!=(str(index),):self.material_tree.selection_set(str(index))
            if persist:self._save_preferences({"entries":{self.project_id:entry["id"]}})
        else:self.entry_hint.configure(text="材料读取后会显示在这里。")
        self._update_continue_label()

    def _material_selected(self,_event=None):
        selected=self.material_tree.selection()
        if selected:
            self.entry_combo.current(int(selected[0]));self._entry_selected(persist=False)

    def _bound_entries(self):
        state=self.current or {}
        ids=state.get("bound_entry_ids") if "bound_entry_ids" in state else (state.get("handoff") or {}).get("entry_ids") or (state.get("workline") or {}).get("entry_ids") or []
        entries=[e for entry_id in ids for e in self.entries if e["id"]==entry_id]
        if "bound_entry_ids" not in state and not entries and 0<=self.entry_combo.current()<len(self.entries):entries=[self.entries[self.entry_combo.current()]]
        return entries[:3]

    def _update_continue_label(self):
        if not hasattr(self,"continue_button"):return
        record = (self.current or {}).get('handoff') or {}
        if record.get('origin') == 'agent_recorded':
            self.continue_button.configure(text=f"查看本次文件 · {len(record.get('file_manifest') or [])} 项")
            return
        entries=self._bound_entries()
        if len(entries)>1:label=f"打开 {len(entries)} 份工作材料"
        elif entries:
            entry=entries[0]
            label=("定位 " if entry.get("default_action")=="folder" else "打开 ")+short_material(entry,14)
        else:label="选择工作材料" if (self.current or {}).get("snapshot") else "读取工作材料"
        self.continue_button.configure(text=label)

    def _quick_open(self,index):
        self.entry_combo.current(index);self.open_selected_entry()

    def material_picker(self,entries,selected,callback,parent=None):
        parent=parent or self.root
        window=self.material_window=_dialog(parent,"选择工作材料")
        window.rowconfigure(1,weight=1)
        header=ttk.Frame(window,style="Card.TFrame",padding=(20,18,20,14));header.grid(sticky="ew")
        ttk.Label(header,text="选择工作材料",style="Title.TLabel",background=WHITE).pack(anchor="w")
        ttk.Label(header,text="选择 1–3 份，作为下一步的启动入口",style="CardMuted.TLabel").pack(anchor="w",pady=(5,0))
        tree=self.material_picker_tree=self._tree(window,[("name","材料",360),("action","操作",140)],7);tree.configure(selectmode="extended")
        tree.grid(row=1,column=0,sticky="nsew",padx=20);self._scroll(window,tree,1)
        for i,entry in enumerate(entries):
            tree.insert("","end",iid=str(i),values=(short_material(entry,45),entry.get("action_label") or "打开"))
            if entry["id"] in selected:tree.selection_add(str(i))
        note=tk.StringVar(value="按住 Ctrl 可选择多份材料。")
        ttk.Label(window,textvariable=note,style="CardMuted.TLabel",padding=(20,8)).grid(row=2,sticky="ew")
        def close():
            window.destroy()
            if parent.winfo_exists() and parent is not self.root:parent.grab_set()
        def save():
            ids=[entries[int(i)]["id"] for i in tree.selection()]
            if not 1<=len(ids)<=3:note.set("请选择 1–3 份材料。");return
            try:callback(ids)
            except Exception as exc:note.set(str(exc));return
            close()
        footer=_dialog_footer(window,3)
        ttk.Button(footer,text="取消",command=close,style="Card.Ghost.TButton").pack(side="left")
        self.material_save_button=ttk.Button(footer,text="使用这些材料",command=save,style="Primary.TButton")
        self.material_save_button.pack(side="right")
        window.protocol("WM_DELETE_WINDOW",close);window.bind("<Escape>",lambda _e:close())
        _center(window,parent,660,460);window.grab_set();tree.focus_set()
        return window

    def bind_materials(self):
        if not self.entries:self.status_var.set("先读取材料，再绑定下一步入口。");return
        project_id,workline_id=self.project_id,self.workline_id
        def save(ids):
            self.service.set_workline_entries(project_id,ids,workline_id=workline_id)
            if (project_id,workline_id)==(self.project_id,self.workline_id):self.load_current()
            self.status_var.set("已更新这条工作线的启动材料，历史交接中的材料记录保留。")
        self.material_picker(self.entries,[e["id"] for e in self._bound_entries()],save)

    def source_action(self,action,changed=False):
        tree=self.changes_tree if changed else self.source_tree
        selected=tree.selection()
        if not selected:self.status_var.set("先选择一条来源或变化。");return
        item=(self.source_changes if changed else self.observations)[int(selected[0])]
        try:
            result=self.service.open_source(self.project_id,item["source_id"],action=action,workline_id=self.workline_id)
            if action=="copy":self.root.clipboard_clear();self.root.clipboard_append(str(result));self.status_var.set("来源路径已复制。")
            else:self.status_var.set("已请求打开所选来源。")
        except Exception as exc:messagebox.showerror("无法打开来源",str(exc),parent=self.root)

    def acknowledge_changes(self):
        state=self.current or {}
        try:
            self.service.acknowledge_changes(self.project_id,int((state.get("handoff") or {}).get("revision",0)),(state.get("snapshot") or {}).get("id"),workline_id=self.workline_id)
            self.load_current();self.status_var.set("已记录复核：下一步继续有效，原交接依据保留。")
        except Exception as exc:messagebox.showerror("复核未保存",str(exc),parent=self.root)

    def import_summary(self):
        from .draft_import import parse_summary
        project_id,workline_id=self.project_id,self.workline_id
        window=self.import_window=_dialog(self.root,"导入助手总结")
        window.columnconfigure(0,weight=1);window.rowconfigure(1,weight=1);window.rowconfigure(3,weight=1)
        header=ttk.Frame(window,style="Card.TFrame",padding=(20,18,20,12));header.grid(sticky="ew")
        ttk.Label(header,text="导入助手总结",style="Title.TLabel",background=WHITE).pack(anchor="w")
        ttk.Label(header,text="粘贴含“本次完成”“下一步”的总结或 JSON · 仅在本机解析",style="CardMuted.TLabel",wraplength=620).pack(anchor="w",pady=(5,0))
        input_text=self.import_input=_text(window,height=6,width=50);input_text.grid(row=1,column=0,sticky="nsew",padx=20);self._scroll(window,input_text,1)
        preview=self.import_preview=_text(window,height=6,width=50,background=BG);preview.grid(row=3,column=0,sticky="nsew",padx=20,pady=(4,0));self._scroll(window,preview,3)
        _set_text(preview,"预览解析后的内容，再存为待确认草稿。")
        error=tk.StringVar();ttk.Label(window,textvariable=error,style="Card.TLabel",foreground=WARNING_INK,wraplength=620,padding=(20,4)).grid(row=4,sticky="ew")
        parsed={}
        def parse():
            try:
                result=parse_summary(input_text.get("1.0","end-1c"));parsed.clear();parsed.update(result)
                _set_text(preview,"助手建议 · 待确认\n\n本次完成：\n"+result["last_done"]+"\n\n下一步：\n"+result["next_action"]+"\n\n阻塞项：\n"+_lines(result["blockers"])+"\n\n"+"\n".join(result.get("_import_warnings") or []))
                error.set("");save_button.configure(state="normal")
            except Exception as exc:parsed.clear();save_button.configure(state="disabled");error.set(str(exc))
        def save():
            if not parsed:return
            if (project_id,workline_id)!=(self.project_id,self.workline_id):error.set("当前工作线已改变，请重新打开导入窗口。");return
            kind="handoff" if parsed.get("last_done") else "plan"
            if self.service.load_draft(project_id,event_kind=kind,workline_id=workline_id):
                error.set("这条工作线已有同类型草稿。请先继续并保存原草稿；本窗口文本保留。")
                return
            data=dict(parsed);window.destroy()
            self.create_handoff_dialog(kind,imported=data)
        self.import_parse_button=ttk.Button(window,text="预览解析结果",command=parse,style="Card.Ghost.TButton")
        self.import_parse_button.grid(row=2,sticky="w",padx=20,pady=(8,0))
        footer=_dialog_footer(window,5)
        ttk.Button(footer,text="取消",command=window.destroy,style="Card.Ghost.TButton").pack(side="left")
        save_button=self.import_save_button=ttk.Button(footer,text="存为待确认草稿",command=save,style="Primary.TButton",state="disabled")
        save_button.pack(side="right")
        def edited(_event):
            if input_text.edit_modified():
                input_text.edit_modified(False);parsed.clear();save_button.configure(state="disabled")
        input_text.edit_modified(False);input_text.bind("<<Modified>>",edited)
        window.bind("<Escape>",lambda _e:window.destroy())
        _center(window,self.root,720,630);window.grab_set();input_text.focus_set()
        return window

    def _background(self, action, callback, label):
        self.busy_count += 1
        self.refresh_button.configure(state="disabled")
        self._busy_visual()
        self.status_var.set(label)

        def worker():
            try:
                self.jobs.put((callback, action(), None))
            except Exception as exc:
                self.jobs.put((callback, None, exc))

        threading.Thread(target=worker, daemon=True, name="resume-card-reader").start()

    def _poll(self):
        if self.closed:
            return
        while True:
            try:
                callback, result, error = self.jobs.get_nowait()
            except queue.Empty:
                break
            self.busy_count -= 1
            if error is not None:
                self.status_var.set("操作未完成：" + str(error))
                self.load_current()
            else:
                try:
                    callback(result)
                except Exception as exc:
                    self.status_var.set("显示失败：" + str(exc))
        if not self.busy_count:
            self.refresh_button.configure(state="normal")
            if self.refresh_progress.winfo_ismapped():self._busy_visual()
            pending,self.auto_pending=self.auto_pending,None
            if pending==(self.project_id,self.workline_id):self._maybe_refresh()
        self.poll_id = self.root.after(80, self._poll)

    def refresh(self,continue_after=False):
        if self.project_id is None or self.busy_count:return
        project_id,workline_id=self.project_id,self.workline_id
        def complete(state):
            if (self.project_id,self.workline_id)==(project_id,workline_id):
                self._render(state)
                if continue_after:self.continue_work();return
                self.status_var.set("材料已刷新；下一步保持原样，变化可单独复核。")
            self.refresh_overviews()
        self._background(lambda:self.service.refresh(project_id,workline_id=workline_id),complete,"正在只读刷新材料，当前续接内容仍可查看…")

    def continue_work(self):
        if self.busy_count:self.status_var.set("正在读取材料，请稍候。");return
        if ((self.current or {}).get('handoff') or {}).get('origin') == 'agent_recorded':
            self.show_latest_files()
            return
        if not (self.current or {}).get("snapshot"):self.refresh(continue_after=True);return
        entries=self._bound_entries()
        if not entries:
            if self.entries:self.bind_materials()
            else:self.status_var.set("尚无可打开材料，请查看来源读取情况。")
            return
        opened=[];errors=[]
        for entry in entries:
            try:
                self.service.open_entry(self.project_id,entry["id"],workline_id=self.workline_id)
                opened.append(short_material(entry))
            except Exception as exc:errors.append(short_material(entry)+"："+str(exc))
        self.status_var.set("已请求打开："+"、".join(opened) if opened else "材料未能打开。")
        if errors:messagebox.showerror("部分材料未打开","\n".join(errors),parent=self.root)

    def create_handoff_dialog(self,event_kind="handoff",imported=None,entry_ids=None):
        if self.project_id is None or self.current is None:return None
        if self.last_dialog and not self.last_dialog.closed:
            if self.last_dialog.event_kind==event_kind and imported is None:
                self.last_dialog.window.lift();return self.last_dialog
            if not self.last_dialog.close():return None
        try:self.last_dialog=HandoffDialog(self,event_kind,imported=imported,entry_ids=entry_ids)
        except Exception as exc:messagebox.showerror("无法打开编辑器",str(exc),parent=self.root);return None
        return self.last_dialog

    def create_plan_dialog(self):return self.create_handoff_dialog("plan")

    def open_selected_entry(self,action=None):
        index=self.entry_combo.current()
        if not 0<=index<len(self.entries):self.status_var.set("尚无可打开的材料，请刷新并查看来源情况。");return
        entry=self.entries[index]
        try:
            result=self.service.open_entry(self.project_id,entry["id"],action=action,workline_id=self.workline_id)
            self._entry_selected()
            if action=="copy":self.root.clipboard_clear();self.root.clipboard_append(str(result));self.status_var.set("材料路径已复制。")
            else:self.status_var.set("已请求打开："+short_material(entry))
        except Exception as exc:messagebox.showerror("无法打开材料",str(exc),parent=self.root)

    def copy_summary(self):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.service.summary(self.project_id, workline_id=self.workline_id))
            self.status_var.set("本机摘要已复制；向外分享前请核对项目路径和内容。")
        except Exception as exc:
            messagebox.showerror("无法复制", str(exc), parent=self.root)

    def export(self, mode="local"):
        try:
            path = self.service.export(self.project_id, mode=mode, workline_id=self.workline_id)
            self.status_var.set("已导出：" + str(path))
            messagebox.showinfo("本机交接已导出", str(path), parent=self.root)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self.root)

    def restore_selected(self):
        selected = self.history_tree.selection()
        if not selected:
            return
        record = self.history_records[int(selected[0])]
        if not messagebox.askyesno("恢复记录", f"将版本 {record['revision']} 的内容保存为新版本？现有历史会保留。", parent=self.root):
            return
        expected = int((self.current.get("handoff") or {}).get("revision", 0))
        try:
            self.service.restore_handoff(self.project_id, record["revision"], expected, workline_id=self.workline_id)
            self.load_current()
            self.status_var.set("已恢复为一个新版本。")
        except Exception as exc:
            messagebox.showerror("恢复未完成", str(exc) + "\n请重新查看当前记录后再尝试。", parent=self.root)
            self.load_current()

    def choose_focus(self):
        if self.project_id != "rvc" or self.busy_count:
            return
        self._background(lambda: self.service.focus_options("rvc"), self._focus_dialog, "正在读取 RVC 任务登记…")

    def _focus_dialog(self, options):
        if self.project_id != "rvc":
            return
        if not options:
            self.status_var.set("没有找到可选择的 RVC 任务。")
            return
        self.focus_window = window = _dialog(self.root,"选择 RVC 任务")
        window.minsize(760, 400)
        window.transient(self.root)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(2, weight=1)
        top = ttk.Frame(window, style="Card.TFrame", padding=(20,18,20,12))
        top.grid(sticky="ew")
        top.columnconfigure(1, weight=1)
        ttk.Label(top,text="选择 RVC 任务",style="Title.TLabel",background=WHITE).grid(row=0,column=0,columnspan=3,sticky="w",pady=(0,14))
        ttk.Label(top, text="搜索", style="CardMuted.TLabel").grid(row=1, column=0)
        self.focus_query = tk.StringVar()
        self.focus_search_entry=ttk.Entry(top, textvariable=self.focus_query,style='Card.TEntry')
        self.focus_search_entry.grid(row=1, column=1, sticky="ew", padx=8)
        self.focus_kind = tk.StringVar(value="全部类型")
        kinds = sorted({str(o.get("kind") or "未标明") for o in options})
        kind_map = {}
        for kind in kinds:
            kind_map.setdefault(display_value(kind), set()).add(kind)
        self.focus_type_combo = ttk.Combobox(top, textvariable=self.focus_kind, state="readonly", values=["全部类型"] + list(kind_map), width=14,style='Card.TCombobox')
        self.focus_type_combo.grid(row=1, column=2)
        ttk.Label(window, text="每个任务独立续接 · 保存状态不代表当前运行状态", style="CardMuted.TLabel", padding=(20, 0, 20, 10)).grid(row=1, column=0, sticky="w")
        self.focus_tree = tree = self._tree(window, [("name", "任务 / 音色", 390), ("kind", "类型", 90), ("status", "保存状态", 160), ("time", "记录时间", 160)], 8)
        tree.grid(row=2, column=0, sticky="nsew", padx=20)
        self._scroll(window, tree, 2)
        current = (self.current.get("project") or {}).get("focus_job") or (self.current.get("snapshot") or {}).get("focus_job")
        if not current:
            label = str((self.current.get("project") or {}).get("focus_label") or "")
            current = next((o["id"] for o in options if o["id"] and o["id"] in label), "")

        def filter_options(*_args):
            query = self.focus_query.get().casefold().strip()
            wanted = kind_map.get(self.focus_kind.get())
            self.focus_filtered = [o for o in options if (not wanted or str(o.get("kind") or "未标明") in wanted) and query in " ".join(str(o.get(k) or "") for k in ("id", "label", "voice_name", "kind", "status")).casefold()]
            tree.delete(*tree.get_children())
            for index, option in enumerate(self.focus_filtered):
                name = str(option.get("voice_name") or option.get("label") or "任务")
                if option.get("id"):name += " · " + str(option["id"])[-8:]
                tree.insert("", "end", iid=str(index), values=(name, display_value(option.get("kind")), display_value(option.get("status")), human_time(option.get("recorded_at"))))
                if option["id"] == current:
                    tree.selection_set(str(index))
                    tree.see(str(index))

        def save():
            selected = tree.selection()
            if not selected:
                return
            try:
                state=self.service.set_focus("rvc", self.focus_filtered[int(selected[0])]["id"])
            except Exception as exc:
                messagebox.showerror("选择未保存", str(exc), parent=window)
                return
            window.destroy()
            self.workline_id=None
            self._render(state) if isinstance(state,dict) else self.load_current()
            self.refresh()

        footer=_dialog_footer(window,3)
        ttk.Button(footer,text="取消",command=window.destroy,style="Card.Ghost.TButton").pack(side="left")
        self.focus_save_button = ttk.Button(footer, text="关注所选任务并刷新", command=save, style="Primary.TButton")
        self.focus_save_button.pack(side="right")
        self.focus_query.trace_add("write", filter_options)
        self.focus_kind.trace_add("write", filter_options)
        filter_options()
        window.bind("<Escape>", lambda _event: window.destroy())
        _center(window,self.root,850,510)
        window.grab_set()
        self.focus_search_entry.focus_set()
        self.status_var.set("选择需要关注的任务；每个任务保留独立的续接记录。")

    def close(self):
        if self.closed:
            return True
        if self.last_dialog and not self.last_dialog.closed and not self.last_dialog.close():
            return False
        if self.root.state() == "normal":
            self._save_preferences({"geometry": f"{self.root.winfo_width()}x{self.root.winfo_height()}"})
        self.closed = True
        self.refresh_progress.stop()
        self.motion.close()
        if self._external_poll_id is not None:
            try:self.root.after_cancel(self._external_poll_id)
            except tk.TclError:pass
        if self._external_db is not None:
            self._external_db.close()
            self._external_db = None
        try:
            self.root.after_cancel(self.poll_id)
        except tk.TclError:
            pass
        self.root.destroy()
        return True

    def run(self):
        self.root.mainloop()


def main(service):
    App(service).run()
