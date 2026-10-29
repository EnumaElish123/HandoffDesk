"""Progress-node file associations. Project contents are never changed."""
from __future__ import annotations

import hashlib
import ntpath
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import posixpath
import re
import stat
import subprocess
import uuid

from .store import now
from . import platform_io


MAX_FILES = 500
METADATA_HASH_LIMIT = 1024 * 1024
EVIDENCE_SIZE_LIMIT = 10 * 1024 * 1024
SCRIPTS = {'.py', '.pyw', '.js', '.jse', '.ts', '.ps1', '.psm1', '.bat', '.cmd',
           '.vbs', '.vbe', '.wsf', '.wsh', '.sh', '.reg', '.sql', '.ipynb'}
DOCUMENTS = {'.md', '.txt', '.json', '.yaml', '.yml', '.toml', '.csv', '.tsv', '.log',
             '.html', '.htm', '.pdf', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
             '.wav', '.mp3', '.m4a', '.flac', '.ogg', '.mp4', '.mkv', '.mov', '.avi',
             '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx', '.rtf', '.zip'}
WEIGHTS = {'.pth', '.pt', '.bin', '.safetensors', '.ckpt', '.index', '.onnx'}


def file_metadata(path, *, hash_limit=METADATA_HASH_LIMIT):
    """Best-effort bounded evidence; never mistake a directory for file content."""
    path = Path(path)
    try:
        before = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            return None
        result = {'size': before.st_size, 'mtime_ns': before.st_mtime_ns}
        if before.st_size <= hash_limit:
            try:
                fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
                with os.fdopen(fd, 'rb') as stream:
                    opened = os.fstat(stream.fileno())
                    if (not stat.S_ISREG(opened.st_mode)
                            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
                        return None
                    data = stream.read(hash_limit + 1)
                    after = os.fstat(stream.fileno())
                if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                    return None
                if len(data) == before.st_size:
                    result['sha256'] = hashlib.sha256(data).hexdigest()
            except OSError:
                # Files can still be associated when another program locks
                # their contents; metadata-only comparison is labelled as such.
                pass
        return result
    except (OSError, ValueError):
        return None


def _version_evidence(recorded, current, *, allowed, exists):
    valid = lambda value: (isinstance(value, dict)
                           and type(value.get('size')) is int and value['size'] >= 0
                           and type(value.get('mtime_ns')) is int)
    recorded = recorded if valid(recorded) else None
    current = current if valid(current) else None
    state, basis = 'unknown', 'unknown'
    if allowed and not exists:
        state = 'missing'
    elif recorded is not None and current is not None:
        different_metadata = any(recorded[key] != current[key] for key in ('size', 'mtime_ns'))
        if different_metadata:
            state, basis = 'changed', 'metadata'
        elif recorded.get('sha256') and current.get('sha256'):
            state, basis = ('unchanged' if recorded['sha256'] == current['sha256'] else 'changed'), 'sha256'
        elif not recorded.get('sha256'):
            state, basis = 'unchanged', 'metadata'
    return {'recorded_metadata': recorded, 'current_metadata': current,
            'version_status': state, 'version_basis': basis}


def local_path(value, *, must_exist=False, resolve_links=True):
    """Accept explicit local paths, never URLs, devices or alternate streams."""
    if not isinstance(value, (str, os.PathLike)):
        raise ValueError('请选择本机文件或文件夹。')
    raw = os.fspath(value)
    if not isinstance(raw, str) or not raw or len(raw) > 32760 or any(ord(c) < 32 for c in raw):
        raise ValueError('文件路径无效。')
    win = PureWindowsPath(raw)
    if raw.startswith(('\\\\', '//')) or '://' in raw or raw.lower().startswith('file:'):
        raise ValueError('只接受本机绝对路径，不接受网址、网络共享或设备路径。')
    if os.name == 'nt':
        if not win.is_absolute() or not re.fullmatch(r'[A-Za-z]:', win.drive):
            raise ValueError('请选择带盘符的本机绝对路径。')
        parts = win.parts[1:]
        if any(any(c in part for c in '<>:"|?*') or part.endswith((' ', '.'))
               or PureWindowsPath(part).is_reserved() for part in parts):
            raise ValueError('文件路径含设备名称、数据流或无效字符。')
    elif not Path(raw).is_absolute():
        raise ValueError('请选择本机绝对路径。')
    try:
        path = Path(raw).resolve() if resolve_links else Path(os.path.abspath(raw))
        if must_exist and not (path.is_file() or path.is_dir()):
            raise ValueError('文件或文件夹不存在，请重新选择。')
        return path
    except (OSError, RuntimeError) as exc:
        raise ValueError('文件位置无法访问，请重新选择。') from exc


def path_key(path):
    raw = str(path)
    # Keep persisted overlay keys stable when a database moves between systems.
    # Windows paths remain case-insensitive; POSIX paths must preserve case.
    win = PureWindowsPath(raw)
    if win.is_absolute() and re.fullmatch(r'[A-Za-z]:', win.drive):
        return ntpath.normcase(ntpath.normpath(raw))
    if raw.startswith('/') and not raw.startswith('//'):
        return posixpath.normpath(raw)
    return os.path.normcase(os.path.normpath(raw))


def _foreign_history_path(value):
    """Parse a foreign recorded location for display only, never filesystem I/O."""
    if not isinstance(value, (str, os.PathLike)):
        return None
    raw = os.fspath(value)
    if (not isinstance(raw, str) or not raw or len(raw) > 32760
            or any(ord(c) < 32 for c in raw) or raw.startswith(('\\\\', '//'))
            or '://' in raw or raw.lower().startswith('file:')):
        return None
    if platform_io.is_windows():
        return PurePosixPath(raw) if raw.startswith('/') else None
    win = PureWindowsPath(raw)
    if not win.is_absolute() or not re.fullmatch(r'[A-Za-z]:', win.drive):
        return None
    if any(any(c in part for c in '<>:"|?*') or part.endswith((' ', '.'))
           or PureWindowsPath(part).is_reserved() for part in win.parts[1:]):
        return None
    return win


def file_id(project_id, node_id, path):
    return hashlib.sha256((project_id + '\0' + node_id + '\0' + path_key(path)).encode('utf-8')).hexdigest()[:24]


def merge_files(base, state):
    hidden = set(state.get('hidden', []))
    values = {path_key(item['path']): dict(item) for item in base if path_key(item['path']) not in hidden}
    for item in state.get('additions', []):
        if path_key(item['path']) not in hidden:
            values[path_key(item['path'])] = dict(item)
    return list(values.values())


class NodeFiles:
    def __init__(self, service):
        self.service = service
        self.store = service.store

    def _allowed(self, project_id, path):
        project = self.service.registry.get(project_id)
        return any(path.is_relative_to(Path(root).resolve()) for root in project['roots'].values())

    def _candidate(self, project_id, target, origin, purpose='', strict=True):
        try:
            foreign = _foreign_history_path(target) if not strict else None
            path = foreign if foreign is not None else local_path(target, resolve_links=strict)
            if strict and not self._allowed(project_id, path):
                return None
            # Historical source_ref is an absolute recorded location. Resolve
            # links only for current sources; display/open checks old locations.
            item = {'path': os.fspath(target) if foreign is not None else str(path),
                    'origin': origin, 'purpose': str(purpose or '')[:2000]}
            # Do not infer a historical location's kind from a redirected target.
            if strict and path.exists():
                item['is_directory'] = path.is_dir()
            return item
        except (ValueError, OSError, RuntimeError):
            return None

    def _snapshot_files(self, project_id, snapshot, *, strict=False):
        if not snapshot:
            return []
        values = []
        for observation in snapshot.get('observations', []):
            item = self._candidate(project_id, observation.get('source_ref', ''), 'source',
                                   observation.get('source_id', '来源文件'), strict)
            if item:
                values.append(item)
        for entry in snapshot.get('entries', []):
            if entry.get('type') in {'url', 'open_url'}:
                continue
            item = self._candidate(project_id, entry.get('target', ''), 'entry', entry.get('label', ''), strict)
            if item:
                if entry.get('type') == 'open_folder':
                    item['is_directory'] = True
                values.append(item)
        return merge_files(values, {})

    def _current_files(self, project_id, scope, line):
        snapshot = self.store.snapshot(self.store.state(scope)['active_snapshot'], scope)
        values = self._snapshot_files(project_id, snapshot, strict=True)
        try:
            project = self.service._focused_project(project_id, line.get('focus_id'))
        except (ValueError, OSError, RuntimeError):
            # A missing RVC job must not fall back to another task's files.
            return values
        sources = {s['id']: s for s in project.get('sources', [])}
        for source in sources.values():
            try:
                path = self.service.registry.source_path(project, source)
                item = self._candidate(project_id, path, 'source', source.get('label', source['id']))
                if item:
                    values.append(item)
            except (ValueError, OSError, RuntimeError):
                continue
        for entry in project.get('entries', []):
            try:
                if entry.get('type') == 'open_source':
                    target = self.service.registry.source_path(project, sources[entry['source_id']])
                elif entry.get('type') == 'open_folder':
                    target = self.service.registry.resolve(project, entry.get('root', 'main'), entry.get('path', '.'))
                else:
                    continue
                item = self._candidate(project_id, target, 'entry', entry.get('label', target.name))
                if item:
                    values.append(item)
            except (KeyError, ValueError, OSError, RuntimeError):
                continue
        return merge_files(values, {})

    @staticmethod
    def _metadata(line, node_id, kind, title, recorded_at=None, description='', **extra):
        return dict(id=node_id, title=title, workline_id=line['id'], workline_name=line['name'],
                    kind=kind, recorded_at=recorded_at, description=description, **extra)

    def _nodes(self, project_id, workline_id=None, *, context=None):
        project_id = self.service.registry.get(project_id)['id']
        # Context belongs only to this one read. Returning metadata never leaks
        # the frozen manifests, and later refreshes always observe fresh data.
        context = context if context is not None else {}
        scopes = context.setdefault('scopes', {})
        records = context.setdefault('records', {})
        custom_nodes = context.setdefault('custom_nodes', {})
        lines = self.service.list_worklines(project_id)
        if workline_id is not None:
            self.service._scope(project_id, workline_id)
            lines = [line for line in lines if line['id'] == workline_id]
        nodes = []
        for line in lines:
            _, scope, line = self.service._scope(project_id, line['id'])
            scopes[line['id']] = (scope, line)
            snapshot = self.store.snapshot(self.store.state(scope)['active_snapshot'], scope)
            nodes.append(self._metadata(line, 'current:' + line['id'], 'current', '当前工作',
                                       snapshot.get('observed_at') if snapshot else None,
                                       '已登记的来源、材料入口和你关联的文件。'))
            for record in self.store.history(scope):
                kind = record.get('event_kind', 'handoff')
                title = record.get('current_task') or record.get('last_done') or record.get('next_action') or '进度记录'
                node_id = f"record:{line['id']}:{record['revision']}"
                records[node_id] = record
                nodes.append(self._metadata(line, node_id, kind,
                                           title, record.get('recorded_at'), record.get('last_done') or record.get('next_action', ''),
                                           revision=record['revision']))
        allowed_lines = {line['id']: line for line in lines}
        for stored in self.store.file_nodes(project_id):
            line = allowed_lines.get(stored['workline_id'])
            if line:
                custom_nodes[stored['id']] = stored
                nodes.append(self._metadata(line, stored['id'], 'manual', stored['title'],
                                           stored['recorded_at'], stored.get('description', '')))
        return project_id, nodes

    def _resolve(self, project_id, node_id, node=None, *, context=None):
        if not isinstance(node_id, str) or len(node_id) > 220:
            raise ValueError('文件节点编号无效。')
        context = context if context is not None else {}
        if node is None:
            project_id, nodes = self._nodes(project_id, context=context)
            node = next((node for node in nodes if node['id'] == node_id), None)
        if node is None:
            raise ValueError('文件节点不存在，或属于其他项目。')
        cached_scope = context.get('scopes', {}).get(node['workline_id'])
        if cached_scope is None:
            _, scope, line = self.service._scope(project_id, node['workline_id'])
        else:
            scope, line = cached_scope
        notice = ''
        if node['kind'] == 'current':
            base = self._current_files(project_id, scope, line)
            notice = '文件位置来自已登记来源和手动关联；文件夹不会自动展开。'
        elif node['kind'] == 'manual':
            stored = context.get('custom_nodes', {}).get(node_id)
            if stored is None:
                stored = next(item for item in self.store.file_nodes(project_id) if item['id'] == node_id)
            base = stored.get('file_manifest', [])
            notice = '这是创建节点时保存的文件位置；可以继续添加或移除关联。'
        else:
            record = context.get('records', {}).get(node_id)
            if record is None:
                record = next(item for item in self.store.history(scope) if item['revision'] == node['revision'])
            base = record.get('file_manifest')
            if base is None:
                snapshot = self.store.snapshot(record.get('source_snapshot_id'), scope)
                base = self._snapshot_files(project_id, snapshot)
                notice = ('旧记录仅能还原当时来源快照中留存的文件位置。' if snapshot else
                          '这条旧记录没有文件清单或来源快照；可手动补充当时的文件。')
            else:
                notice = '清单保留保存进度时的文件位置；文件内容仍是磁盘上的当前版本。'
        state = self.store.node_file_state(project_id, node_id)
        return project_id, node, base, state, notice

    def list_nodes(self, project_id, workline_id=None):
        context = {}
        project_id, nodes = self._nodes(project_id, workline_id, context=context)
        for node in nodes:
            _, _, base, state, _ = self._resolve(project_id, node['id'], node, context=context)
            node['file_count'] = len(merge_files(base, state))
        return nodes

    def _display_file(self, project_id, node_id, item):
        foreign = _foreign_history_path(item['path'])
        path = foreign if foreign is not None else Path(item['path'])
        allowed, exists, is_dir = True, False, False
        try:
            if foreign is not None:
                raise ValueError('历史文件位置属于其他操作系统。')
            resolved = local_path(path)
            # Re-check redirects at use time; a later junction must not silently
            # change the target that was originally associated.
            allowed = path_key(resolved) == path_key(path)
            if item.get('origin') != 'manual':
                allowed = allowed and self._allowed(project_id, resolved)
            if allowed:
                exists, is_dir = path.exists(), path.is_dir()
        except (ValueError, OSError, RuntimeError):
            allowed = False
        suffix = path.suffix.lower()
        if is_dir or (not exists and item.get('is_directory') is True):
            kind, action, label = '文件夹', 'open', '打开文件夹'
        elif suffix in SCRIPTS:
            kind, action, label = '脚本文本', 'open', '查看文本'
        elif suffix in WEIGHTS:
            kind, action, label = ('模型索引' if suffix == '.index' else '模型权重'), 'reveal', '在资源管理器中打开'
        elif suffix in DOCUMENTS:
            kind, action, label = '文件', 'open', '打开'
        else:
            kind, action, label = '其他文件', 'reveal', '在资源管理器中打开'
        if not exists:
            action, label = 'reveal', '定位所在文件夹'
        evidence = _version_evidence(item.get('recorded_metadata'),
                                     file_metadata(path, hash_limit=EVIDENCE_SIZE_LIMIT if item.get('evidence') else METADATA_HASH_LIMIT)
                                     if allowed and exists and not is_dir else None,
                                     allowed=allowed, exists=exists)
        return dict(item, id=file_id(project_id, node_id, item['path']), name=path.name or str(path),
                    parent=str(path.parent), kind=kind, exists=exists,
                    status='位置不可访问' if not allowed else '可用' if exists else '文件已缺失',
                    open_action=action, action_label=label, **evidence)

    def get(self, project_id, node_id):
        project_id, node, base, state, notice = self._resolve(project_id, node_id)
        files = [self._display_file(project_id, node_id, item) for item in merge_files(base, state)]
        node['file_count'] = len(files)
        return {'node': node, 'files': files, 'notice': notice}

    def _capture_versions(self, project_id, node_id, items):
        # A newly saved record describes save-time versions, even when a file
        # was associated earlier. Restoring an old record keeps its old baseline.
        return [dict(item, recorded_metadata=self._display_file(project_id, node_id, item)['current_metadata'])
                for item in items]

    def _evidence_path(self, relative):
        path = self.service._write_path(relative)
        expected = self.service.data_dir / relative
        if path_key(path) != path_key(expected):
            raise ValueError('证据目录或文件已重定向，已停止保存。')
        return path

    def save_evidence(self, project_id, node_id, identity):
        """Explicitly preserve the current bytes, not a historical version."""
        project_id, node, base, state, _ = self._resolve(project_id, node_id)
        if node['kind'] == 'current':
            raise ValueError('请先选择一条进度记录或保存的文件节点，再保存证据副本。')
        original = self._selected(project_id, node_id, identity, base, state)
        item = self._display_file(project_id, node_id, original)
        if item['status'] != '可用':
            raise ValueError(item['status'] + '：' + item['path'])
        source = Path(item['path'])
        before = source.stat(follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('证据副本只支持普通文件，不复制文件夹。')
        if before.st_size > EVIDENCE_SIZE_LIMIT:
            raise ValueError('证据副本每个文件最多 10 MiB。')
        fd = os.open(source, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
                raise ValueError('文件位置已变化，请刷新后重试。')
            content = stream.read(EVIDENCE_SIZE_LIMIT + 1)
            after = os.fstat(stream.fileno())
        if len(content) > EVIDENCE_SIZE_LIMIT:
            raise ValueError('证据副本每个文件最多 10 MiB。')
        if ((before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
                or path_key(local_path(source)) != path_key(source)):
            raise ValueError('保存期间文件已变化，请刷新后重试。')
        digest = hashlib.sha256(content).hexdigest()
        scope = hashlib.sha256((project_id + '\0' + node_id).encode('utf-8')).hexdigest()[:24]
        suffix = source.suffix if re.fullmatch(r'\.[A-Za-z0-9]{1,12}', source.suffix) else '.bin'
        folder = Path('evidence') / scope
        created = None
        result = {}

        def update(latest):
            nonlocal created
            # Revalidate association under the write lock; another window may
            # have removed/relinked it while the bounded source was read.
            selected = self._selected(project_id, node_id, identity, base, latest)
            if selected['path'] != str(source):
                raise ValueError('文件关联已变化，请刷新后重试。')
            existing = merge_files(base, latest)
            for value in existing:
                evidence = value.get('evidence') or {}
                if evidence.get('source_path') != str(source) or evidence.get('sha256') != digest:
                    continue
                candidate = Path(value['path'])
                try:
                    relative = candidate.relative_to(self.service.data_dir)
                    checked = self._evidence_path(relative)
                    current = file_metadata(checked, hash_limit=EVIDENCE_SIZE_LIMIT)
                except (OSError, ValueError):
                    continue
                if current and current.get('sha256') == digest:
                    result.update(file=value, reused=True)
                    return latest
            if len(existing) >= MAX_FILES:
                raise ValueError(f'每个节点最多关联 {MAX_FILES} 个文件或文件夹。')
            self._evidence_path(folder).mkdir(parents=True, exist_ok=True)
            self._evidence_path(folder)
            # A unique path belongs to this transaction alone. Identical
            # committed copies are reused above, under the same database lock.
            relative = folder / (identity + '-' + digest[:16] + '-' + uuid.uuid4().hex[:16] + suffix)
            target = self._evidence_path(relative)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            created = relative
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            self._evidence_path(relative)
            metadata = file_metadata(target, hash_limit=EVIDENCE_SIZE_LIMIT)
            if not metadata or metadata.get('sha256') != digest:
                raise ValueError('证据副本校验失败，请重试。')
            value = {'path': str(target), 'origin': 'manual', 'purpose': '证据副本（保存时的当前版本）：' + source.name,
                     'is_directory': False, 'recorded_metadata': metadata,
                     'evidence': {'source_path': str(source), 'frozen_at': now(), 'sha256': digest,
                                  'version': 'current_at_save'}}
            additions = list(latest['additions']) + [value]
            result.update(file=value, reused=False)
            return {'additions': additions, 'hidden': list(latest['hidden'])}

        try:
            self.store.update_node_files(project_id, node_id, update)
        except BaseException:
            if created is not None:
                # Remove only our newly-created, still-contained file. Never
                # unlink a redirected destination or an existing evidence copy.
                try:
                    self._evidence_path(created).unlink(missing_ok=True)
                except (OSError, ValueError):
                    pass
            raise
        value = result['file']
        return {'saved': True, 'reused': result['reused'], 'file': self._display_file(project_id, node_id, value),
                'source_path': str(source), 'frozen_at': value['evidence']['frozen_at'],
                'bytes': len(content), 'sha256': digest}

    def create(self, project_id, title, workline_id=None, description='', include_current=True):
        project_id, scope, line = self.service._scope(project_id, workline_id)
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120 or any(ord(c) < 32 for c in title):
            raise ValueError('节点名称应为 1 至 120 字，不含控制字符。')
        if not isinstance(description, str) or len(description) > 12000 or not isinstance(include_current, bool):
            raise ValueError('节点说明或包含当前文件的选项无效。')
        base = self._current_files(project_id, scope, line) if include_current else []
        state = self.store.node_file_state(project_id, 'current:' + line['id']) if include_current else {}
        manifest = self._capture_versions(project_id, 'current:' + line['id'], merge_files(base, state))
        if len(manifest) > MAX_FILES:
            raise ValueError(f'每个节点最多关联 {MAX_FILES} 个文件或文件夹。')
        node = self._metadata(line, 'manual:' + uuid.uuid4().hex, 'manual', title.strip(), now(), description.strip())
        self.store.create_file_node(project_id, dict(node, file_manifest=manifest))
        return dict(node, file_count=len(manifest))

    def add(self, project_id, node_id, paths, purpose=''):
        project_id, _, base, _, _ = self._resolve(project_id, node_id)
        if not isinstance(paths, (list, tuple)) or not 1 <= len(paths) <= MAX_FILES:
            raise ValueError(f'一次请选择 1 至 {MAX_FILES} 个文件或文件夹。')
        if not isinstance(purpose, str) or len(purpose) > 2000:
            raise ValueError('文件用途应为 2000 字以内的文字。')
        selected = [local_path(path, must_exist=True) for path in paths]
        items = [{'path': str(path), 'origin': 'manual', 'purpose': purpose.strip(), 'is_directory': path.is_dir(),
                  'recorded_metadata': file_metadata(path)}
                 for path in selected]
        def update(state):
            additions = {path_key(item['path']): item for item in state['additions']}
            hidden = set(state['hidden'])
            existing = {path_key(item['path']): item for item in merge_files(base, state)}
            for item in items:
                key = path_key(item['path'])
                # Re-selecting files is common in a batch picker. A blank shared
                # purpose must not erase a note that the user already wrote.
                if key in existing and not item['purpose']:
                    continue
                additions[key] = item
                hidden.discard(key)
            result = {'additions': list(additions.values()), 'hidden': sorted(hidden)}
            if len(merge_files(base, result)) > MAX_FILES:
                raise ValueError(f'每个节点最多关联 {MAX_FILES} 个文件或文件夹。')
            return result
        self.store.update_node_files(project_id, node_id, update)
        return self.get(project_id, node_id)

    @staticmethod
    def _selected(project_id, node_id, identity, base, state):
        item = next((item for item in merge_files(base, state)
                     if file_id(project_id, node_id, item['path']) == identity), None)
        if item is None:
            raise ValueError('文件关联已变化或已移除，请刷新文件列表后重新选择。')
        return item

    def update_file(self, project_id, node_id, identity, purpose=''):
        project_id, _, base, _, _ = self._resolve(project_id, node_id)
        if not isinstance(purpose, str) or len(purpose) > 2000:
            raise ValueError('文件用途应为 2000 字以内的文字。')
        def update(state):
            item = self._selected(project_id, node_id, identity, base, state)
            changed = dict(item, purpose=purpose.strip())
            key = path_key(item['path'])
            additions = {path_key(value['path']): value for value in state['additions']}
            additions[key] = changed
            return {'additions': list(additions.values()), 'hidden': list(state['hidden'])}
        self.store.update_node_files(project_id, node_id, update)
        return self.get(project_id, node_id)

    def relocate(self, project_id, node_id, identity, new_path):
        project_id, _, base, _, _ = self._resolve(project_id, node_id)
        target = local_path(new_path, must_exist=True)
        target_key = path_key(target)
        def update(state):
            item = self._selected(project_id, node_id, identity, base, state)
            old_key = path_key(item['path'])
            if old_key == target_key:
                return state
            if any(path_key(value['path']) == target_key for value in merge_files(base, state)):
                raise ValueError('新位置已在这个节点中关联，请选择其他位置；原关联保持不变。')
            # The old base/manifest is immutable. One overlay transaction hides
            # its old position and adds the user's explicitly selected location.
            changed = dict(item, path=str(target), origin='manual', is_directory=target.is_dir(),
                           relinked_from=item['path'])
            additions = {path_key(value['path']): value for value in state['additions']}
            additions.pop(old_key, None)
            additions[target_key] = changed
            hidden = set(state['hidden'])
            hidden.add(old_key)
            hidden.discard(target_key)
            return {'additions': list(additions.values()), 'hidden': sorted(hidden)}
        self.store.update_node_files(project_id, node_id, update)
        return self.get(project_id, node_id)

    def rename(self, project_id, node_id, title, description=None):
        project_id, node, _, _, _ = self._resolve(project_id, node_id)
        if node['kind'] != 'manual':
            raise ValueError('只有自定义节点可以修改名称；进度记录标题保留原样。')
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120 or any(ord(c) < 32 for c in title):
            raise ValueError('节点名称应为 1 至 120 字，不含控制字符。')
        if description is not None and (not isinstance(description, str) or len(description) > 12000):
            raise ValueError('节点说明应为 12000 字以内的文字。')
        def update(stored):
            result = dict(stored, title=title.strip())
            if description is not None:
                result['description'] = description.strip()
            return result
        self.store.update_file_node(project_id, node_id, update)
        return self.get(project_id, node_id)['node']

    def remove(self, project_id, node_id, identity):
        project_id, _, base, state, _ = self._resolve(project_id, node_id)
        item = next((item for item in merge_files(base, state)
                     if file_id(project_id, node_id, item['path']) == identity), None)
        if item is None:
            raise ValueError('文件关联不存在，请刷新文件列表。')
        key = path_key(item['path'])
        def update(state):
            return {'additions': [item for item in state['additions'] if path_key(item['path']) != key],
                    'hidden': sorted(set(state['hidden']) | {key})}
        self.store.update_node_files(project_id, node_id, update)
        return self.get(project_id, node_id)

    def manifest_for_record(self, project_id, scope, line, snapshot_id, restored_from=None):
        if restored_from is not None:
            if not isinstance(restored_from, int) or isinstance(restored_from, bool):
                raise ValueError('恢复来源版本号无效。')
            old = next((item for item in self.store.history(scope) if item['revision'] == restored_from), None)
            if old is None:
                raise ValueError('找不到原始交接版本。')
            if 'file_manifest' in old:
                return old['file_manifest']
            return self._snapshot_files(project_id, self.store.snapshot(old.get('source_snapshot_id'), scope))
        # When the user saved against an older observed snapshot, newer auto
        # entries must not be presented as historical evidence for that record.
        base = (self._snapshot_files(project_id, self.store.snapshot(snapshot_id, scope)) if snapshot_id is not None
                else self._current_files(project_id, scope, line))
        return self._capture_versions(project_id, 'current:' + line['id'],
                                      merge_files(base, self.store.node_file_state(project_id, 'current:' + line['id'])))

    def open(self, project_id, node_id, identity, action='open'):
        if not isinstance(action, str) or action not in {'open', 'reveal', 'copy'}:
            raise ValueError('文件操作必须为 open、reveal 或 copy。')
        item = next((item for item in self.get(project_id, node_id)['files'] if item['id'] == identity), None)
        if item is None:
            raise ValueError('文件关联不存在，请刷新文件列表。')
        if action == 'copy':
            return str(item['path'])
        if item['status'] == '位置不可访问':
            raise ValueError('原文件位置已重定向或不在登记范围内；请重新关联该文件。')
        path = Path(item['path'])
        if action == 'open' and not item['exists']:
            raise ValueError('文件已移动或删除，可定位所在文件夹后重新关联。')
        if action == 'reveal' or (action == 'open' and item['open_action'] == 'reveal'):
            platform_io.reveal_path(path)
        elif path.is_dir():
            platform_io.open_document(path)
        elif path.suffix.lower() in SCRIPTS:
            platform_io.open_text(path)
        else:
            platform_io.open_document(path)
        return str(path)
