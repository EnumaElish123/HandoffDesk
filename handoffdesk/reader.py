"""Shared, paginated reading for desktop and terminal; never refreshes sources."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import json
import os
from pathlib import Path, PureWindowsPath
import stat
import unicodedata

from .node_files import merge_files
from .service import Service, WORKLINE_ID
from .store import now


def safe_text(value):
    """Render controls literally, including bidi/terminal escape sequences."""
    return ''.join(char if char in '\n\t' or not unicodedata.category(char).startswith('C')
                   else f'\\u{ord(char):04x}' for char in str(value))


def format_recorded_at(value, zone=None):
    """Display an absolute instant locally, always retaining a UTC offset."""
    requested = zone or os.environ.get('HANDOFFDESK_TIMEZONE')
    try:
        target = timezone.utc if requested == 'UTC' else (ZoneInfo(requested) if requested else None)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError('无法识别显示时区，请使用如 Asia/Shanghai 或 UTC 的时区名称。') from exc
    try:
        instant = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return safe_text(value)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    local = instant.astimezone(target)
    offset = local.strftime('%z')
    offset = offset[:3] + ':' + offset[3:]
    return local.strftime('%Y-%m-%d %H:%M') + f' {local.tzname()} (UTC{offset})'


class ReaderModel:
    def __init__(self, app_dir=None, data_dir=None):
        self.app_dir, self.data_dir = app_dir, data_dir

    def _service(self):
        # Reload config per operation, and never share SQLite connections across workers.
        from .paths import app_directory, configuration_path
        from .bootstrap import configuration_lock, _publish
        app = app_directory(self.app_dir)
        target = configuration_path(app)
        if not target.exists():
            with configuration_lock(app):
                if not target.exists():
                    _publish(app, target, {'schema_version': 1, 'projects': []}, create=True)
        return Service(self.app_dir, self.data_dir)

    @staticmethod
    def _identity(record_id):
        if not isinstance(record_id, str) or len(record_id) > 380:
            raise ValueError('记录编号无效。')
        parts = record_id.split(':')
        if len(parts) != 3 or not parts[2].isascii() or not parts[2].isdigit() or int(parts[2]) < 1:
            raise ValueError('记录编号无效。')
        return parts[0], parts[1], int(parts[2])

    @staticmethod
    def _record_scope(service, project, line):
        project = service.registry.get(project)['id']
        if not WORKLINE_ID.fullmatch(line):
            raise ValueError('记录的任务编号无效。')
        # Removing a configured task label must not make its immutable history unreadable.
        return project, project if line == 'default' else project + '\x1f' + line

    def registered_projects(self):
        return self._service().list_projects()

    def projects(self):
        service = self._service()
        with service.store.connection() as db:
            counts = dict(db.execute("""SELECT h.project_id, COUNT(CASE WHEN r.record_id IS NULL THEN 1 END) FROM handoffs h
                LEFT JOIN reader_reads r ON r.reader_id='local' AND
                r.record_id=replace(h.project_id, char(31), ':') ||
                    CASE WHEN instr(h.project_id,char(31))=0 THEN ':default' ELSE '' END || ':' || h.revision
                GROUP BY h.project_id"""))
        result = []
        for project in service.list_projects():
            lines = service.list_worklines(project['id'])
            known_lines = {line['id'] for line in lines}
            for scope in counts:
                if scope.startswith(project['id'] + '\x1f'):
                    historical = scope.split('\x1f', 1)[1]
                    if historical not in known_lines:
                        lines.append({'id': historical, 'name': '历史任务', 'entry_ids': []})
                        known_lines.add(historical)
            unread = 0
            for line in lines:
                scope = project['id'] + ('\x1f' + line['id'] if line['id'] != 'default' else '')
                line['unread'] = counts.get(scope, 0)
                unread += line['unread']
            result.append(dict(project, worklines=lines, unread=unread))
        return result

    def records(self, project=None, workline=None, limit=50, cursor=None, unread_only=False, query=''):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('每页记录数应为 1 至 100。')
        if not isinstance(query, str) or len(query) > 500 or type(unread_only) is not bool:
            raise ValueError('筛选条件无效。')
        service = self._service()
        known = {p['id']: p for p in service.list_projects()}
        conditions, params = [], []
        if project:
            project = service.registry.get(project)['id']
            if workline:
                _, scope = self._record_scope(service, project, workline)
                conditions.append('h.project_id=?')
                params.append(scope)
            else:
                conditions.append("(h.project_id=? OR substr(h.project_id,1,length(?)+1)=? || char(31))")
                params.extend([project] * 3)
        elif workline:
            raise ValueError('按任务筛选时请同时指定项目。')
        if cursor is not None:
            if type(cursor) is not int or cursor < 1:
                raise ValueError('分页位置无效。')
            conditions.append('h.rowid<?')
            params.append(cursor)
        if unread_only:
            conditions.append('r.record_id IS NULL')
        if query.strip():
            conditions.append('visible_match(h.project_id,h.revision,h.payload,s.payload)=1')
        # Registration removal must not leak stale project entries into the feed.
        placeholders = ','.join('?' for _ in known)
        conditions.append(f"CASE WHEN instr(h.project_id,char(31))=0 THEN h.project_id ELSE substr(h.project_id,1,instr(h.project_id,char(31))-1) END IN ({placeholders})")
        params.extend(known)
        sql = """SELECT h.rowid AS position,h.*,r.record_id IS NOT NULL AS is_read FROM handoffs h
            LEFT JOIN reader_reads r ON r.reader_id='local' AND
            r.record_id=replace(h.project_id, char(31), ':') ||
                CASE WHEN instr(h.project_id,char(31))=0 THEN ':default' ELSE '' END || ':' || h.revision
            LEFT JOIN snapshots s ON s.id=json_extract(h.payload,'$.source_snapshot_id') AND s.project_id=h.project_id
            WHERE """ + ' AND '.join(conditions) + ' ORDER BY h.rowid DESC LIMIT ?'
        with service.store.connection() as db:
            if query.strip():
                needle = query.strip().casefold()
                names = {pid: {line['id']: line['name'] for line in service.list_worklines(pid)}
                         for pid in known}
                overlays = {(row['project_id'], row['node_id']): json.loads(row['payload'])
                            for row in db.execute('SELECT * FROM node_file_state')}
                snapshots = {}

                def visible_match(scope, revision, raw, snapshot_raw):
                    identity = service.store.scope_identity(scope)
                    pid, line = identity['project_id'], identity.get('workline_id', 'default')
                    if pid not in known:
                        return False
                    payload = json.loads(raw)
                    fields = [known[pid]['name'], names[pid].get(line, '历史任务'),
                              payload.get('current_task', ''), payload.get('last_done', ''),
                              payload.get('next_action', ''), *payload.get('blockers', [])]
                    verification = payload.get('verification') or {}
                    fields.extend([verification.get('summary', ''), (payload.get('actor') or {}).get('name', '')])
                    if any(needle in str(field).casefold() for field in fields):
                        return True
                    base = payload.get('file_manifest')
                    if base is None:
                        key = (scope, payload.get('source_snapshot_id'))
                        if key not in snapshots:
                            snapshot = json.loads(snapshot_raw) if snapshot_raw else None
                            snapshots[key] = service.node_files._snapshot_files(pid, snapshot)
                        base = snapshots[key]
                    files = merge_files(base, overlays.get((pid, f'record:{line}:{revision}'), {}))
                    for item in files:
                        path = PureWindowsPath(item['path']) if PureWindowsPath(item['path']).is_absolute() else Path(item['path'])
                        fields.extend([path.name, item.get('purpose', '')])
                    return any(needle in str(field).casefold() for field in fields)

                db.create_function('visible_match', 4, visible_match)
            rows = db.execute(sql, [*params, limit + 1]).fetchall()
        items = [self._record(service, row, include_files=False) for row in rows[:limit]]
        return {'items': items, 'next_cursor': rows[limit - 1]['position'] if len(rows) > limit else None}

    @staticmethod
    def _record(service, row, *, include_files):
        payload = json.loads(row['payload'])
        identity = service.store.scope_identity(row['project_id'])
        project, line_id = identity['project_id'], identity.get('workline_id', 'default')
        line = next((v for v in service.list_worklines(project) if v['id'] == line_id), {'name': '历史任务'})
        node_id = f"record:{line_id}:{row['revision']}"
        files = []
        if include_files:
            base = payload.get('file_manifest')
            if base is None:
                snapshot = service.store.snapshot(payload.get('source_snapshot_id'), row['project_id'])
                base = service.node_files._snapshot_files(project, snapshot)
            associations = service.store.node_file_state(project, node_id)
            files = [service.node_files._display_file(project, node_id, item)
                     for item in merge_files(base, associations)]
        return {'id': f"{project}:{line_id}:{row['revision']}", 'project_id': project,
                'project_name': service.registry.get(project)['name'], 'workline_id': line_id,
                'workline_name': line['name'], 'title': payload.get('current_task') or '进度记录',
                'kind': payload.get('event_kind', 'handoff'), 'done': payload.get('last_done', ''),
                'next_action': payload.get('next_action', ''), 'blockers': payload.get('blockers', []),
                'verification': payload.get('verification') or {'status': 'not_run', 'summary': '这条历史记录未提供验证说明。'},
                'actor': payload.get('actor') or {'name': '历史记录'}, 'recorded_at': row['recorded_at'],
                'revision': row['revision'], 'read': bool(row['is_read']), 'files': files,
                'file_count': len(files) if include_files else len(payload.get('file_manifest') or [])}

    def detail(self, record_id):
        service = self._service()
        project, line, revision = self._identity(record_id)
        project, scope = self._record_scope(service, project, line)
        with service.store.connection() as db:
            row = db.execute("""SELECT h.*,EXISTS(SELECT 1 FROM reader_reads
                WHERE reader_id='local' AND record_id=?) AS is_read
                FROM handoffs h WHERE project_id=? AND revision=?""", (record_id, scope, revision)).fetchone()
        if row is None:
            raise ValueError('记录不存在，请刷新列表。')
        return self._record(service, row, include_files=True)

    def ack(self, record_ids):
        if not isinstance(record_ids, list) or len(record_ids) > 500:
            raise ValueError('每次最多确认 500 条已阅读记录。')
        service = self._service()
        rows = []
        for record_id in record_ids:
            project, line, revision = self._identity(record_id)
            _, scope = self._record_scope(service, project, line)
            rows.append((record_id, scope, revision))
        with service.store.connection(True) as db:
            for record_id, scope, revision in rows:
                if not db.execute('SELECT 1 FROM handoffs WHERE project_id=? AND revision=?', (scope, revision)).fetchone():
                    raise ValueError('待确认的记录不存在。')
                db.execute("INSERT OR IGNORE INTO reader_reads(reader_id,record_id,read_at) VALUES('local',?,?)", (record_id, now()))
        return {'ok': True, 'record_ids': list(dict.fromkeys(record_ids))}

    def changes(self, cursor=None):
        service = self._service()
        with service.store.connection() as db:
            event = db.execute('SELECT coalesce(max(sequence),0) FROM progress_events').fetchone()[0]
            reads = db.execute('SELECT count(*) FROM reader_reads').fetchone()[0]
            lines = [tuple(row) for row in db.execute('SELECT project_id,workline_id,payload FROM worklines ORDER BY project_id,workline_id')]
        value = hashlib.sha256(json.dumps([service.registry.fingerprint, event, reads, lines], ensure_ascii=False).encode()).hexdigest()
        return {'cursor': value, 'changed': cursor != value}

    def _file(self, record_id, file_id):
        value = self.detail(record_id)
        item = next((item for item in value['files'] if item['id'] == file_id), None)
        if item is None:
            raise ValueError('文件不属于这条记录。')
        return item

    def preview(self, record_id, file_id):
        item = self._file(record_id, file_id)
        if item['status'] != '可用':
            raise ValueError(item['status'] + '：' + item['path'])
        path = Path(item['path'])
        if path.is_dir():
            return {'path': str(path), 'name': item['name'], 'text': '这是文件夹。位置：' + safe_text(path), 'truncated': False, 'previewable': False}
        # No shell, no special files, no following a newly substituted leaf symlink.
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode) or path.resolve() != path:
                raise ValueError('只能预览未重定向的普通文件。')
            data = stream.read(1024 * 1024 + 1)
        if b'\x00' in data[:8192]:
            return {'path': str(path), 'name': item['name'], 'text': '二进制文件，请使用对应应用阅读。', 'truncated': False, 'previewable': False}
        truncated = len(data) > 1024 * 1024
        raw = data[:1024 * 1024].decode('utf-8-sig', errors='replace')
        lines = raw.splitlines(keepends=True)
        truncated = truncated or len(lines) > 2000
        return {'path': str(path), 'name': item['name'], 'text': safe_text(''.join(lines[:2000])),
                'truncated': truncated, 'previewable': True, 'encoding_warning': '\ufffd' in raw}

    def open_file(self, record_id, file_id, action='open'):
        if action not in {'open', 'reveal', 'folder', 'copy'}:
            raise ValueError('文件操作无效。')
        item = self._file(record_id, file_id)
        if action == 'copy':
            return {'path': item['path'], 'action': 'copy'}
        # Read-only code/HTML preview stays inside the new reader.
        from .service import SCRIPTS
        if action == 'open' and Path(item['path']).suffix.lower() in SCRIPTS | {'.html', '.htm', '.svg'}:
            return dict(self.preview(record_id, file_id), action='preview')
        service = self._service()
        project, line, revision = self._identity(record_id)
        path = service.open_node_file(project, f'record:{line}:{revision}', file_id,
                                      'reveal' if action == 'folder' else action)
        return {'path': path, 'action': action, 'requested': True}

    def export_file(self, record_id, path):
        """Write a user-selected Markdown destination without replacing any file."""
        text = self.export(record_id)
        target = Path(path).expanduser().absolute()
        if not target.name or not target.parent.is_dir():
            raise ValueError('导出目录不存在，请选择已有目录。')
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise ValueError('目标已存在，请选择其他文件名；已有文件未被覆盖。') from exc
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
        return {'path': str(target), 'record_id': record_id}

    def export(self, record_id):
        value = self.detail(record_id)
        verification = value['verification']
        result = [f"# {value['title']}", '', f"项目：{value['project_name']} / {value['workline_name']}",
                  f"记录：{value['id']} · {format_recorded_at(value['recorded_at'])}",
                  f"原始时间：{value['recorded_at']}", f"作者：{value['actor'].get('name', '历史记录')}",
                  f"类型：{'计划' if value['kind'] == 'plan' else '进展'}", '', '## 实际完成', value['done'] or '无完成项（计划）',
                  '', '## 下一步', value['next_action'], '', '## 验证',
                  f"{verification['status']}：{verification['summary']}", '', '## 阻碍',
                  '\n'.join('- ' + v for v in value['blockers']) or '未记录阻碍', '', '## 本次文件',
                  '清单保留位置与用途，不包含文件内容；文件仍以磁盘当前版本为准。', '']
        for item in value['files']:
            result.extend([f"- {item['name']}（{item['status']}）", '  用途：' + item.get('purpose', ''), '  位置：' + item['path']])
        return safe_text('\n'.join(result)) + '\n'
