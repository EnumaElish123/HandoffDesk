"""Optional terminal reader. Importing the CLI never imports Textual."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shlex
import sqlite3
import sys


def ensure_terminal():
    if not sys.stdin.isatty() or not sys.stdout.isatty() or os.environ.get('TERM') in {None, '', 'dumb'}:
        raise ValueError('终端阅读需要交互式终端（TTY）。可改用 handoffdesk feed。')
    if importlib.util.find_spec('textual') is None:
        source = Path(__file__).resolve().parents[1]
        if not getattr(sys, 'frozen', False) and (source / 'pyproject.toml').is_file():
            repair = shlex.join([sys.executable, '-m', 'pip', 'install', str(source) + '[tui]'])
            raise ValueError('终端阅读组件尚未安装。使用当前源码修复：' + repair)
        raise ValueError('终端阅读组件尚未安装。请重新安装含 Linux 终端组件的 HandoffDesk 发行包；仍可用 handoffdesk feed 阅读。')


def create_app(app_dir=None, data_dir=None):
    import asyncio
    from rich.text import Text
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.screen import ModalScreen
    from textual.widgets import Footer, Header, Input, Label, OptionList, Select, Static, TextArea
    from textual.widgets.option_list import Option
    from .reader import ReaderModel, safe_text, format_recorded_at
    from .paths import app_directory

    model = ReaderModel(app_dir, data_dir)
    zone = os.environ.get('HANDOFFDESK_TIMEZONE') or None

    class AnchoredOptions(OptionList):
        def row_line(self, index):
            # Textual 6.12's measured row positions include soft-wrapped CJK text.
            self._update_lines()
            return self._index_to_line.get(index, 0)

    class FilesScreen(ModalScreen):
        BINDINGS = [('escape', 'back', '返回'), Binding('ctrl+c', 'copy_path', '复制路径', priority=True),
                    ('m', 'metadata', '位置与用途')]
        CSS = '''
        FilesScreen { background: $surface; }
        #file-modal { width: 100%; height: 100%; }
        #file-heading { height: 1; }
        #file-list { height: 1fr; }
        #file-notice { height: auto; max-height: 5; display: none; }
        #file-preview { height: 1fr; display: none; }
        #file-help { height: 1; }
        FilesScreen.preview-mode #file-list { display: none; }
        FilesScreen.preview-mode #file-preview { display: block; }
        FilesScreen.show-metadata #file-notice { display: block; }
        '''

        def __init__(self, record):
            super().__init__()
            self.record, self.current_path, self.request = record, '', 0

        def compose(self) -> ComposeResult:
            with Vertical(id='file-modal'):
                yield Label('本次文件 · Enter 阅读 · Esc 返回', id='file-heading')
                yield OptionList(*(Option(Text(safe_text(f"{item['name']} · {item.get('status', '')}\n{item.get('purpose', '')}")), id=item['id'])
                                   for item in self.record['files']), id='file-list')
                yield Static('', id='file-notice', markup=False)
                yield TextArea('', read_only=True, id='file-preview', soft_wrap=True)
                yield Static('Enter 阅读 · Esc 返回 · Ctrl+C 复制路径', id='file-help', markup=False)

        async def on_option_list_option_selected(self, event):
            self.request += 1
            request = self.request
            item = next(item for item in self.record['files'] if item['id'] == event.option.id)
            self.current_path = item['path']
            self.add_class('preview-mode')
            self.query_one('#file-heading', Label).update(safe_text(item['name']))
            self.query_one('#file-notice', Static).update(safe_text(item['path']) + '\n用途：' + safe_text(item.get('purpose', '')))
            self.query_one('#file-help', Static).update('PgUp/PgDn 阅读 · m 位置 · Ctrl+C 复制 · Esc 文件列表')
            area = self.query_one('#file-preview', TextArea)
            area.load_text('读取中…')
            area.focus()
            try:
                result = await asyncio.to_thread(model.preview, self.record['id'], item['id'])
                text = result['text']
                if result['truncated']:
                    text += '\n\n[已截断：最多预览 1 MiB / 2,000 行]'
                if result.get('encoding_warning'):
                    text += '\n[部分字节不是 UTF-8，已用替代字符显示]'
            except (ValueError, OSError, sqlite3.Error) as exc:
                text = '无法预览：' + safe_text(exc) + '\n\n请让 Agent 核对文件是否移动，并更新这条记录的文件关联。按 m 查看位置，Ctrl+C 复制原路径。'
            if request == self.request and self.app.is_running and self.is_mounted:
                area.load_text(text)

        def action_back(self):
            if self.has_class('preview-mode'):
                self.request += 1
                self.remove_class('preview-mode', 'show-metadata')
                self.query_one('#file-heading', Label).update('本次文件 · Enter 阅读 · Esc 返回')
                self.query_one('#file-help', Static).update('Enter 阅读 · Esc 返回 · Ctrl+C 复制路径')
                self.query_one('#file-list').focus()
            else:
                self.dismiss()

        def action_metadata(self):
            self.toggle_class('show-metadata')

        def action_copy_path(self):
            if not self.has_class('preview-mode'):
                listing = self.query_one('#file-list', OptionList)
                if listing.highlighted is not None:
                    self.current_path = self.record['files'][listing.highlighted]['path']
            if self.current_path:
                self.app.copy_to_clipboard(self.current_path)
                self.notify('已请求终端复制路径；到达本机剪贴板取决于终端支持。')

    class ExportScreen(ModalScreen):
        BINDINGS = [('escape', 'dismiss', '取消')]
        CSS = '''
        ExportScreen { align: center middle; }
        #export-box { width: 95%; height: auto; max-height: 95%; padding: 1; background: $surface; border: round $accent; }
        #export-message { height: auto; }
        '''

        def __init__(self, record):
            super().__init__()
            self.record = record

        def compose(self):
            import re
            name = re.sub(r'[^\w\-\u4e00-\u9fff]+', '-', self.record['title']).strip('-')[:60] or 'handoff'
            destination = Path.home() / f'{name}-{self.record["revision"]}.md'
            with Vertical(id='export-box'):
                yield Label('导出当前记录 · 确认路径后 Enter 保存 · Esc 取消')
                yield Input(str(destination), id='export-path')
                yield Static('保存 UTF-8 Markdown；文件关联仍是引用。已有文件不会覆盖。', id='export-message', markup=False)

        async def on_input_submitted(self, event):
            if event.input.id != 'export-path':
                return
            try:
                result = await asyncio.to_thread(model.export_file, self.record['id'], Path(event.value).expanduser())
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.query_one('#export-message', Static).update('未导出：' + safe_text(exc))
                return
            path = result['path'] if isinstance(result, dict) else str(result)
            self.app.notify('已导出：' + safe_text(path), timeout=10)
            self.dismiss(path)

    class ConnectionScreen(ModalScreen):
        BINDINGS = [('escape', 'dismiss', '返回'), ('r', 'reload', '重新检查')]
        CSS = '#connection-info { height: 1fr; } #connection-heading { height: 2; }'

        def compose(self):
            yield Static('接入与状态 · r 重新检查 · Esc 返回', id='connection-heading', markup=False)
            yield TextArea('正在检查…', read_only=True, soft_wrap=True, id='connection-info')

        async def on_mount(self):
            await self.action_reload()

        async def action_reload(self):
            from .integration import doctor, format_doctor
            try:
                result = await asyncio.to_thread(doctor, app_directory(app_dir), probe=False)
                text = format_doctor(result)
            except (ValueError, OSError, sqlite3.Error) as exc:
                text = '诊断失败：' + safe_text(exc)
            if self.app.is_running and self in self.app.screen_stack:
                self.query_one('#connection-info', TextArea).load_text(safe_text(text))

    class HandoffApp(App):
        TITLE = 'HandoffDesk'
        SUB_TITLE = '本机进展 · Agent 记录，随时续接'
        CSS = '''
        Screen { background: $background; }
        #filters { height: 3; }
        #project { width: 50%; }
        #task { width: 1fr; }
        #search { height: 3; }
        #status { height: 2; color: $text-muted; padding: 0 1; }
        #body { height: 1fr; }
        #records { width: 40%; height: 1fr; border: round $primary; }
        #detail { width: 1fr; height: 1fr; border: round $accent; padding: 0 1; }
        #empty { width: 1fr; height: 1fr; padding: 1; display: none; }
        .empty-mode #empty { display: block; }
        .empty-mode #records, .empty-mode #detail { display: none; }
        .narrow #records { width: 100%; }
        .narrow #detail { width: 100%; }
        .narrow.list-mode #detail { display: none; }
        .narrow.detail-mode #records { display: none; }
        '''
        BINDINGS = [Binding('q', 'quit', '退出'), Binding('/', 'search', '搜索'),
                    Binding('p', 'projects', '项目', show=False), Binding('t', 'tasks', '任务', show=False),
                    Binding('f', 'files', '文件'), Binding('r', 'refresh', '刷新', show=False),
                    Binding('u', 'unread', '未读', show=False), Binding('escape', 'back', '列表', show=False),
                    Binding('n', 'more', '下一页', show=False), Binding('e', 'export', '导出'),
                    Binding('c', 'connection', '接入状态'), Binding('y', 'continue_task', '续接', show=False),
                    Binding('j', 'next_record', show=False), Binding('k', 'previous_record', show=False),
                    Binding('?', 'help', '帮助'), Binding('x', 'clear_filters', '清空筛选', show=False)]

        def __init__(self):
            super().__init__()
            self.items, self.current, self.feed_cursor, self.next_cursor = [], None, None, None
            self.list_request, self.detail_request = 0, 0
            self.unread_only, self.polling = False, False
            self.project_signature, self.projects_data, self.task_signature = None, [], None
            self.pending_updates = False
            self.reader_screen = None

        def widget(self, selector, kind=None):
            return self.reader_screen.query_one(selector, kind) if kind else self.reader_screen.query_one(selector)

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal(id='filters'):
                yield Select([('所有项目', '')], value='', allow_blank=False, id='project')
                yield Select([('所有任务', '')], value='', allow_blank=False, id='task')
            yield Input(placeholder='搜索项目、任务或进展 /', id='search')
            yield Static('正在读取本机记录…', id='status', markup=False)
            with Horizontal(id='body'):
                yield AnchoredOptions(id='records')
                yield TextArea('选择一条进展，按 Enter 阅读。', read_only=True, soft_wrap=True, id='detail')
                yield Static('', id='empty', markup=False)
            yield Footer()

        async def on_mount(self):
            self.reader_screen = self.screen
            self.reader_screen.add_class('list-mode')
            self.resize_layout()
            try:
                await self.load_projects()
                await self.load_records()
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.status('读取失败，可按 r 重试：' + str(exc))
            self.set_interval(1.5, self.poll)
            self.widget('#records').focus()

        def resize_layout(self):
            if self.reader_screen:
                self.reader_screen.set_class(self.size.width < 100, 'narrow')

        def on_resize(self, event):
            if self.reader_screen:
                self.reader_screen.set_class(event.size.width < 100, 'narrow')

        def status(self, text):
            if not self.is_running or self.reader_screen is None:
                return
            self.widget('#status', Static).update(safe_text(text))

        async def load_projects(self):
            projects = await asyncio.to_thread(model.projects)
            if not self.is_running:
                return
            signature = [(p['id'], p['name'], p['unread'], [(l['id'], l['name']) for l in p['worklines']]) for p in projects]
            self.projects_data = projects
            if signature != self.project_signature:
                selector = self.widget('#project', Select)
                selected = selector.value
                with self.prevent(Select.Changed):
                    selector.set_options([('所有项目', '')] + [(Text(safe_text(f"{p['name']} · {p['unread']} 未读")), p['id']) for p in projects])
                    selector.value = selected if selected in [p['id'] for p in projects] else ''
                self.project_signature = signature
                self.load_tasks()

        def load_tasks(self):
            project = self.widget('#project', Select).value
            options = [('所有任务', '')]
            for p in self.projects_data:
                if project and p['id'] != project:
                    continue
                for line in p['worklines']:
                    label = line['name'] if project else p['name'] + ' / ' + line['name']
                    options.append((Text(safe_text(label)), p['id'] + ':' + line['id']))
            selector = self.widget('#task', Select)
            selected = selector.value
            with self.prevent(Select.Changed):
                selector.set_options(options)
                selector.value = selected if selected in [v for _, v in options] else ''

        def scope(self):
            project = self.widget('#project', Select).value or None
            task = self.widget('#task', Select).value
            line = None
            if isinstance(task, str) and task:
                project, line = task.split(':', 1)
            return project, line

        def filter_changed(self):
            self.detail_request += 1
            self.current = None
            self.reader_screen.remove_class('detail-mode')
            self.reader_screen.add_class('list-mode')
            self.widget('#detail', TextArea).load_text('选择筛选结果，按 Enter 阅读。')
            self.pending_updates = False

        async def empty_message(self):
            project, line = self.scope()
            if project or line or self.unread_only or self.widget('#search', Input).value.strip():
                return '没有符合条件的记录。\n\n可修改搜索文字，或按 Esc 后再按 x 清空筛选。按 c 查看接入状态。'
            from .integration import doctor
            try:
                result = await asyncio.to_thread(doctor, app_directory(app_dir), probe=False)
                if not result.get('connected'):
                    return '尚未接入 Agent，当前没有进展记录。\n\n请运行 handoffdesk connect codex 完成一次接入。按 c 查看状态与具体步骤。'
                if not result.get('fully_verified'):
                    return '接入文件已准备，宿主调用尚未验证。\n\n按 c 查看信任或其他待处理步骤，再让 Agent 完成一次真实任务。'
                return '已接入，正在等待第一条进展。\n\n让 Agent 在项目中完成一次真实任务，然后回来阅读。按 c 查看状态。'
            except (ValueError, OSError, sqlite3.Error) as exc:
                return '接入状态暂不可读取：' + safe_text(exc) + '\n\n按 c 查看诊断，按 r 重试。'

        async def load_records(self, more=False, preserve_depth=False):
            if not self.is_running:
                return
            self.list_request += 1
            request = self.list_request
            project, line = self.scope()
            query = self.widget('#search', Input).value
            prior_items = list(self.items)
            oldest = prior_items[-1]['id'] if preserve_depth and prior_items else None
            try:
                result = await asyncio.to_thread(model.records, project, line, 50,
                                                self.next_cursor if more else None, self.unread_only, query)
                while not more and oldest and oldest not in {v['id'] for v in result['items']} and result['next_cursor']:
                    page = await asyncio.to_thread(model.records, project, line, 100, result['next_cursor'], self.unread_only, query)
                    result['items'].extend(page['items'])
                    result['next_cursor'] = page['next_cursor']
                if request != self.list_request or not self.is_running:
                    return
                listing = self.widget('#records', OptionList)
                highlighted, old_scroll = listing.highlighted, listing.scroll_y
                prior_id = self.items[highlighted]['id'] if highlighted is not None and highlighted < len(self.items) else None
                # Preserve the selected row's visual offset, including wrapped rows.
                old_offset = listing.row_line(highlighted) - old_scroll if highlighted is not None else 0
                combined = self.items + result['items'] if more else result['items']
                self.items = list({item['id']: item for item in combined}.values())
                self.next_cursor = result['next_cursor']
                listing.clear_options()
                listing.add_options([Option(Text(safe_text(f"{'○' if v['read'] else '●'} {v['title']}\n{v['project_name']} / {v['workline_name']}\n{format_recorded_at(v['recorded_at'], zone)}")), id=v['id']) for v in self.items])
                listing.highlighted = next((i for i, item in enumerate(self.items) if item['id'] == prior_id), 0) if self.items else None
                if preserve_depth and prior_id and old_scroll:
                    # Background polling is deferred when scrolled; explicit refresh preserves the anchor.
                    def restore_anchor():
                        if self.is_running and request == self.list_request and listing.highlighted is not None:
                            listing.scroll_to(y=listing.row_line(listing.highlighted) - old_offset, animate=False, force=True)
                    self.call_after_refresh(restore_anchor)
                self.reader_screen.set_class(not self.items and not self.current, 'empty-mode')
                if not self.items:
                    message = await self.empty_message()
                    if request == self.list_request and self.is_running:
                        self.widget('#empty', Static).update(message)
                if not self.is_running:
                    return
                self.status(('未读 · ' if self.unread_only else '') + f"{len(self.items)} 条记录" + (' · n 加载更多' if self.next_cursor else '') + ' · c 接入状态')
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.status('读取失败：' + str(exc))

        async def poll(self):
            if self.polling or self.reader_screen is None:
                return
            self.polling = True
            try:
                value = await asyncio.to_thread(model.changes, self.feed_cursor)
                if not self.is_running:
                    return
                if value['changed']:
                    self.feed_cursor = value['cursor']
                    await self.load_projects()
                    if not self.is_running:
                        return
                    listing = self.widget('#records', OptionList)
                    if self.items and (listing.scroll_y > 0 or (listing.highlighted or 0) > 0):
                        self.pending_updates = True
                        self.status('有更新 · r 加载新进展；当前历史位置保留')
                    else:
                        await self.load_records(preserve_depth=True)
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.status('更新失败，可按 r 重试：' + str(exc))
            finally:
                self.polling = False

        async def on_input_changed(self, event):
            if event.input.id == 'search' and self.reader_screen and self.is_running:
                self.filter_changed()
                await self.load_records()

        async def on_select_changed(self, event):
            if event.select.id in {'project', 'task'} and self.reader_screen and self.is_running:
                if event.select.id == 'project':
                    self.load_tasks()
                self.filter_changed()
                await self.load_records()

        async def on_option_list_option_selected(self, event):
            if event.option_list.id != 'records':
                return
            record_id = event.option.id
            self.detail_request += 1
            request = self.detail_request
            try:
                value = await asyncio.to_thread(model.detail, record_id)
                text = await asyncio.to_thread(model.export, record_id)
                if request != self.detail_request or not self.is_running:
                    return
                self.current = value
                shown = format_recorded_at(value['recorded_at'], zone)
                self.widget('#detail', TextArea).load_text('时间：' + shown + '\n' + text)
                self.reader_screen.remove_class('list-mode')
                self.reader_screen.add_class('detail-mode')
                self.widget('#detail').focus()
                await asyncio.to_thread(model.ack, [record_id])
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.status('无法读取详情：' + str(exc))

        def action_search(self):
            self.widget('#search').focus()

        def action_projects(self):
            self.widget('#project').focus()

        def action_tasks(self):
            self.widget('#task').focus()

        async def action_refresh(self):
            try:
                await self.load_projects()
                await self.load_records(preserve_depth=True)
                self.pending_updates = False
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.status('读取失败：' + str(exc))

        async def action_more(self):
            if self.next_cursor:
                await self.load_records(more=True)

        async def action_unread(self):
            self.unread_only = not self.unread_only
            self.filter_changed()
            await self.load_records()

        async def action_clear_filters(self):
            with self.prevent(Select.Changed, Input.Changed):
                self.widget('#project', Select).value = ''
                self.load_tasks()
                self.widget('#task', Select).value = ''
                self.widget('#search', Input).value = ''
            self.unread_only = False
            self.filter_changed()
            await self.load_records()
            self.widget('#records').focus()

        def action_back(self):
            self.reader_screen.remove_class('detail-mode')
            self.reader_screen.add_class('list-mode')
            self.widget('#records').focus()

        async def action_files(self):
            if self.current and self.current['files']:
                try:
                    current = await asyncio.to_thread(model.detail, self.current['id'])
                    if self.is_running:
                        self.push_screen(FilesScreen(current))
                except (ValueError, OSError, sqlite3.Error) as exc:
                    self.status('读取文件失败：' + str(exc))
            else:
                self.status('先打开一条含附件的记录，再按 f。')

        def action_export(self):
            if self.current:
                self.push_screen(ExportScreen(self.current))
            else:
                self.status('先打开一条记录，再按 e 导出。')

        def action_connection(self):
            self.push_screen(ConnectionScreen())

        def action_continue_task(self):
            if self.current:
                value = self.current
                prompt = f"请继续 HandoffDesk 项目「{value['project_name']}」的任务「{value['workline_name']}」（项目 {value['project_id']}，工作线 {value['workline_id']}）。先用 agent sessions 按项目目录发现会话，核对任务标识后恢复，并阅读记录 {value['id']} 的下一步。"
                prompt += ' 若没有对应会话，则在该工作线上开始续接。'
                self.copy_to_clipboard(prompt)
                self.notify('已请求复制续接指令，可交给 Agent；剪贴板是否可用取决于终端。', timeout=8)
            else:
                self.status('先打开一条记录，再按 y 复制续接指令。')

        def action_next_record(self):
            self.widget('#records', OptionList).action_cursor_down()

        def action_previous_record(self):
            self.widget('#records', OptionList).action_cursor_up()

        def action_help(self):
            self.notify('↑↓/j/k 选择，Enter 阅读，Esc 返回，/ 搜索，p 项目，t 任务，u 未读，x 清空筛选，f 文件，e 导出，c 接入状态，y 续接，n 下一页，q 退出。输入框内字母用于输入。', timeout=15)

    return HandoffApp()


def run(app_dir=None, data_dir=None):
    ensure_terminal()
    create_app(app_dir, data_dir).run()
    return 0
