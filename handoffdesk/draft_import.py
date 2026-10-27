"""Parse an explicitly pasted summary locally, without inferring accomplishments."""
import json
import re


ALIASES = {
    'current_task': 'current_task', '当前工作线': 'current_task', '本次主题补充': 'current_task',
    '当前任务': 'current_task', 'last_done': 'last_done', '本次完成': 'last_done',
    '最近完成': 'last_done', '上次完成': 'last_done', '已完成': 'last_done', '做到哪了': 'last_done',
    'next_action': 'next_action', '下一步': 'next_action', '下次第一步': 'next_action',
    '后续动作': 'next_action', 'blockers': 'blockers', '阻塞项': 'blockers',
    '当前阻塞': 'blockers', '待解决': 'blockers',
}
METADATA_HEADINGS = {'工作线', '交接', '起步计划', '依据快照', '来源读取', '材料入口', '需要留意',
                     'source_snapshot_id', 'entry_ids', 'origin', 'event_kind'}


def _heading(line):
    line = re.sub(r'^\s*(?:#{1,6}\s*|[-*+]\s+|\d+[.、]\s*)', '', line.strip())
    line = line.replace('**', '').strip()
    match = re.match(r'^([^：:]+)[：:]\s*(.*)$', line)
    if match:
        name = match.group(1).strip()
        if name in METADATA_HEADINGS:
            return '__metadata__', match.group(2)
        key = ALIASES.get(name)
        if key:
            return key, match.group(2)
    if line in ALIASES:
        return ALIASES[line], ''
    if line in METADATA_HEADINGS:
        return '__metadata__', ''
    return None


def parse_summary(text):
    """Return unconfirmed draft fields; source IDs are never invented or rebound.

    Supported inputs are one JSON object (optionally fenced), or explicit Chinese
    / English field headings. Unknown prose is rejected instead of guessing what
    was done. The caller must validate an explicit snapshot against its scope.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError('请粘贴带“本次完成”“下一步”标题的总结，或 JSON 对象。')
    if len(text) > 32768:
        raise ValueError('导入内容超过 32768 字，请只保留本次交接相关部分。')
    raw = text.strip().lstrip('\ufeff')
    if raw.startswith('```'):
        match = re.fullmatch(r'```(?:json)?\s*\n(.*?)\n```', raw, re.S | re.I)
        if not match:
            raise ValueError('请粘贴单个完整 JSON 代码块，或不带代码块的文字总结。')
        raw = match.group(1).strip()
    warnings = []
    if raw.startswith(('{', '[')):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError('JSON 格式不完整，请检查引号、逗号和括号。') from exc
        if not isinstance(value, dict):
            raise ValueError('导入 JSON 必须是单个对象。')
        allowed = set(ALIASES) | {'source_snapshot_id', 'entry_ids', 'origin', 'event_kind'}
        unknown = sorted(set(value) - allowed)
        if unknown:
            warnings.append('未导入其他字段：' + '、'.join(unknown[:8]))
        parsed = {}
        for key, item in value.items():
            canonical = ALIASES.get(key, key)
            if canonical in parsed:
                raise ValueError('同一交接字段使用了多个名称，请保留一份：' + canonical)
            parsed[canonical] = item
    else:
        chunks = {}
        field = None
        ignored = []
        for line in raw.splitlines():
            heading = _heading(line)
            if heading:
                field, first = heading
                if field == '__metadata__':
                    field = None
                    ignored.append(line)
                    continue
                if field in chunks:
                    raise ValueError('总结包含重复标题，请合并后再导入：' + field)
                chunks[field] = [first] if first else []
            elif field:
                # Unrecognized Markdown headings start an unrelated section.
                if re.match(r'^\s*#{1,6}\s+', line):
                    field = None
                    ignored.append(line)
                else:
                    chunks[field].append(line)
            elif line.strip():
                ignored.append(line)
        if not chunks:
            raise ValueError('未识别到交接字段。请使用“本次完成：…”和“下一步：…”等标题，或 JSON。')
        parsed = {key: '\n'.join(lines).strip() for key, lines in chunks.items()}
        if ignored:
            warnings.append('标题以外的说明未导入，请在预览中核对。')
    result = {'origin': 'assistant_suggested'}
    for key in ('current_task', 'last_done', 'next_action'):
        item = parsed.get(key, '')
        if not isinstance(item, str) or len(item) > 12000:
            raise ValueError(key + ' 必须为 12000 字以内的文字。')
        result[key] = item.strip()
    blockers = parsed.get('blockers', [])
    if isinstance(blockers, str):
        blockers = [re.sub(r'^\s*(?:[-*+]\s+|\d+[.、]\s*)', '', line).strip()
                    for line in blockers.splitlines() if line.strip()]
    if not isinstance(blockers, list) or len(blockers) > 100 or any(not isinstance(x, str) or len(x) > 2000 for x in blockers):
        raise ValueError('阻塞项必须为文字列表，每条不超过 2000 字。')
    result['blockers'] = [x.strip() for x in blockers if x.strip()]
    sid = parsed.get('source_snapshot_id')
    if sid is not None and (not isinstance(sid, int) or isinstance(sid, bool) or sid <= 0):
        raise ValueError('来源快照编号必须为正整数或 null。')
    result['source_snapshot_id'] = sid
    if sid is None:
        warnings.append('未提供原始来源快照；导入不会自动绑定当前资料。')
    entries = parsed.get('entry_ids', [])
    if not isinstance(entries, list) or len(entries) > 3 or any(not isinstance(x, str) or not x or len(x) > 200 for x in entries):
        raise ValueError('材料编号必须为最多三项的文字列表。')
    result['entry_ids'] = list(dict.fromkeys(entries))
    if not result['last_done'] and not result['next_action']:
        raise ValueError('至少需要填写“本次完成”或“下一步”。')
    warnings.append('这是待确认的助手总结，请核对实际完成内容与下一步再提交。')
    result['_import_warnings'] = warnings
    return result
