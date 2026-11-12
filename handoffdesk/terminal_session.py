"""Leave a dedicated tmux reader running after its sole client disconnects."""
import os
import re
import subprocess


# Resolve the default client for this pane and compare the same snapshot again
# inside the tmux server before targeting that specific terminal.
_PANE_STATE = ('#{pane_pid}:#{window_linked}:#{session_windows}:#{window_panes}:'
               '#{session_attached}:#{session_id}:#{client_pid}:#{client_name}')


def is_managed_reader():
    """Only a launcher creating a dedicated reader pane should set this marker."""
    return (os.environ.get('HANDOFFDESK_TMUX_READER') == '1'
            and bool(os.environ.get('TMUX'))
            and re.fullmatch(r'%[0-9]+', os.environ.get('TMUX_PANE', '')) is not None)


def _tmux(*args):
    try:
        result = subprocess.run(['tmux', *args], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=2, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('无法连接到当前 tmux 阅读器。请检查连接后重试。') from exc
    return result.stdout.strip()


def detach_reader():
    if not is_managed_reader():
        raise ValueError('此阅读器在前台运行，可按 q 退出。')
    pane = os.environ['TMUX_PANE']
    state = _tmux('display-message', '-p', '-t', pane, _PANE_STATE)
    fields = state.split(':')
    if len(fields) != 8 or fields[:4] != [str(os.getpid()), '0', '1', '1']:
        raise ValueError('该阅读器未独占 tmux 会话，请使用当前终端的 tmux 分离操作。')
    clients, session, client_pid, client_tty = fields[4:]
    if clients != '1':
        raise ValueError('需要单个已连接的终端才能在此离开。请在自己的终端使用 tmux 分离操作。')
    if not (re.fullmatch(r'\$[0-9]+', session) and client_pid.isascii() and client_pid.isdigit()
            and re.fullmatch(r'/dev/[a-zA-Z0-9/_.-]+', client_tty)):
        raise ValueError('无法识别当前连接的终端，请使用当前终端的 tmux 分离操作。')
    guard = '#{==:' + _PANE_STATE + ',' + state + '}'
    changed = _tmux('if-shell', '-F', '-t', pane, guard, f"detach-client -t '{client_tty}'",
                    'display-message -p changed')
    if changed:
        raise ValueError('终端连接刚刚发生变化，请重新打开菜单后重试。')
