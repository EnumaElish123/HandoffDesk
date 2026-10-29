"""Durable task bindings and semantic submissions for agents, independent of readers."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import uuid

from .agent_records import AgentRecords, _object, _text
from .paths import app_directory, configuration_path, validate_configuration_writes
from .registry import Registry
from .service import Service
from .store import Store, ConflictError, encode, now
from .workspace import Workspace


class SessionError(ValueError):
    def __init__(self, code, message, *, retryable=False, saved=False, details=None):
        self.code, self.retryable, self.saved = code, retryable, saved
        self.details = details or {}
        super().__init__(message)


def _identity(value, label):
    return None if value is None else _text(value, label, 200, required=True, single_line=True)


def _scope(session):
    return session['project_id'] if session['workline_id'] == 'default' else session['project_id'] + '\x1f' + session['workline_id']


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


class AgentSessions:
    def __init__(self, app_dir=None, data_dir=None):
        self.app_dir = app_directory(app_dir)
        self.data_dir = Path(data_dir or self.app_dir / 'data').resolve()

    def _store(self):
        target = configuration_path(self.app_dir)
        if target.exists():
            validate_configuration_writes(self.app_dir, Registry(target).projects, self.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for name in ('cards.sqlite3', 'cards.sqlite3-wal', 'cards.sqlite3-shm', 'cards.sqlite3-journal'):
            if not (self.data_dir / name).resolve().is_relative_to(self.data_dir):
                raise SessionError('state_unavailable', '数据库路径被重定向到状态目录之外。')
        return Store(self.data_dir / 'cards.sqlite3', configuration=target if target.exists() else None)

    @staticmethod
    def _session(db, session_ref):
        row = db.execute('SELECT * FROM agent_sessions WHERE session_ref=?', (session_ref,)).fetchone()
        if row is None:
            raise SessionError('session_unknown', '会话不存在，请使用 begin 创建任务，或核对状态目录。')
        return dict(row)

    @staticmethod
    def _turn(db, turn_ref):
        row = db.execute('SELECT * FROM agent_turns WHERE turn_ref=?', (turn_ref,)).fetchone()
        if row is None:
            raise SessionError('turn_unknown', '轮次不存在，请先调用 observe，或核对状态目录。')
        return dict(row)

    def begin(self, cwd, task, actor='Agent', host='unknown', host_session_id=None,
              turn_ref=None, workline=None, *, new=False, resume_session=None,
              resume_recording=False, full=True):
        if resume_session:
            if new or workline:
                raise ValueError('继续已有任务时不能同时新建或更换工作线。')
            return dict(self.resume(resume_session, turn_ref=turn_ref, cwd=cwd,
                                    resume_recording=resume_recording, full=full), resumed=True)
        task = _text(task, 'task', 100, required=True, single_line=True)
        actor = _text(actor, 'actor', 100, required=True, single_line=True)
        host = _text(host, 'host', 100, required=True, single_line=True)
        host_session_id = _identity(host_session_id, 'host_session_id')
        if turn_ref:
            turn = self.turn_status(turn_ref)
            if host == 'unknown':
                host = turn['host']
            if host_session_id is None:
                host_session_id = turn['host_session_id']
            if turn['host'] != host or turn['host_session_id'] != host_session_id:
                raise SessionError('session_binding_conflict', '轮次与当前宿主会话不一致。')
        workspace = Workspace(self.app_dir, self.data_dir).connect(cwd)
        service = Service(self.app_dir, self.data_dir)
        project_id = workspace['project']['id']
        if workline:
            service._scope(project_id, workline)
        task_key = _digest({'task': task})
        with service.store.connection(True) as db:
            existing = None
            if host_session_id and not new:
                existing = db.execute('SELECT * FROM agent_sessions WHERE host=? AND host_session_id=? AND task_key=?',
                                      (host, host_session_id, task_key)).fetchone()
            if existing:
                if (existing['project_id'] != project_id or existing['cwd'] != workspace['cwd']
                        or (workline and workline != existing['workline_id'])):
                    raise SessionError('session_binding_conflict', '此宿主任务已经绑定另一目录或任务线；请恢复原会话或明确使用新任务名称。')
                session_ref = existing['session_ref']
                turn_ref = self._activate_turn(db, dict(existing), turn_ref, resume_recording)
            else:
                if not new:
                    candidates = db.execute('''SELECT s.session_ref,s.workline_id FROM agent_sessions s
                        LEFT JOIN worklines w ON w.project_id=s.project_id AND w.workline_id=s.workline_id
                        WHERE s.project_id=? AND s.cwd=? AND (s.task_key=? OR json_extract(w.payload,'$.name')=?)''',
                                            (project_id, workspace['cwd'], task_key, task)).fetchall()
                    if candidates:
                        raise SessionError('task_exists', '发现同名任务，请明确继续已有会话，或用 --new 新建独立任务。',
                                           details={'candidates': [dict(row) for row in candidates],
                                                    'next_action': 'agent sessions --cwd 当前目录；然后 agent resume --session SESSION --cwd 当前目录'})
                if new and host_session_id:
                    task_key = _digest({'task': task, 'instance': uuid.uuid4().hex})
                session_ref = 'session-' + uuid.uuid4().hex
                if turn_ref is None:
                    turn_ref = self._local_turn(db, host, host_session_id)
                elif self._turn(db, turn_ref)['disposition'] is not None:
                    raise SessionError('turn_concluded', '不能在已结束轮次中新建任务，请开始新的工作轮次。')
                line_id = workline or 'task-' + uuid.uuid4().hex[:16]
                if not workline:
                    db.execute('INSERT INTO worklines(project_id,workline_id,payload) VALUES(?,?,?)',
                               (project_id, line_id, encode({'id': line_id, 'name': task, 'entry_ids': []})))
                scope = project_id if line_id == 'default' else project_id + '\x1f' + line_id
                row = db.execute('SELECT MAX(revision) FROM handoffs WHERE project_id=?', (scope,)).fetchone()
                token = 'checkpoint-' + uuid.uuid4().hex
                db.execute('INSERT INTO agent_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                           (session_ref, host, host_session_id, task_key, project_id, line_id,
                            workspace['cwd'], actor, token, turn_ref, now()))
                db.execute('INSERT INTO agent_checkpoints(token,session_ref,operation_id,base_revision,status,created_at) VALUES(?,?,?,?,?,?)',
                           (token, session_ref, 'hdk-' + uuid.uuid4().hex, row[0] or 0, 'issued', now()))
        return dict(self.status(session_ref, full=full, limit=100 if full else 10), workspace_added=workspace['added'],
                    checkout=workspace['checkout'], resumed=existing is not None)

    def _activate_turn(self, db, session, turn_ref=None, resume_recording=False):
        previous = self._turn(db, session['turn_ref']) if session['turn_ref'] else None
        if previous and previous['disposition'] == 'paused' and not resume_recording:
            raise SessionError('recording_paused', '此任务已暂停记录；用户明确恢复后使用 --resume-recording。',
                               details={'writable': False, 'next_action': 'agent resume --session ' + session['session_ref'] + ' --resume-recording'})
        if turn_ref:
            current = self._turn(db, turn_ref)
            if current['host'] != session['host'] or current['host_session_id'] != session['host_session_id']:
                line = db.execute('SELECT payload FROM worklines WHERE project_id=? AND workline_id=?',
                                  (session['project_id'], session['workline_id'])).fetchone()
                title = json.loads(line[0]).get('name', '继续已有任务') if line else '继续已有任务'
                raise SessionError('session_binding_conflict', '当前宿主会话不同；先处理原会话待办，再显式沿用工作线建立新会话。',
                                   details={'session_ref': session['session_ref'], 'recover_pending_first': True,
                                            'next_action': 'begin', 'argv': ['agent', 'begin', '--new', '--workline',
                                            session['workline_id'], '--cwd', session['cwd'], '--task', title,
                                            '--turn', turn_ref]})
            if current['disposition'] is not None:
                raise SessionError('turn_concluded', '指定轮次已经结束，请恢复任务以取得新的可写轮次。')
            if previous and previous['disposition'] in {'interrupted', 'failed', 'paused'} and current['original_turn_ref'] != previous['original_turn_ref']:
                raise SessionError('session_binding_conflict', '恢复未完成任务须使用原恢复链；省略 --turn 可自动创建关联轮次。')
        elif previous and previous['disposition'] is None:
            turn_ref = previous['turn_ref']
        else:
            recovery = previous['original_turn_ref'] if previous and previous['disposition'] in {'interrupted', 'failed', 'paused'} else None
            turn_ref = self._local_turn(db, session['host'], session['host_session_id'], recovery)
        db.execute('UPDATE agent_sessions SET turn_ref=? WHERE session_ref=?', (turn_ref, session['session_ref']))
        return turn_ref

    def sessions(self, cwd, *, limit=20, cursor=None):
        """Discover project tasks without creating a project or state directory."""
        self._pagination(limit, cursor)
        workspace = Workspace(self.app_dir, self.data_dir).inspect(cwd)
        if not workspace['connected'] or not (self.data_dir / 'cards.sqlite3').exists():
            return {'schema_version': 1, 'ok': True, 'items': [], 'next_cursor': None}
        service = Service(self.app_dir, self.data_dir)
        names = {line['id']: line['name'] for line in service.list_worklines(workspace['project']['id'])}
        with service.store.connection() as db:
            rows = db.execute('SELECT rowid AS position,* FROM agent_sessions WHERE project_id=? AND (? IS NULL OR rowid<?) ORDER BY rowid DESC LIMIT ?',
                              (workspace['project']['id'], cursor, cursor, limit + 1)).fetchall()
            items = []
            for row in rows[:limit]:
                session = dict(row)
                turn = self._turn(db, session['turn_ref'])
                latest = db.execute('SELECT payload FROM handoffs WHERE project_id=? ORDER BY revision DESC LIMIT 1', (_scope(session),)).fetchone()
                latest = json.loads(latest[0]) if latest else None
                pending = db.execute("SELECT COUNT(*) FROM agent_checkpoints WHERE session_ref=? AND request IS NOT NULL AND receipt IS NULL AND status<>'superseded'", (session['session_ref'],)).fetchone()[0]
                reports = db.execute("SELECT COUNT(*) FROM agent_checkpoints c WHERE c.session_ref=? AND c.receipt IS NOT NULL AND NOT EXISTS (SELECT 1 FROM agent_reports r WHERE r.record_id=json_extract(c.receipt,'$.record_id'))", (session['session_ref'],)).fetchone()[0]
                same = session['cwd'] == workspace['cwd']
                items.append({'session_ref': session['session_ref'], 'task_id': session['workline_id'],
                              'task': names.get(session['workline_id'], session['workline_id']),
                              'host': session['host'], 'host_session_id': session['host_session_id'],
                              'cwd': session['cwd'], 'same_directory': same, 'disposition': turn['disposition'],
                              'latest': self._record_summary(latest), 'pending_checkpoints': pending,
                              'pending_reports': reports, 'next_action':
                                  '确认恢复记录后使用 resume --resume-recording' if turn['disposition'] == 'paused' else
                                  ('resume --session ' + session['session_ref'] if same else '在原目录恢复；当前目录不同，不自动改绑')})
        return {'schema_version': 1, 'ok': True, 'items': items,
                'next_cursor': rows[limit - 1]['position'] if len(rows) > limit else None}

    @staticmethod
    def _pagination(limit, cursor):
        if type(limit) is not int or not 1 <= limit <= 100 or (cursor is not None and (type(cursor) is not int or cursor < 1)):
            raise ValueError('每页数量须为 1 至 100，分页游标须为正整数。')

    @staticmethod
    def _record_summary(record):
        if not record:
            return None
        return {'title': record.get('current_task', '')[:100], 'revision': record.get('revision'),
                'recorded_at': record.get('recorded_at'), 'actual_done': record.get('last_done', '')[:240],
                'next_action': record.get('next_action', '')[:240]}

    @staticmethod
    def _local_turn(db, host, host_session_id, original_turn_ref=None):
        """Skill-only installs still have a reportable turn without invented host IDs."""
        turn_ref = 'turn-' + uuid.uuid4().hex
        db.execute('INSERT INTO agent_turns(turn_ref,host,host_session_id,original_turn_ref,created_at) VALUES(?,?,?,?,?)',
                   (turn_ref, host, host_session_id, original_turn_ref or turn_ref, now()))
        return turn_ref

    @staticmethod
    def _semantic(payload, cwd):
        allowed = {'kind', 'title', 'actual_done', 'next_action', 'blockers', 'verification', 'files'}
        _object(payload, allowed, {'title', 'next_action'}, 'checkpoint')
        # Use the legacy validator for field limits, enum values and plan semantics.
        files = payload.get('files', [])
        if not isinstance(files, list):
            raise ValueError('files 必须是列表。')
        normalized = []
        for item in files:
            _object(item, {'path', 'purpose', 'role'}, {'path', 'purpose'}, 'files')
            if not isinstance(item['path'], str) or not item['path'] or '\x00' in item['path']:
                raise ValueError('附件路径必须是非空文字。')
            path = Path(item['path']).expanduser()
            # Absolute lexical paths are retained until first commit; retry must
            # not re-resolve a moved symlink or mutate the original request.
            if not path.is_absolute():
                path = Path(cwd) / path
            normalized.append(dict(item, path=str(path)))
        return {'kind': payload.get('kind', 'handoff'), 'title': payload['title'],
                'actual_done': payload.get('actual_done', ''), 'next_action': payload['next_action'],
                'blockers': payload.get('blockers', []),
                'verification': payload.get('verification', {'status': 'not_run', 'summary': '未报告验证。'}),
                'files': normalized}

    @staticmethod
    def _protocol(session, checkpoint, semantic):
        actor = {'name': session['actor']}
        if session['host_session_id']:
            actor['session_id'] = session['host_session_id']
        return {'schema_version': 1, 'project': session['project_id'], 'workline': session['workline_id'],
                'operation_id': checkpoint['operation_id'], 'expected_revision': checkpoint['base_revision'],
                'record': {'event_kind': semantic['kind'], 'title': semantic['title'],
                           'last_done': semantic['actual_done'], 'next_action': semantic['next_action'],
                           'blockers': semantic['blockers']}, 'actor': actor,
                'verification': semantic['verification'], 'files': semantic['files']}

    def checkpoint(self, session_ref, token, payload, turn_ref=None):
        service = Service(self.app_dir, self.data_dir)
        store = service.store
        with store.connection() as db:
            session = self._session(db, session_ref)
            row = db.execute('SELECT * FROM agent_checkpoints WHERE token=? AND session_ref=?',
                             (token, session_ref)).fetchone()
            if row is None:
                raise SessionError('checkpoint_unknown', '提交令牌不属于此会话，请读取 status。')
            checkpoint = dict(row)
        semantic = self._semantic(payload, session['cwd'])
        protocol = self._protocol(session, checkpoint, semantic)
        api = AgentRecords(service)
        prepared = api._prepare(protocol)
        request_hash = _digest(semantic)
        effective_turn = turn_ref or session['turn_ref']
        with store.connection(True) as db:
            row = db.execute('SELECT * FROM agent_checkpoints WHERE token=?', (token,)).fetchone()
            if row['request_hash'] and row['request_hash'] != request_hash:
                raise SessionError('operation_mismatch', '同一令牌已经绑定其他内容；不能更换内容重试。请读取 status 后明确替换失败提交。')
            if row['status'] == 'superseded':
                raise SessionError('operation_mismatch', '此令牌已经被明确替换。')
            if row['receipt']:
                saved = json.loads(row['receipt'])
                return dict(saved, replayed=True, report_status=self._report_status(db, saved['record_id']),
                            report_text=self._report(saved, replayed=True))
            if effective_turn:
                turn = self._turn(db, effective_turn)
                if turn['host'] != session['host'] or turn['host_session_id'] != session['host_session_id']:
                    raise SessionError('session_binding_conflict', '提交轮次与会话宿主不一致。')
                if turn['disposition'] is not None:
                    raise SessionError('turn_concluded', '本轮已结束；请恢复任务取得新的可写轮次。',
                                       details={'writable': False, 'next_action': 'agent resume --session ' + session_ref})
            if row['request'] is None:
                db.execute("UPDATE agent_checkpoints SET request=?,request_hash=?,turn_ref=?,status='pending' WHERE token=?",
                           (encode(semantic), request_hash, effective_turn, token))
            elif row['turn_ref'] and effective_turn:
                first = self._turn(db, row['turn_ref'])
                current = self._turn(db, effective_turn)
                if first['original_turn_ref'] != current['original_turn_ref']:
                    raise SessionError('session_binding_conflict', '该令牌已提交到另一轮次，请原样恢复原轮次。')

        def receipt(saved, replayed, next_token):
            value = dict(api._response(prepared, saved, replayed), ok=True, session_ref=session_ref,
                         checkpoint_token=token, next_checkpoint_token=next_token,
                         report_status='report_pending', turn_ref=checkpoint.get('turn_ref') or effective_turn)
            value['report_text'] = self._report(value, replayed)
            return value
        def turn_guard(db):
            if effective_turn and self._turn(db, effective_turn)['disposition'] is not None:
                raise SessionError('turn_concluded', '保存前轮次已结束；请恢复原任务后重试。')
        try:
            result = store.commit_checkpoint(token, prepared, lambda: api._manifest(prepared['files']), receipt,
                                             turn_guard=turn_guard)
            if result['replayed']:
                result['report_text'] = self._report(result, True)
            return result
        except SessionError:
            raise
        except ConflictError as exc:
            details = {'current': exc.current, 'proposed': semantic,
                       'action': '阅读当前记录和原提案；明确解决后使用 agent resume --replace-checkpoint TOKEN --reason 原因。'}
            error = {'code': 'revision_conflict', 'message': str(exc), **details}
            with store.connection(True) as db:
                db.execute("UPDATE agent_checkpoints SET status='conflict',error=? WHERE token=? AND receipt IS NULL AND status<>'superseded'",
                           (encode(error), token))
            raise SessionError('revision_conflict', str(exc), details=details) from exc
        except (OSError, ValueError) as exc:
            error = {'code': 'attachment_missing' if isinstance(exc, FileNotFoundError) or '不存在' in str(exc) else 'checkpoint_invalid',
                     'message': str(exc), 'action': '修复原路径后原样重试；若需修改内容，先明确替换失败提交。'}
            with store.connection(True) as db:
                db.execute("UPDATE agent_checkpoints SET status='failed',error=? WHERE token=? AND receipt IS NULL AND status<>'superseded'",
                           (encode(error), token))
            raise SessionError(error['code'], str(exc), retryable=True, details=error) from exc

    @staticmethod
    def _report(receipt, replayed=False):
        record = receipt['record']
        action = '已确认先前记录' if replayed else '已记录'
        return (f"{action}『{record['current_task']}』，记录 {receipt['record_id']}，附件 {receipt['file_count']} 项；"
                f"验证（记录者报告）：{record['verification']['summary']}；下一步：{record['next_action']}")

    def status(self, session_ref, checkpoint=None, *, full=True, limit=100, cursor=None, pending_cursor=None):
        self._pagination(limit, cursor)
        self._pagination(limit, pending_cursor)
        store = self._store()
        with store.connection() as db:
            session = self._session(db, session_ref)
            turn = self._turn(db, session['turn_ref']) if session['turn_ref'] else None
            if checkpoint:
                rows = db.execute('SELECT rowid AS position,* FROM agent_checkpoints WHERE session_ref=? AND token=?',
                                  (session_ref, checkpoint)).fetchall()
                if not rows:
                    raise SessionError('checkpoint_unknown', '提交令牌不属于此会话。')
            else:
                rows = db.execute('SELECT rowid AS position,* FROM agent_checkpoints WHERE session_ref=? AND (? IS NULL OR rowid<?) ORDER BY rowid DESC LIMIT ?',
                                  (session_ref, cursor, cursor, limit + 1)).fetchall()
            history = [json.loads(row[0]) for row in db.execute(
                'SELECT payload FROM handoffs WHERE project_id=? ORDER BY revision DESC LIMIT ?', (_scope(session), 3 if full else 1))]
            checkpoints = []
            for row in rows[:limit]:
                value = {key: row[key] for key in ('token', 'status', 'base_revision', 'turn_ref', 'next_token')}
                request = json.loads(row['request']) if row['request'] else None
                error = json.loads(row['error']) if row['error'] else None
                receipt = json.loads(row['receipt']) if row['receipt'] else None
                if receipt:
                    receipt['report_status'] = self._report_status(db, receipt['record_id'])
                if full or checkpoint:
                    value.update(request=request, error=error, receipt=receipt)
                else:
                    value.update(title=request.get('title', '')[:100] if request else None,
                                 error={'code': error.get('code'), 'message': error.get('message', '')[:240]} if error else None,
                                 record_id=receipt['record_id'] if receipt else None)
                checkpoints.append(value)
            predicate = """c.session_ref=? AND c.receipt IS NOT NULL AND NOT EXISTS
                (SELECT 1 FROM agent_reports r WHERE r.record_id=json_extract(c.receipt,'$.record_id'))"""
            pending_count = db.execute('SELECT COUNT(*) FROM agent_checkpoints c WHERE ' + predicate, (session_ref,)).fetchone()[0]
            pending_rows = db.execute('SELECT c.rowid AS position,c.token,c.receipt FROM agent_checkpoints c WHERE ' + predicate +
                                     ' AND (? IS NULL OR c.rowid<?) ORDER BY c.rowid DESC LIMIT ?',
                                     (session_ref, pending_cursor, pending_cursor, limit + 1)).fetchall()
            pending_reports = []
            for row in pending_rows[:limit]:
                receipt = json.loads(row['receipt'])
                if full:
                    pending_reports.append(receipt)
                else:
                    pending_reports.append({'record_id': receipt['record_id'], 'turn_ref': receipt['turn_ref'],
                                            'checkpoint_token': row['token'], 'title': receipt['record']['current_task'][:100],
                                            'file_count': receipt['file_count'], 'report_status': 'report_pending'})
            pending_checkpoints = db.execute("SELECT COUNT(*) FROM agent_checkpoints WHERE session_ref=? AND request IS NOT NULL AND receipt IS NULL AND status<>'superseded'", (session_ref,)).fetchone()[0]
            writable = turn is not None and turn['disposition'] is None
        return {'schema_version': 1, 'ok': True, 'session_ref': session_ref,
                'identity_source': 'host' if session['host_session_id'] else 'handoffdesk',
                'turn_identity_source': 'host' if turn and turn['host_turn_id'] else 'handoffdesk',
                'host': session['host'], 'host_session_id': session['host_session_id'],
                'project': session['project_id'], 'workline': session['workline_id'],
                'cwd': session['cwd'], 'turn_ref': session['turn_ref'],
                'disposition': turn['disposition'] if turn else None, 'writable': writable,
                'next_action': 'checkpoint' if writable else ('resume --resume-recording（用户明确恢复后）' if turn and turn['disposition'] == 'paused' else 'resume'),
                'checkpoint_token': session['current_token'],
                'history': history if full else [self._record_summary(item) for item in history],
                'checkpoints': checkpoints, 'pending_checkpoints_count': pending_checkpoints,
                'next_cursor': rows[limit - 1]['position'] if len(rows) > limit else None,
                'pending_reports': pending_reports, 'pending_reports_count': pending_count,
                'pending_reports_next_cursor': pending_rows[limit - 1]['position'] if len(pending_rows) > limit else None,
                'detail_action': 'agent status --session ' + session_ref + ' --checkpoint TOKEN'}

    def resume(self, session_ref, replace_checkpoint=None, reason='', turn_ref=None, *,
               resume_recording=False, cwd=None, full=True):
        if cwd is not None:
            cwd = Workspace(self.app_dir, self.data_dir).inspect(cwd)['cwd']
        if replace_checkpoint:
            reason = _text(reason, 'reason', 2000, required=True)
        store = self._store()
        with store.connection(True) as db:
            session = self._session(db, session_ref)
            if cwd is not None and cwd != session['cwd']:
                raise SessionError('session_binding_conflict', '当前目录不同于原任务；请在原目录恢复，或明确新建任务。')
            # Validation and activation share the transaction so a failed replacement
            # cannot advance the task to a new round as a side effect.
            if replace_checkpoint:
                row = db.execute('SELECT * FROM agent_checkpoints WHERE token=? AND session_ref=?',
                                 (replace_checkpoint, session_ref)).fetchone()
                if row is None or row['status'] not in {'failed', 'conflict'}:
                    raise SessionError('operation_mismatch', '仅能明确替换失败或冲突的提交；已保存回执不能替换。')
                if session['current_token'] != replace_checkpoint:
                    raise SessionError('operation_mismatch', '该令牌不是本会话当前待处理提交。')
            self._activate_turn(db, session, turn_ref, resume_recording)
            if replace_checkpoint:
                revision = db.execute('SELECT MAX(revision) FROM handoffs WHERE project_id=?', (_scope(session),)).fetchone()[0] or 0
                token = 'checkpoint-' + uuid.uuid4().hex
                db.execute("UPDATE agent_checkpoints SET status='superseded',next_token=?,error=? WHERE token=?",
                           (token, encode({'code': 'explicitly_replaced', 'reason': reason}), replace_checkpoint))
                db.execute('INSERT INTO agent_checkpoints(token,session_ref,operation_id,base_revision,status,created_at) VALUES(?,?,?,?,?,?)',
                           (token, session_ref, 'hdk-' + uuid.uuid4().hex, revision, 'issued', now()))
                db.execute('UPDATE agent_sessions SET current_token=? WHERE session_ref=?', (token, session_ref))
        return self.status(session_ref, full=full, limit=100 if full else 10)

    def observe_turn(self, host='unknown', host_session_id=None, host_turn_id=None, resume_of=None):
        host = _text(host, 'host', 100, required=True, single_line=True)
        host_session_id = _identity(host_session_id, 'host_session_id')
        host_turn_id = _identity(host_turn_id, 'host_turn_id')
        store = self._store()
        with store.connection(True) as db:
            existing = None
            if host_session_id and host_turn_id:
                existing = db.execute('SELECT turn_ref FROM agent_turns WHERE host=? AND host_session_id=? AND host_turn_id=?',
                                      (host, host_session_id, host_turn_id)).fetchone()
            if existing:
                turn_ref = existing['turn_ref']
                if resume_of:
                    current = self._turn(db, turn_ref)
                    original = self._turn(db, resume_of)
                    if current['original_turn_ref'] != original['original_turn_ref']:
                        raise SessionError('turn_binding_conflict', '已观察的轮次不能改绑恢复链。')
            else:
                turn_ref = 'turn-' + uuid.uuid4().hex
                if resume_of:
                    original = self._turn(db, resume_of)
                    if original['host'] != host or original['host_session_id'] != host_session_id:
                        raise SessionError('turn_binding_conflict', '只能恢复同一宿主会话的补救轮次。')
                    original_ref = original['original_turn_ref']
                else:
                    original_ref = turn_ref
                db.execute('INSERT INTO agent_turns(turn_ref,host,host_session_id,host_turn_id,original_turn_ref,created_at) VALUES(?,?,?,?,?,?)',
                           (turn_ref, host, host_session_id, host_turn_id, original_ref, now()))
        return self.turn_status(turn_ref)

    @staticmethod
    def _report_status(db, record_id):
        rows = {row[0] for row in db.execute('SELECT evidence_type FROM agent_reports WHERE record_id=?', (record_id,))}
        if 'observed_in_final' in rows:
            return 'observed_in_final'
        if 'agent_acknowledged' in rows:
            return 'agent_acknowledged'
        return 'report_pending'

    def _turn_status(self, db, turn_ref):
        turn = self._turn(db, turn_ref)
        chain = db.execute('SELECT * FROM agent_turns WHERE original_turn_ref=? ORDER BY rowid',
                           (turn['original_turn_ref'],)).fetchall()
        original = self._turn(db, turn['original_turn_ref'])
        refs = [item['turn_ref'] for item in chain]
        marks = ','.join('?' for _ in refs)
        rows = db.execute(f'SELECT * FROM agent_checkpoints WHERE turn_ref IN ({marks}) ORDER BY rowid', refs).fetchall()
        receipts, pending, awaiting = [], [], []
        for row in rows:
            if row['receipt']:
                receipt = json.loads(row['receipt'])
                status = self._report_status(db, receipt['record_id'])
                value = {'record_id': receipt['record_id'], 'report_text': receipt['report_text'], 'report_status': status}
                receipts.append(value)
                if status == 'report_pending':
                    pending.append(value)
            elif row['request'] is not None and row['status'] != 'superseded':
                awaiting.append({'token': row['token'], 'status': row['status'],
                                 'error': json.loads(row['error']) if row['error'] else None})
        concluded = next((dict(item) for item in reversed(chain) if item['disposition']), turn)
        return {'schema_version': 1, 'ok': True, 'turn_ref': turn_ref,
                'original_turn_ref': turn['original_turn_ref'], 'host': turn['host'],
                'identity_source': 'host' if turn['host_session_id'] and turn['host_turn_id'] else 'handoffdesk',
                'host_session_id': turn['host_session_id'], 'host_turn_id': turn['host_turn_id'],
                'disposition': concluded['disposition'], 'reason': concluded['reason'],
                'receipt_ids': [item['record_id'] for item in receipts], 'receipts': receipts,
                'pending_reports': pending, 'pending_checkpoints': awaiting,
                'remediation_used': bool(original['remediation_used']),
                'delivery_verified': False}

    def turn_status(self, turn_ref):
        with self._store().connection() as db:
            return self._turn_status(db, turn_ref)

    def conclude_turn(self, turn_ref, outcome, receipt_ids=None, reason=''):
        if outcome not in {'recorded', 'no_record', 'paused', 'interrupted', 'failed'}:
            raise ValueError('outcome 必须为 recorded/no_record/paused/interrupted/failed。')
        reason = _text(reason, 'reason', 2000, required=outcome != 'recorded')
        receipt_ids = [] if receipt_ids is None else receipt_ids
        if (not isinstance(receipt_ids, list) or len(receipt_ids) > 1000
                or any(not isinstance(value, str) for value in receipt_ids)):
            raise ValueError('receipt_ids 必须是记录编号列表。')
        receipt_ids = sorted(set(receipt_ids))
        with self._store().connection(True) as db:
            turn = self._turn(db, turn_ref)
            status = self._turn_status(db, turn_ref)
            known = set(status['receipt_ids'])
            if outcome == 'recorded' and (not receipt_ids or set(receipt_ids) != known or status['pending_checkpoints']):
                raise SessionError('retry_pending', '必须引用本轮全部已提交回执，并先处理尚未保存的提交。')
            if outcome == 'no_record' and (known or status['pending_checkpoints']):
                raise SessionError('retry_pending', '本轮已有成果或待处理提交，不能声明无须记录。')
            if set(receipt_ids) - known:
                raise SessionError('receipt_unknown', '回执不属于本轮或其恢复链。')
            if turn['disposition']:
                if (turn['disposition'] != outcome or sorted(json.loads(turn['receipt_ids'])) != receipt_ids
                        or turn['reason'] != reason):
                    raise SessionError('turn_concluded', '轮次已经处置，不能静默改写；请在明确关联的恢复轮次继续。')
            else:
                db.execute('UPDATE agent_turns SET disposition=?,receipt_ids=?,reason=? WHERE turn_ref=?',
                           (outcome, encode(receipt_ids), reason, turn_ref))
        return self.turn_status(turn_ref)

    def ack_report(self, turn_ref, receipt_id, evidence_type, evidence_ref):
        if evidence_type not in {'agent_acknowledged', 'observed_in_final'}:
            raise ValueError('汇报证据须为 agent_acknowledged 或 observed_in_final。')
        evidence_ref = _text(evidence_ref, 'evidence_ref', 2000, required=True)
        with self._store().connection(True) as db:
            status = self._turn_status(db, turn_ref)
            if receipt_id not in status['receipt_ids']:
                raise SessionError('receipt_unknown', '此回执不属于本轮或其恢复链。')
            db.execute('INSERT OR IGNORE INTO agent_reports VALUES(?,?,?,?,?)',
                       (turn_ref, receipt_id, evidence_type, evidence_ref, now()))
        return self.turn_status(turn_ref)

    def claim_remediation(self, turn_ref):
        """Consume one durable budget on the original turn, including continuations."""
        with self._store().connection(True) as db:
            turn = self._turn(db, turn_ref)
            cursor = db.execute('UPDATE agent_turns SET remediation_used=1 WHERE turn_ref=? AND remediation_used=0',
                                (turn['original_turn_ref'],))
            return cursor.rowcount == 1
