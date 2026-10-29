"""Reversible user-level Codex integration and honest installation diagnostics.

Only files recorded in our manifest are owned by this module. Codex remains
responsible for trusting hooks; writing a configuration never grants that trust.
"""
from __future__ import annotations

import base64
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid

from . import __version__


EVENTS = ('SessionStart', 'UserPromptSubmit', 'Stop', 'Interrupt', 'SessionEnd')
MAX_INPUT = 1024 * 1024


def _load(path, default=None):
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f'配置文件无法读取，请保留并检查：{path}') from exc
    if not isinstance(value, dict):
        raise ValueError(f'配置必须是 JSON 对象：{path}')
    return value


def _absolute(value, label):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f'{label} 必须是绝对路径。')
    return path.resolve()


def _safe_target(path):
    path = Path(path)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError(f'受管理文件不能通过符号链接重定向：{path}')
    return path


def _atomic(path, data):
    path = _safe_target(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.handoffdesk-', delete=False) as f:
            temp = Path(f.name)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def _json(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def _digest(data):
    return hashlib.sha256(data).hexdigest()


@contextmanager
def _lock(folder):
    _safe_target(folder)
    folder.mkdir(parents=True, exist_ok=True)
    lock = folder / '.handoffdesk-connect.lock'
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise ValueError('HandoffDesk 接入配置正在更新，请稍后重试。') from exc
    try:
        yield
    finally:
        lock.rmdir()


def installation_registry_path():
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
        return _absolute(base, 'LOCALAPPDATA') / 'HandoffDesk' / 'installation.json'
    base = Path(os.environ.get('XDG_CONFIG_HOME', ''))
    if not base.is_absolute():
        base = Path.home() / '.config'
    return base / 'handoffdesk' / 'installation.json'


def registered_app_directory(registry_path=None):
    path = Path(registry_path) if registry_path is not None else installation_registry_path()
    value = _load(path)
    if value is None:
        return None
    if value.get('schema_version') != 1 or not value.get('state_id') or not value.get('installation_id'):
        raise ValueError(f'HandoffDesk 安装登记不完整，请检查：{path}')
    return _absolute(value.get('app_dir', ''), '安装登记中的状态目录')


def register_installation(app_dir, registry_path=None):
    """Register one selected installation; explicit CLI/environment still win."""
    app = _absolute(app_dir, '状态目录')
    registry = Path(registry_path) if registry_path is not None else installation_registry_path()
    with _lock(registry.parent):
        registered_app_directory(registry)  # Validate before publishing any replacement.
        previous = _load(registry, {})
        metadata = app / 'integrations' / 'state.json'
        state = _load(metadata, {})
        if not state:
            state = {'schema_version': 1, 'state_id': str(uuid.uuid4())}
            _atomic(metadata, _json(state))
        same = previous.get('app_dir') == str(app)
        value = {'schema_version': 1, 'installation_id': previous.get('installation_id') if same else str(uuid.uuid4()),
                 'state_id': state['state_id'], 'app_dir': str(app), 'version': __version__,
                 'executable': os.path.abspath(sys.executable)}
        if registry.exists() and previous != value:
            _atomic(registry.with_name('installation.previous.json'), registry.read_bytes())
        _atomic(registry, _json(value))
    return value


def _prefix(app):
    executable = os.path.abspath(sys.executable)
    frozen = getattr(sys, 'frozen', False)
    if not frozen and sys.platform == 'win32' and Path(executable).name.lower() == 'pythonw.exe':
        console_python = Path(executable).with_name('python.exe')
        if not console_python.is_file():
            raise ValueError('接入需要同环境的 python.exe 控制台入口，以保留 JSON 回执。')
        executable = str(console_python)
    if getattr(sys, 'frozen', False) and sys.platform == 'win32':
        console = Path(executable).with_name('HandoffDeskCLI.exe')
        if console.is_file():
            executable = str(console)
    command = [executable] if frozen else [executable, '-X', 'utf8', '-m', 'handoffdesk']
    return command + ['--app-dir', str(app)]


def shell_command(argv, windows=False):
    """Quote each argument without interpreting user paths as shell code."""
    if not windows:
        return shlex.join(argv)
    # An explicit encoded PowerShell script works from both cmd and PowerShell
    # hook launchers. Single quotes protect paths containing $, `, and spaces.
    quoted = ["'" + str(arg).replace("'", "''") + "'" for arg in argv]
    script = '[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); & ' + ' '.join(quoted) + '; exit $LASTEXITCODE'
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    return 'powershell.exe -NoProfile -NonInteractive -EncodedCommand ' + encoded


def _locations(codex_home=None, skills_home=None):
    codex = _absolute(codex_home or os.environ.get('CODEX_HOME') or Path.home() / '.codex', 'Codex 目录')
    skills = _absolute(skills_home or Path.home() / '.agents' / 'skills', 'Skill 目录')
    return codex, skills


def _manifest_path(app):
    return app / 'integrations' / 'codex.json'


def _managed_hooks(app):
    result = {}
    for event in EVENTS:
        argv = _prefix(app) + ['integration-hook', '--event', event]
        handler = {'type': 'command', 'command': shell_command(argv),
                   'commandWindows': shell_command(argv, windows=True),
                   'timeout': 3 if event in {'Interrupt', 'SessionEnd'} else 10,
                   'statusMessage': 'HandoffDesk：检查记录与汇报'}
        result[event] = {'hooks': [handler]}
    return result


def _without_owned_hooks(document, owned):
    result = json.loads(json.dumps(document))
    hooks = result.setdefault('hooks', {})
    if not isinstance(hooks, dict):
        raise ValueError('现有 hooks.json 的 hooks 必须是对象；未做修改。')
    for event, group in owned.items():
        groups = hooks.get(event, [])
        if not isinstance(groups, list):
            raise ValueError(f'现有 hooks.json 的 {event} 必须是列表；未做修改。')
        # Equality, not a name/prefix match: edited user configuration survives.
        groups = [item for item in groups if item != group]
        if groups:
            hooks[event] = groups
        else:
            hooks.pop(event, None)
    return result


def connect_codex(app_dir, *, codex_home=None, skills_home=None, hooks=True, registry_path=None):
    from .paths import app_directory
    app = app_directory(app_dir)
    codex, skills = _locations(codex_home, skills_home)
    manifest_path = _manifest_path(app)
    with _lock(codex):
        owner_path = codex / 'handoffdesk-installation.json'
        owner = _load(owner_path, {})
        if owner and owner.get('app_dir') != str(app):
            raise ValueError('此 Codex 已连接另一份 HandoffDesk 状态。请先用原状态目录运行 disconnect codex，再连接新安装，避免两个记录器同时补救。')
        old = _load(manifest_path, {})
        if old and (old['codex_home'] != str(codex) or old['skills_home'] != str(skills)):
            raise ValueError('此安装已连接其他 Codex 目录，请先断开原连接。')
        hook_file = codex / 'hooks.json'
        original = _load(hook_file, {})
        merged = _without_owned_hooks(original, old.get('hooks', {}))
        desired_hooks = _managed_hooks(app) if hooks else {}
        for event, group in desired_hooks.items():
            merged['hooks'].setdefault(event, []).append(group)
        template = Path(__file__).parent / 'integrations' / 'handoffdesk' / 'SKILL.md'
        command = shell_command(_prefix(app), windows=sys.platform == 'win32')
        # Skill command is direct PowerShell syntax on Windows for readability.
        if sys.platform == 'win32':
            command = '& ' + ' '.join("'" + arg.replace("'", "''") + "'" for arg in _prefix(app))
        content = template.read_text(encoding='utf-8').replace('@HANDOFFDESK_COMMAND@', command)
        desired_files = {skills / 'handoffdesk' / 'SKILL.md': content.encode('utf-8'),
                         skills / 'handoffdesk' / 'agents' / 'openai.yaml':
                         (template.parent / 'agents' / 'openai.yaml').read_bytes()}
        previous_files = old.get('files', {})
        for path in desired_files:
            _safe_target(path)
            if path.exists() and _digest(path.read_bytes()) != previous_files.get(str(path)):
                raise ValueError(f'同名 Skill 已存在或已被修改，未覆盖：{path}')
        if hooks or old.get('hooks'):
            _safe_target(hook_file)
            desired_files[hook_file] = _json(merged)
        for path in desired_files:
            _safe_target(path)
        before = {path: path.read_bytes() if path.exists() else None for path in desired_files}
        before[manifest_path] = manifest_path.read_bytes() if manifest_path.exists() else None
        _safe_target(owner_path)
        before[owner_path] = owner_path.read_bytes() if owner_path.exists() else None
        registry = Path(registry_path) if registry_path is not None else installation_registry_path()
        for extra in (registry, registry.with_name('installation.previous.json'), app / 'integrations' / 'state.json'):
            _safe_target(extra)
            before[extra] = extra.read_bytes() if extra.exists() else None
        backup = app / 'integrations' / 'backups' / uuid.uuid4().hex
        for index, (path, data) in enumerate(before.items()):
            if data is not None:
                _atomic(backup / f'{index}.original', data)
        _atomic(backup / 'index.json', _json({str(i): str(p) for i, p in enumerate(before)}))
        try:
            for path, data in desired_files.items():
                _atomic(path, data)
            manifest = {'schema_version': 1, 'app_dir': str(app), 'codex_home': str(codex),
                        'skills_home': str(skills), 'hooks': desired_hooks,
                        'program_dir': str(Path(_prefix(app)[0]).parent),
                        'files': {str(p): _digest(b) for p, b in desired_files.items() if p != hook_file},
                        'backup': str(backup), 'version': __version__}
            _atomic(manifest_path, _json(manifest))
            _atomic(owner_path, _json({'schema_version': 1, 'app_dir': str(app)}))
            registration = register_installation(app, registry)
        except BaseException:
            for path, data in before.items():
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic(path, data)
            raise
    return {'ok': True, 'app_dir': str(app), 'skill': str(skills / 'handoffdesk'),
            'hook_status': 'awaiting_host_trust' if hooks else 'skill_only',
            'installation': registration,
            'next_action': '在 Codex 中查看 /hooks，信任 HandoffDesk 定义，然后开启新对话。' if hooks else '开启新的 Codex 对话以加载 Skill。',
            'message': '接入文件已准备；宿主首次信任与实际执行权限需在 Codex 中确认。'}


def disconnect_codex(app_dir, *, program_dir=None):
    from .paths import app_directory
    app = app_directory(app_dir)
    path = _manifest_path(app)
    manifest = _load(path)
    if manifest is None:
        return {'ok': True, 'disconnected': False, 'message': '没有此安装管理的 Codex 接入。'}
    if program_dir is not None and manifest.get('program_dir') != str(_absolute(program_dir, '程序目录')):
        return {'ok': True, 'disconnected': False, 'message': '当前接入属于另一个程序安装，已保留。'}
    removed, preserved = [], []
    with _lock(Path(manifest['codex_home'])):
        hook_file = Path(manifest['codex_home']) / 'hooks.json'
        if hook_file.exists():
            document = _load(hook_file)
            merged = _without_owned_hooks(document, manifest.get('hooks', {}))
            if merged != document:
                _atomic(hook_file, _json(merged))
        for name, digest in manifest.get('files', {}).items():
            target = _safe_target(Path(name))
            if target.exists():
                if _digest(target.read_bytes()) == digest:
                    target.unlink()
                    removed.append(name)
                else:
                    preserved.append(name)
        # Keep backups and all personal records. The disconnected manifest is
        # useful for recovery but is no longer treated as active ownership.
        _atomic(path.with_name('codex.disconnected.json'), _json(manifest))
        path.unlink()
        owner_path = Path(manifest['codex_home']) / 'handoffdesk-installation.json'
        owner = _load(owner_path, {})
        if owner.get('app_dir') == str(app):
            _safe_target(owner_path).unlink()
    return {'ok': True, 'disconnected': True, 'removed': removed, 'preserved_modified_files': preserved,
            'message': '已移除未被修改的接入内容；历史记录、备份和用户修改均保留。'}


def permission_probe(app_dir):
    """Actually exercise file + SQLite writes in this process, away from history."""
    app = _absolute(app_dir, '状态目录')
    try:
        app.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.permission-probe-', dir=app) as folder:
            path = Path(folder)
            (path / 'probe.txt').write_text('handoffdesk permission probe', encoding='utf-8')
            with closing(sqlite3.connect(path / 'probe.sqlite3', timeout=1)) as db, db:
                db.execute('CREATE TABLE probe(value TEXT)')
                db.execute('INSERT INTO probe VALUES (?)', ('ok',))
                db.commit()
                if db.execute('SELECT value FROM probe').fetchone()[0] != 'ok':
                    raise OSError('SQLite probe readback failed')
        return {'ok': True, 'scope': 'current_process', 'message': '当前进程可写临时文件和独立 SQLite；未改动记录数据库。'}
    except (OSError, sqlite3.Error) as exc:
        return {'ok': False, 'scope': 'current_process', 'message': str(exc)}


def doctor(app_dir, *, codex_home=None, skills_home=None, probe=False):
    from .paths import app_directory
    app = app_directory(app_dir)
    issues = []

    def issue(code, message, next_action, severity='error'):
        issues.append(dict(code=code, message=message, next_action=next_action, severity=severity))

    def load_checked(path):
        try:
            return _load(path, {})
        except (ValueError, OSError) as exc:
            issue('configuration_unreadable', str(exc), '保留原配置，修复该文件后重新运行 doctor。')
            return {}

    manifest = load_checked(_manifest_path(app))
    codex, skills = _locations(codex_home or manifest.get('codex_home'), skills_home or manifest.get('skills_home'))
    command = shell_command(_prefix(app), windows=sys.platform == 'win32')
    connect_action = command + ' connect codex'
    reader_status = 'available'
    try:
        from .registry import Registry
        if (app / 'config' / 'projects.json').exists():
            Registry(app / 'config' / 'projects.json')
        db_path = app / 'data' / 'cards.sqlite3'
        if db_path.exists():
            with sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True) as db:
                db.execute('SELECT count(*) FROM handoffs').fetchone()
    except (ValueError, OSError, sqlite3.Error) as exc:
        reader_status = 'unavailable'
        issue('reader_unavailable', '当前记录无法读取：' + str(exc), '检查此状态目录的配置、数据库和读取权限；保留原文件后恢复备份。')
    if not manifest:
        issue('not_connected', '尚未为此状态目录连接 Codex。', connect_action)
    executable = shutil.which('codex')
    host = {'executable': executable, 'status': 'executable_unavailable'}
    if executable:
        try:
            completed = subprocess.run([executable, '--version'], capture_output=True, text=True, timeout=5)
            if completed.returncode == 0:
                host.update(status='available', version=completed.stdout.strip()[:200])
                features = subprocess.run([executable, 'features', 'list'], capture_output=True, text=True, timeout=5)
                match = re.search(r'^hooks\s+\S+\s+(true|false)\s*$', features.stdout, re.MULTILINE)
                host['hooks_feature'] = match.group(1) == 'true' if features.returncode == 0 and match else 'unknown'
            else:
                host.update(status='executable_failed', message='Codex 启动命令未成功运行。')
        except (OSError, subprocess.TimeoutExpired) as exc:
            host.update(status='executable_failed', message=str(exc))
    if host['status'] == 'executable_unavailable':
        issue('host_executable_unavailable', '当前 PATH 中检测不到 Codex CLI；这不能证明桌面宿主未安装。',
              '在使用 Codex 的同一终端确认 codex --version；桌面应用用户请在实际任务中让 Agent 运行 doctor --probe。阅读器可独立使用。')
    elif host['status'] != 'available':
        issue('host_executable_failed', '已找到 Codex 入口，但启动失败：' + str(executable),
              '运行 ' + shell_command([executable, '--version'], windows=sys.platform == 'win32') + ' 检查并修复入口，再运行 doctor。')
    checks = []
    for name, digest in manifest.get('files', {}).items():
        file = Path(name)
        try:
            valid = file.is_file() and _digest(file.read_bytes()) == digest
        except OSError:
            valid = False
        checks.append({'path': name, 'ok': valid})
    if manifest and (not checks or not all(item['ok'] for item in checks)):
        issue('managed_files_changed', '接入文件缺失或已被修改。', '保留用户修改，核对 Skill 文件后再连接；安装器不会覆盖修改过的文件。')
    locations_match = (not manifest or (str(codex) == manifest.get('codex_home') and str(skills) == manifest.get('skills_home')))
    if not locations_match:
        issue('integration_location_mismatch', '本次检查目录与已登记的接入目录不同。', '省略目录参数以检查现有接入，或先明确断开再连接到新目录。')
    hook_document = load_checked(codex / 'hooks.json')
    hook_values = hook_document.get('hooks', {})
    if not isinstance(hook_values, dict):
        issue('hooks_invalid', 'hooks 配置结构无效。', '修复 hooks.json 的 hooks 对象后重新检查。')
        hook_values = {}
    hooks_intact = all(isinstance(hook_values.get(event), list) and group in hook_values[event]
                       for event, group in manifest.get('hooks', {}).items())
    hooks_requested = bool(manifest.get('hooks'))
    if hooks_requested and not hooks_intact:
        issue('hooks_changed', 'HandoffDesk 的 hooks 定义缺失或已被修改。', '核对 hooks.json 并保留其他接入内容；重新运行 connect codex 后按宿主提示信任。')
    if manifest and not hooks_requested:
        issue('skill_only', '当前为 Skill-only 接入，记录取决于 Agent 调用，没有结束时的 hook 补救。',
              '开启新的 Codex 对话加载 Skill，并完成一次实际任务；需要 hooks 时运行 connect codex。', 'info')
    if hooks_requested and host.get('hooks_feature') is False:
        issue('hooks_disabled', '此 Codex CLI 的 hooks 功能未启用。',
              '在 Codex 中检查 hooks 设置；宿主不支持时可改用 connect codex --skill-only。')
    elif hooks_requested and host['status'] == 'available' and host.get('hooks_feature') == 'unknown':
        issue('hooks_support_unknown', '无法从当前 CLI 确认 hooks 功能支持。',
              '在实际 Codex 中检查 /hooks；不支持时使用 connect codex --skill-only。', 'warning')
    if hooks_requested:
        issue('host_trust_unverified', '首次 hook 信任和真实宿主调用尚未验证。',
              '在 Codex 中查看 /hooks 并完成信任；让实际任务保存一次记录并核对汇报。', 'warning')
    invocation = load_checked(app / 'integrations' / 'last-hook.json')
    probe_result = permission_probe(app) if probe else {'status': 'not_run'}
    if probe and not probe_result['ok']:
        issue('permission_denied', '当前进程无法写入状态目录：' + probe_result['message'],
              '为实际 Agent 进程授予此状态目录的文件与 SQLite 写权限，再运行 doctor --probe。')
    elif not probe:
        issue('permissions_not_checked', '尚未检查实际调用进程的写权限。', command + ' doctor --probe', 'info')
    issues.sort(key=lambda item: {'error': 0, 'warning': 1, 'info': 2}[item['severity']])
    checked_ok = bool(manifest) and not any(item['severity'] == 'error' for item in issues)
    layers = [
        {'id': 'reader', 'label': '阅读', 'status': reader_status, 'message': '阅读无需 Codex 或 hooks 就绪。'},
        {'id': 'skill', 'label': 'Skill 文件', 'status': 'installed' if manifest and checks and all(x['ok'] for x in checks) else 'not_ready', 'message': str(skills / 'handoffdesk')},
        {'id': 'host', 'label': 'Codex CLI', 'status': host['status'], 'message': host.get('message', host.get('version', '未检测到命令入口'))},
        {'id': 'hooks', 'label': 'Hooks', 'status': 'configured' if hooks_requested and hooks_intact else 'skill_only' if manifest else 'not_connected', 'message': '定义存在与实际执行是不同状态。'},
        {'id': 'trust', 'label': '宿主信任', 'status': 'not_verified', 'message': '手动探针与 handler 日志不证明宿主已信任。'},
        {'id': 'permissions', 'label': '写权限', 'status': 'passed' if probe_result.get('ok') else 'failed' if probe else 'not_run', 'message': probe_result.get('message', '请在实际 Agent 进程检查。')},
    ]
    return {'ok': checked_ok, 'fully_verified': False,
            'app_dir': str(app), 'version': __version__, 'host': host,
            'connected': bool(manifest), 'files': checks, 'hooks_configured': hooks_requested and hooks_intact,
            'host_trust': 'not_verified', 'last_handler_invocation': invocation,
            'permission_probe': probe_result, 'layers': layers, 'issues': issues,
            'next_action': issues[0]['next_action'] if issues else '完成一次真实任务并核对记录与汇报。',
            'message': issues[0]['message'] if issues else '本机检查通过；真实宿主信任和自动调用仍需实际任务验证。'}


def format_doctor(value):
    """Human-readable rendering shared by the CLI and terminal reader."""
    labels = {'available': '可用', 'unavailable': '不可用', 'installed': '已安装', 'not_ready': '未就绪',
              'executable_unavailable': '未检测到入口', 'executable_failed': '启动失败', 'configured': '已配置',
              'skill_only': '仅 Skill', 'not_connected': '未接入', 'not_verified': '尚未验证',
              'passed': '通过（仅当前进程）', 'failed': '失败', 'not_run': '尚未检查'}
    lines = ['HandoffDesk 接入与状态', value['message'], '']
    lines.extend(f"{layer['label']}：{labels.get(layer['status'], layer['status'])}。{layer['message']}" for layer in value.get('layers', []))
    lines.extend(['', '下一步：' + value['next_action']])
    for item in value.get('issues', [])[1:]:
        lines.extend(['', item['message'], '操作：' + item['next_action']])
    lines.extend(['', '记录目录：' + value['app_dir'], '以上不代表用户已收到或已阅读任何记录。'])
    return '\n'.join(lines)


def _text_context(event, text):
    return {'hookSpecificOutput': {'hookEventName': event, 'additionalContext': text}}


def handle_hook(app_dir, event, payload):
    """Adapt stable host fields only; never parse or save the full transcript."""
    if event not in EVENTS or not isinstance(payload, dict):
        raise ValueError('无效的 Codex hook 事件。')
    from .paths import app_directory
    from .agent_session import AgentSessions
    from .store import now
    app = app_directory(app_dir)
    sessions = AgentSessions(app)
    # Invocation evidence is not evidence of host trust: tests/manual calls use
    # this same entry point and cannot certify that Codex loaded the definition.
    _atomic(app / 'integrations' / 'last-hook.json', _json({'event': event, 'observed_at': now(), 'host_trust_verified': False}))
    command = shell_command(_prefix(app), windows=False)
    if event == 'SessionStart':
        return _text_context(event, 'HandoffDesk 已接入此执行机器。项目成果、决策、阻塞和计划使用 $handoffdesk 记录；每次成功保存立即汇报记录编号。'
                             '纯问答使用内部 no_record 处置。用户要求暂停记录时尊重其选择。入口：' + command + '。先运行 doctor --probe 检查实际写入权限。')
    host_session = payload.get('session_id')
    host_turn = payload.get('turn_id')
    if not isinstance(host_session, str) or not host_session or not isinstance(host_turn, str) or not host_turn:
        return {'systemMessage': 'HandoffDesk：宿主未提供 session_id/turn_id，使用 Skill 模式；没有创建猜测的轮次关联。'} if event != 'SessionEnd' else {}
    resume_of = None
    if event == 'UserPromptSubmit':
        match = re.search(r'\[HandoffDesk recovery: ([a-zA-Z0-9_-]+)\]', str(payload.get('prompt', '')))
        if match:
            candidate = sessions.turn_status(match.group(1))
            if candidate.get('host_session_id') == host_session and candidate.get('remediation_used'):
                resume_of = match.group(1)
    turn = sessions.observe_turn('codex', host_session_id=host_session, host_turn_id=host_turn, resume_of=resume_of)
    ref = turn['turn_ref']
    if event == 'UserPromptSubmit':
        return _text_context(event, f'HandoffDesk 本轮标识：{ref}。进行项目任务时把 --turn {ref} 传给 agent begin/checkpoint；'
                             f'保存后立即汇报并 ack-report；结束时 agent conclude --turn {ref} --outcome recorded。'
                             '纯问答 conclude 为 no_record 并给简短原因；用户暂停记录则 paused，不创建空项目。')
    if event == 'Interrupt':
        sessions.conclude_turn(ref, 'interrupted', reason='Host interrupted the turn.')
        return {}
    if event == 'SessionEnd':
        return {}
    # A returned final text is observable evidence of mentioning a receipt, not
    # evidence that the user received/read it, or that a mid-turn report happened.
    last = payload.get('last_assistant_message')
    if isinstance(last, str):
        for receipt in turn.get('receipts', []):
            if receipt.get('report_status') == 'observed_in_final':
                continue
            record_id = receipt['record_id']
            if re.search(r'(?<![A-Za-z0-9_:\-])' + re.escape(record_id) + r'(?![A-Za-z0-9_:\-])', last):
                sessions.ack_report(ref, record_id, 'observed_in_final', 'Stop.last_assistant_message')
        turn = sessions.turn_status(ref)
    incomplete = bool(turn.get('pending_reports') or turn.get('pending_checkpoints') or not turn.get('disposition'))
    if not incomplete or turn.get('disposition') in {'paused', 'interrupted'}:
        return {}
    if payload.get('stop_hook_active') or not sessions.claim_remediation(ref):
        return {'systemMessage': 'HandoffDesk：本轮仍有未完成记录或汇报，已保留恢复状态；不再自动续跑。'}
    return {'decision': 'block', 'reason': f'[HandoffDesk recovery: {turn["original_turn_ref"]}] '
            f'仅补齐本轮 HandoffDesk 处置（turn {ref}），不扩展用户任务。已有回执先恢复并简短汇报，不重复记账；'
            '有实际项目成果则用 $handoffdesk 保存，有错误如实汇报；纯问答 conclude no_record，暂停则 paused。最后 conclude 并结束。'}
