"""Exercise a Linux TUI executable in a real, narrow pseudo-terminal."""
import argparse
import errno
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import tempfile
import termios
import time


def run_terminal(command, timeout):
    master, slave = pty.openpty()
    start = time.monotonic()
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 85, 0, 0))
        proc = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave,
                                env=dict(os.environ, TERM='xterm-256color'), start_new_session=True)
    except BaseException:
        os.close(master)
        raise
    finally:
        os.close(slave)

    def wait_for(marker):
        data = bytearray()
        deadline = time.monotonic() + timeout
        while marker not in data:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([master], [], [], left)[0]:
                break
            try:
                chunk = os.read(master, 65536)
            except OSError as exc:
                if exc.errno != errno.EIO:
                    raise
                break
            if not chunk:
                break
            data.extend(chunk)
        if marker not in data:
            raise RuntimeError(f'Terminal did not display {marker!r}: {bytes(data[-4000:])!r}')
        return time.monotonic()

    try:
        listed = wait_for(b'MilestoneProbe')
        os.write(master, b'\r')
        opened = wait_for(b'DetailProbe')
        os.write(master, b'\x1b')
        time.sleep(0.2)
        os.write(master, b'q')
        # Drain screen updates and terminal restoration while the child exits.
        deadline = time.monotonic() + timeout
        while proc.poll() is None:
            left = deadline - time.monotonic()
            if left <= 0:
                raise RuntimeError('Terminal did not quit after Escape and q')
            if select.select([master], [], [], min(left, 0.25))[0]:
                try:
                    if not os.read(master, 65536):
                        break
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
                    break
        if proc.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
            raise RuntimeError('Terminal exited with an error')
        return {'list_s': round(listed - start, 3), 'detail_s': round(opened - start, 3)}
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        os.close(master)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--temp-parent', type=Path)
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--compare', type=Path, help='Measure another executable against the same record')
    args = parser.parse_args()
    if args.repeat < 1 or args.timeout <= 0:
        parser.error('repeat and timeout must be positive')
    exe = args.executable.resolve()
    with tempfile.TemporaryDirectory(dir=args.temp_parent) as temp:
        root = Path(temp)
        project = root / 'project'
        project.mkdir()
        state = ['--app-dir', str(root / 'state')]

        def run(*cmd, payload=None):
            result = subprocess.run([exe, *state, *cmd], capture_output=True, text=True,
                                    input=json.dumps(payload) if payload else None, timeout=args.timeout)
            if result.returncode:
                raise RuntimeError(result.stderr or result.stdout)
            return json.loads(result.stdout)

        session = run('agent', 'begin', '--cwd', str(project), '--task', 'Terminal startup', '--actor', 'Smoke test')
        checkpoint = ['agent', 'checkpoint', '--session', session['session_ref'],
                      '--checkpoint', session['checkpoint_token'], '--stdin']
        payload = {'kind': 'handoff', 'title': 'MilestoneProbe', 'actual_done': 'DetailProbe',
                   'next_action': 'Check the terminal.'}
        started = time.monotonic()
        receipt = run(*checkpoint, payload=payload)
        print(json.dumps({"checkpoint_s": round(time.monotonic() - started, 3)}), flush=True)
        replay = run(*checkpoint, payload=payload)
        if not replay['replayed'] or replay['record_id'] != receipt['record_id']:
            raise RuntimeError('Repeated checkpoint did not return the original receipt')
        if run('feed')['items'][0]['id'] != receipt['record_id']:
            raise RuntimeError('Checkpoint was missing after restarting the CLI')

        for binary, repeat in [(exe, args.repeat)] + ([(args.compare.resolve(), 1)] if args.compare else []):
            for _ in range(repeat):
                start = time.monotonic()
                subprocess.run([binary, '--version'], capture_output=True, check=True, timeout=args.timeout)
                version = time.monotonic() - start
                result = run_terminal([binary, *state, 'tui'], args.timeout)
                print(json.dumps({'executable': str(binary), 'version_s': round(version, 3), **result}), flush=True)


if __name__ == '__main__':
    main()
