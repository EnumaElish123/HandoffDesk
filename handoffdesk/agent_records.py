"""Versioned, non-interactive agent submissions with an atomic file manifest."""
from __future__ import annotations

import hashlib
import json

from .node_files import MAX_FILES, file_id, file_metadata, local_path, path_key
from .store import ConflictError


MAX_PAYLOAD_BYTES = 1024 * 1024
VERIFICATION_STATES = ('passed', 'partial', 'not_run', 'failed')


class AgentAttachmentError(ValueError):
    """Locate the failed submitted item without changing retry semantics."""
    def __init__(self, code, index, input_path, resolved_path, reason):
        self.code = code
        self.details = {'file_index': index, 'input_path': input_path,
                        'resolved_path': resolved_path, 'reason': reason}
        super().__init__(f'files[{index}]（{input_path}）：{reason}')


def _object(value, allowed, required, label):
    if not isinstance(value, dict):
        raise ValueError(f'{label} 必须是 JSON 对象。')
    unknown = set(value) - set(allowed)
    missing = set(required) - set(value)
    if unknown:
        raise ValueError(f'{label} 含未知字段：' + ', '.join(sorted(map(str, unknown))))
    if missing:
        raise ValueError(f'{label} 缺少字段：' + ', '.join(sorted(missing)))
    return value


def _text(value, label, maximum, *, required=False, single_line=False):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise ValueError(f'{label} 必须是 {maximum} 字以内的文字。')
    value = value.strip()
    if required and not value:
        raise ValueError(f'{label} 不能为空。')
    if single_line and any(ord(char) < 32 for char in value):
        raise ValueError(f'{label} 不能包含换行或控制字符。')
    return value


class AgentRecords:
    """Use an explicit project/workline; never change the human's selection."""

    def __init__(self, service):
        self.service = service

    @staticmethod
    def schema():
        """Return a discoverable schema without a database or project registry."""
        text = lambda maximum, minimum=0: {'type': 'string', 'minLength': minimum, 'maxLength': maximum}
        record = {
            'type': 'object', 'additionalProperties': False,
            'required': ['title', 'next_action'],
            'properties': {
                'title': text(120, 1), 'last_done': text(12000), 'next_action': text(12000, 1),
                'event_kind': {'enum': ['handoff', 'plan'], 'default': 'handoff'},
                'blockers': {'type': 'array', 'maxItems': 100, 'items': text(2000, 1)},
            },
            'allOf': [{
                'if': {'properties': {'event_kind': {'const': 'plan'}}, 'required': ['event_kind']},
                'then': {'properties': {'last_done': {'const': ''}}},
                'else': {'required': ['last_done'], 'properties': {'last_done': text(12000, 1)}},
            }],
        }
        return {
            '$schema': 'https://json-schema.org/draft/2020-12/schema',
            'title': 'HandoffDesk Agent 记录协议 1',
            'description': '一次保存进度与本次文件清单。verification 是操作者报告的验证情况；文件清单不备份内容。输入最多 1 MiB。',
            'type': 'object', 'additionalProperties': False,
            'required': ['schema_version', 'project', 'workline', 'operation_id', 'expected_revision',
                         'record', 'actor', 'verification', 'files'],
            'properties': {
                'schema_version': {'const': 1}, 'project': text(200, 1), 'workline': text(140, 1),
                'operation_id': text(200, 1), 'expected_revision': {'type': 'integer', 'minimum': 0},
                'record': record,
                'actor': {'type': 'object', 'additionalProperties': False, 'required': ['name'],
                          'properties': {'name': text(100, 1), 'session_id': text(200, 1)}},
                'verification': {'type': 'object', 'additionalProperties': False,
                                 'required': ['status', 'summary'],
                                 'properties': {'status': {'enum': list(VERIFICATION_STATES)},
                                                'summary': text(12000, 1)}},
                'files': {'type': 'array', 'maxItems': MAX_FILES,
                          'items': {'type': 'object', 'additionalProperties': False,
                                    'required': ['path', 'purpose'],
                                    'properties': {'path': text(32760, 1), 'purpose': text(2000),
                                                   'role': text(100)}}},
                'source_snapshot_id': {'type': ['integer', 'null'], 'minimum': 1},
            },
        }

    def _prepare(self, payload):
        allowed = {'schema_version', 'project', 'workline', 'operation_id', 'expected_revision',
                   'record', 'actor', 'verification', 'files', 'source_snapshot_id'}
        _object(payload, allowed, allowed - {'source_snapshot_id'}, '提交')
        try:
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
        except (TypeError, ValueError, UnicodeError) as exc:
            raise ValueError('提交必须是可编码为 UTF-8 的 JSON 数据。') from exc
        if len(encoded) > MAX_PAYLOAD_BYTES:
            raise ValueError('提交不能超过 1 MiB。')
        if type(payload['schema_version']) is not int or payload['schema_version'] != 1:
            raise ValueError('不支持的 Agent 记录协议版本，schema_version 必须为 1。')
        project = _text(payload['project'], 'project', 200, required=True, single_line=True)
        workline = _text(payload['workline'], 'workline', 140, required=True, single_line=True)
        project, scope, line = self.service._scope(project, workline)
        operation = _text(payload['operation_id'], 'operation_id', 200, required=True, single_line=True)
        revision = payload['expected_revision']
        if type(revision) is not int or revision < 0:
            raise ValueError('expected_revision 必须是非负整数。')
        record = _object(payload['record'], {'title', 'last_done', 'next_action', 'blockers', 'event_kind'},
                         {'title', 'next_action'}, 'record')
        kind = record.get('event_kind', 'handoff')
        if not isinstance(kind, str) or kind not in {'handoff', 'plan'}:
            raise ValueError('record.event_kind 必须为 handoff 或 plan。')
        title = _text(record['title'], 'record.title', 120, required=True, single_line=True)
        done = _text(record.get('last_done', ''), 'record.last_done', 12000, required=kind == 'handoff')
        if kind == 'plan' and done:
            raise ValueError('计划不能带有完成项；请使用 handoff 记录实际完成。')
        next_action = _text(record['next_action'], 'record.next_action', 12000, required=True)
        blockers = record.get('blockers', [])
        if not isinstance(blockers, list) or len(blockers) > 100:
            raise ValueError('record.blockers 必须是最多 100 项的文字列表。')
        blockers = [_text(item, 'record.blockers', 2000, required=True) for item in blockers]
        actor = _object(payload['actor'], {'name', 'session_id'}, {'name'}, 'actor')
        actor = {'name': _text(actor['name'], 'actor.name', 100, required=True, single_line=True),
                 **({'session_id': _text(actor['session_id'], 'actor.session_id', 200, required=True,
                                        single_line=True)} if 'session_id' in actor else {})}
        verification = _object(payload['verification'], {'status', 'summary'}, {'status', 'summary'}, 'verification')
        if not isinstance(verification['status'], str) or verification['status'] not in VERIFICATION_STATES:
            raise ValueError('verification.status 必须为 passed、partial、not_run 或 failed。')
        verification = {'status': verification['status'],
                        'summary': _text(verification['summary'], 'verification.summary', 12000, required=True)}
        sid = payload.get('source_snapshot_id')
        if sid is not None and (type(sid) is not int or sid < 1 or not self.service.store.snapshot(sid, scope)):
            raise ValueError('source_snapshot_id 不存在，或属于另一条工作线。')
        files = payload['files']
        if not isinstance(files, list) or len(files) > MAX_FILES:
            raise ValueError(f'files 必须是最多 {MAX_FILES} 项的列表。')
        normalized, seen = [], set()
        for index, item in enumerate(files):
            label = f'files[{index}]'
            item = _object(item, {'path', 'purpose', 'role'}, {'path', 'purpose'}, label)
            raw = item['path']
            if not isinstance(raw, str) or not raw or len(raw) > 32760:
                raise ValueError(label + '.path 必须是完整的本机绝对路径。')
            # Keep the submitted location in the digest. Resolving links here
            # would make an identical retry change after a file was moved.
            path = local_path(raw, resolve_links=False)
            key = path_key(path)
            if key in seen:
                raise ValueError(f'{label} 的文件路径重复，请合并用途后提交。')
            seen.add(key)
            normalized.append({'path': str(path), 'purpose': _text(item['purpose'], label + '.purpose', 2000),
                               'role': _text(item.get('role', ''), label + '.role', 100, single_line=True)})
        canonical = {'schema_version': 1, 'project': project, 'workline': line['id'],
                     'record': {'title': title, 'last_done': done, 'next_action': next_action,
                                'blockers': blockers, 'event_kind': kind},
                     'actor': actor, 'verification': verification, 'files': normalized,
                     'source_snapshot_id': sid}
        digest = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True,
                                          separators=(',', ':')).encode('utf-8')).hexdigest()
        stored = {'event_kind': kind, 'current_task': title, 'last_done': done, 'next_action': next_action,
                  'blockers': blockers, 'origin': 'agent_recorded', 'source_snapshot_id': sid,
                  'entry_ids': [], 'actor': actor, 'verification': verification,
                  'agent_protocol_version': 1}
        return {'project': project, 'scope': scope, 'workline': line['id'], 'operation_id': operation,
                'expected_revision': revision, 'digest': digest, 'record': stored, 'files': normalized}

    @staticmethod
    def _manifest(files):
        manifest, seen = [], set()
        for index, item in enumerate(files):
            resolved = item['path']
            try:
                path = local_path(item['path'])
                resolved = str(path)
                if not (path.is_file() or path.is_dir()):
                    raise AgentAttachmentError('attachment_missing', index, item['path'], resolved,
                                               '文件或文件夹不存在，请重新选择。')
            except AgentAttachmentError:
                raise
            except (ValueError, OSError) as exc:
                raise AgentAttachmentError('attachment_unreadable', index, item['path'], resolved, str(exc)) from exc
            key = path_key(path)
            if key in seen:
                raise AgentAttachmentError('attachment_invalid', index, item['path'], resolved,
                                           '多个附件解析为同一个文件，请合并用途后提交。')
            seen.add(key)
            manifest.append(dict(item, path=str(path), origin='manual', is_directory=path.is_dir(),
                                 recorded_metadata=file_metadata(path)))
        return manifest

    def validate(self, payload):
        """Validate the current inputs, without saving any progress or links."""
        prepared = self._prepare(payload)
        old = self.service.store.handoff_operation(prepared['scope'], prepared['operation_id'])
        if old:
            if old.get('agent_request_hash') != prepared['digest']:
                raise ValueError('相同操作编号已用于另一份内容，请使用新的操作编号。')
            return dict(self._response(prepared, old, True), valid=True)
        manifest = self._manifest(prepared['files'])
        current = self.service.store.current_handoff(prepared['scope'])
        if prepared['expected_revision'] != (current['revision'] if current else 0):
            raise ConflictError(current, prepared['record'])
        return {'schema_version': 1, 'valid': True, 'project': prepared['project'],
                'workline': prepared['workline'], 'expected_revision': prepared['expected_revision'],
                'replayed': False, 'record': dict(prepared['record'], file_manifest=manifest),
                'files': manifest, 'file_count': len(manifest)}

    def _response(self, prepared, saved, replayed):
        node_id = f"record:{prepared['workline']}:{saved['revision']}"
        files = [dict(item, id=file_id(prepared['project'], node_id, item['path']))
                 for item in saved.get('file_manifest', [])]
        return {'schema_version': 1, 'project': prepared['project'], 'workline': prepared['workline'],
                'record_id': f"{prepared['project']}:{prepared['workline']}:{saved['revision']}",
                'node_id': node_id, 'revision': saved['revision'], 'replayed': replayed,
                'record': saved, 'files': files, 'file_count': len(files)}

    def save(self, payload):
        prepared = self._prepare(payload)
        saved, replayed = self.service.store.save_agent_record(
            prepared['scope'], prepared['record'], prepared['expected_revision'],
            prepared['operation_id'], prepared['digest'], lambda: self._manifest(prepared['files']))
        return self._response(prepared, saved, replayed)
