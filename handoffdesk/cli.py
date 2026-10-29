import argparse
import json
from pathlib import Path
import sys
import sqlite3

from .service import Service
from .store import ConflictError
from . import __version__


def output(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def scoped_project(parser, json_flag=False):
    parser.add_argument('project')
    parser.add_argument('--workline', help='工作线编号；省略时使用该项目当前工作线')
    if json_flag:
        parser.add_argument('--json', action='store_true')


def main(argv=None):
    from . import agent_cli, agent_session_cli, integration_cli
    actual_argv = list(sys.argv[1:] if argv is None else argv)
    machine = '--json' in actual_argv or any(item in agent_cli.COMMANDS | integration_cli.COMMANDS | {'init', 'project', 'agent', 'feed', 'read'} for item in actual_argv)

    class Parser(argparse.ArgumentParser):
        def error(self, message):
            if machine:
                output({'schema_version': 1, 'ok': False, 'error': {'code': 'invalid_arguments', 'message': message}})
                self.exit(2)
            super().error(message)

    parser = Parser(description="HandoffDesk：只读来源，本地保存交接。")
    parser.add_argument('--version', action='version', version=f'HandoffDesk {__version__}')
    parser.add_argument("--data-dir", type=Path, help="独立数据目录（禁止放入接入项目）")
    parser.add_argument("--app-dir", type=Path, help="状态目录，含 config/projects.json 与 data；优先于 HANDOFFDESK_HOME")
    subs = parser.add_subparsers(dest="command")
    desktop = subs.add_parser("desktop", help="打开本地 Web 桌面窗口（Windows 默认）")
    desktop.add_argument('--browser', action='store_true', help='在本机浏览器预览；仅监听回环地址')
    subs.add_parser('legacy-desktop', help='打开旧 Tk 窗口（兼容入口）')
    subs.add_parser('tui', help='在终端阅读进展，无需图形环境（Linux 默认）')
    feed = subs.add_parser('feed', help='查询各项目的最新阶段记录')
    feed.add_argument('--project')
    feed.add_argument('--workline')
    feed.add_argument('--limit', type=int, default=50)
    feed.add_argument('--cursor', type=int)
    feed.add_argument('--unread', action='store_true')
    feed.add_argument('--query', default='')
    read = subs.add_parser('read', help='按记录编号阅读完整阶段及文件')
    read.add_argument('record_id')
    read.add_argument('--markdown', action='store_true')
    read.add_argument('--ack', action='store_true', help='明确标记这条记录已读')
    init = subs.add_parser('init', help='首次初始化；无参数创建本地通用示例，已有配置不会覆盖')
    init.add_argument('--project', dest='project_id')
    init.add_argument('--name')
    init.add_argument('--root', type=Path)
    projects = subs.add_parser('project', help='接入本机项目，仅读取项目文件')
    project_commands = projects.add_subparsers(dest='project_command', required=True)
    add = project_commands.add_parser('add', help='添加独立项目，不修改项目内文件')
    add.add_argument('project_id')
    add.add_argument('--name')
    add.add_argument('--root', type=Path, required=True)
    listing = subs.add_parser("list", help="列出项目")
    listing.add_argument("--json", action="store_true")
    for name in ("show", "refresh", "history"):
        cmd = subs.add_parser(name)
        scoped_project(cmd, json_flag=True)
    cmd = subs.add_parser("handoff", help="导入一份交接；默认标为助手建议")
    scoped_project(cmd)
    cmd.add_argument("--file", type=Path, required=True)
    cmd.add_argument("--expect-revision", type=int, required=True)
    cmd.add_argument("--operation-id")
    cmd = subs.add_parser('plan', help='设置工作线与下一步，不生成完成项')
    scoped_project(cmd)
    cmd.add_argument('--file', type=Path, required=True)
    cmd.add_argument('--expect-revision', type=int, required=True)
    cmd.add_argument('--operation-id')
    cmd = subs.add_parser('changes', help='查看相对交接或上一刷新发生的来源变化')
    scoped_project(cmd, json_flag=True)
    cmd = subs.add_parser("restore", help="将历史交接复制成一个新版本")
    scoped_project(cmd)
    cmd.add_argument("--revision", type=int, required=True)
    cmd.add_argument("--expect-revision", type=int, required=True)
    cmd = subs.add_parser("export")
    scoped_project(cmd)
    cmd.add_argument("--format", choices=["markdown"], default="markdown")
    cmd.add_argument("--mode", choices=["local"], default="local")
    cmd = subs.add_parser("open", help="打开材料；模型等默认打开所在文件夹")
    scoped_project(cmd)
    cmd.add_argument("entry")
    cmd.add_argument('--action', choices=['open', 'folder', 'copy'], help='copy 仅输出路径，不启动程序')
    cmd = subs.add_parser('source', help='查看来源原文、所在文件夹或路径')
    scoped_project(cmd)
    cmd.add_argument('source_id')
    cmd.add_argument('--action', choices=['open', 'folder', 'copy'], default='open')
    cmd = subs.add_parser('worklines', help='查看、新建、选择工作线，或绑定 1–3 份材料')
    cmd.add_argument('project')
    cmd.add_argument('--workline', help='bind 操作的目标工作线；省略时使用当前工作线')
    cmd.add_argument('--json', action='store_true')
    line_subs = cmd.add_subparsers(dest='workline_command')
    line_subs.add_parser('list')
    line_create = line_subs.add_parser('create')
    line_create.add_argument('name')
    line_select = line_subs.add_parser('select')
    line_select.add_argument('workline_id')
    line_bind = line_subs.add_parser('bind')
    line_bind.add_argument('entries', nargs='+', help='登记的材料入口编号（最多 3 项）')
    line_bind.add_argument('--workline', dest='bind_workline', help='绑定到指定工作线')
    cmd = subs.add_parser('review', help='明确复核当前来源变化，保留原交接依据')
    scoped_project(cmd, json_flag=True)
    cmd.add_argument('--expect-revision', type=int, required=True)
    cmd.add_argument('--snapshot', type=int, required=True, help='已经检查过的当前快照编号')
    cmd = subs.add_parser('reviews', help='查看当前工作线的复核历史')
    scoped_project(cmd, json_flag=True)
    cmd = subs.add_parser("focus")
    cmd.add_argument("project")
    cmd.add_argument("--job-id", help="留空字符串恢复历史示例；省略参数则列出候选")
    agent_cli.add_parsers(subs)
    agent_session_cli.register(subs)
    integration_cli.register(subs)
    args = parser.parse_args(actual_argv)
    try:
        if args.command == 'agent':
            output(agent_session_cli.run(args))
            return 0
        if args.command in integration_cli.COMMANDS:
            result = integration_cli.run(args)
            if result is not None:
                if args.command == 'doctor' and getattr(args, 'human', False):
                    print(integration_cli.format_doctor(result))
                else:
                    output(result)
            if args.command == 'doctor' and result and not result.get('ok'):
                return 2
            return 0
        command = args.command or ('desktop' if sys.platform == 'win32' else 'tui')
        if command == 'tui':
            from .tui import run
            return run(args.app_dir, args.data_dir)
        if command == 'desktop':
            from .web_desktop import run
            return run(args.app_dir, args.data_dir, browser=getattr(args, 'browser', False)) or 0
        if command in {'feed', 'read'}:
            from .reader import ReaderModel
            model = ReaderModel(args.app_dir, args.data_dir)
            if command == 'feed':
                output(model.records(args.project, args.workline, args.limit, args.cursor, args.unread, args.query))
            else:
                print(model.export(args.record_id)) if args.markdown else output(model.detail(args.record_id))
                if args.ack:
                    model.ack([args.record_id])
            return 0
        if args.command == 'init':
            from .bootstrap import initialize
            output(initialize(args.app_dir, project_id=args.project_id, name=args.name, root=args.root, data_dir=args.data_dir))
            return 0
        if args.command == 'project':
            from .bootstrap import add_project
            output(add_project(args.app_dir, args.project_id, name=args.name, root=args.root, data_dir=args.data_dir))
            return 0
        if args.command == 'record' and args.record_command == 'schema':
            output(agent_cli.AgentRecords.schema())
            return 0
        if args.command == 'configure-roots':
            output(agent_cli.configure_roots(args.app_dir, args.file, args.data_dir))
            return 0
        if command == 'list':
            from .reader import ReaderModel
            output(ReaderModel(args.app_dir, args.data_dir).registered_projects())
            return 0
        service = Service(args.app_dir, args.data_dir)
        if command in agent_cli.COMMANDS:
            output(agent_cli.dispatch(args, service, sys.stdin))
        elif command == "legacy-desktop":
            from . import platform_io
            platform_io.ensure_desktop(tk_required=True)
            from .desktop import main as desktop_main
            desktop_main(service)
        elif command == "show":
            output(service.show(args.project, workline_id=args.workline)) if args.json else print(service.summary(args.project, workline_id=args.workline))
        elif command == "refresh":
            card = service.refresh(args.project, workline_id=args.workline)
            output(card) if args.json else print(service.summary(args.project, workline_id=args.workline))
            if card.get("last_attempt") and not card["last_attempt"].get("usable"):
                return 2
        elif command == "history":
            output(service.history(args.project, workline_id=args.workline))
        elif command in {"handoff", "plan"}:
            if args.file.stat().st_size > 262144:
                raise ValueError("交接文件不能超过 256 KiB。")
            payload = json.loads(args.file.read_text(encoding="utf-8-sig"))
            save = service.save_plan if command == 'plan' else service.save_handoff
            output(save(args.project, payload, args.expect_revision, args.operation_id, workline_id=args.workline))
        elif command == 'changes':
            card = service.show(args.project, workline_id=args.workline)
            output({'workline': card.get('workline'), 'comparison': card.get('change_comparison'),
                    'review': card.get('review'), 'changes': card.get('source_changes', [])})
        elif command == "restore":
            output(service.restore_handoff(args.project, args.revision, args.expect_revision, workline_id=args.workline))
        elif command == "export":
            print(service.export(args.project, args.mode, workline_id=args.workline))
        elif command == "open":
            result = service.open_entry(args.project, args.entry, action=args.action, workline_id=args.workline)
            if args.action == 'copy':
                print(result)
        elif command == 'source':
            result = service.open_source(args.project, args.source_id, action=args.action, workline_id=args.workline)
            if args.action == 'copy':
                print(result)
        elif command == 'worklines':
            if args.workline_command in (None, 'list'):
                output(service.list_worklines(args.project))
            elif args.workline_command == 'create':
                output(service.create_workline(args.project, args.name))
            elif args.workline_command == 'select':
                output(service.select_workline(args.project, args.workline_id))
            elif args.workline_command == 'bind':
                output(service.set_workline_entries(args.project, args.entries,
                    workline_id=args.bind_workline or args.workline))
        elif command == 'review':
            output(service.acknowledge_changes(args.project, args.expect_revision,
                args.snapshot, workline_id=args.workline))
        elif command == 'reviews':
            output(service.review_history(args.project, workline_id=args.workline))
        elif command == "focus":
            if args.job_id is None:
                output(service.focus_options(args.project))
            else:
                service.set_focus(args.project, args.job_id)
                print("关注任务已更新，请刷新记录。")
        return 0
    except ConflictError as exc:
        output({'schema_version': 1, 'ok': False, 'error': {'code': 'revision_conflict', 'message': str(exc)},
                'current': exc.current, 'proposed': exc.proposed} if machine else
               {"error": str(exc), "current": exc.current, "proposed": exc.proposed})
        return 3
    except (ValueError, OSError, sqlite3.Error) as exc:
        if machine:
            error = {'code': getattr(exc, 'code', 'invalid_input' if isinstance(exc, ValueError) else 'unavailable'), 'message': str(exc)}
            for field in ('retryable', 'saved', 'details'):
                if hasattr(exc, field):
                    error[field] = getattr(exc, field)
            output({'schema_version': 1, 'ok': False, 'error': error})
        else:
            print(f"错误：{exc}", file=sys.stderr)
        return 2
