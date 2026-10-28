"""Optional terminal reader. Importing the CLI never imports Textual."""
from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys


def ensure_terminal():
    if not sys.stdin.isatty() or not sys.stdout.isatty() or os.environ.get('TERM') in {None, '', 'dumb'}:
        raise ValueError('终端阅读需要交互式终端（TTY）。可改用 handoffdesk feed 或 handoffdesk list --json。')
    if importlib.util.find_spec('textual') is None:
        raise ValueError('终端阅读组件尚未安装。请安装 HandoffDesk 的 tui 组件：pip install "handoffdesk[tui]"。')


def create_app(app_dir=None, data_dir=None):
    import asyncio
    from rich.text import Text
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.screen import ModalScreen
    from textual.widgets import Footer, Header, Input, Label, OptionList, Select, Static, TextArea
    from textual.widgets.option_list import Option
    from .reader import ReaderModel, safe_text

    model = ReaderModel(app_dir, data_dir)

    class FilesScreen(ModalScreen):
        BINDINGS = [('escape', 'dismiss', '返回'), ('ctrl+c', 'copy_path', '复制路径')]
        CSS = '''
        FilesScreen { align: center middle; }
        #file-modal { width: 95%; height: 95%; background: $surface; border: round $accent; padding: 1; }
        #file-list { height: 8; }
        #file-notice { height: auto; max-height: 4; }
        #file-preview { height: 1fr; }
        '''

        def __init__(self, record):
            super().__init__()
            self.record, self.current_path, self.request = record, '', 0

        def compose(self) -> ComposeResult:
            with Vertical(id='file-modal'):
                yield Label('本次文件 · Enter 预览 · Esc 返回')
                yield OptionList(*(Option(Text(safe_text(f"{item['name']} · {item.get('purpose', '')}")), id=item['id'])
                                   for item in self.record['files']), id='file-list')
                yield Static('位置属于运行 HandoffDesk 的机器。复制功能取决于终端支持；完整路径也会显示在下方。', id='file-notice', markup=False)
                yield TextArea('', read_only=True, id='file-preview', soft_wrap=True)

        async def on_option_list_option_selected(self, event):
            self.request += 1
            request = self.request
            item = next(item for item in self.record['files'] if item['id'] == event.option.id)
            self.current_path = item['path']
            area = self.query_one('#file-preview', TextArea)
            area.load_text(safe_text(item['path']) + '\n\n读取中…')
            try:
                result = await asyncio.to_thread(model.preview, self.record['id'], item['id'])
                text = safe_text(item['path']) + '\n用途：' + safe_text(item.get('purpose', '')) + '\n\n' + result['text']
                if result['truncated']:
                    text += '\n\n[已截断：最多预览 1 MiB / 2,000 行]'
                if result.get('encoding_warning'):
                    text += '\n[部分字节不是 UTF-8，已用替代字符显示]'
            except (ValueError, OSError, sqlite3.Error) as exc:
                text = safe_text(item['path']) + '\n\n无法预览：' + safe_text(exc)
            if request == self.request:
                area.load_text(text)

        def action_copy_path(self):
            if self.current_path:
                self.app.copy_to_clipboard(self.current_path)
                self.notify('已向终端请求复制；是否到达本机剪贴板取决于终端支持。')

    class HandoffApp(App):
        TITLE = 'HandoffDesk'
        SUB_TITLE = '本机进展 · Agent 记录，随时续接'
        CSS = '''
        Screen { background: $background; }
        #filters { height: auto; max-height: 6; }
        #project { width: 34%; }
        #search { width: 1fr; }
        #status { height: 2; color: $text-muted; padding: 0 1; }
        #body { height: 1fr; }
        #records { width: 36%; height: 1fr; border: round $primary; }
        #detail { width: 1fr; height: 1fr; border: round $accent; padding: 0 1; }
        .narrow #records { width: 100%; }
        .narrow #detail { width: 100%; }
        .narrow.list-mode #detail { display: none; }
        .narrow.detail-mode #records { display: none; }
        '''
        BINDINGS = [Binding('q', 'quit', '退出'), Binding('/', 'search', '搜索'),
                    Binding('p', 'projects', '项目'), Binding('f', 'files', '文件'),
                    Binding('r', 'refresh', '刷新'), Binding('u', 'unread', '未读'),
                    Binding('escape', 'back', '列表'), Binding('n', 'more', '下一页'),
                    Binding('j', 'next_record', show=False), Binding('k', 'previous_record', show=False),
                    Binding('?', 'help', '帮助')]

        def __init__(self):
            super().__init__()
            self.items, self.current, self.feed_cursor, self.next_cursor = [], None, None, None
            self.list_request, self.detail_request = 0, 0
            self.unread_only, self.polling = False, False
            self.project_signature = None

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal(id='filters'):
                yield Select([('所有项目', '')], value='', allow_blank=False, id='project')
                yield Input(placeholder='搜索标题或正文 /', id='search')
            yield Static('正在读取本机记录…', id='status', markup=False)
            with Horizontal(id='body'):
                yield OptionList(id='records')
                yield TextArea('从左侧选择一条阶段记录，按 Enter 阅读。', read_only=True, soft_wrap=True, id='detail')
            yield Footer()

        async def on_mount(self):
            self.screen.add_class('list-mode')
            self.resize_layout()
            try:
                await self.load_projects()
                await self.load_records()
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.status('读取失败，可按 r 重试：' + str(exc))
            self.set_interval(1.5, self.poll)
            self.query_one('#records').focus()

        def resize_layout(self):
            self.screen.set_class(self.size.width < 100, 'narrow')

        def on_resize(self, event):
            self.screen.set_class(event.size.width < 100, 'narrow')

        def status(self, text):
            self.query_one('#status', Static).update(safe_text(text))

        async def load_projects(self):
            projects = await asyncio.to_thread(model.projects)
            signature = [(p['id'], p['name'], p['unread']) for p in projects]
            if signature != self.project_signature:
                selector = self.query_one('#project', Select)
                selected = selector.value
                selector.set_options([('所有项目', '')] + [(Text(safe_text(f"{p['name']} · {p['unread']} 未读")), p['id']) for p in projects])
                selector.value = selected if selected in [p['id'] for p in projects] else ''
                self.project_signature = signature

        async def load_records(self, more=False, preserve_depth=False):
            self.list_request += 1
            request = self.list_request
            selector = self.query_one('#project', Select)
            project = selector.value if isinstance(selector.value, str) else ''
            query = self.query_one('#search', Input).value
            try:
                result = await asyncio.to_thread(model.records, project or None, None, 50,
                                                self.next_cursor if more else None, self.unread_only, query)
                target = len(self.items) if preserve_depth else 50
                while not more and len(result['items']) < target and result['next_cursor']:
                    page = await asyncio.to_thread(model.records, project or None, None,
                                                   min(100, target - len(result['items'])),
                                                   result['next_cursor'], self.unread_only, query)
                    result['items'].extend(page['items'])
                    result['next_cursor'] = page['next_cursor']
                if request != self.list_request:
                    return
                listing = self.query_one('#records', OptionList)
                highlighted = listing.highlighted
                prior_id = self.items[highlighted]['id'] if highlighted is not None and highlighted < len(self.items) else None
                self.items = self.items + result['items'] if more else result['items']
                self.next_cursor = result['next_cursor']
                listing.clear_options()
                listing.add_options([Option(Text(safe_text(f"{'○' if v['read'] else '●'} {v['title']}\n{v['project_name']} · {v['recorded_at'][:16]}")), id=v['id']) for v in self.items])
                listing.highlighted = next((i for i, item in enumerate(self.items) if item['id'] == prior_id), 0) if self.items else None
                self.status(('未读 · ' if self.unread_only else '') + f"{len(self.items)} 条记录" + (' · n 加载更多' if self.next_cursor else '') + ' · 新记录自动出现，当前详情保留')
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.status('读取失败：' + str(exc))

        async def poll(self):
            if self.polling:
                return
            self.polling = True
            try:
                value = await asyncio.to_thread(model.changes, self.feed_cursor)
                if value['changed']:
                    self.feed_cursor = value['cursor']
                    await self.load_projects()
                    await self.load_records(preserve_depth=True)
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.status('更新失败，可按 r 重试：' + str(exc))
            finally:
                self.polling = False

        async def on_input_changed(self, event):
            if event.input.id == 'search':
                await self.load_records()

        async def on_select_changed(self, event):
            if event.select.id == 'project':
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
                if request != self.detail_request:
                    return
                self.current = value
                self.query_one('#detail', TextArea).load_text(text)
                self.screen.remove_class('list-mode')
                self.screen.add_class('detail-mode')
                self.query_one('#detail').focus()
                await asyncio.to_thread(model.ack, [record_id])
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.status('无法读取详情：' + str(exc))

        def action_search(self):
            self.query_one('#search').focus()

        def action_projects(self):
            self.query_one('#project').focus()

        async def action_refresh(self):
            try:
                await self.load_projects()
                await self.load_records(preserve_depth=True)
            except (ValueError, OSError, sqlite3.Error) as exc:
                self.status('读取失败：' + str(exc))

        async def action_more(self):
            if self.next_cursor:
                await self.load_records(more=True)

        async def action_unread(self):
            self.unread_only = not self.unread_only
            await self.load_records()

        def action_back(self):
            self.screen.remove_class('detail-mode')
            self.screen.add_class('list-mode')
            self.query_one('#records').focus()

        def action_files(self):
            if self.current and self.current['files']:
                self.push_screen(FilesScreen(self.current))
            else:
                self.status('先打开一条含附件的记录，再按 f。')

        def action_next_record(self):
            self.query_one('#records', OptionList).action_cursor_down()

        def action_previous_record(self):
            self.query_one('#records', OptionList).action_cursor_up()

        def action_help(self):
            self.notify('↑↓ 或 j/k 选记录，Enter 阅读，Esc 返回，/ 搜索，p 项目，u 未读，f 附件，n 下一页，q 退出。输入框内字母用于输入。', timeout=10)

    return HandoffApp()


def run(app_dir=None, data_dir=None):
    ensure_terminal()
    create_app(app_dir, data_dir).run()
    return 0
