"""Small JSON command surface; semantic payloads never require internal project IDs."""
from pathlib import Path
import sys

from .agent_cli import read_payload
from .agent_session import AgentSessions
from .workspace import Workspace

COMMANDS = {'agent'}


def register(subparsers):
    parser = subparsers.add_parser('agent', help='自动绑定当前项目、记录阶段并恢复未完成汇报')
    commands = parser.add_subparsers(dest='agent_command', required=True)
    begin = commands.add_parser('begin', help='开始或恢复固定项目/任务绑定')
    begin.add_argument('--cwd', type=Path, default=Path.cwd())
    begin.add_argument('--task', required=True)
    begin.add_argument('--actor', default='Agent')
    begin.add_argument('--host', default='unknown')
    begin.add_argument('--host-session-id')
    begin.add_argument('--turn', dest='turn_ref')
    begin.add_argument('--workline')
    checkpoint = commands.add_parser('checkpoint', help='保存语义内容，返回稳定回执和下一令牌')
    checkpoint.add_argument('--session', required=True)
    checkpoint.add_argument('--checkpoint', required=True)
    checkpoint.add_argument('--turn', dest='turn_ref')
    source = checkpoint.add_mutually_exclusive_group(required=True)
    source.add_argument('--stdin', action='store_true')
    source.add_argument('--file', type=Path)
    status = commands.add_parser('status', help='读取保存状态、原提案与待汇报回执')
    status.add_argument('--session', required=True)
    status.add_argument('--checkpoint')
    resume = commands.add_parser('resume', help='恢复原任务；明确解决失败或冲突后可替换令牌')
    resume.add_argument('--session', required=True)
    resume.add_argument('--replace-checkpoint')
    resume.add_argument('--reason', default='')
    resume.add_argument('--turn', dest='turn_ref')
    observe = commands.add_parser('observe', help='观察宿主轮次，不创建项目')
    observe.add_argument('--host', default='unknown')
    observe.add_argument('--host-session-id')
    observe.add_argument('--host-turn-id')
    observe.add_argument('--resume-of')
    turn = commands.add_parser('turn-status', help='读取轮次汇报与有限补救状态')
    turn.add_argument('--turn', dest='turn_ref', required=True)
    conclude = commands.add_parser('conclude', help='处置轮次，区分成果、纯问答、暂停与中断')
    conclude.add_argument('--turn', dest='turn_ref', required=True)
    conclude.add_argument('--outcome', required=True, choices=['recorded', 'no_record', 'paused', 'interrupted', 'failed'])
    conclude.add_argument('--receipt', action='append', default=[])
    conclude.add_argument('--reason', default='')
    ack = commands.add_parser('ack-report', help='确认汇报证据，不代表用户已读')
    ack.add_argument('--turn', dest='turn_ref', required=True)
    ack.add_argument('--receipt', required=True)
    ack.add_argument('--evidence-type', choices=['agent_acknowledged', 'observed_in_final'], required=True)
    ack.add_argument('--evidence-ref', required=True)
    inspect = commands.add_parser('inspect', help='只读识别当前工作目录')
    inspect.add_argument('--cwd', type=Path, default=Path.cwd())
    relocate = commands.add_parser('relocate', help='明确重定位项目根目录，保留历史编号')
    relocate.add_argument('--project', required=True)
    relocate.add_argument('--root', type=Path, required=True)


add_parsers = register


def run(args, stream=None):
    api = AgentSessions(args.app_dir, args.data_dir)
    command = args.agent_command
    if command == 'begin':
        return api.begin(args.cwd, args.task, args.actor, args.host, args.host_session_id,
                         args.turn_ref, args.workline)
    if command == 'checkpoint':
        return api.checkpoint(args.session, args.checkpoint,
                              read_payload(args.file, stream or sys.stdin), args.turn_ref)
    if command == 'status':
        return api.status(args.session, args.checkpoint)
    if command == 'resume':
        return api.resume(args.session, args.replace_checkpoint, args.reason, args.turn_ref)
    if command == 'observe':
        return api.observe_turn(args.host, args.host_session_id, args.host_turn_id, args.resume_of)
    if command == 'turn-status':
        return api.turn_status(args.turn_ref)
    if command == 'conclude':
        return api.conclude_turn(args.turn_ref, args.outcome, args.receipt, args.reason)
    if command == 'ack-report':
        return api.ack_report(args.turn_ref, args.receipt, args.evidence_type, args.evidence_ref)
    workspace = Workspace(args.app_dir, args.data_dir)
    if command == 'inspect':
        return dict(schema_version=1, ok=True, **workspace.inspect(args.cwd))
    if command == 'relocate':
        return dict(schema_version=1, ok=True, **workspace.relocate(args.project, args.root))
    raise ValueError('未知 Agent 命令。')
