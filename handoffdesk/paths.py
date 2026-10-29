"""Resolve writable user state without depending on a source checkout."""
import os
from pathlib import Path
import sys


def _comparison_path(value):
    """Use one identity for resolved Windows drive/UNC path spellings."""
    path = Path(value)
    if os.name == 'nt':
        name = str(path)
        if name.startswith('\\\\?\\UNC\\'):
            return Path('\\\\' + name[8:])
        if (name.startswith('\\\\?\\') and len(name) >= 7
                and name[4].isalpha() and name[5:7] == ':\\'):
            return Path(name[4:])
    return path


def _is_within(path, parent):
    """Compare already resolved paths without changing filesystem operations."""
    return _comparison_path(path).is_relative_to(_comparison_path(parent))


def _resolve_path(value):
    """Keep ordinary Windows paths stable when realpath adds a device prefix.

    A concurrent creator can make non-strict Windows resolution return the
    extended-length spelling of an otherwise ordinary path. Preserve explicitly
    supplied device paths for actual filesystem operations.
    """
    path = Path(value)
    resolved = path.resolve()
    if not str(path).startswith('\\\\?\\'):
        return _comparison_path(resolved)
    return resolved


def app_directory(explicit=None):
    """An explicit home wins over the environment and platform defaults."""
    value = explicit if explicit is not None else os.environ.get('HANDOFFDESK_HOME')
    if value is not None:
        path = Path(value).expanduser()
        if not str(value).strip() or not path.is_absolute():
            raise ValueError('HandoffDesk 状态目录必须是本机绝对路径（--app-dir / HANDOFFDESK_HOME）。')
        return _resolve_path(path)
    from .integration import registered_app_directory
    registered = registered_app_directory()
    if registered is not None:
        return _resolve_path(registered)
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
        if not base.is_absolute():
            raise ValueError('LOCALAPPDATA 必须是本机绝对路径。')
        return _resolve_path(base / 'HandoffDesk')
    base = Path(os.environ.get('XDG_DATA_HOME', ''))
    if not base.is_absolute():
        base = Path.home() / '.local' / 'share'
    return _resolve_path(base / 'handoffdesk')


def configuration_path(app):
    """Configuration and its file must remain inside the chosen state directory."""
    app = _resolve_path(app)
    config = _resolve_path(app / 'config')
    target = config / 'projects.json'
    if not _is_within(config, app) or not _is_within(_resolve_path(target), config):
        raise ValueError('项目配置不能通过符号链接指向状态目录之外。')
    return target


def require_configuration(app):
    target = configuration_path(app)
    if not target.is_file():
        raise ValueError('HandoffDesk 尚未初始化。请先运行 handoffdesk init（或 python -m handoffdesk init）；使用相同的 --app-dir / HANDOFFDESK_HOME。')
    return target


def validate_configuration_writes(app, projects, data_dir=None):
    """Check all effective roots before writing configuration or startup state."""
    app = _resolve_path(app)
    config = configuration_path(app).parent
    data_folders = [_resolve_path(app / 'data')]
    if data_dir is not None:
        data_folders.append(_resolve_path(data_dir))
    for project in projects:
        for value in project['roots'].values():
            root = _resolve_path(value)
            if (_is_within(app, root) or _is_within(config, root)
                    or _is_within(root, config)
                    or any(_is_within(folder, root) or _is_within(root, folder) for folder in data_folders)):
                raise ValueError('项目根目录不能与工具的配置或数据目录重叠；状态目录必须独立于接入项目。')
