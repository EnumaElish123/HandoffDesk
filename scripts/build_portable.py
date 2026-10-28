"""Build a relocatable release on the target platform, never bundle user state."""
import argparse
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = (args.output or root / 'dist').resolve()
    command = [sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm',
               '--distpath', str(output), '--workpath', str(root / 'build' / 'portable'),
               str(root / 'scripts' / 'portable.spec')]
    subprocess.run(command, cwd=root, check=True)
    print('Built:', output / 'HandoffDesk')
    print('Before release: verify on a clean target OS, include dependency licenses, and run the release checks in docs/agent-first.md.')


if __name__ == '__main__':
    main()
