"""Connect only the task's directory; preserve stable project identity across checkouts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

from .bootstrap import _project, _publish, configuration_lock
from .paths import app_directory, configuration_path, validate_configuration_writes
from .registry import Registry


class WorkspaceError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _directory(value):
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise WorkspaceError('invalid_workspace', '工作目录必须是已存在的本机绝对目录。')
    return path.resolve()


def _git_identity(path):
    try:
        result = subprocess.run(['git', '-C', str(path), 'rev-parse', '--show-toplevel', '--git-common-dir'],
                                capture_output=True, text=True, encoding='utf-8', errors='replace',
                                timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    parts = result.stdout.splitlines()
    if result.returncode or len(parts) != 2:
        return None
    root = Path(parts[0]).resolve()
    common = Path(parts[1])
    common = (path / common).resolve() if not common.is_absolute() else common.resolve()
    return {'root': str(root), 'git_common_dir': str(common)}


class Workspace:
    def __init__(self, app_dir=None, data_dir=None):
        self.app_dir = app_directory(app_dir)
        self.data_dir = data_dir

    def inspect(self, cwd):
        cwd = _directory(cwd)
        target = configuration_path(self.app_dir)
        projects = Registry(target).projects if target.exists() else []
        matches = []
        for project in projects:
            for value in project['roots'].values():
                root = Path(value).resolve()
                if cwd.is_relative_to(root):
                    matches.append((len(root.parts), project, root))
        identity = _git_identity(cwd)
        if matches:
            longest = max(item[0] for item in matches)
            closest = [item for item in matches if item[0] == longest]
            if len({item[1]['id'] for item in closest}) != 1:
                raise WorkspaceError('project_ambiguous', '工作目录同时属于多个项目，请明确修正根目录映射。')
            _, project, root = closest[0]
            return {'connected': True, 'project': project, 'cwd': str(cwd), 'root': str(root),
                    'checkout': identity, 'match': 'registered_root'}
        if identity:
            peers = []
            for project in projects:
                roots = [Path(root).resolve() for root in project['roots'].values()]
                known = []
                for root in roots:
                    if not root.is_dir():
                        continue
                    observed = _git_identity(root)
                    # rev-parse also succeeds inside a monorepo subdirectory.
                    # A separately registered subdirectory does not own the
                    # enclosing Git repository or any of its other worktrees.
                    if observed and Path(observed['root']) == root:
                        known.append(observed['git_common_dir'])
                if not known and not any(root.exists() for root in roots):
                    # Retain explicit historical identity only when its old
                    # checkout is unavailable, never overrule an existing root.
                    known.append(project.get('workspace_identity', {}).get('git_common_dir'))
                if identity['git_common_dir'] in known:
                    peers.append(project)
            if len(peers) > 1:
                raise WorkspaceError('project_ambiguous', '该 Git worktree 对应多个登记项目，请明确项目归属。')
            if peers:
                return {'connected': True, 'project': peers[0], 'cwd': str(cwd), 'root': identity['root'],
                        'checkout': identity, 'match': 'git_common_dir'}
        return {'connected': False, 'project': None, 'cwd': str(cwd),
                'root': identity['root'] if identity else str(cwd), 'checkout': identity, 'match': None}

    def connect(self, cwd, name=None):
        inspected = self.inspect(cwd)
        if inspected['connected']:
            validate_configuration_writes(self.app_dir, [inspected['project']], self.data_dir)
            return dict(inspected, added=False)
        target = configuration_path(self.app_dir)
        # Validate before creating any state beside a business root.
        candidate = _project(self.app_dir, 'project-' + uuid.uuid4().hex[:16],
                             name or Path(inspected['root']).name[:100] or 'Project', inspected['root'], self.data_dir)
        if target.exists():
            validate_configuration_writes(self.app_dir, Registry(target).projects, self.data_dir)
        elif target.with_name('roots.local.json').exists():
            raise WorkspaceError('state_unavailable', '发现独立目录映射但缺少项目登记，请先恢复原配置。')
        with configuration_lock(self.app_dir):
            inspected = self.inspect(cwd)
            if inspected['connected']:
                return dict(inspected, added=False)
            if target.exists():
                registry = Registry(target)
                validate_configuration_writes(self.app_dir, registry.projects, self.data_dir)
                new_root = Path(inspected['root'])
                if any(Path(value).resolve().is_relative_to(new_root)
                       for project in registry.projects for value in project['roots'].values()):
                    raise WorkspaceError('project_ambiguous', '该目录包含已登记项目，请在明确的项目目录开始任务。')
                config = json.loads(target.read_text(encoding='utf-8-sig'))
            else:
                config = {'schema_version': 1, 'projects': []}
            if inspected['checkout']:
                candidate['workspace_identity'] = {'git_common_dir': inspected['checkout']['git_common_dir']}
            config['projects'].append(candidate)
            _publish(self.app_dir, target, config, create=not target.exists())
        return dict(inspected, project=candidate, connected=True, added=True, match='new_registration')

    def relocate(self, project_id, path):
        """An explicit move preserves IDs and history; it never moves user files."""
        path = _directory(path)
        target = configuration_path(self.app_dir)
        registry = Registry(target)
        validate_configuration_writes(self.app_dir, registry.projects, self.data_dir)
        _project(self.app_dir, project_id, None, path, self.data_dir)
        with configuration_lock(self.app_dir):
            registry = Registry(target)
            project = registry.get(project_id)
            for other in registry.projects:
                if other['id'] == project['id']:
                    continue
                for root in other['roots'].values():
                    known = Path(root).resolve()
                    if path.is_relative_to(known) or known.is_relative_to(path):
                        raise WorkspaceError('project_ambiguous', '新目录与其他登记项目重叠。')
            if 'main' not in project['roots']:
                raise WorkspaceError('invalid_workspace', '此项目使用多个自定义根目录，请通过 configure-roots 明确调整。')
            override = target.with_name('roots.local.json')
            if override.exists():
                value = json.loads(override.read_text(encoding='utf-8-sig'))
                value['projects'].setdefault(project['id'], {})['main'] = str(path)
                changed = Registry(target, roots_override=value)
                validate_configuration_writes(self.app_dir, changed.projects, self.data_dir)
                if override.is_symlink():
                    raise WorkspaceError('state_unavailable', '目录映射不能通过符号链接重定向。')
                pending = None
                try:
                    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                                     prefix='.roots-', suffix='.tmp', delete=False) as handle:
                        pending = Path(handle.name)
                        json.dump(value, handle, ensure_ascii=False, indent=2)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(pending, override)
                    pending = None
                finally:
                    if pending is not None:
                        pending.unlink(missing_ok=True)
            else:
                config = json.loads(target.read_text(encoding='utf-8-sig'))
                item = next(item for item in config['projects'] if item['id'] == project['id'])
                item['roots']['main'] = str(path)
                identity = _git_identity(path)
                item['workspace_identity'] = ({'git_common_dir': identity['git_common_dir']}
                                              if identity and Path(identity['root']) == path else {})
                _publish(self.app_dir, target, config, create=False)
        return {'project': project['id'], 'root': str(path), 'relocated': True}
