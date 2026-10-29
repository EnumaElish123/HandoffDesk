"""Developer/source installer; portable release bundles need no system Python."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

# The source installer runs before the package is installed. Resolve state using
# the same source module as every runtime entry.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from handoffdesk.paths import app_directory
from handoffdesk.integration import registered_app_directory, installation_registry_path


def install_user_launcher(prefix, command, bin_dir=None, *, previous_prefix=None):
    """Publish only an absent or unchanged launcher owned by this prefix."""
    import shlex
    prefix = Path(prefix)
    target = Path(bin_dir or Path.home() / ".local" / "bin") / "handoffdesk"
    manifest = prefix / "launcher.json"
    def ownership(path):
        if path.is_symlink() or not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text())
            return value if isinstance(value, dict) else {}
        except (ValueError, OSError):
            return {}

    previous = ownership(manifest)
    content = ("#!/bin/sh\nexec " + shlex.join([str(command)]) + ' "$@"\n').encode()
    occupied = target.exists() or target.is_symlink()
    owned = (not target.is_symlink() and target.is_file()
             and previous.get("path") == str(target)
             and hashlib.sha256(target.read_bytes()).hexdigest() == previous.get("sha256"))
    old_manifest = None
    if occupied and not owned and previous_prefix is not None and Path(previous_prefix) != prefix:
        candidate = Path(previous_prefix) / 'launcher.json'
        prior = ownership(candidate)
        owned = (not target.is_symlink() and target.is_file()
                 and prior.get('path') == str(target)
                 and hashlib.sha256(target.read_bytes()).hexdigest() == prior.get('sha256'))
        if owned:
            old_manifest = candidate
    if occupied and not owned:
        return {"installed": False, "path": str(target), "message": "已有同名入口，已保留；请使用下方绝对启动命令。"}
    target.parent.mkdir(parents=True, exist_ok=True)
    if occupied:
        target.write_bytes(content)
    else:
        # Exclusive creation prevents overwriting a command created concurrently.
        with target.open("xb") as output:
            output.write(content)
    target.chmod(0o755)
    manifest.write_text(json.dumps({"path": str(target), "sha256": hashlib.sha256(content).hexdigest()}, indent=2))
    if old_manifest is not None:
        # The old installer must no longer own the now-replaced user command.
        old_manifest.unlink()
    return {"installed": True, "path": str(target),
            "on_path": str(target.parent) in os.environ.get("PATH", "").split(os.pathsep)}


def remove_user_launcher(prefix):
    """Remove the unchanged owned entry; retain user replacements and state."""
    manifest = Path(prefix) / "launcher.json"
    if not manifest.exists():
        return False
    owned = json.loads(manifest.read_text())
    target = Path(owned["path"])
    if target.is_symlink() or not target.is_file():
        return False
    if hashlib.sha256(target.read_bytes()).hexdigest() != owned.get("sha256"):
        return False
    target.unlink()
    manifest.unlink()
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description='Install HandoffDesk for the current user without editing project files.')
    parser.add_argument('--prefix', type=Path, help='Installation directory, separate from user records')
    parser.add_argument('--state-dir', type=Path, help='Use an existing or new state directory')
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--no-connect', action='store_true', help='Install without connecting Codex')
    parser.add_argument('--skill-only', action='store_true')
    parser.add_argument('--codex-home', type=Path)
    parser.add_argument('--skills-home', type=Path)
    parser.add_argument('--registry-path', type=Path)
    parser.add_argument('--check', action='store_true', help='Show chosen paths without writing or downloading')
    parser.add_argument('--remove-launcher', action='store_true', help='Remove only the unchanged user command owned by this installation; keep program and records')
    args = parser.parse_args(argv)
    if sys.version_info < (3, 10):
        parser.error('Source installation requires Python 3.10+. A portable release includes its own Python.')
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
        prefix = args.prefix or base / 'HandoffDeskApp'
        executable = prefix / 'venv' / 'Scripts' / 'python.exe'
        extra = 'desktop'
    else:
        prefix = args.prefix or Path.home() / '.local' / 'lib' / 'handoffdesk'
        executable = prefix / 'venv' / 'bin' / 'python'
        extra = 'tui'
    prefix = prefix.expanduser().resolve()
    if args.remove_launcher:
        if args.check:
            print(json.dumps({'remove_launcher': str(prefix / 'launcher.json'), 'check_only': True}))
        else:
            print(json.dumps({'removed': remove_user_launcher(prefix)}))
        return 0
    try:
        # Even an explicit new state must not silently overwrite a broken registry.
        registered = registered_app_directory(args.registry_path)
        state = app_directory(args.state_dir, registry_path=args.registry_path)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    previous_prefix = None
    if registered is not None:
        previous_registry = Path(args.registry_path or installation_registry_path())
        prior = json.loads(previous_registry.read_text(encoding='utf-8-sig'))
        prior_executable = Path(prior.get('executable', ''))
        if prior_executable.is_absolute() and prior_executable.parent.name == 'bin' and prior_executable.parent.parent.name == 'venv':
            previous_prefix = prior_executable.parent.parent.parent
    state_source = ('explicit' if args.state_dir is not None else 'environment' if os.environ.get('HANDOFFDESK_HOME') is not None
                    else 'registered' if registered is not None else 'default')
    # Preserve the venv path: resolving its Python symlink would install into
    # the base interpreter on upgrades.
    executable = executable.expanduser().absolute()
    if state == prefix or state.is_relative_to(prefix) or prefix.is_relative_to(state):
        parser.error('Install and state directories must be separate; upgrades should not replace records.')
    print(json.dumps({'installation': str(prefix), 'state': str(state), 'connect_codex': not args.no_connect,
                      'state_source': state_source, 'reusing_records': registered == state, 'extras': extra, 'source': str(args.source.resolve()), 'check_only': args.check}, ensure_ascii=False))
    if args.check:
        return 0
    prefix.mkdir(parents=True, exist_ok=True)
    if not executable.is_file():
        venv.EnvBuilder(with_pip=True).create(prefix / 'venv')
    subprocess.run([str(executable), '-m', 'pip', 'install', f'{args.source.resolve()}[{extra}]'], check=True)
    base_command = [str(executable), '-m', 'handoffdesk', '--app-dir', str(state)]
    if not args.no_connect:
        command = base_command + ['connect', 'codex']
        if args.skill_only:
            command.append('--skill-only')
        for option, value in (('--codex-home', args.codex_home), ('--skills-home', args.skills_home), ('--registry-path', args.registry_path)):
            if value is not None:
                command.extend([option, str(value.expanduser().resolve())])
        subprocess.run(command, check=True)
    else:
        script = 'from handoffdesk.integration import register_installation; import sys; register_installation(sys.argv[1],sys.argv[2] or None)'
        subprocess.run([str(executable), '-c', script, str(state), str(args.registry_path or '')], check=True)
    if sys.platform == 'win32':
        # No global PATH changes: launchers are owned by this installation.
        windowed = str(executable.with_name('pythonw.exe')).replace('%', '%%')
        state_argument = str(state).replace('%', '%%')
        launcher = '@echo off\r\n"' + windowed + '" -m handoffdesk --app-dir "' + state_argument + '" desktop\r\n'
        target = prefix / 'Start HandoffDesk.cmd'
        target.write_text(launcher, encoding='utf-8')
    else:
        import shlex
        launcher = '#!/bin/sh\nexec ' + shlex.join(base_command) + ' "$@"\n'
        target = prefix / 'handoffdesk'
        target.write_text(launcher, encoding='utf-8')
        target.chmod(0o755)
        entry = install_user_launcher(prefix, target, previous_prefix=previous_prefix)
        print(json.dumps({'launcher': entry}, ensure_ascii=False))
        if entry.get('installed') and not entry.get('on_path'):
            print('用户命令目录尚未加入 PATH；现在即可使用下面的绝对命令，无需先修改终端配置。')
    import shlex
    start = subprocess.list2cmdline([str(target)]) if sys.platform == 'win32' else shlex.join([str(target), 'tui'])
    print(('已沿用已有记录目录。' if registered == state else '已选定记录目录。') + str(state))
    print('安装完成。立即阅读：\n' + start)
    if not args.no_connect:
        print('接入文件已准备；首次 hook 信任仍需在 Codex 中确认。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
