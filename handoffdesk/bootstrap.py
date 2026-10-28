"""Explicit, local-only onboarding. Registered project files are never written."""
import json
import os
from pathlib import Path, PureWindowsPath
import re
import tempfile
import time
from contextlib import contextmanager

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


@contextmanager
def configuration_lock(app, timeout=10):
    """OS-owned lock is released on process exit; legacy directory locks stay explicit."""
    config = configuration_path(app).parent
    config.mkdir(parents=True, exist_ok=True)
    if (config / '.projects.lock').exists():
        raise ValueError('项目配置正在被更新；请确认旧版本写入已结束后再处理 config/.projects.lock。')
    guard = config / '.projects.guard'
    if guard.is_symlink() or not guard.resolve().is_relative_to(config):
        raise ValueError('项目配置锁不能重定向。')
    handle = guard.open('a+b')
    acquired = False
    try:
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise ValueError('项目配置正在被更新；请稍后重试。')
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _publish(app, target, value, *, create):
    """Caller holds configuration_lock; validate and atomically publish."""
    config = target.parent
    if configuration_path(app) != target:
        raise ValueError('配置路径在操作期间发生变化。')
    pending = None
    try:
        if create and (target.exists() or target.is_symlink()):
            raise ValueError('已存在项目配置，init 不会覆盖；请使用 handoffdesk project add 添加项目。')
        encoded = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        with tempfile.NamedTemporaryFile(mode='wb', dir=config, prefix='.projects-', suffix='.tmp', delete=False) as handle:
            pending = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        Registry(pending)
        if configuration_path(app) != target:
            raise ValueError('配置路径在操作期间发生变化。')
        os.replace(pending, target)
        pending = None
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def _write(app, target, value, *, create):
    with configuration_lock(app):
        if not create and '_expected_bytes' in value:
            expected = value.pop('_expected_bytes')
            if target.read_bytes() != expected:
                raise ValueError('项目配置已变化，请重新运行 project add。')
        _publish(app, target, value, create=create)


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
    # Reject unsafe existing mappings before creating the lock file.
    validate_configuration_writes(app, Registry(target).projects, data_dir)
    project = _project(app, project_id, name, root, data_dir)
    with configuration_lock(app):
        # Re-read after acquiring the lock: parallel registrations are merged.
        original = target.read_bytes()
        registry = Registry(target)
        validate_configuration_writes(app, registry.projects, data_dir)
        if any(item['id'] == project_id for item in registry.projects):
            raise ValueError('项目 ID 已存在，请使用不同编号。')
        new_root = Path(project['roots']['main'])
        for existing in registry.projects:
            for value in existing['roots'].values():
                known = Path(value).resolve()
                if new_root.is_relative_to(known) or known.is_relative_to(new_root):
                    raise ValueError('项目根目录与已登记项目重叠，请选择独立目录。')
        value = json.loads(original.decode('utf-8-sig'))
        value['projects'].append(project)
        _publish(app, target, value, create=False)
    return {'schema_version': 1, 'ok': True, 'added': True, 'app_dir': str(app), 'configuration': str(target), 'project': project}
