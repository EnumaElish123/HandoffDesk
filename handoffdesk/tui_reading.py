"""Reading controls imported by the optional terminal UI."""
from __future__ import annotations

import shlex
import unicodedata
from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from .reader import format_recorded_at, safe_text


VERIFICATION = {'passed': '验证通过', 'partial': '部分验证',
                'not_run': '尚未验证', 'failed': '验证未通过'}


def _one_line(value):
    return ' '.join(safe_text(value).split())


def render_record_option(record, query='', zone=None):
    """Keep list rows to three logical lines; a preview retains the full title."""
    read = '已读' if record['read'] else '未读'
    prompt = Text(no_wrap=True, overflow='ellipsis')
    prompt.append(read + '  ', style='dim' if record['read'] else 'bold')
    prompt.append(_one_line(record['title']), style='bold')
    when = format_recorded_at(record['recorded_at'], zone)
    stamp = when.split(' ', 2)
    short_date = ' '.join(stamp[:2])
    prompt.append('\n' + short_date + '  ' + _one_line(record['project_name'])
                  + ' / ' + _one_line(record['workline_name']), style='dim')
    if query.strip() and record.get('match_excerpt'):
        prompt.append('\n' + _one_line(record['match_excerpt']))
    else:
        status = VERIFICATION.get(record['verification'].get('status'), '尚未验证')
        if record['file_count']:
            status += f" · 附件 {record['file_count']}"
        if record['blockers']:
            status += ' · 有阻塞'
        prompt.append('\n' + status, style='dim')
    return prompt


def render_preview(record, zone=None):
    """A highlight is a preview and never acknowledges the record."""
    status = VERIFICATION.get(record['verification'].get('status'), '尚未验证')
    stamp = format_recorded_at(record['recorded_at'], zone)
    sections = ['预览 · Enter 阅读', record['title'],
                f"{record['project_name']} / {record['workline_name']}",
                f"{stamp} · {status} · 附件 {record['file_count']}",
                '', '下一步', record['next_action'] or '未填写']
    if record['blockers']:
        sections.extend(['', '阻塞', ' · '.join(record['blockers'])])
    if record['done']:
        sections.extend(['', '实际完成', record['done']])
    return safe_text('\n'.join(sections))


def render_detail(record, zone=None, *, metadata=False):
    """Format the opened record for reading. Exports retain their full metadata."""
    verification = record['verification']
    status = VERIFICATION.get(verification.get('status'), '尚未验证')
    stamp = format_recorded_at(record['recorded_at'], zone)
    kind = '计划' if record['kind'] == 'plan' else '进展'
    sections = [record['title'],
                f"{record['project_name']} / {record['workline_name']}",
                f"{kind} · {status} · 附件 {record['file_count']} · {stamp.split(' (UTC')[0]}",
                '', '下一步', record['next_action'] or '未填写']
    if record['blockers']:
        sections.extend(['', '阻塞', *('• ' + item for item in record['blockers'])])
    if record['done']:
        sections.extend(['', '实际完成', record['done']])
    sections.extend(['', '验证', verification.get('summary') or status])
    if metadata:
        sections.extend(['', '记录信息', '记录编号  ' + record['id'],
                         '记录时间  ' + stamp, '原始时间  ' + record['recorded_at'],
                         '记录者  ' + record['actor'].get('name', '历史记录')])
        for item in record['files']:
            sections.extend(['', item['name'] + ' · ' + item.get('status', ''),
                             item.get('purpose', ''), item['path']])
    return safe_text('\n'.join(sections))


def continuation_text(record):
    return safe_text(
        f"请继续 HandoffDesk 项目「{record['project_name']}」的任务「{record['workline_name']}」。\n"
        f"项目编号 {record['project_id']}，任务编号 {record['workline_id']}，记录 {record['id']}。\n"
        "先用 agent sessions 按项目目录查找会话，核对任务编号后恢复。"
        "如果没有对应会话，在该工作线上开始续接。\n\n"
        f"记录中的下一步\n{record['next_action'] or '请先阅读最新记录。'}"
    )


class ReadingDialog(ModalScreen):
    BINDINGS = [Binding('escape', 'dismiss', '返回', priority=True)]
    DEFAULT_CSS = """
    ReadingDialog { align: center middle; }
    ReadingDialog > Vertical {
        width: 90%; max-width: 88; height: 85%;
        padding: 0 1; border: round $accent; background: $surface;
    }
    ReadingDialog .dialog-title { height: auto; text-style: bold; }
    ReadingDialog .dialog-hint { height: auto; color: $text-muted; }
    ReadingDialog OptionList, ReadingDialog TextArea { height: 1fr; }
    ReadingDialog .dialog-buttons { height: auto; align-horizontal: right; }
    ReadingDialog .dialog-buttons Button { min-width: 8; margin-left: 1; }
    """


class ActionMenu(ReadingDialog):
    """Return the selected action id; callers supply only commands valid here."""
    BINDINGS = [
        Binding('up', 'move(-1)', '上一项', priority=True),
        Binding('down', 'move(1)', '下一项', priority=True),
    ]

    def __init__(self, title, choices, *, placeholder='搜索操作…'):
        super().__init__()
        self.heading = title
        self.choices = list(choices)
        self.placeholder = placeholder

    def compose(self):
        with Vertical():
            yield Static(safe_text(self.heading), classes='dialog-title', markup=False)
            yield Input(placeholder=self.placeholder, id='action-query')
            yield OptionList(id='action-choices')
            yield Static('', id='action-hint', classes='dialog-hint', markup=False)

    def on_mount(self):
        self.filter_choices('')
        self.query_one('#action-query', Input).focus()

    def filter_choices(self, query):
        def normalized(text):
            return unicodedata.normalize('NFKC', text).casefold()

        terms = normalized(query).split()
        listing = self.query_one('#action-choices', OptionList)
        options = []
        for action, label, key, aliases in self.choices:
            corpus = normalized(' '.join([action, label, key, aliases]))
            if all(term in corpus for term in terms):
                prompt = Text(safe_text(label))
                if key:
                    prompt.append('   ' + safe_text(key), style='dim')
                options.append(Option(prompt, id=action))
        listing.clear_options()
        listing.add_options(options)
        hint = '↑↓ 选择 · Enter 执行 · Esc 返回' if listing.option_count else '没有符合条件的操作。修改搜索词或按 Esc 返回。'
        self.query_one('#action-hint', Static).update(hint)
        if listing.option_count:
            listing.highlighted = 0

    def on_input_changed(self, event):
        self.filter_choices(event.value)

    def action_move(self, delta):
        listing = self.query_one('#action-choices', OptionList)
        if delta < 0:
            listing.action_cursor_up()
        else:
            listing.action_cursor_down()

    def on_input_submitted(self, event):
        listing = self.query_one('#action-choices', OptionList)
        if listing.highlighted is not None:
            self.dismiss(listing.get_option_at_index(listing.highlighted).id)

    def on_option_list_option_selected(self, event):
        self.dismiss(event.option.id)


class ReadOnlyDialog(ReadingDialog):
    BINDINGS = [Binding('q', 'app.quit', '退出')]


class ReferencePage(ReadOnlyDialog):
    def __init__(self, title, body):
        super().__init__()
        self.heading, self.body = title, body

    def compose(self):
        with Vertical():
            yield Static(self.heading, classes='dialog-title', markup=False)
            yield TextArea(self.body, read_only=True, soft_wrap=True, id='help-body')
            yield Static('↑↓ / PgUp / PgDn 滚动 · Esc 返回', classes='dialog-hint')
        yield Footer(compact=True, show_command_palette=False)

    def on_mount(self):
        self.query_one(TextArea).focus()


TUI_SHORTCUTS = """项目和任务
↑↓ / j k    选择项目、任务或列表入口
Enter       进入项目或任务，或打开选中入口
a / +       添加项目或新建任务
r           刷新项目和任务
/           搜索当前范围的记录

记录列表和正文
↑↓ / j k    在列表选择记录，宽屏同步显示预览
Enter       从列表打开正文并标记已读
f           查看选中节点的附件、用途和路径
p / t       切换项目 / 任务
u / x       筛选未读 / 清空筛选
/           搜索记录（Esc 返回）
r / n       刷新记录 / 加载下一页
m           展开或收起正文元数据
y / e       复制续接说明 / 导出当前记录
更多操作    将打开的记录标为未读，或查看项目高级命令示例

附件
↑↓ / Enter  选择文件 / 阅读预览
Ctrl+C      复制当前文件的路径
PgUp/PgDn   翻阅文件预览
m           在预览中显示或收起位置和用途
Esc         返回文件列表或记录

浏览、帮助与表单
. / ?       更多操作、全部快捷键和 CLI 命令
Ctrl+P      按名称查找当前可执行操作
c           查看接入状态；在诊断页按 r 重新检查
Tab / Shift+Tab  切换焦点
Enter       打开选中项或提交表单
Esc         关闭弹窗、退出搜索或返回上一层
q           退出阅读器
专用 tmux 阅读器可从更多操作选择“离开并保持运行”。"""


class HelpScreen(ReadOnlyDialog):
    BINDINGS = [Binding('c', 'jump_cli', 'CLI 命令', show=False)]

    def __init__(self, context, choices):
        super().__init__()
        from .cli import build_parser, command_catalog
        self.context = context
        self.choices = list(choices)
        root = build_parser()
        self.commands = [(root, '查看全局选项与顶层命令'), *command_catalog(root)]

    def compose(self):
        with Vertical():
            yield Static('更多操作 · ' + safe_text(self.context), classes='dialog-title', markup=False)
            yield Static('↑↓ 选择 · Enter 打开或执行 · Esc 返回', classes='dialog-hint')
            options = [Option('全部界面快捷键', id='shortcuts'),
                       Option('CLI 命令目录 · handoffdesk --help   (c)', id='jump-cli'),
                       Option(Text('当前位置可执行的操作', style='bold'), disabled=True)]
            for action, label, key, _ in self.choices:
                if action != 'help':
                    prompt = Text(safe_text(label))
                    if key:
                        prompt.append('   ' + safe_text(key), style='dim')
                    options.append(Option(prompt, id=action))
            options.append(Option(Text('CLI 命令和用法', style='bold'), disabled=True))
            for index, (parser, description) in enumerate(self.commands):
                prompt = Text(parser.prog + ' --help', style='bold')
                prompt.append('\n' + description, style='dim')
                options.append(Option(prompt, id=f'cli-{index}'))
            yield OptionList(*options, id='help-choices')
        yield Footer(compact=True, show_command_palette=False)

    def on_mount(self):
        self.query_one(OptionList).focus()

    def action_jump_cli(self):
        listing = self.query_one(OptionList)
        listing.highlighted = listing.get_option_index('cli-0')

    def on_option_list_option_selected(self, event):
        action = event.option.id
        if action == 'jump-cli':
            self.action_jump_cli()
        elif action == 'shortcuts':
            self.app.push_screen(ReferencePage('全部界面快捷键', TUI_SHORTCUTS))
        elif action.startswith('cli-'):
            parser, description = self.commands[int(action[4:])]
            self.app.push_screen(ReferencePage(parser.prog + ' --help', description + '\n\n' + parser.format_help()))
        else:
            self.dismiss(action)


class AdvancedScreen(ReadOnlyDialog):
    """Expose existing CLI workflows with the same storage and project scope."""
    def __init__(self, project_id, workline_id=None, *, app_dir=None, data_dir=None):
        super().__init__()
        prefix = ['handoffdesk']
        if app_dir is not None:
            prefix.extend(['--app-dir', str(app_dir)])
        if data_dir is not None:
            prefix.extend(['--data-dir', str(data_dir)])
        scope = [project_id]
        if workline_id:
            scope.extend(['--workline', workline_id])
        self.context = ' / '.join(filter(None, [project_id, workline_id]))
        lines = ['查看当前范围', '以下命令可复制到同一台机器的终端。']
        if not workline_id:
            lines.append('项目级入口采用该项目当前选中的工作线。')
        for title, args in [('列出所有任务', ['worklines', project_id, 'list']),
                            ('阅读任务交接历史', ['history', *scope]),
                            ('查看来源变化', ['changes', *scope]),
                            ('查看以往复核', ['reviews', *scope])]:
            lines.extend(['', title, shlex.join(prefix + args)])
        lines.extend(['', '管理操作',
                      '来源刷新会保存新的来源快照。可先查看 refresh --help。',
                      '写入计划或交接时先核对任务与当前版本，再按 plan 或 handoff 的 --help 准备文件。',
                      '恢复历史会将选定记录保存为新版本。先查 history，再用 restore --help 核对参数。',
                      '绑定材料、选择焦点作业或确认来源变化，可分别查看 worklines、focus、review 的 --help。'])
        self.instructions = safe_text('\n'.join(lines))

    def compose(self):
        with Vertical():
            yield Static('高级操作 · ' + safe_text(self.context), classes='dialog-title', markup=False)
            yield TextArea(self.instructions, id='advanced-body', read_only=True, soft_wrap=True)
            yield Button('返回 (Esc)', id='advanced-close')
        yield Footer(compact=True, show_command_palette=False)

    def on_mount(self):
        self.query_one('#advanced-body', TextArea).focus()

    def on_button_pressed(self, event):
        self.dismiss()


class CopyScreen(ReadOnlyDialog):
    """Send the requested text to the terminal and keep the source selectable."""
    def __init__(self, title, text):
        super().__init__()
        self.heading = title
        self.content = text

    def compose(self):
        with Vertical():
            yield Static(safe_text(self.heading), classes='dialog-title', markup=False)
            yield TextArea(safe_text(self.content), read_only=True, soft_wrap=True, id='copy-text')
            yield Static('', id='copy-status', classes='dialog-hint', markup=False)
            with Horizontal(classes='dialog-buttons'):
                yield Button('再次复制', id='copy-again')
                yield Button('返回 (Esc)', id='copy-close')
        yield Footer(compact=True, show_command_palette=False)

    def on_mount(self):
        self.copy_content()
        self.query_one('#copy-text', TextArea).focus()

    def copy_content(self):
        self.app.copy_to_clipboard(self.content)
        self.query_one('#copy-status', Static).update(
            '已请求终端复制。如果剪贴板未更新，可选择上方原文并使用终端的复制操作。')

    def on_button_pressed(self, event):
        if event.button.id == 'copy-again':
            self.copy_content()
        else:
            self.dismiss()
