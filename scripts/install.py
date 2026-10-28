"""Developer/source installer; portable release bundles need no system Python."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv


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
    args = parser.parse_args(argv)
    if sys.version_info < (3, 10):
        parser.error('Source installation requires Python 3.10+. A portable release includes its own Python.')
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
        prefix = args.prefix or base / 'HandoffDeskApp'
        state = args.state_dir or base / 'HandoffDesk'
        executable = prefix / 'venv' / 'Scripts' / 'python.exe'
        extra = 'desktop'
    else:
        prefix = args.prefix or Path.home() / '.local' / 'lib' / 'handoffdesk'
        state = args.state_dir or Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share') / 'handoffdesk'
        executable = prefix / 'venv' / 'bin' / 'python'
        extra = 'tui'
    prefix, state = prefix.expanduser().resolve(), state.expanduser().resolve()
    # Preserve the venv path: resolving its Python symlink would install into
    # the base interpreter on upgrades.
    executable = executable.expanduser().absolute()
    if state == prefix or state.is_relative_to(prefix) or prefix.is_relative_to(state):
        parser.error('Install and state directories must be separate; upgrades should not replace records.')
    print(json.dumps({'installation': str(prefix), 'state': str(state), 'connect_codex': not args.no_connect,
                      'extras': extra, 'source': str(args.source.resolve()), 'check_only': args.check}, ensure_ascii=False))
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
        (prefix / 'Start HandoffDesk.cmd').write_text(launcher, encoding='utf-8')
    else:
        import shlex
        launcher = '#!/bin/sh\nexec ' + shlex.join(base_command) + ' "$@"\n'
        target = prefix / 'handoffdesk'
        target.write_text(launcher, encoding='utf-8')
        target.chmod(0o755)
    print('Installation complete. Hook trust is completed inside Codex; the installer does not bypass it.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
