"""Bounded observations; source projects are never changed or executed."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .registry import RegistryError

MAX_JSON = 2 * 1024 * 1024
TEXT_PREVIEW = 16 * 1024


class SourceReadError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _signature(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _read_stable(path, *, max_bytes=MAX_JSON, retries=2, tail=False, metadata_only=False):
    """Read one stable bounded file; an atomic replacement triggers a retry."""
    path = Path(path)
    max_bytes = max(1, min(int(max_bytes), 16 * MAX_JSON))
    retries = max(0, min(int(retries), 2))
    for attempt in range(retries + 1):
        try:
            before = path.stat()
            if not path.is_file():
                raise SourceReadError("来源不是普通文件")
            if not tail and not metadata_only and before.st_size > max_bytes:
                raise SourceReadError(f"文件超过读取上限 {max_bytes} 字节（实际 {before.st_size}）")
            with path.open("rb") as stream:
                opened = os.fstat(stream.fileno())
                if metadata_only:
                    raw = b""
                else:
                    if tail:
                        stream.seek(max(0, opened.st_size - max_bytes))
                    raw = stream.read(max_bytes + (0 if tail else 1))
                closed = os.fstat(stream.fileno())
            after = path.stat()
            if _signature(before) != _signature(opened) or _signature(opened) != _signature(closed) or _signature(closed) != _signature(after):
                if attempt < retries:
                    continue
                raise SourceReadError("unstable：读取期间文件持续变化，请稍后刷新")
            if not tail and not metadata_only and len(raw) > max_bytes:
                raise SourceReadError("读取期间文件超过大小上限")
            return raw, after
        except SourceReadError:
            raise
        except OSError as exc:
            raise SourceReadError(f"无法读取：{exc}") from exc
    raise SourceReadError("unstable：文件变化")


def read_json(path, max_bytes=MAX_JSON, retries=2):
    raw, _ = _read_stable(path, max_bytes=max_bytes, retries=retries)
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
        raise SourceReadError(f"JSON 无效：{exc}") from exc


def json_pointer(document, pointer):
    if pointer == "":
        return document
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise KeyError(pointer)
    current = document
    for encoded in pointer.split("/")[1:]:
        if re.search(r"~(?![01])", encoded):
            raise KeyError(pointer)
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and re.fullmatch(r"0|[1-9][0-9]*", token) and int(token) < len(current):
            current = current[int(token)]
        else:
            raise KeyError(pointer)
    return current


def _observe(registry, project, source):
    observation = {"source_id": source["id"], "status": "error", "value": {}, "source_ref": source.get("path", ""),
                   "observed_at": _now(), "source_recorded_at": None, "source_mtime": None, "fingerprint": None, "error": None}
    raw_document = None
    try:
        path = registry.source_path(project, source)
        observation["source_ref"] = str(path)
        defaults = registry.defaults
        limit = int(defaults.get("max_json_bytes", MAX_JSON))
        retries = int(defaults.get("read_retries", 2))
        fmt = source.get("format", "text")
        if fmt == "binary_entry_only":
            raw, stat = _read_stable(path, retries=retries, metadata_only=True)
            value = {"size_bytes": stat.st_size, "entry_only": True}
            fingerprint_raw = f"metadata:{stat.st_size}:{stat.st_mtime_ns}".encode()
            observation["fingerprint_scope"] = "metadata"
        elif fmt == "json":
            raw, stat = _read_stable(path, max_bytes=limit, retries=retries)
            try:
                raw_document = json.loads(raw.decode("utf-8-sig"))
            except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
                raise SourceReadError(f"JSON 无效：{exc}") from exc
            fields = source.get("fields")
            value = {}
            missing = []
            if fields:
                for field, pointer in fields.items():
                    try:
                        value[field] = json_pointer(raw_document, pointer)
                    except KeyError:
                        if field not in source.get("optional_fields", []):
                            missing.append(f"{field} ({pointer})")
                if missing:
                    value["_missing_fields"] = missing
            elif isinstance(raw_document, dict):
                value = raw_document.copy()
            else:
                value = {"document": raw_document}
            fingerprint_raw = raw
            observation["fingerprint_scope"] = "file_bytes"
        else:
            tail = fmt in ("log", "log_tail")
            read_limit = int(defaults.get("log_tail_bytes", TEXT_PREVIEW)) if tail else limit
            raw, stat = _read_stable(path, max_bytes=read_limit, retries=retries, tail=tail)
            text = raw.decode("utf-8-sig", errors="replace")
            value = {"excerpt": text[:TEXT_PREVIEW], "truncated": len(text) > TEXT_PREVIEW or stat.st_size > len(raw),
                     "size_bytes": stat.st_size, "excerpt_position": "tail" if tail else "head"}
            fingerprint_raw = raw if not tail else raw + f":{stat.st_size}:{stat.st_mtime_ns}".encode()
            observation["fingerprint_scope"] = "tail_and_metadata" if tail else "file_bytes"
        if registry.source_path(project, source) != path:
            raise SourceReadError("unstable：读取期间来源路径发生变化")
        observation["source_mtime"] = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds")
        observation["fingerprint"] = hashlib.sha256(fingerprint_raw).hexdigest()
        observation["value"] = value
        timestamp = value.get("source_recorded_at") or value.get("finished_at")
        if timestamp is None and isinstance(raw_document, dict):
            timestamp = next((raw_document[k] for k in ("updated_at", "updated_utc", "generated_utc", "created_utc", "finished_at", "confirmed_at") if isinstance(raw_document.get(k), str)), None)
        observation["source_recorded_at"] = timestamp if isinstance(timestamp, str) else None
        if value.get("_missing_fields"):
            observation["error"] = "缺少登记字段：" + "、".join(value["_missing_fields"])
        else:
            observation["status"] = "ok"
        if observation["source_recorded_at"]:
            try:
                event_time = datetime.fromisoformat(observation["source_recorded_at"].replace("Z", "+00:00"))
                if event_time.tzinfo is None:
                    observation["time_warning"] = "源记录时间未注明时区"
                elif (event_time - datetime.now(timezone.utc)).total_seconds() > 300:
                    observation["time_warning"] = "源记录时间晚于本次读取时间，请核对源时钟"
            except ValueError:
                observation["time_warning"] = "源记录时间格式无法解析，原值已保留"
    except (SourceReadError, RegistryError, ValueError, TypeError, OSError) as exc:
        observation["error"] = str(exc)
        raw_document = None
    return observation, raw_document


def _git_observation(registry, project):
    obs = {"source_id": f"{project['id']}.git", "status": "error", "value": {}, "source_ref": project["roots"].get("main", ""),
           "observed_at": _now(), "source_recorded_at": None, "source_mtime": None, "fingerprint": None, "error": None}
    executable = os.environ.get("HANDOFFDESK_GIT") or registry.defaults.get("git_executable") or shutil.which("git")
    if not executable:
        obs["error"] = "未找到 Git；当前代码状态未检查"
        return obs
    root = registry.resolve(project, "main", ".")
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
    command = [str(executable), "--no-optional-locks", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", "-C", str(root),
               "status", "--porcelain=v2", "--branch", "-z", "--untracked-files=normal"]
    try:
        with tempfile.TemporaryFile(dir=registry.path.parent) as output:
            result = subprocess.run(command, stdout=output, stderr=subprocess.PIPE, shell=False, env=env,
                                    timeout=min(10, max(0.1, float(registry.defaults.get("git_timeout_seconds", 3)))),
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            output.seek(0)
            raw = output.read(MAX_JSON + 1)
        if result.returncode:
            raise SourceReadError("Git 查询失败：" + result.stderr[:2048].decode("utf-8", errors="replace").strip())
        if len(raw) > MAX_JSON:
            raise SourceReadError("Git 状态输出超过 2 MiB，未完成统计")
        value = {"branch": None, "head": None, "tracked_changes": 0, "untracked_entries": 0, "unmerged_entries": 0,
                 "validation": "只观察 Git 状态，未执行测试"}
        records = raw.decode("utf-8", errors="replace").split("\x00")
        skip_next = False
        for row in records:
            if skip_next:
                skip_next = False
                continue
            if row.startswith("# branch.oid "):
                value["head"] = row[len("# branch.oid "):]
            elif row.startswith("# branch.head "):
                value["branch"] = row[len("# branch.head "):]
            elif row.startswith(("1 ", "2 ", "u ")):
                value["tracked_changes"] += 1
                value["unmerged_entries"] += int(row.startswith("u "))
                skip_next = row.startswith("2 ")
            elif row.startswith("? "):
                value["untracked_entries"] += 1
        obs.update(status="ok", value=value, fingerprint=hashlib.sha256(raw).hexdigest())
    except (OSError, subprocess.SubprocessError, SourceReadError) as exc:
        obs["error"] = str(exc)
    return obs


def _source_label(source):
    labels = {"current_plan_index": "计划索引", "execution_package": "执行计划包", "validation_scope": "验证规则",
              "session_report_example": "登记的会话报告", "reading_entry_not_verified_run": "阅读材料（未核对对应运行）",
              "historical_final_record": "历史最终状态", "historical_execution_summary": "历史执行摘要"}
    named = {"rvc.selected_state": "关注任务状态", "rvc.followup_state": "接续任务状态", "rvc.previous_state": "前序任务状态",
             "rvc.restore_record": "历史恢复验证", "rvc.selected_result": "关注任务结果", "rvc.followup_result": "接续任务结果"}
    return source.get("label") or labels.get(source.get("purpose")) or named.get(source["id"]) or Path(source.get("path", source["id"])).name


def _display(value):
    if value is None:
        return "未指定（null）"
    if value is True:
        return "是"
    if value is False:
        return "否"
    return str(value)


def _job_id(source):
    parts = source.get("path", "").replace("\\", "/").split("/")
    try:
        at = parts.index("jobs")
        return parts[at + 1]
    except (ValueError, IndexError):
        return None


def _rvc_relations(registry, project, sources, observations, documents, warnings, facts):
    """Follow only explicit resumed_by links; never choose a latest job."""
    states = {}
    results = {}
    for source in sources:
        document = documents.get(source["id"])
        job_id = _job_id(source)
        name = source.get("path", "").replace("\\", "/").split("/")[-1]
        if job_id and isinstance(document, dict):
            if name == "state.json":
                states[job_id] = document
            elif name == "result.json":
                results[job_id] = document
            elif name == "job.json" and document.get("id") != job_id:
                warnings.append(f"状态来源不一致：任务目录 {job_id} 与 job.json 的 id 不符")
    queue = list(states)
    followed = 0
    while queue and followed < 12:
        job_id = queue.pop(0)
        followed += 1
        target = states[job_id].get("resumed_by")
        if not target:
            continue
        if not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", target):
            warnings.append(f"任务 {job_id} 的 resumed_by 无效，未读取目标")
            continue
        if target not in states:
            source = {"id": f"rvc.chain.{target}.state", "root": "applio", "path": f"workflows/jobs/{target}/state.json", "format": "json",
                      "fields": {"recorded_status": "/status", "source_recorded_at": "/updated_at", "resumed_by": "/resumed_by"}, "optional_fields": ["resumed_by"]}
            obs, doc = _observe(registry, project, source)
            observations.append(obs)
            sources.append(source)
            documents[source["id"]] = doc
            if obs["status"] != "ok" or not isinstance(doc, dict):
                warnings.append(f"续接目标 {target} 无法核实：{obs['error']}")
                continue
            states[target] = doc
            queue.append(target)
            result_source = {"id": f"rvc.chain.{target}.result", "root": "applio", "path": f"workflows/jobs/{target}/result.json", "format": "json",
                             "fields": {"result_status": "/status", "outputs": "/outputs", "finished_at": "/finished_at"}}
            try:
                result_exists = registry.source_path(project, result_source).is_file()
            except (OSError, RegistryError):
                result_exists = False
            if result_exists:
                result_obs, result_doc = _observe(registry, project, result_source)
                observations.append(result_obs)
                sources.append(result_source)
                documents[result_source["id"]] = result_doc
                if isinstance(result_doc, dict):
                    results[target] = result_doc
        if target in states:
            facts.append(f"文件记录：{job_id}（{states[job_id].get('status', '未知')}）→ {target}（{states[target].get('status', '未知')}），已由后续任务接续")
    if queue:
        warnings.append("续接关系超过 12 条，已停止跟踪；请直接选择目标任务")
    for start in states:
        visited = set()
        current = start
        while current in states:
            if current in visited:
                warnings.append(f"状态来源不一致：续接关系存在环（{current}）")
                break
            visited.add(current)
            next_id = states[current].get("resumed_by")
            if not isinstance(next_id, str):
                break
            current = next_id
    terminal = {"completed", "failed", "cancelled", "canceled"}
    for job_id, result in results.items():
        if job_id in states:
            state_status = states[job_id].get("status")
            result_status = result.get("status")
            if result_status in terminal and state_status != result_status:
                warnings.append(f"状态来源不一致：任务 {job_id} state={state_status}，result={result_status}；可能处于写入间隔，请刷新复核")


def collect_project(registry, project_id, project_override=None):
    project = project_override if project_override is not None else registry.get(project_id)
    observations, facts, warnings, entries = [], [], [], []
    sources = [dict(source) for source in project.get("sources", [])]
    documents = {}
    for source in sources:
        observation, document = _observe(registry, project, source)
        observations.append(observation)
        documents[source["id"]] = document
    if "rvc_job_chain" in project.get("adapters", []):
        _rvc_relations(registry, project, sources, observations, documents, warnings, facts)
    source_map = {source["id"]: source for source in sources}
    for observation in observations:
        source = source_map[observation["source_id"]]
        value = observation["value"]
        label = _source_label(source)
        if observation["status"] != "ok":
            warnings.append(f"{label}：{observation['error']}")
            continue
        if observation.get("time_warning"):
            warnings.append(f"{label}：{observation['time_warning']}")
        if "decision_status" in value:
            facts.append(f"登记提案：{value.get('task', '')} / {value.get('slice', '')}；状态 {value['decision_status']}；采纳选项 {_display(value.get('accepted_option'))}；规范补丁已应用：{_display(value.get('patch_applied'))}")
        if "execution_status" in value:
            facts.append(f"{label}：文件记录 {value['execution_status']}" + (f"；未解决条件 {value['unresolved']}" if "unresolved" in value else ""))
        if "protocol_status" in value:
            facts.append(f"协议登记状态：{value['protocol_status']}（与执行完成状态分别解释）")
        if "required_conditions" in value and "completed_conditions" in value:
            facts.append(f"历史审计记录：预定条件 {value['completed_conditions']}/{value['required_conditions']} 完成；不代表科学结论已验证")
        if "recorded_status" in value and not source["id"].endswith("previous_state"):
            facts.append(f"{label}：文件记录 {value['recorded_status']}；当前运行未检查")
        if "restored_epoch" in value:
            facts.append(f"历史恢复验证：第 {value['restored_epoch']} 轮 / {value.get('restored_step', '未知')} 步；优化器恢复 {_display(value.get('optimizer_restored'))}")
        if "percent" in value:
            facts.append(f"保存的工作流进度：{value.get('stage', '未知阶段')}，{value['percent']}%；不是训练 epoch 完成率")
        if source.get("purpose") == "saved_service_entry_not_live_status" or source["id"] == "rvc.server_record":
            warnings.append("实时服务未检查；服务地址和 PID 仅为保存记录")
            url = value.get("url")
            if isinstance(url, str):
                parsed = urlsplit(url)
                if parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password:
                    entries.append({"id": "service-url", "label": "打开保存的服务地址（未探测）", "type": "url", "target": url})
                else:
                    warnings.append("保存的服务 URL 无效，未生成入口")
        outputs = value.get("outputs")
        if value.get('result_status') == 'completed' and not isinstance(outputs, list):
            warnings.append(f'{label}：记录为 completed，但没有可用的 outputs 列表，产物入口未核实')
        if isinstance(outputs, list):
            for index, item in enumerate(outputs[:30]):
                if not isinstance(item, str):
                    warnings.append("结果 outputs 含非路径项，未生成入口")
                    continue
                try:
                    target = registry.resolve(project, "applio", item)
                    entries.append({"id": f"output-{hashlib.sha256(item.encode()).hexdigest()[:12]}", "label": target.name,
                                    "type": "folder" if target.is_dir() else "file", "target": str(target)})
                except (RegistryError, OSError) as exc:
                    warnings.append(f"结果入口被拒绝：{item}；{exc}")
    for entry in project.get("entries", []):
        try:
            kind = entry.get("type")
            if kind == "open_source":
                source = source_map[entry["source_id"]]
                target = registry.source_path(project, source)
                label = entry.get("label") or _source_label(source)
                output_kind = "file"
            elif kind == "open_folder":
                target = registry.resolve(project, entry.get("root", "main"), entry.get("path", "."))
                label = entry.get("label") or target.name
                output_kind = "folder"
            elif kind in ("url", "open_url"):
                target = entry.get("url", entry.get("target", ""))
                parsed = urlsplit(target)
                if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                    raise RegistryError("网页入口仅支持明确登记的 HTTP/HTTPS URL")
                label = entry.get("label") or target
                output_kind = "url"
            else:
                raise RegistryError("不支持的材料入口类型")
            entries.append({"id": entry["id"], "label": label, "type": output_kind, "target": str(target)})
        except (KeyError, RegistryError, ValueError, OSError) as exc:
            warnings.append(f"入口 {entry.get('id', '?')} 不可用：{exc}")
    if "git_readonly" in project.get("adapters", []):
        try:
            git = _git_observation(registry, project)
        except (RegistryError, OSError, ValueError) as exc:
            git = {"source_id": f"{project['id']}.git", "status": "error", "value": {}, "source_ref": project["roots"].get("main", ""), "observed_at": _now(), "source_recorded_at": None, "source_mtime": None, "fingerprint": None, "error": str(exc)}
        observations.append(git)
        if git["status"] == "ok":
            value = git["value"]
            facts.append(f"Git 观察：{value['branch']}；已跟踪变更 {value['tracked_changes']}，未跟踪入口 {value['untracked_entries']}；未执行测试")
        else:
            warnings.append(git["error"])
    if not facts:
        good = sum(obs["status"] == "ok" for obs in observations)
        facts.append(f"已读取 {good}/{len(observations)} 个登记来源；当前工作线由交接记录指定")
    # Deterministic order without duplicate dynamic entries or repeated cycle warnings.
    seen = set()
    entries = [entry for entry in entries if not (entry["id"] in seen or seen.add(entry["id"]))]
    return {"observed_at": _now(), "observations": observations, "facts": list(dict.fromkeys(facts)),
            "warnings": list(dict.fromkeys(warnings)), "entries": entries}
