"""Resolve writable user state without depending on a source checkout."""
import os
from pathlib import Path
import sys


def app_directory(explicit=None, *, registry_path=None):
    """An explicit home wins over the environment and platform defaults."""
    value = explicit if explicit is not None else os.environ.get('HANDOFFDESK_HOME')
    if value is not None:
        path = Path(value).expanduser()
        if not str(value).strip() or not path.is_absolute():
            raise ValueError('HandoffDesk 状态目录必须是本机绝对路径（--app-dir / HANDOFFDESK_HOME）。')
        return path.resolve()
    from .integration import registered_app_directory
    registered = registered_app_directory(registry_path)
    if registered is not None:
        return registered
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
        if not base.is_absolute():
            raise ValueError('LOCALAPPDATA 必须是本机绝对路径。')
        return (base / 'HandoffDesk').resolve()
    base = Path(os.environ.get('XDG_DATA_HOME', ''))
    if not base.is_absolute():
        base = Path.home() / '.local' / 'share'
    return (base / 'handoffdesk').resolve()


def configuration_path(app):
    """Configuration and its file must remain inside the chosen state directory."""
    app = Path(app).resolve()
    config = (app / 'config').resolve()
    target = config / 'projects.json'
    if not config.is_relative_to(app) or not target.resolve().is_relative_to(config):
        raise ValueError('项目配置不能通过符号链接指向状态目录之外。')
    return target


def require_configuration(app):
    target = configuration_path(app)
    if not target.is_file():
        raise ValueError('HandoffDesk 尚未初始化。请先运行 handoffdesk init（或 python -m handoffdesk init）；使用相同的 --app-dir / HANDOFFDESK_HOME。')
    return target


def validate_configuration_writes(app, projects, data_dir=None):
    """Check all effective roots before writing configuration or startup state."""
    app = Path(app).resolve()
    config = configuration_path(app).parent
    data_folders = [(app / 'data').resolve()]
    if data_dir is not None:
        data_folders.append(Path(data_dir).resolve())
    for project in projects:
        for value in project['roots'].values():
            root = Path(value).resolve()
            if (app.is_relative_to(root) or config.is_relative_to(root)
                    or root.is_relative_to(config)
                    or any(folder.is_relative_to(root) or root.is_relative_to(folder) for folder in data_folders)):
                raise ValueError('项目根目录不能与工具的配置或数据目录重叠；状态目录必须独立于接入项目。')
