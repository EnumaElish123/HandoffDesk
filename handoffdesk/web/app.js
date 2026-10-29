/* HandoffDesk's local reader. Record and file contents are always text nodes. */
"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const state = { projects: [], project: null, workline: null, unread: false, query: "", items: [],
    next: null, selected: null, detail: null, cursor: null, listRequest: 0, detailRequest: 0,
    busy: false, pollBusy: false, manualRefreshPending: false, previewText: "", previousFocus: null, started: false,
    detailRefresh: 0, fileErrors: new Map() };
  const token = new URLSearchParams(location.hash.slice(1)).get("token");
  const labels = { passed: "验证通过", partial: "部分验证", not_run: "尚未验证", failed: "验证未通过" };
  let searchTimer, toastTimer, polling, connectionFocus;

  function node(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined && text !== null) el.textContent = String(text);
    return el;
  }

  function button(text, className, handler) {
    const el = node("button", className, text);
    el.type = "button";
    el.addEventListener("click", handler);
    return el;
  }

  function displayDate(value, full = false) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value || "日期未知");
    const options = full
      ? { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }
      : { month: "2-digit", day: "2-digit" };
    return date.toLocaleString("zh-CN", options);
  }

  async function api(method, ...args) {
    let response;
    try {
      if (window.pywebview && window.pywebview.api) {
        response = await window.pywebview.api[method](...args);
      } else if (token) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 15000);
        try {
          const result = await fetch("/api", { method: "POST", headers: { "Content-Type": "application/json", "X-HandoffDesk-Token": token },
            body: JSON.stringify({ method, args }), signal: controller.signal, cache: "no-store" });
          response = await result.json();
          if (!result.ok) response = Object.assign({}, response, {
            ok: false,
            error: response.error || "本机读取请求未成功，请重新打开或刷新阅读器。",
            category: response.category || (result.status === 400 ? "operation" : "connection"),
          });
        } finally { clearTimeout(timeout); }
      } else {
        throw new Error("请从 HandoffDesk 打开阅读窗口。");
      }
    } catch (failure) {
      const error = failure instanceof Error ? failure : new Error(String(failure));
      error.category = "connection";
      throw error;
    }
    if (!response || !response.ok) {
      const error = new Error((response && response.error) || "本机读取服务暂不可用。");
      error.category = response ? response.category || "operation" : "connection";
      error.code = response && response.code;
      throw error;
    }
    return response.data;
  }

  function toast(message) {
    clearTimeout(toastTimer);
    $("toast").textContent = message;
    $("toast").hidden = false;
    toastTimer = setTimeout(() => { $("toast").hidden = true; }, 4500);
  }

  function showError(error, retry = null, label = "操作未完成") {
    const text = error && error.name === "AbortError" ? "读取超时，请刷新重试。" : (error && error.message) || String(error);
    if (error && error.category === "connection") {
      $("connection-error").textContent = text;
      $("connection-error").hidden = false;
      $("live-state").textContent = "等待重新连接";
    } else {
      const container = $("operation-error");
      container.replaceChildren(node("p", "", label + "：" + text));
      if (retry) container.append(button("重试", "text-button", async () => { container.hidden = true; await retry(); }));
      container.append(button("关闭提示", "text-button", () => { container.hidden = true; }));
      container.hidden = false;
    }
  }

  function clearError() {
    $("connection-error").hidden = true;
    const dot = node("span", "online-dot");
    dot.setAttribute("aria-hidden", "true");
    $("live-state").replaceChildren(dot, document.createTextNode("自动更新"));
  }

  function renderProjects() {
    const total = state.projects.reduce((sum, project) => sum + (project.unread || 0), 0);
    const current = state.projects.find((project) => project.id === state.project);
    const unread = current ? current.unread || 0 : total;
    $("all-count").textContent = String(total);
    $("project-count").textContent = String(state.projects.length).padStart(2, "0");
    $("unread-count").textContent = String(unread);
    $("unread-tab-count").textContent = unread ? String(unread) : "";
    $("page-title").textContent = current ? current.name : "全部进展";
    document.title = (current ? current.name : "工作进展") + " · HandoffDesk";
    $("overview-text").textContent = unread
      ? `有 ${unread} 条新进展等待阅读。完成内容、下一步和文件，都在一起。`
      : "Agent 完成阶段工作后，进展会出现在这里。";
    $("all-projects").classList.toggle("selected", !state.project);
    $("all-projects").setAttribute("aria-current", !state.project ? "page" : "false");
    const projectElements = state.projects.map((project) => {
      const el = button("", "nav-item" + (project.id === state.project ? " selected" : ""), () => chooseProject(project.id));
      el.title = project.name;
      el.setAttribute("aria-current", project.id === state.project ? "page" : "false");
      const dot = node("span", "project-dot"); dot.setAttribute("aria-hidden", "true");
      el.append(dot, node("span", "", project.name));
      if (project.unread) el.append(node("span", "count", project.unread));
      return el;
    });
    if (!projectElements.length) projectElements.push(node("p", "list-placeholder", "Agent 记录工作时，项目会自动出现在这里。"));
    $("project-list").replaceChildren(...projectElements);
    const all = node("option", "", "全部项目"); all.value = "";
    $("project-select").replaceChildren(all, ...state.projects.map((project) => {
      const option = node("option", "", project.name); option.value = project.id; return option;
    }));
    $("project-select").value = state.project || "";
    const allLines = node("option", "", "全部任务"); allLines.value = "";
    const lines = current ? current.worklines || [] : [];
    $("workline-select").replaceChildren(allLines, ...lines.map((line) => {
      const duplicate = lines.filter((other) => (other.name || other.id) === (line.name || line.id)).length > 1;
      const count = Number.isInteger(line.record_count) ? ` · ${line.record_count} 条记录` : "";
      const identity = duplicate ? ` · ${line.id.slice(-8)}` : "";
      const option = node("option", "", (line.name || line.id) + count + identity);
      option.title = line.latest_recorded_at ? "最近记录：" + displayDate(line.latest_recorded_at, true) : "暂无记录";
      option.value = line.id; return option;
    }));
    $("workline-select").value = state.workline || "";
    $("workline-select").disabled = !current;
  }

  function renderList() {
    const focused = $("record-list").contains(document.activeElement) ? document.activeElement.dataset.recordId : null;
    $("list-count").textContent = state.items.length ? String(state.items.length).padStart(2, "0") + (state.next ? "+" : "") : "";
    $("list-label").textContent = state.query ? "搜索结果" : state.unread ? "等待阅读" : "最近的工作";
    if (!state.items.length) {
      $("record-list").replaceChildren(node("div", "list-placeholder", state.query ? "没有找到相关记录。试试其他关键词。" : state.unread ? "这里的进展已经全部读过了。" : "还没有进展记录。Agent 记录阶段工作后，这里就会更新。"));
    } else {
      const elements = state.items.map((record) => {
        const el = button("", "record-card" + (record.id === state.selected ? " selected" : ""), () => openRecord(record.id));
        el.dataset.recordId = record.id;
        el.setAttribute("aria-pressed", record.id === state.selected ? "true" : "false");
        const meta = node("div", "record-meta");
        meta.append(node("span", "record-project", record.project_name), node("time", "record-date", displayDate(record.recorded_at)));
        const title = node("div", "record-title");
        if (!record.read) { const dot = node("span", "unread-dot"); dot.setAttribute("aria-label", "未读"); title.append(dot); }
        title.append(document.createTextNode(record.title || "进度记录"));
        const excerpt = node("p", "record-excerpt", record.done || record.next_action);
        const bottom = node("div", "record-bottom");
        bottom.append(node("span", "kind-label" + (record.kind === "plan" ? " plan" : ""), record.kind === "plan" ? "工作计划" : "阶段进展"),
          node("span", "", record.file_count ? `${record.file_count} 个文件` : `第 ${record.revision} 次记录`));
        el.append(meta, title, excerpt, bottom);
        return el;
      });
      $("record-list").replaceChildren(...elements);
      if (focused) {
        const replacement = elements.find((el) => el.dataset.recordId === focused);
        if (replacement) replacement.focus({ preventScroll: true });
      }
    }
    $("load-more").hidden = !state.next;
    $("load-more").disabled = state.busy;
  }

  function section(title, symbol, style, content) {
    const el = node("section", "record-section");
    const heading = node("h3", "section-heading");
    const icon = node("span", "section-icon " + (style || ""), symbol); icon.setAttribute("aria-hidden", "true");
    heading.append(icon, document.createTextNode(title));
    el.append(heading, content);
    return el;
  }

  async function copyText(text) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
        toast("已复制");
        return;
      }
    } catch (_) { /* Fall through to an explicit copy selection. */ }
    const field = node("textarea", "sr-only"); field.value = text;
    document.body.append(field); field.focus(); field.select();
    let copied = false;
    try { copied = document.execCommand("copy"); } catch (_) { /* Manual selection remains available. */ }
    field.remove();
    if (copied) toast("已复制");
    else openPreview({ name: "请手动选择并复制", text, path: "当前环境未提供剪贴板权限。", truncated: false });
  }

  function renderDetail(record) {
    const pane = $("detail-pane");
    const back = button("← 返回进展列表", "back-to-list", () => {
      $("reading-layout").classList.remove("show-detail");
      const selected = Array.from($("record-list").querySelectorAll("button")).find((el) => el.dataset.recordId === state.selected);
      if (selected) selected.focus();
    });
    const kicker = node("div", "detail-kicker"), breadcrumb = node("div", "detail-breadcrumb");
    breadcrumb.append(node("span", "project-label", record.project_name), node("span", "", "/"), node("span", "", record.workline_name || "默认任务"));
    const tools = node("div", "detail-tools");
    const copySummary = async () => {
      try { await copyText(await api("export", record.id)); }
      catch (error) { showError(error, copySummary, `复制「${record.title}」未完成`); }
    };
    const exportRecord = async () => {
      try {
        if (window.pywebview && window.pywebview.api) {
          const result = await api("save_export", record.id);
          if (result.saved) toast("记录已导出");
        } else {
          const [text, filename] = await Promise.all([api("export", record.id), api("export_filename", record.id)]);
          const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
          const link = node("a"); link.href = URL.createObjectURL(blob); link.download = filename;
          document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(link.href), 1000);
          toast("已请求下载记录");
        }
      } catch (error) { showError(error, exportRecord, `导出「${record.title}」未完成`); }
    };
    const copyButton = button("复制摘要", "text-button", copySummary), exportButton = button("导出 ↗", "text-button", exportRecord);
    copyButton.dataset.focusKey = "copy-summary"; exportButton.dataset.focusKey = "export-record";
    tools.append(copyButton, exportButton);
    kicker.append(breadcrumb, tools);
    const title = node("h2", "detail-title", record.title);
    const byline = node("div", "detail-byline");
    byline.append(node("span", "avatar", "✦"), node("span", "", (record.actor && record.actor.name) || "历史记录"), node("span", "byline-divider", "·"),
      node("time", "", displayDate(record.recorded_at, true)), node("span", "byline-divider", "·"), node("span", "", record.kind === "plan" ? "工作计划" : "阶段进展"));
    pane.replaceChildren(back, kicker, title, byline);
    if (record.done) pane.append(section("这次完成了", "✓", "", node("div", "record-body", record.done)));
    else if (record.kind === "plan") pane.append(section("关于这份计划", "◇", "", node("div", "record-body", "这是下一阶段的工作安排，尚未作为已完成工作记录。")));
    const next = node("div", "next-box"); next.append(node("div", "record-body", record.next_action || "未填写下一步。"));
    pane.append(section(record.kind === "plan" ? "计划要做的事" : "接下来做什么", "↗", "next", next));
    if (record.blockers && record.blockers.length) {
      const list = node("ul", "blocker-list"); record.blockers.forEach((text) => list.append(node("li", "", text)));
      pane.append(section("需要留意", "!", "blocked", list));
    }
    const verification = record.verification || { status: "not_run", summary: "未提供验证说明。" };
    const verified = node("div", "verification " + (Object.prototype.hasOwnProperty.call(labels, verification.status) ? verification.status : "not_run"));
    const verifyText = node("div"); verifyText.append(node("strong", "", (labels[verification.status] || "验证说明") + " · 记录者报告"), node("p", "", verification.summary));
    verified.append(node("span", "verification-dot", verification.status === "passed" ? "✓" : "◉"), verifyText);
    pane.append(verified);
    const files = node("div", "file-list");
    for (const file of record.files || []) {
      const card = node("article", "file-card");
      card.dataset.fileId = file.id;
      const extension = file.is_directory ? "DIR" : (file.name || "").split(".").pop().slice(0, 4).toUpperCase();
      const info = node("div");
      const displayName = file.evidence && file.evidence.source_path
        ? String(file.evidence.source_path).split(/[\\/]/).pop() + "（证据副本）" : file.name || file.path;
      info.append(node("h4", "file-name", displayName), node("p", "file-purpose", file.purpose || "未填写用途"), node("p", "file-path", file.path));
      if (file.status && file.status !== "可用") info.append(node("p", "file-status", file.status));
      if (file.evidence) {
        info.append(node("p", "file-version", "证据副本 · 保存于 " + displayDate(file.evidence.frozen_at, true) + "，保存的是当时的当前内容，不是原记录时的快照。"));
      }
      if (file.version_status === "changed") {
        info.append(node("p", "file-version changed", file.evidence ? "证据副本与保存时相比已变化；预览显示当前副本内容。" : "与记录时相比已变化；预览显示当前内容。"));
      } else if (file.version_status === "unchanged") {
        const hashed = file.recorded_metadata && file.recorded_metadata.sha256;
        info.append(node("p", "file-version", hashed ? "内容与记录时一致（已核对内容指纹）。" : "大小和修改时间与记录时一致，未逐字比对内容。"));
      } else if (file.version_status === "unknown") {
        info.append(node("p", "file-version", file.is_directory ? "目录引用不包含内容快照。" : "无可比对的记录时版本信息；预览显示当前内容。"));
      }
      const actions = node("div", "file-actions");
      const fileButton = (label, action) => {
        const control = button(label, "", () => runFileOperation(record.id, file, action));
        control.dataset.focusKey = file.id + ":" + action;
        return control;
      };
      actions.append(fileButton("预览", "preview"), fileButton("打开", "open"), fileButton("所在位置", "reveal"), fileButton("复制路径", "copy"));
      if (!file.is_directory && file.status === "可用" && !file.evidence) {
        const freeze = fileButton("保存证据副本", "freeze");
        freeze.title = "保存点击时的当前文件，最多 10 MiB；不是原记录时的历史快照。";
        if (file.current_metadata && file.current_metadata.size > 10 * 1024 * 1024) {
          freeze.disabled = true;
          freeze.title = "文件超过 10 MiB，不能在此保存证据副本。";
        }
        actions.append(freeze);
      }
      info.append(actions);
      const issue = state.fileErrors.get(record.id + ":" + file.id);
      if (issue) {
        const error = node("div", "operation-error file-operation-error"); error.setAttribute("role", "alert");
        error.append(node("p", "", issue.message), fileButton("重试", issue.action),
          button("关闭提示", "text-button", () => { state.fileErrors.delete(record.id + ":" + file.id); redrawDetail(state.detail); }));
        info.append(error);
      }
      card.append(node("div", "file-icon", extension), info); files.append(card);
    }
    if (!files.children.length) files.append(node("p", "file-empty", "本次没有附带文件。"));
    const attachmentSection = section("本次文件", "▤", "files", files);
    attachmentSection.querySelector("h3").append(node("span", "file-count", (record.files || []).length));
    if (files.querySelector(".file-card")) attachmentSection.insertBefore(node("p", "file-reference-note", "文件关联读取当前内容。可主动保存当前版本的证据副本（最多 10 MiB），不会自动保存原记录时的文件。"), files);
    pane.append(attachmentSection);
    const footer = node("div", "detail-bottom");
    const reading = node("div", "read-controls");
    const readToggle = button(record.read ? "标为未读" : "标为已读", "text-button read-toggle", () => setRecordRead(record.id, !record.read));
    readToggle.dataset.focusKey = "read-toggle";
    reading.append(node("span", "read-badge", record.read ? "✓ 已读" : "未读"), readToggle);
    footer.append(node("span", "record-identity", "记录 " + record.id), reading);
    pane.append(footer);
  }

  async function setRecordRead(id, read) {
    try {
      await api(read ? "ack" : "mark_unread", [id]);
      const local = state.items.find((item) => item.id === id);
      if (local) local.read = read;
      if (state.selected === id && state.detail) {
        state.detail.read = read;
        redrawDetail(state.detail);
      }
      state.projects = await api("projects");
      renderProjects(); await loadRecords({ passive: true });
    } catch (error) { showError(error, () => setRecordRead(id, read), "更新阅读状态未完成"); }
  }

  function redrawDetail(record) {
    if (!record || state.selected !== record.id) return;
    const pane = $("detail-pane"), scroll = { x: window.scrollX, y: window.scrollY, pane: pane.scrollTop };
    const focus = pane.contains(document.activeElement) ? document.activeElement.dataset.focusKey : null;
    const previous = state.previousFocus && state.previousFocus.dataset.focusKey;
    renderDetail(record);
    const controls = Array.from(pane.querySelectorAll("[data-focus-key]"));
    if (focus) controls.find((control) => control.dataset.focusKey === focus)?.focus({ preventScroll: true });
    if (previous && !state.previousFocus.isConnected) state.previousFocus = controls.find((control) => control.dataset.focusKey === previous) || null;
    pane.scrollTop = scroll.pane;
    window.scrollTo(scroll.x, scroll.y);
  }

  async function refreshCurrentDetail(id = state.selected) {
    if (!id || state.selected !== id) return;
    const request = ++state.detailRefresh, selection = state.detailRequest;
    const record = await api("detail", id);
    if (request !== state.detailRefresh || selection !== state.detailRequest || state.selected !== id) return;
    state.detail = record;
    redrawDetail(record);
  }

  async function runFileOperation(recordId, file, action) {
    const key = recordId + ":" + file.id, selection = state.detailRequest;
    try {
      let result;
      if (action === "preview") result = await api("preview", recordId, file.id);
      else if (action === "freeze") result = await api("freeze_file", recordId, file.id);
      else result = await api("open_file", recordId, file.id, action);
      if (state.fileErrors.get(key)?.action === action) state.fileErrors.delete(key);
      await refreshCurrentDetail(recordId);
      if (state.selected !== recordId || selection !== state.detailRequest) return;
      if (action === "preview" || result.action === "preview") openPreview(result);
      else if (action === "copy") await copyText(result.path);
      else toast(action === "freeze" ? "已保存当前文件的证据副本" : action === "reveal" ? "已请求打开所在位置" : "已请求在本机打开");
    } catch (error) {
      if (error.category === "connection") showError(error);
      else state.fileErrors.set(key, { message: error.message || String(error), action });
      if (state.selected === recordId && selection === state.detailRequest) {
        try { await refreshCurrentDetail(recordId); }
        catch (refreshError) { showError(refreshError); redrawDetail(state.detail); }
      }
    }
  }

  function openPreview(value) {
    if (!$("connection-modal").hidden) closeConnection();
    state.previousFocus = document.activeElement;
    state.previewText = value.text || "";
    $("preview-title").textContent = value.name || "文件预览";
    $("preview-note").textContent = value.path || "";
    $("preview-content").textContent = state.previewText;
    $("preview-status").textContent = value.truncated ? "内容较长，仅预览前 1 MiB / 2000 行。" : value.encoding_warning ? "部分字符无法按 UTF-8 解码，已显示替代字符。" : value.previewable === false ? "此文件不支持文本预览。" : "只读预览 · 文件当前内容";
    $("preview-modal").hidden = false;
    $("preview-close").focus();
  }

  function closePreview() {
    $("preview-modal").hidden = true;
    if (state.previousFocus && state.previousFocus.isConnected) state.previousFocus.focus();
  }

  function closeConnection() {
    $("connection-modal").hidden = true;
    if (connectionFocus && connectionFocus.isConnected) connectionFocus.focus();
  }

  async function loadConnection() {
    const content = $("connection-content");
    content.replaceChildren(node("p", "connection-loading", "正在检查本机连接…"));
    try {
      const status = await api("connection_status");
      const summary = node("div", "connection-summary");
      const headline = status.reader_uses_custom_data ? "需要统一阅读与记录目录" : !status.connected ? "尚未连接 Codex"
        : !status.ok ? "连接文件需要检查"
        : status.hooks_configured ? "连接已安装，等待宿主信任确认" : "已连接 Skill";
      const description = status.reader_uses_custom_data ? "当前窗口使用独立数据目录。连接后写入的位置必须与阅读位置一致，现已暂停此窗口的连接操作。" : !status.connected ? "连接一次，让 Agent 在工作中主动记录阶段进展。"
        : !status.ok ? "部分连接文件已变化。重新连接会保留已有用户配置；若同名文件被修改，会先报告问题。"
        : status.hooks_configured ? "首次信任由 Codex 确认。目前尚未验证宿主信任和任务中的写入权限。"
        : "Skill 已安装；是否自动调用取决于 Agent。完整接入还包含阶段结束时的记录提醒。";
      summary.append(node("strong", "", headline), node("p", "", description));
      const rows = [
        ["版本", "HandoffDesk " + status.version],
        ["本机数据", status.app_dir],
        ["记录目录", status.reader_data_dir || status.app_dir],
        ["Codex", status.host && status.host.version ? status.host.version : "未找到可用的 Codex 命令；桌面应用需在宿主中另行检查。"],
        ["记录范围", "当前设备上的项目与历史；不会自动同步到其他电脑。"],
      ].map(([label, text]) => {
        const row = node("div", "connection-row"); row.append(node("span", "", label), node("span", "", text)); return row;
      });
      const help = node("p", "connection-help", status.reader_uses_custom_data
        ? "请先用统一的 --app-dir 重新打开 HandoffDesk，再连接 Codex。当前项目和历史仍可继续阅读。"
        : status.native_connect
        ? "点击连接后，在支持 hooks 的 Codex 中确认 HandoffDesk 的首次信任，再让 Agent 运行一次权限检查。无需手动登记每个项目。"
        : "在运行 Agent 的同一台电脑上执行下面的连接命令。随后由 Codex 确认首次信任，并让 Agent 检查任务中的写入权限。");
      const command = node("pre", "connection-command", status.connect_command);
      const actions = node("div", "connection-actions");
      if (status.native_connect) {
        const connect = button(status.connected ? "更新 Codex 连接" : "连接 Codex", "button primary", async () => {
          connect.disabled = true; connect.textContent = "正在连接…";
          try {
            await api("connect_codex");
            await loadConnection();
            toast("连接配置已安装，请在 Codex 中确认首次信任");
          } catch (error) {
            content.append(node("p", "connection-error-text", error.message || String(error)));
            connect.disabled = false; connect.textContent = "重试连接";
          }
        });
        actions.append(connect);
      }
      if (status.connect_command) actions.append(button("复制连接命令", "button secondary", () => copyText(status.connect_command)));
      content.replaceChildren(summary, ...rows, help, ...(status.connect_command ? [command] : []), actions);
    } catch (error) {
      content.replaceChildren(node("p", "connection-error-text", error.message || String(error)), button("重试检查", "button secondary", loadConnection));
    }
  }

  async function openRecord(id, { automatic = false } = {}) {
    const request = ++state.detailRequest;
    try {
      const record = await api("detail", id);
      if (request !== state.detailRequest) return;
      state.selected = id; state.detail = record;
      renderDetail(record); renderList();
      if (!automatic) {
        $("reading-layout").classList.add("show-detail");
        if (window.innerWidth <= 930) $("detail-pane").scrollIntoView({ block: "start" });
      }
      if (automatic) return;
      await api("ack", [id]);
      if (request !== state.detailRequest) return;
      record.read = true;
      const local = state.items.find((item) => item.id === id); if (local) local.read = true;
      const badge = $("detail-pane").querySelector(".read-badge"); if (badge) badge.textContent = "✓ 已读";
      const toggle = $("detail-pane").querySelector(".read-toggle"); if (toggle) toggle.textContent = "标为未读";
      state.projects = await api("projects");
      renderProjects(); renderList(); clearError();
    } catch (error) { if (request === state.detailRequest) showError(error, () => openRecord(id, { automatic }), "读取记录未完成"); }
  }

  async function loadRecords({ append = false, passive = false } = {}) {
    const request = ++state.listRequest;
    const selection = state.detailRequest;
    const desiredCount = passive ? Math.max(50, state.items.length) : 50;
    const selectedProject = state.project, selectedWorkline = state.workline;
    const unreadOnly = state.unread, query = state.query;
    state.busy = true; $("load-more").disabled = true;
    try {
      let data = await api("records", selectedProject, selectedWorkline, 50, append ? state.next : null, unreadOnly, query);
      if (request !== state.listRequest) return;
      if (append) {
        const seen = new Set(state.items.map((item) => item.id));
        state.items = [...state.items, ...data.items.filter((item) => !seen.has(item.id))];
        state.next = data.next_cursor;
      } else {
        // Re-read the loaded depth so another reader's acknowledgements also
        // update older pages, without collapsing the list back to its first page.
        const items = [...data.items];
        while (passive && data.next_cursor && items.length < desiredCount) {
          data = await api("records", selectedProject, selectedWorkline, 50, data.next_cursor, unreadOnly, query);
          if (request !== state.listRequest) return;
          items.push(...data.items);
        }
        state.items = items; state.next = data.next_cursor;
      }
      renderList(); clearError();
      if (!state.selected && selection === state.detailRequest && state.items.length && window.innerWidth > 930 && !passive) await openRecord(state.items[0].id, { automatic: true });
      return true;
    } catch (error) { if (request === state.listRequest) showError(error, () => loadRecords({ append, passive }), "读取记录列表未完成"); return false; }
    finally { if (request === state.listRequest) { state.busy = false; $("load-more").disabled = false; } }
  }

  function resetDetail() {
    ++state.detailRequest; state.selected = null; state.detail = null;
    $("reading-layout").classList.remove("show-detail");
    const empty = node("div", "detail-empty"); empty.append(node("span", "empty-symbol", "↗"), node("h2", "", "每一次进展，都有来处。"), node("p", "", "选择一条记录，查看完成内容、下一步和相关文件。"));
    $("detail-pane").replaceChildren(empty);
  }

  async function chooseProject(id) {
    state.project = id || null; state.workline = null; state.items = []; state.next = null;
    resetDetail(); renderProjects(); await loadRecords();
  }

  async function refresh({ manual = false } = {}) {
    if (state.pollBusy) {
      if (manual) state.manualRefreshPending = true;
      return;
    }
    state.pollBusy = true;
    try {
      const changes = await api("changes", state.cursor);
      if (changes.changed || manual) {
        const projects = await api("projects");
        state.projects = projects;
        if (state.project && !projects.some((project) => project.id === state.project)) {
          state.project = null; state.workline = null; state.items = []; resetDetail();
        }
        renderProjects();
        if (!await loadRecords({ passive: true })) return;
        await refreshCurrentDetail();
        state.cursor = changes.cursor;
      }
      clearError(); if (manual) toast("已刷新进展");
    } catch (error) { showError(error, () => refresh({ manual: true }), "刷新进展未完成"); }
    finally {
      state.pollBusy = false;
      if (state.manualRefreshPending) {
        state.manualRefreshPending = false;
        refresh({ manual: true });
      }
    }
  }

  async function start() {
    if (state.started) return;
    state.started = true;
    try {
      state.projects = await api("projects"); renderProjects();
      const changes = await api("changes"); state.cursor = changes.cursor;
      if (!await loadRecords()) state.cursor = null;
    } catch (error) { showError(error); }
    polling = setInterval(() => { if (!document.hidden) refresh(); }, 1500);
  }

  $("all-projects").addEventListener("click", () => chooseProject(null));
  $("project-select").addEventListener("change", (event) => chooseProject(event.target.value));
  $("workline-select").addEventListener("change", () => { state.workline = $("workline-select").value || null; resetDetail(); loadRecords(); });
  [false, true].forEach((unread) => {
    $(unread ? "filter-unread" : "filter-all").addEventListener("click", () => {
      state.unread = unread;
      $("filter-all").classList.toggle("active", !unread); $("filter-all").setAttribute("aria-pressed", String(!unread));
      $("filter-unread").classList.toggle("active", unread); $("filter-unread").setAttribute("aria-pressed", String(unread));
      resetDetail(); loadRecords();
    });
  });
  $("search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.query = $("search").value; resetDetail(); loadRecords(); }, 250); });
  $("load-more").addEventListener("click", () => loadRecords({ append: true }));
  $("refresh").addEventListener("click", () => refresh({ manual: true }));
  $("preview-close").addEventListener("click", closePreview);
  $("preview-copy").addEventListener("click", () => copyText(state.previewText));
  $("preview-modal").addEventListener("click", (event) => { if (event.target === $("preview-modal")) closePreview(); });
  $("connection-status").addEventListener("click", () => {
    connectionFocus = document.activeElement;
    $("connection-modal").hidden = false; $("connection-close").focus(); loadConnection();
  });
  $("connection-close").addEventListener("click", closeConnection);
  $("connection-modal").addEventListener("click", (event) => { if (event.target === $("connection-modal")) closeConnection(); });
  document.addEventListener("keydown", (event) => {
    const modal = !$("preview-modal").hidden ? $("preview-modal") : !$("connection-modal").hidden ? $("connection-modal") : null;
    if (modal) {
      if (event.key === "Escape") { event.preventDefault(); modal === $("preview-modal") ? closePreview() : closeConnection(); }
      if (event.key === "Tab") {
        const focusable = Array.from(modal.querySelectorAll("button:not([disabled]),[tabindex='0']"));
        const first = focusable[0], last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    } else if ((event.ctrlKey || event.metaKey) && event.key === "k") { event.preventDefault(); $("search").focus(); }
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && state.started) refresh(); });
  window.addEventListener("beforeunload", () => clearInterval(polling));
  window.addEventListener("pywebviewready", start);
  if (token || (window.pywebview && window.pywebview.api)) start();
  else setTimeout(() => { if (!state.started) showError(Object.assign(new Error("正在连接桌面窗口；若此页面单独打开，请从 HandoffDesk 重新启动。"), { category: "connection" })); }, 8000);
})();
