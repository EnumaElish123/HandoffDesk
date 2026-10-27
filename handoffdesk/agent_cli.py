"""Stable, headless commands for agents and per-machine project roots."""
import json
import os
from pathlib import Path
import tempfile

from .agent_records import AgentRecords
from .registry import Registry
from .paths import app_directory, require_configuration, validate_configuration_writes

COMMANDS = {'record', 'nodes', 'files', 'configure-roots'}
LIMIT = 1024 * 1024


def add_parsers(subs):
    record = subs.add_parser('record', help='Agent: structured records with atomic file manifests')
    commands = record.add_subparsers(dest='record_command', required=True)
    commands.add_parser('schema', help='Print the input contract without opening the database')
    context = commands.add_parser('context', help='Read a workline before submitting')
    context.add_argument('project')
    context.add_argument('--workline', required=True)
    for name in ('validate', 'save'):
        command = commands.add_parser(name)
        source = command.add_mutually_exclusive_group(required=True)
        source.add_argument('--file', type=Path)
        source.add_argument('--stdin', action='store_true')
    nodes = subs.add_parser('nodes', help='List progress nodes as JSON')
    nodes.add_argument('project')
    nodes.add_argument('--workline', required=True)
    files = subs.add_parser('files', help='Read one node file manifest as JSON')
    files.add_argument('project')
    files.add_argument('--node', required=True)
    roots = subs.add_parser('configure-roots', help='Read or save this machine project-root mapping')
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
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    if len(encoded) > 262144:
        raise ValueError('保存后的本机目录配置不能超过 256 KiB。')
    # Validate every root before atomically replacing the local-only mapping.
    validate_configuration_writes(app, registry.projects, data_dir)
    pending = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', dir=config,
                                         prefix='.roots-', suffix='.tmp', delete=False) as handle:
            pending = Path(handle.name)
            handle.write(encoded)
        os.replace(pending, target)
        pending = None
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)
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
    api = AgentRecords(service)
    return api.validate(payload) if args.record_command == 'validate' else api.save(payload)
