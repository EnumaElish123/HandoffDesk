"""CLI adapter kept separate so installation checks never import a GUI."""
import argparse
import json
from pathlib import Path
import sys

from . import integration


COMMANDS = {'connect', 'disconnect', 'doctor', 'integration-hook'}


def register(subparsers):
    connect = subparsers.add_parser('connect', help='一次连接 Agent，保留已有用户配置')
    connect.add_argument('host', choices=['codex'])
    connect.add_argument('--codex-home', type=Path)
    connect.add_argument('--skills-home', type=Path)
    connect.add_argument('--registry-path', type=Path, help=argparse.SUPPRESS)
    connect.add_argument('--skill-only', action='store_true', help='只安装 Skill，不安装 hooks')
    disconnect = subparsers.add_parser('disconnect', help='移除本工具的 Agent 接入，保留全部记录')
    disconnect.add_argument('host', choices=['codex'])
    disconnect.add_argument('--program-dir', type=Path, help=argparse.SUPPRESS)
    doctor = subparsers.add_parser('doctor', help='检查安装、宿主、状态目录与实际写入权限')
    doctor.add_argument('--codex-home', type=Path)
    doctor.add_argument('--skills-home', type=Path)
    doctor.add_argument('--probe', action='store_true', help='创建临时文件和独立 SQLite 检查本进程权限')
    hook = subparsers.add_parser('integration-hook', help=argparse.SUPPRESS)
    hook.add_argument('--event', choices=integration.EVENTS, required=True)


def run(args):
    if getattr(args, 'data_dir', None) is not None:
        raise ValueError('Agent 接入使用安装登记中的统一状态目录；请用 --app-dir 指定含 data 的目录，接入命令不支持单独的 --data-dir。')
    if args.command == 'connect':
        return integration.connect_codex(args.app_dir, codex_home=args.codex_home,
                                        skills_home=args.skills_home, hooks=not args.skill_only,
                                        registry_path=args.registry_path)
    if args.command == 'disconnect':
        return integration.disconnect_codex(args.app_dir, program_dir=args.program_dir)
    if args.command == 'doctor':
        return integration.doctor(args.app_dir, codex_home=args.codex_home, skills_home=args.skills_home, probe=args.probe)
    data = sys.stdin.read(integration.MAX_INPUT + 1)
    if len(data.encode('utf-8')) > integration.MAX_INPUT:
        raise ValueError('Hook 输入不能超过 1 MiB。')
    try:
        payload = json.loads(data)
        return integration.handle_hook(args.app_dir, args.event, payload)
    except (ValueError, OSError) as exc:
        # A failing integration must not manufacture a successful receipt or
        # prevent the user from ending/interruption of their own task.
        return {'systemMessage': 'HandoffDesk 接入检查未完成：' + str(exc)}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--app-dir', type=Path)
    register(parser.add_subparsers(dest='command', required=True))
    try:
        value = run(parser.parse_args(argv))
        print(json.dumps(value, ensure_ascii=False, default=str))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
