"""Stable, headless commands for agents and per-machine project roots."""
import json
from pathlib import Path


COMMANDS = {'record', 'nodes', 'files', 'configure-roots'}
LIMIT = 1024 * 1024


def add_parsers(subs):
    record = subs.add_parser('record', help='管理 Agent 记录与附件清单')
    commands = record.add_subparsers(dest='record_command', required=True)
    commands.add_parser('schema', help='查看记录的输入格式')
    context = commands.add_parser('context', help='读取提交记录所需的工作线上下文')
    context.add_argument('project')
    context.add_argument('--workline', required=True)
    for name in ('validate', 'save'):
        command = commands.add_parser(name, help='校验记录及文件清单' if name == 'validate' else '保存记录及文件清单')
        source = command.add_mutually_exclusive_group(required=True)
        source.add_argument('--file', type=Path)
        source.add_argument('--stdin', action='store_true')
    nodes = subs.add_parser('nodes', help='列出工作线的进展节点（JSON）')
    nodes.add_argument('project')
    nodes.add_argument('--workline', required=True)
    files = subs.add_parser('files', help='查看指定节点的附件清单（JSON）')
    files.add_argument('project')
    files.add_argument('--node', required=True)
    roots = subs.add_parser('configure-roots', help='查看或保存本机项目目录映射')
    source = roots.add_mutually_exclusive_group()
    source.add_argument('--show', action='store_true')
    source.add_argument('--file', type=Path)


def read_payload(path=None, stream=None):
    if path is not None:
        with Path(path).open('rb') as handle:
            raw = handle.read(LIMIT + 1)
    else:
        raw = getattr(stream, 'buffer', stream).read(LIMIT + 1)
    if isinstance(raw, str):
        raw = raw.encode('utf-8')
    if len(raw) > LIMIT:
        raise ValueError('输入 JSON 不能超过 1 MiB。')
    try:
        value = json.loads(raw.decode('utf-8-sig'))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('输入必须是有效的 UTF-8 JSON：' + str(exc)) from exc
    if not isinstance(value, dict):
        raise ValueError('输入必须是一个 JSON 对象。')
    return value


def configure_roots(app_dir, path=None, data_dir=None):
    from .bootstrap import configuration_lock, publish_root_mapping
    from .registry import Registry
    from .paths import app_directory, require_configuration, validate_configuration_writes

    app = app_directory(app_dir)
    require_configuration(app)
    config = (app / 'config').resolve()
    if not config.is_relative_to(app):
        raise ValueError('配置目录不能重定向到工具目录之外。')
    target = config / 'roots.local.json'
    if not target.resolve().is_relative_to(config):
        raise ValueError('本机目录映射不能重定向到配置目录之外。')
    if path is None:
        if target.exists():
            return read_payload(target)
        base = read_payload(config / 'projects.json')
        return {'schema_version': 1, 'projects': {p['id']: p['roots'] for p in base['projects']}}
    payload = read_payload(path)
    registry = Registry(config / 'projects.json', roots_override=payload)
    # Check the proposed ownership before creating a lock in this directory.
    validate_configuration_writes(app, registry.projects, data_dir)
    with configuration_lock(app):
        # Registration or relocation could have changed which roots are effective.
        registry = publish_root_mapping(app, payload, data_dir)
    return {'schema_version': 1, 'saved': True, 'path': str(target),
            'projects': {p['id']: p['roots'] for p in registry.projects}}


def dispatch(args, service, stream):
    if args.command == 'nodes':
        return {'schema_version': 1, 'nodes': service.list_file_nodes(args.project, args.workline)}
    if args.command == 'files':
        return dict(schema_version=1, **service.get_node_files(args.project, args.node))
    if args.record_command == 'context':
        state = service.show(args.project, workline_id=args.workline)
        record = state.get('handoff')
        return {'schema_version': 1, 'project': state['project']['id'],
                'workline': state['workline']['id'], 'current_revision': (record or {}).get('revision', 0),
                'record': record, 'source_snapshot_id': (state.get('snapshot') or {}).get('id'),
                'roots': service.registry.get(args.project)['roots'],
                'nodes': service.list_file_nodes(args.project, args.workline)}
    payload = read_payload(args.file, stream)
    from .agent_records import AgentRecords
    api = AgentRecords(service)
    return api.validate(payload) if args.record_command == 'validate' else api.save(payload)
