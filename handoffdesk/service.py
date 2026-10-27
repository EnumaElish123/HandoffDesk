"""Application boundary: observations are read-only; all writes belong to data_dir."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
from urllib.parse import urlparse
import uuid
import webbrowser

from .registry import Registry
from .collectors import collect_project, read_json
from .store import Store, ConflictError
from .node_files import NodeFiles
from . import platform_io
from .paths import app_directory, require_configuration


ORIGINS = {"user_directed": "用户指定", "assistant_suggested": "助手建议", "plan_derived": "计划引用", "agent_recorded": "Agent 记录"}
JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")
WORKLINE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,139}\Z')
SCRIPTS = {'.py','.js','.ts','.ps1','.psm1','.bat','.cmd','.vbs','.sh','.reg','.sql','.ipynb'}
DOCUMENTS = {'.md','.txt','.json','.yaml','.yml','.toml','.csv','.tsv','.log','.html','.htm','.pdf','.png','.jpg','.jpeg','.wav','.mp3','.m4a','.flac','.docx','.pptx','.xlsx','.zip'}
WEIGHTS = {'.pth','.pt','.bin','.safetensors','.ckpt','.index','.onnx'}


class Service:
    def __init__(self, app_dir=None, data_dir=None):
        self.app_dir = app_directory(app_dir)
        self.registry = Registry(require_configuration(self.app_dir))
        self.data_dir = Path(data_dir or self.app_dir / "data").resolve()
        for project in self.registry.projects:
            for root in project["roots"].values():
                if self.data_dir.is_relative_to(Path(root).resolve()):
                    raise ValueError("工具数据目录不能放在已接入的项目目录内。")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for name in ('cards.sqlite3-wal', 'cards.sqlite3-shm', 'cards.sqlite3-journal'):
            self._write_path(name)
        self.store = Store(self._write_path('cards.sqlite3'))
        self.node_files = NodeFiles(self)
        self._focus_lock = threading.RLock()

    def _write_path(self, relative):
        path = (self.data_dir / relative).resolve()
        if not path.is_relative_to(self.data_dir):
            raise ValueError("工具数据路径重定向到了数据目录之外，已停止写入。")
        for project in self.registry.projects:
            if any(path.is_relative_to(Path(root).resolve()) for root in project['roots'].values()):
                raise ValueError("工具数据路径指向业务项目，已停止写入。")
        return path

    def list_projects(self):
        return [{"id": p["id"], "name": p["name"], "aliases": p.get("aliases", [])} for p in self.registry.projects]

    def list_file_nodes(self, project_id, workline_id=None):
        return self.node_files.list_nodes(project_id, workline_id)

    def get_node_files(self, project_id, node_id):
        return self.node_files.get(project_id, node_id)

    def create_file_node(self, project_id, title, workline_id=None, description='', include_current=True):
        return self.node_files.create(project_id, title, workline_id, description, include_current)

    def add_node_files(self, project_id, node_id, paths, purpose=''):
        return self.node_files.add(project_id, node_id, paths, purpose)

    def remove_node_file(self, project_id, node_id, file_id):
        return self.node_files.remove(project_id, node_id, file_id)

    def update_node_file(self, project_id, node_id, file_id, purpose=''):
        return self.node_files.update_file(project_id, node_id, file_id, purpose)

    def relocate_node_file(self, project_id, node_id, file_id, new_path):
        return self.node_files.relocate(project_id, node_id, file_id, new_path)

    def rename_file_node(self, project_id, node_id, title, description=None):
        return self.node_files.rename(project_id, node_id, title, description)

    def open_node_file(self, project_id, node_id, file_id, action='open'):
        return self.node_files.open(project_id, node_id, file_id, action)

    def list_worklines(self, project_id):
        project = self.registry.get(project_id)
        project_id = project['id']
        legacy_focus = (self.store.state(project_id)['settings'] or {}).get('focus_job')
        lines = {'default': {'id':'default','name':'原有工作线','entry_ids':[], 'focus_id':legacy_focus}}
        for item in project.get('worklines', []):
            if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not WORKLINE_ID.fullmatch(item['id']):
                raise ValueError('登记的工作线编号格式无效。')
            if item['id'] == 'default' or item['id'] in lines:
                raise ValueError('登记的工作线编号重复。')
            lines[item['id']] = dict(item, entry_ids=list(item.get('entry_ids', [])))
        for item in self.store.worklines(project_id):
            lines[item['id']] = dict(lines.get(item['id'], {}), **item)
        return list(lines.values())

    def _scope(self, project_id, workline_id=None):
        project_id = self.registry.get(project_id)['id']
        selected = self.store.selected_workline(project_id) if workline_id is None else workline_id
        if not isinstance(selected, str) or not WORKLINE_ID.fullmatch(selected):
            raise ValueError('工作线编号格式无效。')
        line = next((item for item in self.list_worklines(project_id) if item['id'] == selected), None)
        if line is None:
            if workline_id is not None:
                raise ValueError('工作线不存在，请重新选择。')
            selected = 'default'
            line = self.list_worklines(project_id)[0]
        scope = project_id if selected == 'default' else project_id + '\x1f' + selected
        return project_id, scope, dict(line)

    def create_workline(self, project_id, name):
        project_id = self.registry.get(project_id)['id']
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100 or any(ord(char) < 32 for char in name):
            raise ValueError('工作线名称应为 1 至 100 字，不含控制字符。')
        name = name.strip()
        if any(line['name'] == name for line in self.list_worklines(project_id)):
            raise ValueError('已有同名工作线，请选择现有工作线或换一个名称。')
        value = {'id':'line-' + uuid.uuid4().hex[:16], 'name':name, 'entry_ids':[]}
        return self.store.save_workline(project_id, value)

    def select_workline(self, project_id, workline_id):
        project_id, _, line = self._scope(project_id, workline_id)
        self.store.select_workline(project_id, line['id'])
        return self.show(project_id, line['id'])

    def _entry_ids(self, scope, entry_ids, snapshot_id=None):
        if not isinstance(entry_ids, list) or len(entry_ids) > 3 or any(not isinstance(item, str) or len(item)>200 for item in entry_ids):
            raise ValueError('请选择最多 3 份材料。')
        if len(set(entry_ids)) != len(entry_ids):
            raise ValueError('材料入口不可重复。')
        if entry_ids:
            snapshot = self.store.snapshot(snapshot_id, scope) if snapshot_id is not None else self.store.snapshot(self.store.state(scope)['active_snapshot'], scope)
            known = {entry['id'] for entry in (snapshot or {}).get('entries', [])}
            if any(item not in known for item in entry_ids):
                raise ValueError('材料入口不属于这条工作线的登记快照，请刷新或重新选择。')
        return list(entry_ids)

    def set_workline_entries(self, project_id, entry_ids, workline_id=None):
        project_id, scope, line = self._scope(project_id, workline_id)
        selected = self._entry_ids(scope, entry_ids)
        current = self.store.current_handoff(scope)
        return self.store.save_workline(project_id, dict(line, entry_ids=selected,
            entry_override_revision=current.get('revision',0) if current else 0))

    def project_overviews(self):
        result = []
        for project in self.list_projects():
            card = self.show(project['id'])
            drafts = 0
            for line in self.list_worklines(project['id']):
                _, scope, _ = self._scope(project['id'], line['id'])
                drafts += sum(self.store.load_draft(scope, kind) is not None for kind in ('plan','handoff'))
            handoff = card['handoff'] or {}
            result.append(dict(project, workline_name=card['workline']['name'], workline_id=card['workline']['id'],
                               draft_count=drafts, blocker_count=len(handoff.get('blockers', [])),
                               updated_at=handoff.get('recorded_at'), pending_changes_count=len(card['source_changes'])))
        return result

    def preferences(self):
        return self.store.preferences()

    def save_preferences(self, patch):
        if not isinstance(patch, dict) or set(patch) - {'last_project', 'geometry', 'entries', 'auto_refresh', 'reduced_motion'}:
            raise ValueError('界面偏好字段无效。')
        clean = {}
        if 'reduced_motion' in patch:
            if not isinstance(patch['reduced_motion'], bool):
                raise ValueError('减少动态效果设置必须为布尔值。')
            clean['reduced_motion'] = patch['reduced_motion']
        if 'auto_refresh' in patch:
            if not isinstance(patch['auto_refresh'], bool):
                raise ValueError('自动刷新设置必须为布尔值。')
            clean['auto_refresh'] = patch['auto_refresh']
        if 'last_project' in patch:
            clean['last_project'] = self.registry.get(patch['last_project'])['id']
        if 'geometry' in patch:
            geometry = patch['geometry']
            if not isinstance(geometry, str) or not re.fullmatch(r'\d{3,5}x\d{3,5}(?:[+-]\d{1,6}[+-]\d{1,6})?', geometry):
                raise ValueError('窗口尺寸格式无效。')
            width, height = map(int, re.match(r'(\d+)x(\d+)', geometry).groups())
            if not 480 <= width <= 10000 or not 360 <= height <= 10000:
                raise ValueError('窗口尺寸超出支持范围。')
            clean['geometry'] = geometry
        if 'entries' in patch:
            entries = patch['entries']
            if not isinstance(entries, dict) or len(entries) > len(self.registry.projects):
                raise ValueError('材料偏好必须是项目到入口编号的字典。')
            clean['entries'] = {}
            for key, entry_id in entries.items():
                project_id = self.registry.get(key)['id']
                if not isinstance(entry_id, str) or len(entry_id) > 200 or '\x00' in entry_id:
                    raise ValueError('材料入口编号无效。')
                clean['entries'][project_id] = entry_id
        return self.store.save_preferences(clean)

    @staticmethod
    def _revision(value, name='版本号'):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f'{name}必须是非负整数。')
        return value

    def load_draft(self, project_id, event_kind='handoff', workline_id=None):
        _, scope, _ = self._scope(project_id, workline_id)
        if event_kind not in {'plan','handoff'}:
            raise ValueError('未知草稿类型。')
        return self.store.load_draft(scope, event_kind)

    def save_draft(self, project_id, payload, expected_draft_revision, workline_id=None):
        project_id, scope, line = self._scope(project_id, workline_id)
        self._revision(expected_draft_revision, '草稿版本号')
        if not isinstance(payload, dict):
            raise ValueError('草稿必须是一个 JSON 对象。')
        draft_id = payload.get('draft_id')
        try:
            if not isinstance(draft_id, str):
                raise ValueError()
            draft_id = str(uuid.UUID(draft_id))
        except (ValueError, AttributeError) as exc:
            raise ValueError('草稿编号必须是 UUID。') from exc
        base_revision = self._revision(payload.get('base_revision'), '原始记录版本号')
        current = self.store.current_handoff(scope)
        if base_revision > (current['revision'] if current else 0):
            raise ValueError('草稿原始版本不存在。')
        kind = payload.get('event_kind', 'handoff')
        if not isinstance(kind, str) or kind not in {'handoff', 'plan'}:
            raise ValueError('未知草稿类型。')
        clean = {'draft_id':draft_id, 'base_revision':base_revision, 'event_kind':kind}
        for field in ('current_task', 'last_done', 'next_action'):
            value = payload.get(field, '')
            if not isinstance(value, str) or len(value) > 12000:
                raise ValueError(f'{field} 必须是 12000 字以内的文字。')
            clean[field] = value
        blockers = payload.get('blockers', [])
        if isinstance(blockers, str):
            blockers = blockers.splitlines()
        if not isinstance(blockers, list) or len(blockers) > 100 or any(not isinstance(item, str) or len(item) > 2000 for item in blockers):
            raise ValueError('草稿阻塞项必须是文字列表，每条不超过 2000 字。')
        clean['blockers'] = blockers
        origin = payload.get('origin', 'user_directed')
        if not isinstance(origin, str) or origin not in ORIGINS:
            raise ValueError('未知草稿来源。')
        clean['origin'] = origin
        sid = payload.get('source_snapshot_id')
        if sid is not None and (not isinstance(sid, int) or isinstance(sid, bool) or not self.store.snapshot(sid, scope)):
            raise ValueError('草稿来源快照不存在，或属于另一条工作线。')
        clean['source_snapshot_id'] = sid
        if 'entry_ids' in payload:
            clean['entry_ids'] = self._entry_ids(scope, payload['entry_ids'], sid)
        return self.store.save_draft(scope, clean, expected_draft_revision)

    def delete_draft(self, project_id, draft_id, expected_revision, workline_id=None, event_kind=None):
        _, scope, _ = self._scope(project_id, workline_id)
        self._revision(expected_revision, '草稿版本号')
        return self.store.delete_draft(scope, draft_id, expected_revision, event_kind)

    @staticmethod
    def _brief(value):
        if isinstance(value, (dict, list)):
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
            return value if len(encoded) <= 240 else encoded[:240] + '…'
        return value[:240] + '…' if isinstance(value, str) and len(value) > 240 else value

    def _source_changes(self, project, before, after):
        if after.get('error') and not after.get('observations'):
            return [{'source_id':'__refresh__','label':'来源刷新','path':'','status':'unavailable',
                     'fields':[{'field':'读取错误','before':None,'after':self._brief(after['error'])}]}]
        labels = {source['id']:source.get('label') or source.get('path', source['id']).replace('\\','/').split('/')[-1]
                  for source in project.get('sources', [])}
        previous = {o['source_id']:o for o in before.get('observations', [])}
        current = {o['source_id']:o for o in after.get('observations', [])}
        changes = []
        missing = {'missing':True}
        for source_id in sorted(previous.keys() | current.keys())[:200]:
            old, new = previous.get(source_id), current.get(source_id)
            fields = []
            status = None
            if new is None:
                status = 'removed'
            elif new.get('status') != 'ok':
                status = 'unavailable'
                fields.append({'field':'读取状态', 'before':old.get('status') if old else missing,
                               'after':self._brief(new.get('error') or '无法读取')})
            elif old is None:
                status = 'added'
            elif old.get('status') != 'ok' or old.get('fingerprint') != new.get('fingerprint') or old.get('source_ref') != new.get('source_ref'):
                status = 'changed'
                if old.get('source_ref') != new.get('source_ref'):
                    fields.append({'field':'来源路径','before':old.get('source_ref'),'after':new.get('source_ref')})
                if old.get('status') != 'ok':
                    fields.append({'field':'读取状态','before':old.get('status'),'after':'ok'})
                evidence = old if old.get('status') == 'ok' else old.get('last_success')
                if evidence and evidence.get('source_ref') != new.get('source_ref'):
                    evidence = None
                old_value, new_value = evidence.get('value', {}) if evidence else {}, new.get('value', {})
                if evidence and isinstance(old_value, dict) and isinstance(new_value, dict):
                    text_source = 'excerpt' in old_value or 'excerpt' in new_value
                    if text_source:
                        if evidence.get('fingerprint') != new.get('fingerprint'):
                            fields.append({'field':'内容','before':'原有文件内容','after':'文件内容已变化，请打开来源查看'})
                    else:
                        for key in sorted(old_value.keys() | new_value.keys()):
                            if str(key).startswith('_'):
                                continue
                            old_field, new_field = old_value.get(key, missing), new_value.get(key, missing)
                            if (key in old_value) != (key in new_value) or type(old_field) != type(new_field) or old_field != new_field:
                                fields.append({'field':key,'before':self._brief(old_field),'after':self._brief(new_field)})
                            if len(fields) >= 20:
                                break
            if status:
                source = new or old
                changes.append({'source_id':source_id, 'label':labels.get(source_id, source_id),
                                'path':source.get('source_ref', ''), 'status':status, 'fields':fields,
                                'before_fingerprint':old.get('fingerprint') if old else None,
                                'after_fingerprint':new.get('fingerprint') if new else None})
        for key, label in (('focus_job','关注任务'), ('registry_fingerprint','接入配置')):
            if before.get(key) != after.get(key):
                changes.append({'source_id':'__'+key+'__','label':label,'path':'','status':'changed',
                                'fields':[{'field':label,'before':before.get(key),'after':after.get(key)}]})
        return changes

    def _focus(self, project_id, workline_id=None):
        return self._scope(project_id, workline_id)[2].get('focus_id')

    def show(self, project_id, workline_id=None):
        project = copy.deepcopy(self.registry.get(project_id))
        project_id, scope, line = self._scope(project_id, workline_id)
        state = self.store.state(scope)
        snapshot = self.store.snapshot(state["active_snapshot"], scope)
        handoff = self.store.current_handoff(scope)
        last_completion = next((entry for entry in self.store.history(scope)
                                if entry.get('event_kind', 'handoff') == 'handoff' and entry.get('last_done')), None)
        warnings = list(snapshot.get("warnings", [])) if snapshot else ["尚无读取记录，请点击刷新记录。"]
        if project_id == "rvc":
            focus = line.get('focus_id')
            project['focus_job'] = focus
            project["focus_label"] = line['name'] if focus else "未指定（当前展示已登记的历史续训示例）"
        attempt = state["last_attempt"]
        if attempt and (attempt.get("errors") or attempt.get("message")):
            names = "、".join(attempt.get("errors", []))
            warnings.append(f"最近读取存在问题（{attempt['observed_at']}）：{names or attempt.get('message')}。请在来源页核对。")
            if not attempt.get("usable") and snapshot:
                warnings.append("本次读取失败，仍显示上一次有效快照；它不代表当前状态。")
        if snapshot and snapshot.get("registry_fingerprint") != self.registry.fingerprint:
            warnings.append("接入配置已变化，请重新刷新记录。")
        revision = handoff.get('revision', 0) if handoff else 0
        review = next((r for r in self.store.review_history(scope) if r['handoff_revision'] == revision), None)
        reviewed = self.store.snapshot(review['snapshot_id'], scope) if review else None
        if reviewed and reviewed.get('registry_fingerprint') != self.registry.fingerprint:
            review, reviewed = None, None
        if handoff:
            bound = self.store.snapshot(handoff.get("source_snapshot_id"), scope)
            if not bound:
                warnings.append("这条交接没有绑定来源快照；完成项和下一步是记录者填写的内容。")
            elif snapshot:
                effective_bound = reviewed or bound
                old_fp = {o["source_id"]: o.get("fingerprint") for o in effective_bound.get("observations", [])}
                current_fp = {o["source_id"]: o.get("fingerprint") for o in snapshot.get("observations", [])}
                if old_fp != current_fp or effective_bound.get("focus_job") != snapshot.get("focus_job") or effective_bound.get("registry_fingerprint") != snapshot.get("registry_fingerprint"):
                    warnings.append("交接依据已发生变化，请复核下一步；原交接已保留。")
            elif project_id == "rvc" and bound.get("focus_job") != line.get('focus_id'):
                warnings.append("关注任务已改变；已有交接仍对应原任务，请刷新后复核。")
        latest_attempt_snapshot = self.store.snapshot(attempt['snapshot_id'], scope) if attempt and not attempt.get('usable') else None
        comparison = None
        baseline = reviewed or (self.store.snapshot(handoff.get('source_snapshot_id'), scope) if handoff else None)
        observed = latest_attempt_snapshot or snapshot
        if baseline:
            comparison = 'review' if reviewed else 'handoff'
        elif observed and not handoff:
            baseline = self.store.snapshot(observed.get('previous_snapshot_id'), scope)
            comparison = 'previous_refresh' if baseline else None
        changes = self._source_changes(project, baseline, observed) if baseline and observed else []
        entries = [self._material_entry(entry) for entry in (snapshot or {}).get('entries', [])]
        known = {entry['id'] for entry in entries}
        # Preset preferences may refer to a source that is temporarily missing.
        # Keep the preference record intact and expose availability separately.
        explicit_binding = line.get('entry_override_revision') == revision
        if explicit_binding:
            declared = list(line.get('entry_ids', []))
        elif handoff is not None and 'entry_ids' in handoff:
            declared = list(handoff['entry_ids'])
        else:
            declared = list(line['entry_ids']) if line.get('entry_ids') else None
        if declared is None and line['id'] == 'default':
            legacy = (self.preferences().get('entries') or {}).get(project_id)
            declared = [legacy] if legacy in known else None
        if declared is None:
            declared = [entries[0]['id']] if entries else []
        selected = [eid for eid in declared if eid in known]
        missing_entries = [eid for eid in declared if eid not in known]
        if missing_entries:
            warnings.append('下一步绑定的部分材料当前不可用，请刷新并重新选择材料。')
        return {"project": project, "handoff": handoff, "snapshot": snapshot,
                "warnings": list(dict.fromkeys(warnings)), "last_attempt": attempt,
                "latest_attempt_snapshot": latest_attempt_snapshot, 'last_completion': last_completion,
                'source_changes': changes, 'change_comparison': comparison,
                'workline':line, 'worklines':self.list_worklines(project_id), 'entries':entries,
                'selected_entry_ids':selected, 'bound_entry_ids':selected, 'missing_entry_ids':missing_entries,
                'drafts':{kind:self.store.load_draft(scope, kind) for kind in ('plan','handoff')},
                'review':review, 'pending_changes_count':len(changes),
                'context_summary':self._context_summary(project_id, snapshot, line)}

    def refresh(self, project_id, workline_id=None):
        project_id, scope, line = self._scope(project_id, workline_id)
        with self._focus_lock:
            generation = self.store.begin_refresh(scope)
            focus = line.get('focus_id')
            previous = self.show(project_id, line['id'])["snapshot"]
        try:
            project = self._focused_project(project_id, focus)
            collected = collect_project(self.registry, project_id, project_override=project)
            collected["registry_fingerprint"] = self.registry.fingerprint
            collected["focus_job"] = focus
            collected['previous_snapshot_id'] = previous['id'] if previous else None
            good = any(o.get("status") == "ok" for o in collected.get("observations", []))
            if previous and previous.get("focus_job") == focus and previous.get('registry_fingerprint') == self.registry.fingerprint:
                old = {o["source_id"]: o for o in previous.get("observations", [])}
                for observation in collected.get("observations", []):
                    prior = old.get(observation["source_id"])
                    if observation.get("status") != "ok" and prior and observation.get('source_ref') == prior.get('source_ref'):
                        cached = prior if prior.get("status") == "ok" else prior.get("last_success")
                        if cached:
                            observation["last_success"] = cached
                            collected.setdefault("warnings", []).append(f"{observation['source_id']} 本次失败；来源页保留上次成功读取值及时间。")
            self.store.finish_refresh(scope, generation, collected, usable=good)
        except Exception as exc:
            self.store.finish_refresh(scope, generation, {"error": str(exc), "observations": [],
                                      "warnings": [str(exc)], "focus_job": focus,
                                      'registry_fingerprint': self.registry.fingerprint,
                                      'previous_snapshot_id': previous['id'] if previous else None}, usable=False)
        return self.show(project_id, line['id'])

    def save_handoff(self, project_id, payload, expected_revision, operation_id=None, workline_id=None):
        project_id, scope, line = self._scope(project_id, workline_id)
        if not isinstance(payload, dict):
            raise ValueError("交接必须是一个 JSON 对象。")
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 0:
            raise ValueError("请提供当前交接版本号；首次保存使用 0。")
        kind = payload.get('event_kind', 'handoff')
        if not isinstance(kind, str) or kind not in {'handoff', 'plan'}:
            raise ValueError('未知记录类型。')
        result = {'event_kind': kind}
        for key in ("current_task", "last_done", "next_action"):
            value = payload.get(key, "")
            if not isinstance(value, str) or len(value) > 12000:
                raise ValueError(f"{key} 必须是 12000 字以内的文字。")
            result[key] = value.strip()
        if not result["next_action"] or (kind == 'handoff' and not result['last_done']):
            raise ValueError("请填写本次完成和下一步。")
        if kind == 'plan':
            result['last_done'] = ''
        blockers = payload.get("blockers", [])
        if isinstance(blockers, str):
            blockers = [line.strip() for line in blockers.splitlines() if line.strip()]
        if not isinstance(blockers, list) or len(blockers) > 100 or any(not isinstance(x, str) or len(x) > 2000 for x in blockers):
            raise ValueError("阻塞项必须是文字列表，每条不超过 2000 字。")
        result["blockers"] = blockers
        origin = payload.get("origin", "assistant_suggested")
        if not isinstance(origin, str) or origin not in ORIGINS:
            raise ValueError("未知交接来源。")
        result["origin"] = origin
        sid = payload.get("source_snapshot_id")
        if sid is not None and (not isinstance(sid, int) or isinstance(sid, bool) or not self.store.snapshot(sid, scope)):
            raise ValueError("来源快照不存在，或属于另一条工作线。")
        result["source_snapshot_id"] = sid
        if 'entry_ids' in payload:
            result['entry_ids'] = self._entry_ids(scope, payload['entry_ids'], sid)
        if payload.get("restored_from") is not None:
            result["restored_from"] = payload["restored_from"]
        if operation_id is not None and (not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 200):
            raise ValueError("操作编号无效。")
        result['file_manifest'] = self.node_files.manifest_for_record(
            project_id, scope, line, sid, result.get('restored_from'))
        if result.get('restored_from') is not None:
            original = next((record for record in self.store.history(scope)
                             if record['revision'] == result['restored_from']), None)
            if original:
                for key in ('actor', 'verification', 'agent_protocol_version'):
                    if key in original:
                        result[key] = copy.deepcopy(original[key])
        return self.store.save_handoff(scope, result, expected_revision, operation_id)

    def save_plan(self, project_id, payload, expected_revision, operation_id=None, workline_id=None):
        if not isinstance(payload, dict):
            raise ValueError('计划必须是一个 JSON 对象。')
        return self.save_handoff(project_id, dict(payload, event_kind='plan', last_done=''), expected_revision, operation_id, workline_id)

    def history(self, project_id, workline_id=None):
        _, scope, _ = self._scope(project_id, workline_id)
        return self.store.history(scope)

    def restore_handoff(self, project_id, revision, expected_revision, workline_id=None):
        old = next((h for h in self.history(project_id, workline_id) if h["revision"] == revision), None)
        if not old:
            raise ValueError("找不到这条历史交接。")
        payload = {k: old[k] for k in ("current_task", "last_done", "next_action", "blockers", "origin", "source_snapshot_id")}
        payload["restored_from"] = revision
        payload['event_kind'] = old.get('event_kind', 'handoff')
        if 'entry_ids' in old:
            payload['entry_ids'] = old['entry_ids']
        return self.save_handoff(project_id, payload, expected_revision, workline_id=workline_id)

    def summary(self, project_id, workline_id=None):
        card = self.show(project_id, workline_id)
        handoff, snap = card["handoff"], card["snapshot"]
        lines = [f"# {card['project']['name']} · HandoffDesk", "", f"工作线：{card['workline']['name']}", "这份摘要来自本地缓存；文件记录不代表进程正在运行。", ""]
        if handoff:
            completion = card.get('last_completion')
            completion_text = completion['last_done'] if completion else '尚无完成记录'
            kind_label = '起步计划' if handoff.get('event_kind') == 'plan' else '交接'
            if handoff.get('current_task'):
                lines += [f"本次主题补充：{handoff['current_task']}"]
            lines += [f"上次完成：{completion_text}",
                      f"下一步：{handoff['next_action']}", f"阻塞项：{'；'.join(handoff['blockers']) or '未填写'}",
                      f"{kind_label}：第 {handoff['revision']} 版，{ORIGINS[handoff['origin']]}，{handoff['recorded_at']}",
                      f"依据快照：{handoff.get('source_snapshot_id') or '未绑定'}", ""]
        else:
            lines += ["尚未留下交接；下一步未指定。", ""]
        if snap:
            lines += [f"来源读取：{snap['observed_at']}（快照 {snap['id']}）", ""]
            lines += ["- " + fact for fact in snap.get("facts", [])]
            lines += ["", "材料入口："]
            lines += [f"- {e['label']}：{e['target']}" for e in snap.get("entries", [])]
        if card["warnings"]:
            lines += ["", "需要留意："] + ["- " + w for w in card["warnings"]]
        return "\n".join(lines) + "\n"

    def export(self, project_id, mode="local", workline_id=None):
        project_id = self.registry.get(project_id)['id']
        if mode != "local":
            raise ValueError("此版本只支持本地完整导出，分享前请自行核对内容。")
        folder = self._write_path('exports')
        folder.mkdir(exist_ok=True)
        path = self._write_path(f"exports/{project_id}-{uuid.uuid4().hex[:12]}.md")
        path.write_text(self.summary(project_id, workline_id), encoding="utf-8")
        return path

    def acknowledge_changes(self, project_id, expected_revision, snapshot_id, workline_id=None):
        _, scope, line = self._scope(project_id, workline_id)
        self._revision(expected_revision)
        self._revision(snapshot_id, '来源快照编号')
        snapshot = self.store.snapshot(snapshot_id, scope)
        if not snapshot or snapshot.get('registry_fingerprint') != self.registry.fingerprint:
            raise ValueError('请先读取当前工作线的最新来源，再确认复核。')
        if snapshot.get('focus_job') != line.get('focus_id'):
            raise ValueError('来源与当前工作线任务不一致，请重新刷新。')
        return self.store.save_review(scope, expected_revision, snapshot_id)

    def review_history(self, project_id, workline_id=None):
        _, scope, _ = self._scope(project_id, workline_id)
        return self.store.review_history(scope)

    @staticmethod
    def _context_summary(project_id, snapshot, line):
        if not snapshot:
            return '选择工作线，查看关键材料，再留下第一步。'
        if project_id != 'rvc':
            return ''
        statuses = {'completed':'已完成','paused':'已暂停','failed':'失败','running':'记录为运行中','cancelled':'已取消'}
        observations = snapshot.get('observations', [])
        selected = next((o.get('value', {}) for o in observations if o.get('source_id') == 'rvc.selected_state' and o.get('status') == 'ok'), {})
        status = selected.get('recorded_status')
        if not status:
            return '历史续训示例；选择一个任务后，可单独保存它的下一步。当前运行状态未检查。'
        text = '历史记录：' + statuses.get(status, str(status))
        if selected.get('resumed_by'):
            successors = [o.get('value', {}).get('recorded_status') for o in observations
                          if '.chain.' in o.get('source_id', '') and o.get('status') == 'ok']
            text += '；已由后续任务接续'
            if successors:
                text += '（' + statuses.get(successors[-1], str(successors[-1])) + '）'
        return text + '。当前运行状态未检查。'

    @staticmethod
    def _material_entry(entry):
        result = dict(entry)
        target = str(entry.get('target', ''))
        suffix = Path(target).suffix.lower()
        kind, action = '文档', 'open'
        if entry.get('type') == 'url':
            kind = '本机服务'
        elif entry.get('type') in {'folder','directory','open_folder'}:
            kind = '文件夹'
        elif suffix in WEIGHTS:
            kind, action = '模型索引' if suffix == '.index' else '模型权重', 'folder'
        elif suffix in SCRIPTS:
            kind = '脚本文本'
        elif suffix == '.zip':
            kind = '打包产物'
        elif suffix in {'.wav','.mp3','.m4a','.flac'}:
            kind = '音频'
        elif suffix not in DOCUMENTS:
            kind, action = '其他文件', 'folder'
        if str(entry.get('id', '')).startswith('output-'):
            result['label'] = kind + ' · ' + Path(target).name
        result.update(kind=kind, default_action=action,
                      action_label='打开所在文件夹' if action == 'folder' else '查看脚本文本' if kind == '脚本文本' else '打开')
        return result

    def _open_material(self, project, entry, action):
        if action not in {'open','folder','copy'}:
            raise ValueError('材料操作必须为 open、folder 或 copy。')
        if entry.get('type') == 'url':
            url = urlparse(entry['target'])
            if url.scheme not in {'http','https'} or url.hostname not in {'localhost','127.0.0.1','::1'} or url.username or url.password:
                raise ValueError('此版本仅打开本机服务地址。')
            if action == 'copy':
                return entry['target']
            if action == 'folder':
                raise ValueError('服务地址没有所在文件夹。')
            platform_io.ensure_desktop()
            webbrowser.open(entry['target'])
            return entry['target']
        target = Path(entry['target']).resolve()
        roots = [Path(root).resolve() for root in project['roots'].values()]
        if not any(target.is_relative_to(root) for root in roots):
            raise ValueError('材料路径超出登记的项目目录。')
        if action == 'copy':
            return str(target)
        if action == 'folder':
            folder = target if target.is_dir() else target.parent
            while not folder.is_dir():
                parent = folder.parent
                if parent == folder or not any(parent.is_relative_to(root) for root in roots):
                    raise ValueError('材料所在目录不存在。')
                folder = parent
            if not any(folder.is_relative_to(root) for root in roots):
                raise ValueError('材料目录超出登记范围。')
            platform_io.open_document(folder)
            return str(folder)
        if not target.exists():
            raise ValueError('材料当前不存在；可打开所在文件夹或复制路径。')
        if target.is_dir():
            platform_io.open_document(target)
        elif target.suffix.lower() in SCRIPTS:
            platform_io.open_text(target)
        elif target.suffix.lower() in DOCUMENTS:
            platform_io.open_document(target)
        else:
            raise ValueError('此类型不直接打开，请使用“打开所在文件夹”。')
        return str(target)

    def open_entry(self, project_id, entry_id, action=None, workline_id=None):
        card = self.show(project_id, workline_id)
        snap = card["snapshot"]
        if not snap or snap.get("registry_fingerprint") != self.registry.fingerprint:
            raise ValueError("请先刷新记录，再打开材料。")
        entry = next((e for e in card['entries'] if e["id"] == entry_id), None)
        if not entry:
            raise ValueError("材料入口未登记。")
        return self._open_material(card['project'], entry, action or entry['default_action'])

    def open_source(self, project_id, source_id, action='open', workline_id=None):
        project_id, scope, line = self._scope(project_id, workline_id)
        card = self.show(project_id, line['id'])
        candidates = [card.get('latest_attempt_snapshot'), card.get('snapshot')]
        observed = card.get('latest_attempt_snapshot') or card.get('snapshot')
        if observed:
            candidates.append(self.store.snapshot(observed.get('previous_snapshot_id'), scope))
        if card.get('handoff'):
            candidates.append(self.store.snapshot(card['handoff'].get('source_snapshot_id'), scope))
        if card.get('review'):
            candidates.append(self.store.snapshot(card['review']['snapshot_id'], scope))
        for snapshot in candidates:
            observation = next((o for o in (snapshot or {}).get('observations', []) if o.get('source_id') == source_id), None)
            if observation and observation.get('source_ref'):
                return self._open_material(card['project'], {'type':'file','target':observation['source_ref']}, action)
        raise ValueError('该来源没有可打开的登记文件；请先刷新或选择具体来源。')

    def focus_options(self, project_id):
        project = self.registry.get(project_id)
        project_id = project['id']
        if project_id != "rvc":
            return []
        folder = self.registry.resolve(project, "applio", "workflows/jobs")
        options = [{"id": "", "label": "未指定（查看已登记的历史续训示例）", 'kind': '', 'voice_name':'', 'status':'', 'recorded_at':None}]
        # Bounded, one-level enumeration. Never inspect media or model contents.
        names = []
        with os.scandir(folder) as entries:
            for i, entry in enumerate(entries):
                if i >= 2000:
                    raise ValueError("任务目录超过 2000 项，请用命令行指定关注任务编号。")
                if entry.is_dir(follow_symlinks=False) and JOB_ID.fullmatch(entry.name):
                    names.append(entry.name)
        for name in sorted(names, reverse=True)[:200]:
            try:
                info = read_json(self.registry.resolve(project, "applio", f"workflows/jobs/{name}/job.json"))
                if not isinstance(info, dict):
                    raise ValueError('任务元数据必须是对象')
                try:
                    state = read_json(self.registry.resolve(project, 'applio', f'workflows/jobs/{name}/state.json'))
                    state = state if isinstance(state, dict) else {}
                except (OSError, ValueError):
                    state = {}
                kind, voice_name = str(info.get('kind', '未知类型')), str(info.get('voice_name', ''))
                options.append({"id": name, "label": f"{name} · {kind} · {voice_name}",
                                'kind':kind, 'voice_name':voice_name, 'status':str(state.get('status', '未读取')),
                                'recorded_at':state.get('updated_at') or info.get('created_at')})
            except (OSError, ValueError):
                options.append({"id": name, "label": f"{name} · 元数据无法读取", 'kind':'未知', 'voice_name':'', 'status':'无法读取', 'recorded_at':None})
        return options

    def set_focus(self, project_id, focus_id):
        project = self.registry.get(project_id)
        project_id = project['id']
        if project_id != "rvc":
            raise ValueError("任务选择当前仅用于 RVC；其他项目请在交接中填写当前工作线。")
        if focus_id is not None and not isinstance(focus_id, str):
            raise ValueError('任务编号格式无效。')
        if focus_id:
            if not isinstance(focus_id, str) or not JOB_ID.fullmatch(focus_id):
                raise ValueError("任务编号格式无效。")
            path = self.registry.resolve(project, "applio", f"workflows/jobs/{focus_id}/job.json")
            if not path.is_file():
                raise ValueError("所选任务的 job.json 不存在。")
            metadata = read_json(path)
            if not isinstance(metadata, dict):
                raise ValueError('任务元数据必须为对象。')
            kind = str(metadata.get('kind', '任务'))
            kind_name = {'train':'训练','infer':'换声','inference':'换声','prepare':'素材筛选'}.get(kind, kind)
            voice = str(metadata.get('voice_name') or '未命名音色')
            stamp = str(metadata.get('created_at') or '')[:16].replace('T',' ')
            if len(stamp) <= 10 and re.match(r'^\d{8}_\d{6}', focus_id):
                stamp = focus_id[:4] + '-' + focus_id[4:6] + '-' + focus_id[6:8] + ' ' + focus_id[9:11] + ':' + focus_id[11:13]
            elif not stamp and re.match(r'^\d{8}', focus_id):
                stamp = focus_id[:4] + '-' + focus_id[4:6] + '-' + focus_id[6:8]
            line_id = 'job-' + focus_id
            value = {'id':line_id, 'name':f'{voice} · {kind_name}' + (f' · {stamp}' if stamp else '') + f' · {focus_id[-6:]}',
                     'focus_id':focus_id, 'voice_name':voice, 'kind':kind}
        else:
            legacy = self.list_worklines(project_id)[0]
            if not legacy.get('focus_id'):
                return self.select_workline(project_id, 'default')
            line_id = 'rvc-history'
            value = {'id':line_id,'name':'历史续训示例','focus_id':None}
        with self._focus_lock:
            self.store.save_workline(project_id, value)
            self.store.select_workline(project_id, line_id)
        return self.show(project_id, line_id)

    def _focused_project(self, project_id, focus):
        project = copy.deepcopy(self.registry.get(project_id))
        if project_id != "rvc" or not focus:
            return project
        if not JOB_ID.fullmatch(focus):
            raise ValueError("保存的关注任务编号无效。")
        base = f"workflows/jobs/{focus}/"
        sources = [{"id": "rvc.selected_job", "root": "applio", "path": base + "job.json", "format": "json",
                    "fields": {"kind": "/kind", "created_at": "/created_at"}},
                   {"id": "rvc.selected_state", "root": "applio", "path": base + "state.json", "format": "json",
                    "fields": {"recorded_status": "/status", "source_recorded_at": "/updated_at", "resumed_by": "/resumed_by"},
                    "optional_fields": ["resumed_by"]}]
        metadata = read_json(self.registry.resolve(project, 'applio', base + 'job.json'))
        if not isinstance(metadata, dict):
            raise ValueError('所选任务的 job.json 必须是对象。')
        resume = metadata.get('resume')
        predecessor = resume.get('source_job_id') if isinstance(resume, dict) else None
        if isinstance(predecessor, str) and JOB_ID.fullmatch(predecessor) and predecessor != focus:
            sources.append({'id': 'rvc.previous_state', 'root': 'applio',
                            'path': f'workflows/jobs/{predecessor}/state.json', 'format': 'json',
                            'fields': {'recorded_status': '/status', 'resumed_by': '/resumed_by', 'source_recorded_at': '/updated_at'},
                            'optional_fields': ['resumed_by']})
        optional = [
            ("selected_result", "result.json", {"result_status": "/status", "outputs": "/outputs", "finished_at": "/finished_at"}),
            ("progress", "progress.json", {"stage": "/stage", "percent": "/percent", "source_recorded_at": "/updated_at"}),
            ("restore_record", "resume_validation.json", {"restored_epoch": "/restored_epoch", "restored_step": "/restored_global_step", "optimizer_restored": "/optimizer_restored_by_trainer", "source_recorded_at": "/confirmed_at"})]
        for sid, filename, fields in optional:
            if self.registry.resolve(project, "applio", base + filename).is_file():
                sources.append({"id": "rvc." + sid, "root": "applio", "path": base + filename, "format": "json", "fields": fields,
                                "optional_fields": ['outputs'] if sid == 'selected_result' else []})
        sources += [s for s in project["sources"] if s["id"] == "rvc.server_record"]
        project["sources"] = sources
        project["entries"] = [{"id": "job", "label": "所选任务配置", "type": "open_source", "source_id": "rvc.selected_job"},
                              {"id": "state", "label": "所选任务状态记录", "type": "open_source", "source_id": "rvc.selected_state"},
                              {"id": "job-folder", "label": "所选任务目录", "type": "open_folder", "root": "applio", "path": base}]
        if any(s["id"] == "rvc.selected_result" for s in sources):
            project["entries"].append({"id": "result", "label": "所选任务结果", "type": "open_source", "source_id": "rvc.selected_result"})
        project["focus_job"] = focus
        project.pop("examples", None)
        return project
