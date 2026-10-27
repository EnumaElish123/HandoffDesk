"""A compact, native view of the files associated with each progress node."""
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .presentation import human_time
from . import platform_io
from .theme import FONT, INK, MUTED, WHITE, WARNING_INK, style_text


NODE_KINDS = {"current": "当前", "plan": "下一步", "handoff": "交接", "manual": "自定节点"}
ORIGINS = {"manual": "手动关联", "source": "已登记来源", "entry": "已登记材料", "agent": "Agent 登记"}


class NodeFilesPanel:
    """Keep node browsing separate from the app's selected workline."""

    def __init__(self, app, parent):
        self.app, self.parent = app, parent
        self.file_manager_name = '资源管理器' if platform_io.is_windows() else '文件管理器'
        self.project_id = None
        self.current_node_id = None
        self.node_values = []
        self.nodes = []
        self.last_result = {"node": None, "files": [], "notice": ""}
        self._workline_id = None
        self._files = {}
        self._updating = False
        self._read_failed = False
        self._detail_id = None
        self.node_dialog = self.purpose_dialog = self.node_info_dialog = None
        self.edit_purpose_dialog = self.rename_dialog = self.file_info_dialog = None
        self.node_scope_var = tk.StringVar(master=parent, value="当前工作线")
        self.search_var = tk.StringVar(master=parent)
        self.count_var = tk.StringVar(master=parent, value="选择进度节点，查看已登记文件")
        self.selection_var = tk.StringVar(master=parent, value="完整路径")
        self._build()
        self.search_var.trace_add("write", self._search_changed)
        self._set_available(False)

    def _build(self):
        from .desktop import AutoScrollbar

        frame = self.parent
        # Keep the app's rounded assets and focus states, but give the file list
        # priority over toolbar padding in the minimum supported window.
        style = getattr(self.app, "style", None) or ttk.Style(frame)
        for name in ("Files.TButton", "Files.Primary.TButton", "Files.Card.Ghost.TButton", "Files.Danger.TButton"):
            style.configure(name, padding=(6, 2), font=(FONT, 10), width=0)
        for name in ("Files.Card.TCombobox", "Files.Card.TEntry"):
            style.configure(name, padding=(6, 2))
        style.configure("Files.Treeview", rowheight=30)
        style.configure("Files.Treeview.Heading", padding=(8, 4))
        style.configure("Files.TMenubutton", padding=(6, 2), font=(FONT, 10))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=1)
        chooser = ttk.Frame(frame, style="Card.TFrame")
        chooser.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        chooser.columnconfigure(1, weight=1)
        self.node_scope_combo = ttk.Combobox(chooser, state="readonly", style="Files.Card.TCombobox",
                                            textvariable=self.node_scope_var, values=("当前工作线", "全部工作线"), width=9)
        self.node_scope_combo.grid(row=0, column=0, padx=(0, 6))
        self.node_scope_combo.bind("<<ComboboxSelected>>", self._scope_selected)
        self.node_combo = ttk.Combobox(chooser, state="readonly", style="Files.Card.TCombobox", width=1)
        self.node_combo.grid(row=0, column=1, sticky="ew")
        self.node_combo.bind("<<ComboboxSelected>>", self._node_selected)
        self.new_node_button = ttk.Button(chooser, text="新建节点", style="Files.Card.Ghost.TButton", command=self.create_node)
        self.new_node_button.grid(row=0, column=2, padx=(5, 0))

        search = ttk.Frame(frame, style="Card.TFrame")
        search.grid(row=1, column=0, sticky="ew", pady=(0, 1))
        search.columnconfigure(1, weight=1)
        ttk.Label(search, text="查找", style="CardMuted.TLabel").grid(row=0, column=0, padx=(0, 8))
        self.search_entry = ttk.Entry(search, textvariable=self.search_var, style="Files.Card.TEntry", width=1)
        self.search_entry.grid(row=0, column=1, sticky="ew")
        self.search_entry.bind("<Escape>", lambda _e: self.search_var.set(""))
        self.search_entry.bind("<Down>", self._focus_files)
        self.refresh_button = ttk.Button(search, text="刷新状态", style="Files.Card.Ghost.TButton", command=self.refresh)
        self.refresh_button.grid(row=0, column=2, padx=(5, 0))
        summary = ttk.Frame(frame, style="Card.TFrame")
        summary.grid(row=2, column=0, sticky="ew", pady=(0, 2))
        summary.columnconfigure(0, weight=1)
        self.count_label = ttk.Label(summary, textvariable=self.count_var, style="CardMuted.TLabel", width=1)
        self.count_label.grid(row=0, column=0, sticky="ew")
        self.info_button = ttk.Button(summary, text="节点说明", style="Files.Card.Ghost.TButton", command=self.show_node_info)
        self.info_button.grid(row=0, column=1)
        self.more_button = ttk.Menubutton(summary, text="更多", style="Files.TMenubutton")
        self.more_button.grid(row=0, column=2, padx=(3, 0))
        self.more_menu = self.context_menu = tk.Menu(self.more_button, tearoff=False, bg=WHITE, fg=INK,
            font=(FONT, 10), postcommand=self._update_menu)
        for label, command in (("查看文件详情…", self.show_file_info), ("修改用途…", self.edit_selected_purpose),
                               ("重新定位…", self.relocate_selected)):
            self.more_menu.add_command(label=label, command=command)
        self.more_menu.add_separator()
        self.more_menu.add_command(label="修改节点名称…", command=self.rename_node)
        self.more_button.configure(menu=self.more_menu)

        table = ttk.Frame(frame, style="Card.TFrame")
        table.grid(row=3, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.files_tree = ttk.Treeview(table, columns=("name", "purpose", "location", "status"),
                                      show="headings", height=3, selectmode="browse", style="Files.Treeview")
        for name, label, width in (("name", "文件 / 文件夹", 150), ("purpose", "用途", 100),
                                   ("location", "所在位置", 160), ("status", "状态", 100)):
            self.files_tree.heading(name, text=label, anchor="w")
            self.files_tree.column(name, width=width, minwidth=90, stretch=name == "location", anchor="w")
        self.files_tree.grid(row=0, column=0, sticky="nsew")
        self.files_tree.tag_configure("unavailable", foreground=WARNING_INK)
        self.files_tree.bind("<<TreeviewSelect>>", self._file_selected)
        self.files_tree.bind("<Double-1>", self._double_click)
        self.files_tree.bind("<Return>", lambda _e: self._activate())
        self.files_tree.bind("<Control-c>", lambda _e: self._activate("copy"))
        self.files_tree.bind("<Control-C>", lambda _e: self._activate("copy"))
        self.files_tree.bind("<Delete>", lambda _e: self._remove_key())
        self.files_tree.bind("<Control-f>", self._focus_search)
        self.files_tree.bind("<Button-3>", self._context_menu)
        self.files_tree.bind("<Shift-F10>", self._context_menu)
        self.files_tree.bind("<F2>", lambda _e: self._edit_purpose_key())
        self.table_y_scroll = AutoScrollbar(table, command=self.files_tree.yview)
        self.table_y_scroll.grid(row=0, column=1, sticky="ns")
        self.table_x_scroll = AutoScrollbar(table, orient="horizontal", command=self.files_tree.xview)
        self.table_x_scroll.grid(row=1, column=0, sticky="ew")
        self.files_tree.configure(yscrollcommand=self.table_y_scroll.set, xscrollcommand=self.table_x_scroll.set)
        self.empty_label = tk.Label(self.files_tree, text="选择进度节点，查看相关文件", bg=WHITE,
                                    fg=MUTED, font=(FONT, 10), justify="center")
        self.empty_label.place(relx=.5, rely=.57, anchor="center")

        detail = ttk.Frame(frame, style="Card.TFrame")
        detail.grid(row=4, column=0, sticky="ew", pady=(4, 3))
        detail.columnconfigure(1, weight=1)
        ttk.Label(detail, text="路径", style="CardMuted.TLabel").grid(row=0, column=0, sticky="nw", padx=(0, 8), pady=(2, 0))
        self.path_text = tk.Text(detail, height=2, width=1, wrap="char", relief="flat", borderwidth=0,
                                 font=(FONT, 9), bg=WHITE, fg=INK, padx=0, pady=1, takefocus=True)
        style_text(self.path_text, editable=False)
        self.path_text.grid(row=0, column=1, sticky="ew")
        self.path_scroll = AutoScrollbar(detail, command=self.path_text.yview)
        self.path_scroll.grid(row=0, column=2, sticky="ns")
        self.path_text.configure(yscrollcommand=self.path_scroll.set, state="disabled")
        self.path_text.bind("<Control-a>", self._select_path)
        self.path_text.bind("<Control-A>", self._select_path)
        self.path_text.bind("<Control-c>", self._copy_path_selection)
        self.path_text.bind("<Control-C>", self._copy_path_selection)

        actions = ttk.Frame(frame, style="Card.TFrame")
        actions.grid(row=5, column=0, sticky="ew")
        self.open_button = ttk.Button(actions, text="打开", style="Files.Primary.TButton", command=self.open_selected)
        self.open_button.pack(side="left")
        self.reveal_button = ttk.Button(actions, text=self.file_manager_name, style="Files.TButton", command=lambda: self.open_selected("reveal"))
        self.reveal_button.pack(side="left", padx=(4, 0))
        self.copy_button = ttk.Button(actions, text="复制路径", style="Files.Card.Ghost.TButton", command=lambda: self.open_selected("copy"))
        self.copy_button.pack(side="left", padx=(3, 0))
        self.add_button = ttk.Button(actions, text="关联文件", style="Files.Card.Ghost.TButton", command=self.add_files)
        self.add_button.pack(side="left", padx=(5, 0))
        self.folder_button = ttk.Button(actions, text="关联文件夹", style="Files.Card.Ghost.TButton", command=self.add_folder)
        self.folder_button.pack(side="left", padx=(2, 0))
        self.remove_button = ttk.Button(actions, text="移除关联", style="Files.Danger.TButton", command=self.remove_selected)
        self.remove_button.pack(side="right")

    def _status(self, value):
        target = getattr(self.app, "status_var", None)
        if target is not None:
            target.set(value)

    @staticmethod
    def _enable(widget, enabled):
        widget.state(["!disabled"] if enabled else ["disabled"])

    def _set_available(self, available):
        has_node = bool(available and self.current_node_id)
        writable = bool(available and not self._read_failed)
        self._enable(self.new_node_button, writable)
        self._enable(self.refresh_button, available)
        self._enable(self.node_scope_combo, available)
        self._enable(self.node_combo, bool(available and self.nodes))
        self._enable(self.search_entry, has_node)
        self._enable(self.add_button, has_node and writable)
        self._enable(self.folder_button, has_node and writable)
        self._enable(self.info_button, has_node)
        self._enable(self.more_button, has_node)
        self._file_selected()

    def _node_label(self, node):
        kind = NODE_KINDS.get(node.get("kind"), "节点")
        title = " ".join(str(node.get("title") or "未命名节点").split())
        title = title[:45] + "…" if len(title) > 46 else title
        workline = " ".join(str(node.get("workline_name") or "默认工作线").split())
        workline = workline[:20] + "…" if len(workline) > 21 else workline
        if self.node_scope_var.get() == "当前工作线":
            workline = ""
        date = human_time(node.get("recorded_at")) if node.get("recorded_at") else ""
        return " · ".join(part for part in (workline, kind, title, date) if part)

    def _scope_selected(self, _event=None):
        self.refresh()

    def _sorted_nodes(self, nodes, workline_id):
        def key(node):
            try:
                stamp = datetime.fromisoformat(str(node.get("recorded_at") or "").replace("Z", "+00:00")).timestamp()
            except (ValueError, TypeError, OSError, OverflowError):
                stamp = 0
            own_line = node.get("workline_id") == workline_id
            return (not own_line, "" if own_line else str(node.get("workline_name") or "").casefold(),
                    node.get("kind") != "current", -stamp, -int(node.get("revision") or 0), str(node["id"]))
        return sorted(nodes, key=key)

    def refresh(self, project_id=None, preferred_node_id=None, preferred_file_id=None):
        project_id = project_id or getattr(self.app, "project_id", None)
        workline_id = getattr(self.app, "workline_id", None)
        changed_scope = (project_id, workline_id) != (self.project_id, self._workline_id)
        previous = preferred_node_id or (None if changed_scope else self.current_node_id)
        previous_id = self.current_node_id
        if changed_scope:
            self.node_scope_var.set("当前工作线")
        service = self.app.service
        if not project_id or not hasattr(service, "list_file_nodes"):
            self.project_id, self._workline_id = project_id, workline_id
            self.nodes, self.node_values, self.current_node_id = [], [], None
            self.node_combo.configure(values=[])
            self.node_combo.set("")
            self.last_result = {"node": None, "files": [], "notice": ""}
            self._render_files()
            self._set_available(False)
            return
        try:
            all_lines = self.node_scope_var.get() == "全部工作线"
            nodes = list(service.list_file_nodes(project_id, workline_id=None if all_lines else workline_id or "default"))
            by_id = {str(node["id"]): node for node in nodes}
            if preferred_node_id and previous not in by_id and not all_lines:
                all_nodes = list(service.list_file_nodes(project_id))
                if any(str(node["id"]) == previous for node in all_nodes):
                    self.node_scope_var.set("全部工作线")
                    nodes = all_nodes
                    by_id = {str(node["id"]): node for node in nodes}
            if previous not in by_id:
                current = next((node for node in nodes if node.get("kind") == "current" and
                                (node.get("workline_id") == workline_id or workline_id is None)), None)
                previous = str((current or (nodes[0] if nodes else {})).get("id", "")) or None
            result = service.get_node_files(project_id, previous) if previous else {"node": None, "files": [], "notice": ""}
            self.project_id, self._workline_id = project_id, workline_id
            self.nodes = self._sorted_nodes(nodes, workline_id)
            self.node_values = [self._node_label(node) for node in self.nodes]
            self.node_combo.configure(values=self.node_values)
            if changed_scope or previous != previous_id:
                self._updating = True
                self.search_var.set("")
                self._updating = False
            self.current_node_id = previous
            if previous:
                self.node_combo.current(next(i for i, node in enumerate(self.nodes) if str(node["id"]) == previous))
            else:
                self.node_combo.set("")
            self.last_result = result
            self._read_failed = False
            self._render_files(preserve_view=not changed_scope and previous == previous_id,
                               preferred_file_id=preferred_file_id)
            self._set_available(True)
        except Exception as exc:
            self._read_failed = True
            if changed_scope:
                # Cached files may only remain visible inside their original scope.
                self.project_id, self._workline_id = project_id, workline_id
                self.nodes, self.node_values, self.current_node_id = [], [], None
                self.node_combo.configure(values=[])
                self.node_combo.set("")
                self.last_result = {"node": None, "files": [], "notice": str(exc)}
                self._render_files()
            elif self.current_node_id:
                original_index = next((i for i, node in enumerate(self.nodes) if str(node["id"]) == self.current_node_id), None)
                if original_index is not None:
                    self.node_combo.current(original_index)
            self.count_var.set("读取失败 · 已保留上次清单，请刷新重试" if self.current_node_id else "文件清单读取失败，请刷新重试")
            self.count_label.configure(foreground=WARNING_INK)
            if not self.current_node_id:
                self.empty_label.configure(text="文件清单读取失败\n可刷新状态重试")
            self._set_available(True)
            self._status("文件清单读取失败：" + str(exc))

    def show_node(self, node_id):
        self.refresh(preferred_node_id=str(node_id))

    def _node_selected(self, _event=None):
        index = self.node_combo.current()
        if not 0 <= index < len(self.nodes):
            return
        target = str(self.nodes[index]["id"])
        self.show_node(target)

    def _search_changed(self, *_args):
        if not self._updating:
            self._render_files()

    def _render_files(self, preserve_view=False, preferred_file_id=None):
        old_selection = self.files_tree.selection()
        previous = old_selection[0] if old_selection else None
        old_y, old_x = self.files_tree.yview(), self.files_tree.xview()
        all_files = list(self.last_result.get("files") or [])
        self._files = {str(item["id"]): item for item in all_files}
        query = self.search_var.get().strip().casefold()
        words = query.split()
        visible = [item for item in all_files if all(word in " ".join(str(item.get(key) or "")
                   for key in ("name", "path", "parent", "purpose")).casefold() for word in words)]
        self._updating = True
        children = self.files_tree.get_children()
        if children:
            self.files_tree.delete(*children)
        for item in visible:
            status = item.get("status") or ("可用" if item.get("exists") else "文件已缺失")
            status = {"available": "可用", "missing": "文件已缺失", "inaccessible": "位置不可访问"}.get(status, status)
            self.files_tree.insert("", "end", iid=str(item["id"]), values=(item.get("name") or Path(item.get("path") or "").name,
                item.get("purpose") or "—", item.get("parent") or item.get("path") or "—", status),
                tags=() if item.get("exists") else ("unavailable",))
        self._updating = False
        if visible:
            self.empty_label.place_forget()
            visible_ids = {str(item["id"]) for item in visible}
            preferred = str(preferred_file_id) if preferred_file_id is not None else None
            selected = (preferred if preferred in visible_ids else
                        previous if previous in visible_ids else str(visible[0]["id"]))
            explicit_selection = preferred is not None and selected == preferred
            self.files_tree.selection_set(selected)
            self.files_tree.focus(selected)
            if explicit_selection and self.files_tree.winfo_viewable():
                # Clearing a filter can add scrollbars and resize the viewport.
                # Lay it out before making the newly linked file visible.
                self.files_tree.update_idletasks()
            if preserve_view and previous == selected and not explicit_selection:
                if old_y:
                    self.files_tree.yview_moveto(old_y[0])
                if old_x:
                    self.files_tree.xview_moveto(old_x[0])
            elif not self.files_tree.winfo_viewable() or self.files_tree.winfo_height() <= 30:
                # A hidden notebook page can still have a one-pixel viewport.
                # Treeview.see() may scroll its first row offscreen at that size;
                # the following visible-page refresh would retain that offset.
                self.files_tree.yview_moveto(0)
            else:
                self.files_tree.see(selected)
        else:
            self.empty_label.configure(text="没有匹配的文件\n可按 Esc 清空查找" if query else
                "此节点还没有文件\n点击下方“关联文件”或“关联文件夹”" if self.current_node_id else "新建节点，开始整理项目文件")
            self.empty_label.place(relx=.5, rely=.57, anchor="center")
        manual = sum(item.get("origin") == "manual" for item in all_files)
        missing = sum(not item.get("exists") for item in all_files)
        count = f"{len(visible)} / {len(all_files)} 项" if query else f"{len(all_files)} 项"
        historical = (self.last_result.get("node") or {}).get("kind") in ("plan", "handoff")
        legacy = "旧记录" in str(self.last_result.get("notice") or "")
        parts = (["旧记录 · 历史依据有限"] if legacy else ["历史路径清单"] if historical else []) + [count, f"已登记 {len(all_files)-manual}", f"手动关联 {manual}"]
        if missing:
            parts.append(f"不可用 {missing}")
        self.count_var.set(" · ".join(parts) if self.current_node_id else "选择进度节点，查看已登记文件")
        if self._read_failed:
            self.count_var.set("读取失败 · 已保留上次清单，请刷新重试")
        self.count_label.configure(foreground=WARNING_INK if legacy or self._read_failed else MUTED)
        self._file_selected()

    def _selected_file(self):
        selected = self.files_tree.selection()
        return self._files.get(selected[0]) if selected else None

    def _file_selected(self, _event=None):
        if self._updating:
            return
        item = self._selected_file()
        path = str(item.get("path") or "") if item else "选择文件，即可查看并复制完整路径"
        identity = (self.current_node_id, (item or {}).get("id"), path)
        if identity != self._detail_id:
            self.path_text.configure(state="normal")
            self.path_text.delete("1.0", "end")
            self.path_text.insert("1.0", path)
            self.path_text.configure(state="disabled")
            self.path_text.yview_moveto(0)
            self._detail_id = identity
        if item:
            origin = ORIGINS.get(item.get("origin"), "已登记来源")
            kind = "文件夹" if self._is_folder(item) else str(item.get("kind") or "文件")
            if kind in ("file", "document"):
                kind = "文件"
            self.selection_var.set(f"完整路径 · {origin} · {kind}")
        else:
            self.selection_var.set("完整路径")
        label = str((item or {}).get("action_label") or "打开")
        # Keep two short, predictable action targets even for long backend labels.
        label = {"在资源管理器中打开": "定位文件", "在文件管理器中打开": "定位文件", "定位所在文件夹": "定位文件", "查看文本": "查看文本"}.get(label, label)
        self.open_button.configure(text=label)
        available = bool(item and item.get("exists") and not self._read_failed)
        self._enable(self.open_button, available)
        self._enable(self.reveal_button, bool(item) and not self._read_failed)
        self._enable(self.copy_button, bool(item))
        self._enable(self.remove_button, bool(item) and not self._read_failed)
        self._update_menu()

    def _update_menu(self):
        item = self._selected_file()
        writable = bool(self.current_node_id and not self._read_failed)
        node = self.last_result.get("node") or {}
        for label, enabled in (("查看文件详情…", bool(item)), ("修改用途…", writable and bool(item)),
                               ("重新定位…", writable and bool(item)),
                               ("修改节点名称…", writable and node.get("kind") == "manual")):
            self.more_menu.entryconfigure(label, state="normal" if enabled else "disabled")

    def _context_menu(self, event):
        row = self.files_tree.identify_row(event.y) if getattr(event, "num", None) == 3 else None
        if row:
            self.files_tree.selection_set(row)
            self.files_tree.focus(row)
            self._file_selected()
        if not self.current_node_id:
            return "break"
        self._update_menu()
        if getattr(event, "num", None) == 3:
            x, y = event.x_root, event.y_root
        else:
            box = self.files_tree.bbox(self.files_tree.focus())
            x = self.files_tree.winfo_rootx() + 20
            y = self.files_tree.winfo_rooty() + (box[1] + box[3] if box else 30)
        try:
            self.more_menu.tk_popup(x, y)
        finally:
            self.more_menu.grab_release()
        return "break"

    def _edit_purpose_key(self):
        if not self._read_failed:
            self.edit_selected_purpose()
        return "break"

    def _focus_search(self, _event=None):
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, "end")
        return "break"

    def _focus_files(self, _event=None):
        self.files_tree.focus_set()
        return "break"

    def _select_path(self, _event=None):
        self.path_text.tag_add("sel", "1.0", "end-1c")
        return "break"

    def _copy_path_selection(self, _event=None):
        try:
            text = self.path_text.get("sel.first", "sel.last")
        except tk.TclError:
            text = str((self._selected_file() or {}).get("path") or "")
        if text:
            self.app.root.clipboard_clear()
            self.app.root.clipboard_append(text)
            self._status("路径已复制。")
        return "break"

    def _double_click(self, event):
        row = self.files_tree.identify_row(event.y)
        if row and self.files_tree.identify_region(event.x, event.y) in ("cell", "tree"):
            self.files_tree.selection_set(row)
            self.open_selected()
        return "break"

    def _activate(self, action=None):
        self.open_selected(action)
        return "break"

    def _remove_key(self):
        self.remove_selected()
        return "break"

    def open_selected(self, action=None):
        item = self._selected_file()
        if not item:
            return
        action = action or item.get("open_action") or "open"
        if self._read_failed and action != "copy":
            self._status("文件清单读取失败，请先刷新状态再打开文件。")
            return
        if action == "open" and not item.get("exists"):
            self._status("这个位置目前不可用；可复制原路径，或重新关联文件。")
            return
        try:
            cached_copy = self._read_failed and action == "copy"
            result = str(item.get("path") or "") if cached_copy else self.app.service.open_node_file(
                self.project_id, self.current_node_id, item["id"], action=action)
            if action == "copy":
                self.app.root.clipboard_clear()
                self.app.root.clipboard_append(str(result))
                self._status("已复制上次清单中的完整路径；请刷新确认文件状态。" if cached_copy else "完整路径已复制。")
            else:
                self._status("已请求" + ("在" + self.file_manager_name + "中显示：" if action == "reveal" else "打开：") + str(item.get("name") or item["path"]))
        except Exception as exc:
            self._error("无法打开文件", exc)
            self.refresh()

    def _error(self, title, exc, parent=None):
        self._status(title + "：" + str(exc))
        messagebox.showerror(title, str(exc), parent=parent or self.app.root)

    def _initial_directory(self):
        selected = self._selected_file()
        if selected and selected.get("exists"):
            return str(selected.get("path") if self._is_folder(selected) else selected.get("parent") or "")
        if selected and selected.get("path"):
            try:
                folder = Path(selected["path"]).parent
                while not folder.is_dir() and folder != folder.parent:
                    folder = folder.parent
                if folder.is_dir():
                    return str(folder)
            except (OSError, RuntimeError):
                pass
        return ""

    @staticmethod
    def _is_folder(item):
        return item.get("kind") in ("folder", "directory", "dir", "文件夹")

    def show_file_info(self):
        if self._selected_file():
            self.show_node_info(selected_only=True)
            self.file_info_dialog = self.node_info_dialog

    def show_node_info(self, selected_only=False):
        from .desktop import AutoScrollbar, _center, _dialog, _dialog_footer, _set_text, _text
        from .presentation import record_attribution

        node = self.last_result.get("node")
        if not node:
            return
        if self.node_info_dialog is not None and self.node_info_dialog.winfo_exists():
            self.node_info_dialog.destroy()
        window = self.node_info_dialog = _dialog(self.app.root, "文件详情" if selected_only else "节点说明")
        window.rowconfigure(1, weight=1)
        ttk.Label(window, text="完整文件信息" if selected_only else "节点与文件说明", style="CardSection.TLabel", padding=(20, 14)).grid(row=0, column=0, sticky="ew")
        content = ttk.Frame(window, style="Card.TFrame", padding=(20, 0))
        content.grid(row=1, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        window.detail_text = _text(content, height=10, width=30, padx=0, pady=0)
        window.detail_text.grid(row=0, column=0, sticky="nsew")
        scroll = AutoScrollbar(content, command=window.detail_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        window.detail_text.configure(yscrollcommand=scroll.set)
        parts = [str(node.get("title") or "未命名节点"),
                 f"工作线：{node.get('workline_name') or '默认工作线'}",
                 f"类型：{NODE_KINDS.get(node.get('kind'), '节点')}",
                 f"时间：{human_time(node.get('recorded_at'))}"]
        if node.get("description"):
            parts += ["", "节点说明", str(node["description"])]
        if node.get('kind') in {'handoff', 'plan'}:
            try:
                record = next((item for item in self.app.service.history(self.project_id,
                    workline_id=node.get('workline_id')) if item.get('revision') == node.get('revision')), {})
                attribution = record_attribution(record, detailed=True)
                if attribution:parts += ['', attribution]
            except Exception:
                # A stale read can still show the cached node's paths.
                pass
        parts += ["", "文件清单说明", str(self.last_result.get("notice") or "仅显示已登记和手动关联的文件位置。")]
        selected = self._selected_file()
        if selected:
            parts += ["", "所选文件", str(selected.get("name") or ""),
                      "用途：" + str(selected.get("purpose") or "尚未填写"),
                      "文件角色：" + str(selected.get("role") or "未指定"),
                      "关联方式：" + ORIGINS.get(selected.get("origin"), "已登记来源"),
                      "位置：" + str(selected.get("path") or "")]
            if selected_only:
                parts = [str(selected.get("name") or "文件"), "",
                         "用途", str(selected.get("purpose") or "尚未填写"), "",
                         "文件角色：" + str(selected.get("role") or "未指定"), "",
                         "完整路径", str(selected.get("path") or ""), "",
                         "类型：" + str(selected.get("kind") or "文件"),
                         "状态：" + str(selected.get("status") or ""),
                         "关联方式：" + ORIGINS.get(selected.get("origin"), "已登记来源"),
                         "所属节点：" + str(node.get("title") or ""),
                         "工作线：" + str(node.get("workline_name") or "")]
                if selected.get("relinked_from"):
                    parts += ["", "之前的位置", str(selected["relinked_from"])]
        _set_text(window.detail_text, "\n".join(parts))
        footer = _dialog_footer(window, 2)
        ttk.Button(footer, text="关闭", style="Card.Ghost.TButton", command=window.destroy).pack(side="right")
        window.bind("<Escape>", lambda _e: window.destroy())
        _center(window, self.app.root, 560, 420)
        window.grab_set()
        window.detail_text.focus_set()

    def edit_selected_purpose(self):
        from .desktop import _center, _dialog, _dialog_footer

        item = self._selected_file()
        if not item or self._read_failed:
            return
        project_id, node_id, identity = self.project_id, self.current_node_id, item["id"]
        if self.edit_purpose_dialog is not None and self.edit_purpose_dialog.winfo_exists():
            self.edit_purpose_dialog.lift()
            return
        window = self.edit_purpose_dialog = _dialog(self.app.root, "修改文件用途")
        window.resizable(True, False)
        ttk.Label(window, text="修改用途", style="CardSection.TLabel", padding=(20, 14)).grid(row=0, column=0, sticky="ew")
        body = ttk.Frame(window, style="Card.TFrame", padding=(20, 0, 20, 8))
        body.grid(row=1, column=0, sticky="ew");body.columnconfigure(0, weight=1)
        name = str(item.get("name") or "所选文件")
        ttk.Label(body, text=name[:62] + ("…" if len(name) > 62 else ""), style="CardMuted.TLabel", wraplength=430).grid(sticky="w", pady=(0, 8))
        window.purpose_var = tk.StringVar(master=window, value=item.get("purpose") or "")
        entry = ttk.Entry(body, textvariable=window.purpose_var, style="Card.TEntry", width=35)
        entry.grid(row=1, sticky="ew")
        window.error_var = tk.StringVar(master=window, value="仅修改此节点中的用途；留空可清除用途。")
        ttk.Label(body, textvariable=window.error_var, style="CardMuted.TLabel", wraplength=430).grid(row=2, sticky="ew", pady=(8, 0))
        footer = _dialog_footer(window, 2)
        def save():
            try:
                self.app.service.update_node_file(project_id, node_id, identity, purpose=window.purpose_var.get())
            except Exception as exc:
                window.error_var.set(str(exc))
                return
            window.destroy()
            self.refresh(project_id, preferred_node_id=node_id)
            self._status("已更新此文件的用途。")
        window.save_button = ttk.Button(footer, text="保存用途", style="Primary.TButton", command=save)
        window.save_button.pack(side="right")
        ttk.Button(footer, text="取消", style="Card.Ghost.TButton", command=window.destroy).pack(side="right", padx=(0, 6))
        window.bind("<Return>", lambda _e: save())
        window.bind("<Escape>", lambda _e: window.destroy())
        _center(window, self.app.root, 500, 245)
        window.grab_set();entry.focus_set();entry.selection_range(0, "end")

    def relocate_selected(self):
        item = self._selected_file()
        if not item or self._read_failed:
            return
        project_id, node_id, identity = self.project_id, self.current_node_id, item["id"]
        directory = self._initial_directory()
        if self._is_folder(item):
            target = filedialog.askdirectory(parent=self.app.root, title="重新选择此节点的文件夹位置", initialdir=directory, mustexist=True)
        else:
            target = filedialog.askopenfilename(parent=self.app.root, title="重新选择此节点的文件位置", initialdir=directory,
                                               initialfile=str(item.get("name") or ""))
        if not target:
            return
        try:
            target_path = Path(target).resolve()
            result = self.app.service.relocate_node_file(project_id, node_id, identity, target)
            selected = next((row for row in result.get("files", [])
                             if Path(row.get("path") or "") == target_path), None)
            updating = self._updating
            self._updating = True
            try:
                self.search_var.set("")
            finally:
                self._updating = updating
            self.refresh(project_id, preferred_node_id=node_id,
                         preferred_file_id=selected["id"] if selected else None)
            self._status("文件位置已更新，但清单读取失败，请刷新状态。" if self._read_failed else
                         "已更新此节点的文件位置，原用途已保留。")
        except Exception as exc:
            self._error("无法更新文件位置", exc)

    def rename_node(self):
        from .desktop import _center, _dialog, _dialog_footer, _text

        node = self.last_result.get("node") or {}
        if node.get("kind") != "manual" or self._read_failed:
            return
        project_id, node_id = self.project_id, self.current_node_id
        if self.rename_dialog is not None and self.rename_dialog.winfo_exists():
            self.rename_dialog.lift();return
        window = self.rename_dialog = _dialog(self.app.root, "修改节点名称")
        window.resizable(True, False)
        ttk.Label(window, text="修改节点名称与说明", style="CardSection.TLabel", padding=(20, 14)).grid(row=0, column=0, sticky="ew")
        body = ttk.Frame(window, style="Card.TFrame", padding=(20, 0, 20, 8))
        body.grid(row=1, column=0, sticky="ew");body.columnconfigure(0, weight=1)
        ttk.Label(body, text="归属工作线：" + str(node.get("workline_name") or "默认工作线"), style="CardMuted.TLabel", wraplength=430).grid(sticky="w", pady=(0, 8))
        window.title_var = tk.StringVar(master=window, value=node.get("title") or "")
        entry = ttk.Entry(body, textvariable=window.title_var, style="Card.TEntry", width=35)
        entry.grid(row=1, sticky="ew")
        ttk.Label(body, text="说明", style="CardMuted.TLabel").grid(row=2, sticky="w", pady=(9, 5))
        window.description_text = _text(body, height=2, width=30)
        window.description_text.grid(row=3, sticky="ew")
        window.description_text.insert("1.0", node.get("description") or "")
        window.error_var = tk.StringVar(master=window, value="仅修改这个自定节点；文件关联与工作线保持不变。")
        ttk.Label(body, textvariable=window.error_var, style="CardMuted.TLabel", wraplength=430).grid(row=4, sticky="ew", pady=(7, 0))
        footer = _dialog_footer(window, 2)
        def save():
            try:
                self.app.service.rename_file_node(project_id, node_id, window.title_var.get(),
                                                  description=window.description_text.get("1.0", "end-1c"))
            except Exception as exc:
                window.error_var.set(str(exc));return
            window.destroy();self.refresh(project_id, preferred_node_id=node_id)
            self._status("节点名称与说明已更新。")
        window.save_button = ttk.Button(footer, text="保存修改", style="Primary.TButton", command=save)
        window.save_button.pack(side="right")
        ttk.Button(footer, text="取消", style="Card.Ghost.TButton", command=window.destroy).pack(side="right", padx=(0, 6))
        entry.bind("<Return>", lambda _e: save());window.bind("<Escape>", lambda _e: window.destroy())
        _center(window, self.app.root, 500, 345)
        window.grab_set();entry.focus_set();entry.selection_range(0, "end")

    def add_files(self):
        if not self.current_node_id or self._read_failed:
            return
        paths = filedialog.askopenfilenames(parent=self.app.root, title="选择要关联到此节点的文件（可多选）", initialdir=self._initial_directory())
        if paths:
            self._add_paths(paths)

    def add_folder(self):
        if not self.current_node_id or self._read_failed:
            return
        path = filedialog.askdirectory(parent=self.app.root, title="选择要关联到此节点的文件夹", initialdir=self._initial_directory(), mustexist=True)
        if path:
            self._add_paths([path])

    def _add_paths(self, paths):
        if not self.current_node_id or self._read_failed:
            return
        project_id, node_id = self.project_id, self.current_node_id
        purpose = self._ask_purpose(paths)
        if purpose is None:
            return
        try:
            self.app.service.add_node_files(project_id, node_id, list(paths), purpose=purpose)
            self.search_var.set("")
            self.refresh(project_id, preferred_node_id=node_id)
            self._status(f"已关联 {len(paths)} 个位置，原文件保留在原处。")
        except Exception as exc:
            self._error("无法关联文件", exc)

    def _ask_purpose(self, paths):
        from .desktop import _center, _dialog, _dialog_footer

        window = self.purpose_dialog = _dialog(self.app.root, "关联文件")
        window.resizable(False, False)
        ttk.Label(window, text=f"关联 {len(paths)} 个位置", style="CardSection.TLabel", padding=(20, 14)).grid(row=0, column=0, sticky="ew")
        body = ttk.Frame(window, style="Card.TFrame", padding=(20, 0, 20, 8))
        body.grid(row=1, column=0, sticky="ew")
        body.columnconfigure(0, weight=1)
        ttk.Label(body, text="用途（可留空）", style="CardMuted.TLabel").grid(sticky="w", pady=(0, 6))
        window.purpose_var = tk.StringVar(master=window)
        entry = ttk.Entry(body, textvariable=window.purpose_var, style="Card.TEntry", width=35)
        entry.grid(row=1, sticky="ew")
        ttk.Label(body, text="例如：实验结果、训练素材、待修改的代码", style="CardMuted.TLabel").grid(row=2, sticky="w", pady=(6, 0))
        footer = _dialog_footer(window, 2)
        result = {"purpose": None}

        def accept():
            result["purpose"] = window.purpose_var.get().strip()
            window.destroy()

        window.save_button = ttk.Button(footer, text="关联", style="Primary.TButton", command=accept)
        window.save_button.pack(side="right")
        ttk.Button(footer, text="取消", style="Card.Ghost.TButton", command=window.destroy).pack(side="right", padx=(0, 6))
        window.bind("<Return>", lambda _e: accept())
        window.bind("<Escape>", lambda _e: window.destroy())
        _center(window, self.app.root, 460, 230)
        window.grab_set()
        entry.focus_set()
        window.wait_window()
        return result["purpose"]

    def create_node(self):
        from .desktop import _center, _dialog, _dialog_footer, _text

        if not self.project_id or self._read_failed:
            return
        if self.node_dialog is not None and self.node_dialog.winfo_exists():
            self.node_dialog.lift()
            return
        project_id, workline_id = self.project_id, getattr(self.app, "workline_id", None)
        window = self.node_dialog = _dialog(self.app.root, "新建进度节点")
        window.resizable(True, False)
        ttk.Label(window, text="留住一个具体的进度节点", style="CardSection.TLabel", padding=(20, 14)).grid(row=0, column=0, sticky="ew")
        body = ttk.Frame(window, style="Card.TFrame", padding=(20, 0, 20, 6))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        project_name = str(((self.app.current or {}).get("project") or {}).get("name") or project_id)
        workline_name = str(((self.app.current or {}).get("workline") or {}).get("name") or workline_id or "默认工作线")
        ttk.Label(body, text=f"归属：{project_name} · {workline_name}", style="CardMuted.TLabel", wraplength=430).grid(row=0, sticky="ew", pady=(0, 9))
        ttk.Label(body, text="节点名称", style="CardMuted.TLabel").grid(row=1, sticky="w", pady=(0, 5))
        window.title_var = tk.StringVar(master=window)
        title_entry = ttk.Entry(body, textvariable=window.title_var, style="Card.TEntry", width=35)
        title_entry.grid(row=2, sticky="ew")
        ttk.Label(body, text="说明（可留空）", style="CardMuted.TLabel").grid(row=3, sticky="w", pady=(10, 5))
        window.description_text = _text(body, height=2, width=30)
        window.description_text.grid(row=4, sticky="ew")
        window.include_current_var = tk.BooleanVar(master=window, value=True)
        ttk.Checkbutton(body, text="带入当前工作线已登记的文件", variable=window.include_current_var,
                        style="Card.TCheckbutton").grid(row=5, sticky="w", pady=(10, 0))
        window.error_var = tk.StringVar(master=window, value="之后可以继续关联文件和文件夹。")
        ttk.Label(body, textvariable=window.error_var, style="CardMuted.TLabel", wraplength=430).grid(row=6, sticky="ew", pady=(6, 0))
        footer = _dialog_footer(window, 2)

        def save():
            title = window.title_var.get().strip()
            if not title:
                window.error_var.set("请先填写节点名称。")
                title_entry.focus_set()
                return
            try:
                node = self.app.service.create_file_node(project_id, title, workline_id=workline_id,
                    description=window.description_text.get("1.0", "end").strip(), include_current=window.include_current_var.get())
            except Exception as exc:
                window.error_var.set(str(exc))
                return
            window.destroy()
            self.refresh(project_id, preferred_node_id=str(node["id"]))
            self._status("已新建节点：" + title)

        window.save_button = ttk.Button(footer, text="创建节点", style="Primary.TButton", command=save)
        window.save_button.pack(side="right")
        ttk.Button(footer, text="取消", style="Card.Ghost.TButton", command=window.destroy).pack(side="right", padx=(0, 6))
        title_entry.bind("<Return>", lambda _e: save())
        window.bind("<Escape>", lambda _e: window.destroy())
        _center(window, self.app.root, 500, 390)
        window.grab_set()
        title_entry.focus_set()

    def remove_selected(self):
        item = self._selected_file()
        if not item or self._read_failed:
            return
        name = item.get("name") or item.get("path") or "所选文件"
        if not messagebox.askyesno("移除文件关联", f"从这个节点移除“{name}”的关联？\n\n原文件不会删除，其他节点的关联也会保留。", parent=self.app.root):
            return
        try:
            self.app.service.remove_node_file(self.project_id, self.current_node_id, item["id"])
            self.refresh()
            self._status("已移除此节点的关联，原文件未改动。")
        except Exception as exc:
            self._error("无法移除关联", exc)
