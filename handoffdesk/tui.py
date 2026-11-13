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
    from concurrent.futures import ThreadPoolExecutor
    from time import monotonic
    from rich.text import Text
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.screen import ModalScreen, Screen
    from textual.widgets import Button, Footer, Input, Label, OptionList, Select, Static, TextArea
    from textual.widgets.option_list import Option
    from .reader import ReaderModel, safe_text
    from .paths import app_directory
    from . import terminal_session

    from .tui_projects import make_project_screens, save_reading_position

    from .tui_reading import (ActionMenu, AdvancedScreen, CopyScreen, HelpScreen, ReadOnlyDialog, continuation_text,
                              render_detail, render_preview, render_record_option)

    model = ReaderModel(app_dir, data_dir)
    Home = make_project_screens(model)
    zone = os.environ.get('HANDOFFDESK_TIMEZONE') or None

    class AnchoredOptions(OptionList):
        def row_line(self, index):
            # Textual 6.12's measured row positions include soft-wrapped CJK text.
            self._update_lines()
            return self._index_to_line.get(index, 0)

    class FilesScreen(ModalScreen):
        BINDINGS = [('escape', 'back', '返回'), ('q', 'app.quit', '退出'), Binding('ctrl+c', 'copy_path', '复制路径', priority=True),
                    ('m', 'metadata', '位置与用途')]
        CSS = '''
        FilesScreen { background: $surface; }
        #file-modal { width: 100%; height: 100%; }
        #file-heading { height: 1; color: $accent; text-style: bold; }
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
            self.heading = f"节点附件 · {len(record['files'])} 项 · {safe_text(record['title'])}"

        def file_option(self, item):
            info = [item['kind'], item['status']]
            if item.get('current_metadata') is not None:
                info.append(f"{item['current_metadata']['size']:,} B")
            elif item.get('recorded_metadata') is not None:
                info.append(f"记录时 {item['recorded_metadata']['size']:,} B")
            prompt = Text(overflow='fold')
            prompt.append(safe_text(item['name']), style='bold')
            prompt.append('\n' + ' · '.join(info), style='dim')
            prompt.append('\n用途：' + safe_text(item.get('purpose') or '未填写'))
            prompt.append('\n路径：' + safe_text(item['path']))
            return Option(prompt, id=item['id'])

        def compose(self) -> ComposeResult:
            with Vertical(id='file-modal'):
                yield Label(self.heading, id='file-heading', markup=False)
                if self.record['files']:
                    yield OptionList(*(self.file_option(item) for item in self.record['files']), id='file-list')
                else:
                    yield Static('此节点没有关联附件。', markup=False)
                yield Static('', id='file-notice', markup=False)
                yield TextArea('', read_only=True, id='file-preview', soft_wrap=True)
                yield Static('Enter 预览 · Ctrl+C 复制路径 · Esc 返回' if self.record['files'] else 'Esc 返回',
                             id='file-help', markup=False)
            yield Footer(compact=True)

        async def on_option_list_option_selected(self, event):
            self.request += 1
            request = self.request
            item = next(item for item in self.record['files'] if item['id'] == event.option.id)
            self.current_path = item['path']
            self.add_class('preview-mode')
            self.refresh_bindings()
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
                self.refresh_bindings()
                self.query_one('#file-heading', Label).update(self.heading)
                self.query_one('#file-help', Static).update('Enter 预览 · Ctrl+C 复制路径 · Esc 返回')
                self.query_one('#file-list').focus()
            else:
                self.dismiss()

        def action_metadata(self):
            self.toggle_class('show-metadata')

        def check_action(self, action, parameters):
            if action == 'metadata':
                return self.has_class('preview-mode')
            if action == 'copy_path':
                return bool(self.record['files'])
            return super().check_action(action, parameters)

        def action_copy_path(self):
            if not self.has_class('preview-mode'):
                listing = self.query_one('#file-list', OptionList)
                if listing.highlighted is not None:
                    self.current_path = self.record['files'][listing.highlighted]['path']
            if self.current_path:
                self.app.push_screen(CopyScreen('复制文件路径', self.current_path))

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
                yield Label(Text('导出 · ' + safe_text(self.record['title']) + ' · Enter 保存 · Esc 取消'))
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
        BINDINGS = [('escape', 'dismiss', '返回'), ('q', 'app.quit', '退出'), ('r', 'reload', '重新检查')]
        CSS = '#connection-info { height: 1fr; } #connection-heading { height: 2; }'

        def compose(self):
            yield Static('接入与状态 · r 重新检查 · Esc 返回', id='connection-heading', markup=False)
            yield TextArea('正在检查…', read_only=True, soft_wrap=True, id='connection-info')
            yield Footer(compact=True)

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

    class FeedScreen(Screen):
        def on_screen_resume(self):
            self.app.finish_feed_entry()

    class HandoffApp(App):
        MODES = {'_default': FeedScreen, 'projects': Home}
        ENABLE_COMMAND_PALETTE = False  # HandoffDesk supplies its own contextual menu.
        TITLE = 'HandoffDesk'
        SUB_TITLE = '本机进展 · Agent 记录，随时续接'
        CSS = '''
        Screen { background: $background; }
        #filters { display: none; }
        #reader-heading { height: 1; }
        #node-attachments { width: auto; min-width: 0; margin-right: 1; }
        #breadcrumb { width: 1fr; height: 1; padding: 0 1; text-style: bold; color: $accent; }
        #search { display: none; }
        .search-mode #search { display: block; }
        #project { width: 50%; }
        #task { width: 1fr; }
        #search { height: 3; }
        #status { height: auto; max-height: 3; color: $text-muted; padding: 0 1; }
        #body { height: 1fr; }
        #records { width: 40%; height: 1fr; border: round $primary; }
        #detail { width: 1fr; height: 1fr; border: round $accent; padding: 0 1; }
        #empty { width: 1fr; height: 1fr; padding: 1; display: none; overflow-y: auto; }
        .empty-mode #empty { display: block; }
        .empty-mode #records, .empty-mode #detail { display: none; }
        .narrow #records { width: 100%; }
        .narrow #detail { width: 100%; }
        .narrow.list-mode #detail { display: none; }
        .narrow.detail-mode #records { display: none; }
        '''
        BINDINGS = [Binding('full_stop', 'help', '更多操作', key_display='.'),
                    Binding('q', 'quit', '退出'), Binding('/', 'search', '搜索'),
                    Binding('p', 'projects', '项目', show=False), Binding('t', 'tasks', '任务', show=False),
                    Binding('f', 'files', '附件'), Binding('r', 'refresh', '刷新', show=False),
                    Binding('u', 'unread', '未读', show=False), Binding('escape', 'back', '返回'),
                    Binding('n', 'more', '下一页', show=False), Binding('e', 'export', '导出'),
                    Binding('c', 'connection', '接入状态', show=False), Binding('m', 'metadata', '元数据', show=False),
                    Binding('ctrl+p', 'command_palette', '查找操作', show=False, priority=True), Binding('y', 'continue_task', '续接', show=False),
                    Binding('j', 'next_record', show=False), Binding('k', 'previous_record', show=False),
                    Binding('?', 'help', '帮助'), Binding('x', 'clear_filters', '清空筛选', show=False)]

        def __init__(self):
            super().__init__()
            self.items, self.current, self.feed_cursor, self.next_cursor = [], None, None, None
            self.list_request, self.detail_request = 0, 0
            self.unread_only, self.polling = False, False
            self.project_signature, self.projects_data = None, []
            self.pending_updates = False
            self.reader_screen = None
            self.monitor = model.change_monitor()
            self.monitor_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix='handoffdesk-watch')
            self.next_poll = 0
            self.poll_delay = 1.5
            self.search_timer = None
            self.search_busy = self.search_queued = False
            self.write_lock = asyncio.Lock()
            self.record_notice = None
            self.metadata_visible = False
            self.search_origin = None
            self.pending_entry = None
            self.feed_visit = 0
            self.feed_ready = False

        def widget(self, selector, kind=None):
            return self.reader_screen.query_one(selector, kind) if kind else self.reader_screen.query_one(selector)

        def compose(self) -> ComposeResult:
            with Horizontal(id='reader-heading'):
                yield Static('全部进展', markup=False, id='breadcrumb')
                yield Button('查看附件 (f)', id='node-attachments', compact=True, disabled=True)
            with Horizontal(id='filters'):
                yield Select([('所有项目', '')], value='', allow_blank=False, id='project')
                yield Select([('所有任务', '')], value='', allow_blank=False, id='task')
            yield Input(placeholder='搜索项目、任务或进展 /', id='search')
            yield Static('正在读取本机记录…', id='status', markup=False)
            with Horizontal(id='body'):
                yield AnchoredOptions(id='records')
                yield TextArea('选择一条进展，按 Enter 阅读。', read_only=True, soft_wrap=True, id='detail')
                yield Static('', id='empty', markup=False)
            yield Footer(compact=True)

        async def on_mount(self):
            self.reader_screen = self.screen
            self.reader_screen.add_class('list-mode')
            self.resize_layout()
            self.set_interval(1.5, self.schedule_poll)
            self.switch_mode('projects')

        async def on_unmount(self):
            await asyncio.get_running_loop().run_in_executor(self.monitor_thread, self.monitor.close)
            self.monitor_thread.shutdown(wait=True)

        async def changes(self, cursor):
            return await asyncio.get_running_loop().run_in_executor(self.monitor_thread, self.monitor.changes, cursor)

        def schedule_poll(self):
            if self.is_running and not self.polling and monotonic() >= self.next_poll:
                self.run_worker(self.poll(), group='watch')

        def open_feed(self, project=None, line=None, record_id=None, *, search=False):
            self.pending_entry = None
            self.feed_ready = False
            self.switch_mode('_default')
            self.feed_visit += 1
            self.list_request += 1
            self.detail_request += 1
            self.current = None
            self.items = []
            self.widget('#node-attachments', Button).disabled = True
            self.widget('#records', OptionList).clear_options()
            self.feed_cursor = self.next_cursor = None
            self.unread_only = self.pending_updates = False
            self.search_origin = None
            self.reader_screen.remove_class('empty-mode', 'detail-mode', 'search-mode')
            self.reader_screen.add_class('list-mode')
            self.status('正在读取进展…')
            self.widget('#records').focus()
            self.run_worker(self.prepare_feed(self.feed_visit, project, line, record_id, search),
                            group='open-feed', exclusive=True)

        async def prepare_feed(self, visit, project, line, record_id, search):
            try:
                # Capture a baseline before loading the page, so a concurrent commit is seen on the next poll.
                initial = await self.changes(None)
                if visit != self.feed_visit:
                    return
                self.feed_cursor = initial['cursor']
                await self.load_projects()
                if visit != self.feed_visit or self.current_mode == 'projects':
                    return
                with self.prevent(Select.Changed, Input.Changed):
                    if project and not any(p['id'] == project for p in self.projects_data):
                        raise ValueError('项目登记已变更')
                    self.widget('#project', Select).value = project or ''
                    self.load_tasks()
                    self.widget('#task', Select).value = f'{project}:{line}' if line else ''
                    self.widget('#search', Input).value = ''
                self.feed_ready = True
                self.filter_changed()
                await self.load_records()
                if visit == self.feed_visit and self.current_mode != 'projects':
                    self.pending_entry = (record_id, search)
                    self.finish_feed_entry()
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.status('读取失败，可返回项目重选：' + safe_text(exc))

        def finish_feed_entry(self):
            if not self.pending_entry or self.screen is not self.reader_screen:
                return
            record_id, search = self.pending_entry
            self.pending_entry = None
            if record_id:
                self.run_worker(self.open_record(record_id), group='open-detail', name=record_id)
            elif search:
                self.action_search()
            else:
                self.widget('#records').focus()

        def check_action(self, action, parameters):
            if self.reader_screen is None:
                return super().check_action(action, parameters)
            reader_actions = {'back', 'files', 'export', 'search', 'projects', 'tasks', 'refresh',
                              'unread', 'clear_filters', 'continue_task', 'more', 'next_record', 'previous_record', 'metadata', 'mark_unread'}
            if action in reader_actions - {'back'} and self.screen is self.reader_screen and not self.feed_ready:
                return False
            if action in reader_actions and self.screen is not self.reader_screen:
                return False
            if action == 'files':
                return self.attachment_target() is not None
            if action in {'export', 'continue_task', 'mark_unread', 'metadata'}:
                return bool(self.current)
            if isinstance(self.screen, ModalScreen):
                if action in {'help', 'connection', 'command_palette'}:
                    return False
                if action == 'quit' and not isinstance(self.screen, (ReadOnlyDialog, FilesScreen, ConnectionScreen)):
                    return False
            if action == 'quit' and isinstance(self.focused, Input):
                return False
            return super().check_action(action, parameters)

        def resize_layout(self):
            if self.reader_screen:
                self.reader_screen.set_class(self.size.width < 100, 'narrow')

        def on_resize(self, event):
            if self.reader_screen:
                self.reader_screen.set_class(event.size.width < 100, 'narrow')

        def status(self, text):
            if not self.is_running or self.reader_screen is None:
                return
            self.widget('#status', Static).update(safe_text(self.record_notice if self.current and self.record_notice else text))

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
            self.widget('#node-attachments', Button).disabled = self.attachment_target() is None
            self.refresh_bindings()
            self.update_heading()

        def scope_name(self):
            project, line = self.scope()
            for item in self.projects_data:
                if item['id'] == project:
                    for task in item['worklines']:
                        if task['id'] == line:
                            return item['name'] + ' / ' + task['name']
                    return item['name'] + ' / 全部进展'
            return '全部项目 / 全部进展'

        def update_heading(self):
            scope = safe_text(self.scope_name())
            self.widget('#breadcrumb', Static).update(('搜索记录 · ' if self.search_origin else '项目 / ') + scope)
            self.widget('#search', Input).placeholder = '搜索记录 · ' + scope

        async def empty_message(self):
            project, line = self.scope()
            if self.unread_only and not self.widget('#search', Input).value.strip():
                return '已经读完。按 u 查看全部记录，或按 Esc 返回。'
            if self.unread_only or self.widget('#search', Input).value.strip():
                return '没有符合条件的记录。\n\n可修改搜索文字，或按 Esc 后再按 x 清空筛选。按 c 查看接入状态。'
            if project or line:
                return ('已登记，还没有进展。请让 Agent 在此项目目录保存第一条进展。'
                        + (f'使用 agent begin 时传入 --workline {line}。' if line else '')
                        + '按 Esc 返回。')
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
                ids = [item['id'] for item in self.items]
                displayed = [option.id for option in listing.options]
                prompts = [render_record_option(item, query, zone) for item in self.items]
                if ids[:len(displayed)] == displayed:
                    for index, (rid, prompt) in enumerate(zip(ids, prompts)):
                        if index == listing.option_count:
                            listing.add_options(Option(p, id=k) for k, p in zip(ids[index:], prompts[index:]))
                            break
                        if listing.get_option_at_index(index).prompt != prompt:
                            listing.replace_option_prompt(rid, prompt)
                else:
                    listing.set_options(Option(prompt, id=rid) for rid, prompt in zip(ids, prompts))
                listing.highlighted = next((i for i, item in enumerate(self.items) if item['id'] == prior_id), 0) if self.items else None
                self.widget('#node-attachments', Button).disabled = self.attachment_target() is None
                self.refresh_bindings()
                if preserve_depth and prior_id and old_scroll:
                    # Background polling is deferred when scrolled; explicit refresh preserves the anchor.
                    def restore_anchor():
                        if self.is_running and request == self.list_request and listing.highlighted is not None:
                            listing.scroll_to(y=listing.row_line(listing.highlighted) - old_offset, animate=False, force=True)
                    self.call_after_refresh(restore_anchor)
                was_empty = self.reader_screen.has_class('empty-mode')
                self.reader_screen.set_class(not self.items and not self.current, 'empty-mode')
                if was_empty and self.items and self.screen is self.reader_screen and not isinstance(self.focused, Input):
                    listing.focus()
                if not self.items:
                    message = await self.empty_message()
                    if request == self.list_request and self.is_running:
                        self.widget('#empty', Static).update(message)
                if request != self.list_request or not self.is_running:
                    return
                self.status(('未读 · ' if self.unread_only else '') + f"{len(self.items)} 条记录" + (' · n 加载更多' if self.next_cursor else '') + ' · ↑↓ 选择 · Enter 阅读')
            except (ValueError, OSError, sqlite3.Error) as exc:
                if request == self.list_request and self.is_running:
                    self.status('读取失败：' + safe_text(exc))

        async def poll(self):
            home = self.current_mode == 'projects'
            origin = self.screen
            if self.polling or (home and not isinstance(origin, Home)) or (not home and not self.feed_ready):
                return
            self.polling = True
            try:
                visit = self.feed_visit
                value = await self.changes(self.feed_cursor)
                if not self.is_running or visit != self.feed_visit or self.screen is not origin:
                    return
                self.poll_delay = 1.5 if value['changed'] else min(12, self.poll_delay * 2)
                if value['changed']:
                    if home:
                        self.feed_cursor = value['cursor']
                        origin.action_refresh()
                        return
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
                self.next_poll = monotonic() + self.poll_delay
                self.polling = False

        def enqueue_search(self):
            self.search_timer = None
            if not self.search_origin or not self.feed_ready:
                return
            self.search_queued = True
            if not self.search_busy:
                self.search_busy = True
                self.run_worker(self.run_search(), group='search')

        async def run_search(self):
            try:
                while self.search_queued:
                    self.search_queued = False
                    await self.load_records()
            finally:
                self.search_busy = False

        def on_input_changed(self, event):
            if event.input.id == 'search' and self.feed_ready and self.screen is self.reader_screen and self.is_running:
                self.filter_changed()
                self.list_request += 1
                self.search_queued = False
                if self.search_timer:
                    self.search_timer.stop()
                self.search_timer = self.set_timer(0.18, self.enqueue_search)

        async def on_select_changed(self, event):
            if event.select.id in {'project', 'task'} and self.feed_ready and self.screen is self.reader_screen and self.is_running:
                if event.select.id == 'project':
                    self.load_tasks()
                self.filter_changed()
                await self.load_records()

        async def on_option_list_option_selected(self, event):
            if event.option_list.id != 'records':
                return
            self.current = None
            self.refresh_bindings()
            self.status('正在打开记录…')
            self.run_worker(self.open_record(event.option.id), name=event.option.id, group='open-detail')

        async def open_record(self, record_id):
            self.detail_request += 1
            request = self.detail_request
            try:
                value = await asyncio.to_thread(model.detail, record_id)
                self.metadata_visible = False
                text = render_detail(value, zone)
                if request != self.detail_request or not self.is_running:
                    return
                self.current = value
                self.widget('#node-attachments', Button).disabled = False
                self.record_notice = None
                self.widget('#detail', TextArea).load_text(text)
                self.reader_screen.remove_class('list-mode')
                self.reader_screen.add_class('detail-mode')
                self.widget('#detail').focus()
                self.status('Esc 返回列表 · m 元数据 · y 续接')
                self.refresh_bindings()
                async with self.write_lock:
                    await self.remember_record(record_id, value)

            except (ValueError, OSError, sqlite3.Error) as exc:
                if request == self.detail_request and self.is_running:
                    self.status('无法读取详情：' + safe_text(exc))

        async def remember_record(self, record_id, value):
            await self.ack_record(record_id)
            try:
                await asyncio.to_thread(save_reading_position, model, value)
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.status('阅读位置未保存：' + safe_text(exc))

        async def ack_record(self, record_id):
            try:
                await asyncio.to_thread(model.ack, [record_id])
            except (ValueError, OSError, sqlite3.Error) as exc:
                if self.current and self.current['id'] == record_id:
                    self.record_notice = '正文已打开，已读状态待同步 · r 重试：' + safe_text(exc)
                    self.status(self.record_notice)
            else:
                if self.current and self.current['id'] == record_id:
                    self.record_notice = None

        def action_search(self):
            if self.search_origin is None:
                listing = self.widget('#records', OptionList)
                index = listing.highlighted
                record = self.items[index]['id'] if index is not None and index < len(self.items) else None
                self.search_origin = (*self.scope(), self.unread_only, record, listing.scroll_y, len(self.items))
            self.reader_screen.add_class('search-mode')
            self.filter_changed()
            self.widget('#search').focus()

        def apply_scope(self, project, line):
            with self.prevent(Select.Changed):
                self.widget('#project', Select).value = project or ''
                self.load_tasks()
                self.widget('#task', Select).value = f'{project}:{line}' if line else ''

        async def choose_scope(self, value):
            if value is None:
                return
            project, _, line = value.partition(':')
            self.apply_scope(None if project == '*' else project, line or None)
            self.filter_changed()
            await self.load_records()

        def action_projects(self):
            choices = [('*', '全部项目', '', '跨项目 全部')]
            choices += [(p['id'], p['name'], '', p['id']) for p in self.projects_data]
            self.push_screen(ActionMenu('切换项目', choices, placeholder='查找项目…'), self.choose_scope)

        def action_tasks(self):
            project, _ = self.scope()
            choices = [(project or '*', '全部任务', '', '所有')]
            for p in self.projects_data:
                if not project or project == p['id']:
                    for line in p['worklines']:
                        choices.append((p['id']+':'+line['id'], p['name']+' / '+line['name'], '', line['id']))
            self.push_screen(ActionMenu('切换任务', choices, placeholder='查找任务…'), self.choose_scope)

        def on_option_list_option_highlighted(self, event):
            listing = event.option_list
            index = event.option_index
            if (listing.id != 'records' or self.screen is not self.reader_screen
                    or index != listing.highlighted or index >= len(self.items)
                    or self.items[index]['id'] != event.option.id):
                return
            if self.current:
                if not listing.has_focus or self.current['id'] == event.option.id:
                    return
                self.filter_changed()
            elif listing.has_focus:
                # Invalidate a late read without cancelling a displayed record's acknowledgement.
                opening = next((w for w in reversed(self.workers) if w.group == 'open-detail'), None)
                if opening is not None and opening.name != event.option.id:
                    self.detail_request += 1
            self.widget('#detail', TextArea).load_text(render_preview(self.items[index], zone))

        async def action_refresh(self):
            self.next_poll = 0
            self.poll_delay = 1.5
            self.status('正在刷新记录…')
            if self.current and self.record_notice:
                async with self.write_lock:
                    if self.current:
                        await self.ack_record(self.current['id'])
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

        async def action_back(self):
            if self.current:
                self.filter_changed()
                self.widget('#records').focus()
                self.refresh_bindings()
            elif self.search_origin:
                project, line, unread, anchor, scroll, depth = self.search_origin
                self.search_origin = None
                self.search_queued = False
                if self.search_timer:
                    self.search_timer.stop()
                    self.search_timer = None
                self.reader_screen.remove_class('search-mode')
                self.apply_scope(project, line)
                self.unread_only = unread
                with self.prevent(Input.Changed):
                    self.widget('#search', Input).value = ''
                self.filter_changed()
                await self.load_records()
                while len(self.items) < depth and self.next_cursor:
                    await self.load_records(more=True)
                listing = self.widget('#records', OptionList)
                listing.highlighted = next((i for i,v in enumerate(self.items) if v['id'] == anchor), 0) if self.items else None
                listing.scroll_to(y=scroll, animate=False)
                listing.focus()
            else:
                self.feed_ready = False
                self.feed_visit += 1
                self.list_request += 1
                self.detail_request += 1
                self.switch_mode('projects')

        def attachment_target(self):
            if self.current:
                return self.current
            listing = self.widget('#records', OptionList)
            index = listing.highlighted
            return self.items[index] if index is not None and index < len(self.items) else None

        async def on_button_pressed(self, event):
            if event.button.id == 'node-attachments' and self.screen is self.reader_screen:
                self.widget('#detail' if self.current else '#records').focus()
                await self.action_files()

        async def action_files(self):
            target = self.attachment_target()
            if target is None:
                return
            record_id = target['id']
            visit = self.feed_visit
            try:
                record = await asyncio.to_thread(model.detail, record_id)
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.status('读取附件失败：' + str(exc))
                return
            if self.is_running and self.screen is self.reader_screen and visit == self.feed_visit:
                selected = self.attachment_target()
                if selected and selected['id'] == record_id:
                    self.push_screen(FilesScreen(record))

        def action_export(self):
            if self.current:
                self.push_screen(ExportScreen(self.current))
            else:
                self.status('先打开一条记录，再按 e 导出。')

        def action_connection(self):
            self.push_screen(ConnectionScreen())

        def action_continue_task(self):
            if self.current:
                self.push_screen(CopyScreen('复制续接说明', continuation_text(self.current)))

        def action_metadata(self):
            if self.current:
                self.metadata_visible = not self.metadata_visible
                area = self.widget('#detail', TextArea)
                area.load_text(render_detail(self.current, zone, metadata=self.metadata_visible))
                if self.metadata_visible:
                    area.scroll_end(animate=False)
                else:
                    area.scroll_home(animate=False)

        async def action_mark_unread(self):
            if not self.current:
                return
            try:
                record_id = self.current['id']
                async with self.write_lock:
                    await asyncio.to_thread(model.mark_unread, [record_id])
            except (OSError, ValueError, sqlite3.Error) as exc:
                self.status('标为未读失败：' + safe_text(exc))
                return
            self.filter_changed()
            self.widget('#records').focus()
            await self.load_records(preserve_depth=True)
            self.notify('已标为未读')

        def action_advanced(self):
            project, line = self.scope() if self.screen is self.reader_screen else (getattr(self.screen, 'project_id', None), None)
            if project:
                self.push_screen(AdvancedScreen(project, line, app_dir=app_dir, data_dir=data_dir))

        def action_help_quit(self):
            if self.check_action('quit', ()):
                self.notify('按 q 退出阅读器。')

        def menu_choices(self):
            if self.screen is self.reader_screen:
                actions = [('search','搜索记录','/','查找'),('projects','切换项目或扩大搜索范围','p','全部项目'),
                           ('tasks','切换任务','t','范围'),('unread','切换未读筛选','u','全部 已读'),
                           ('clear_filters','清空所有筛选','x','范围'),('refresh','刷新记录','r','更新')]
                if self.next_cursor:
                    actions.append(('more','加载更多记录','n','翻页'))
                if self.current:
                    actions += [('continue_task','复制续接说明','y','复制 下一步'),('export','导出当前记录','e','保存'),
                                ('metadata','展开或收起元数据','m','编号 作者 时间'),('mark_unread','标为未读','','稍后阅读')]
                if self.attachment_target():
                    actions.insert(0, ('files','查看节点附件','f','附件 文件 路径'))
                project = self.scope()[0]
            else:
                actions = list(self.screen.actions_for_menu()) if hasattr(self.screen,'actions_for_menu') else []
                project = getattr(self.screen, 'project_id', None)
            if project:
                actions.append(('advanced','项目高级操作','','来源 历史 计划 材料 CLI'))
            actions.extend([('connection','接入与诊断','c','连接 状态'),('help','操作帮助','?','键盘')])
            if terminal_session.is_managed_reader():
                actions.append(('detach','离开并保持运行','','分离 暂离 保留位置 tmux detach'))
            actions.append(('quit','退出阅读器','q','关闭 结束'))
            return actions

        async def action_detach(self):
            try:
                await asyncio.to_thread(terminal_session.detach_reader)
            except ValueError as exc:
                self.notify(safe_text(exc), severity='warning')

        def action_command_palette(self):
            self.open_actions(search=True)

        def action_help(self):
            self.open_actions(search=False)

        def open_actions(self, *, search):
            if isinstance(self.screen, ModalScreen):
                return
            origin = self.screen
            choices = self.menu_choices()
            async def selected(action):
                if action and self.screen is origin:
                    if action == 'quit':
                        # Dismissing the menu can restore focus to the search input.
                        # Its typing guard applies to q, not to this selected command.
                        await self.action_quit()
                        return
                    target = origin if hasattr(origin, 'actions_for_menu') and any(
                        row[0] == action for row in origin.actions_for_menu()) else self
                    await self.run_action(action, default_namespace=target)
            if search:
                screen = ActionMenu('查找操作', choices)
            else:
                context = self.scope_name() if origin is self.reader_screen else '项目导航'
                screen = HelpScreen(context, choices)
            self.push_screen(screen, selected)

        def action_next_record(self):
            listing = self.widget('#records', OptionList)
            if self.current:
                self.filter_changed()
            listing.focus()
            listing.action_cursor_down()

        def action_previous_record(self):
            listing = self.widget('#records', OptionList)
            if self.current:
                self.filter_changed()
            listing.focus()
            listing.action_cursor_up()

    return HandoffApp()


def run(app_dir=None, data_dir=None):
    ensure_terminal()
    create_app(app_dir, data_dir).run()
    return 0
