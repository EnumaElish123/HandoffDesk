"""Explicit, local-only onboarding. Registered project files are never written."""
import json
import os
from pathlib import Path, PureWindowsPath
import re
import tempfile

from .paths import app_directory, configuration_path, require_configuration, validate_configuration_writes
from .registry import Registry


def _project(app, project_id, name, root, data_dir=None):
    if not isinstance(project_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', project_id):
        raise ValueError('项目 ID 只能含字母、数字、下划线和连字符。')
    name = name if name is not None else project_id
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100 or any(ord(c) < 32 for c in name):
        raise ValueError('项目名称应为 1 至 100 字，不含控制字符。')
    path = Path(root).expanduser()
    if not path.is_absolute() or (os.name != 'nt' and PureWindowsPath(str(root)).drive):
        raise ValueError('项目根目录必须是本机绝对目录。')
    if not path.is_dir():
        raise ValueError('项目根目录必须已经存在，且为目录。')
    path = path.resolve()
    project = {'id': project_id, 'name': name.strip(), 'roots': {'main': str(path)},
               'adapters': ['registered_files', 'git_readonly'], 'sources': [], 'entries': []}
    validate_configuration_writes(app, [project], data_dir)
    return project


def _write(app, target, value, *, create):
    """Serialize cooperating writers and atomically publish a validated config."""
    config = target.parent
    config.mkdir(parents=True, exist_ok=True)
    if configuration_path(app) != target:
        raise ValueError('配置路径在操作期间发生变化。')
    lock = config / '.projects.lock'
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise ValueError('项目配置正在被更新；请稍后重试。如上次进程意外结束，请确认没有写入进程后删除 config/.projects.lock。') from exc
    pending = None
    try:
        if create and (target.exists() or target.is_symlink()):
            raise ValueError('已存在项目配置，init 不会覆盖；请使用 handoffdesk project add 添加项目。')
        if not create:
            # Reject concurrent edits between our initial read and write.
            expected = value.pop('_expected_bytes')
            if target.read_bytes() != expected:
                raise ValueError('项目配置已变化，请重新运行 project add。')
        encoded = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        with tempfile.NamedTemporaryFile(mode='wb', dir=config, prefix='.projects-', suffix='.tmp', delete=False) as handle:
            pending = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        # Registry resolves roots.local.json next to this file as usual.
        Registry(pending)
        if configuration_path(app) != target:
            raise ValueError('配置路径在操作期间发生变化。')
        os.replace(pending, target)
        pending = None
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)
        lock.rmdir()


def initialize(app_dir=None, *, project_id=None, name=None, root=None, data_dir=None):
    app = app_directory(app_dir)
    target = configuration_path(app)
    if target.exists() or target.is_symlink():
        raise ValueError('已存在项目配置，init 不会覆盖；请使用 handoffdesk project add 添加项目。')
    if (target.parent / 'roots.local.json').exists():
        raise ValueError('已有本机目录映射，请先检查现有配置；init 不会覆盖。')
    if root is None:
        if project_id is not None or name is not None:
            raise ValueError('指定项目编号或名称时必须同时提供 --root。')
        project_id, name = 'demo', 'Demo project'
        demo = (app / 'examples' / 'demo-project').resolve()
        if not demo.is_relative_to(app):
            raise ValueError('示例目录不能重定向到状态目录之外。')
        demo.mkdir(parents=True, exist_ok=True)
        readme = demo / 'README.md'
        if not readme.exists():
            with readme.open('x', encoding='utf-8') as handle:
                handle.write('# Demo project\n\nThis is a local HandoffDesk example. Add your own project with `handoffdesk project add`.\n')
        project = _project(app, project_id, name, demo, data_dir)
        project['sources'] = [{'id': 'demo.readme', 'root': 'main', 'path': 'README.md', 'format': 'markdown', 'purpose': 'project_overview'}]
        project['entries'] = [{'id': 'readme', 'type': 'open_source', 'source_id': 'demo.readme', 'label': 'Demo README'}]
    else:
        if project_id is None:
            raise ValueError('接入现有项目时必须提供 --project ID。')
        project = _project(app, project_id, name, root, data_dir)
    _write(app, target, {'schema_version': 1, 'projects': [project]}, create=True)
    return {'schema_version': 1, 'ok': True, 'initialized': True, 'app_dir': str(app), 'configuration': str(target), 'project': project}


def add_project(app_dir, project_id, *, name=None, root, data_dir=None):
    app = app_directory(app_dir)
    target = require_configuration(app)
    original = target.read_bytes()
    registry = Registry(target)
    # Existing manual configuration/local mappings must also be safe before
    # creating the lock and temporary file next to projects.json.
    validate_configuration_writes(app, registry.projects, data_dir)
    if any(project['id'] == project_id for project in registry.projects):
        raise ValueError('项目 ID 已存在，请使用不同编号。')
    project = _project(app, project_id, name, root, data_dir)
    new_root = Path(project['roots']['main'])
    for existing in registry.projects:
        for value in existing['roots'].values():
            known = Path(value).resolve()
            if new_root.is_relative_to(known) or known.is_relative_to(new_root):
                raise ValueError('项目根目录与已登记项目重叠，请选择独立目录。')
    value = json.loads(original.decode('utf-8-sig'))
    value['projects'].append(project)
    value['_expected_bytes'] = original
    _write(app, target, value, create=False)
    return {'schema_version': 1, 'ok': True, 'added': True, 'app_dir': str(app), 'configuration': str(target), 'project': project}
