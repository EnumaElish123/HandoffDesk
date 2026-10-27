"""Check files selected for publication; ignored local state is never included."""
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {'data', 'backups', 'exports', 'runtime', 'verification', 'work', '.venv', '__pycache__'}
FORBIDDEN_FILES = {'config/projects.json', 'config/roots.local.json', '.env'}
SECRET_PATTERNS = [
    re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b'),
    re.compile(rb'\bAKIA[A-Z0-9]{16}\b'),
]


def check(paths):
    failures = []
    for name in paths:
        relative = PurePosixPath(name)
        path = ROOT / name
        if not path.is_file():
            failures.append(f'{name}: missing or not a regular file')
            continue
        if (set(relative.parts) & FORBIDDEN_PARTS or name in FORBIDDEN_FILES
                or relative.name in {'projects.json', 'roots.local.json'}
                or re.search(r'\.(?:sqlite3?|db)(?:-[\w-]+)?$', name, flags=re.IGNORECASE)
                or path.suffix.lower() in {'.log', '.pem', '.key', '.pyc', '.bak', '.zip', '.gz', '.whl', '.7z', '.exe', '.dll', '.so'}
                or any(part == '.env' or part.startswith('.env.') for part in relative.parts)):
            failures.append(f'{name}: local state or private artifact')
        if path.is_symlink():
            failures.append(f'{name}: symlinks are not accepted in the public source tree')
        data = path.read_bytes()
        if len(data) > 2 * 1024 * 1024:
            failures.append(f'{name}: unexpectedly large source artifact')
        if any(pattern.search(data) for pattern in SECRET_PATTERNS):
            failures.append(f'{name}: possible credential; inspect locally')
    return failures


def main():
    result = subprocess.run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                            cwd=ROOT, check=True, capture_output=True)
    paths = sorted(set(result.stdout.decode('utf-8').split('\0')) - {''})
    failures = check(paths)
    for item in failures:
        print(item, file=sys.stderr)
    print(f'Public source check: {len(paths)} files, {len(failures)} findings')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
