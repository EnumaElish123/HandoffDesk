"""Project and task navigation for the optional Linux terminal reader."""
from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path
import sqlite3
import uuid

from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from .bootstrap import add_project, initialize
from .paths import app_directory, configuration_path
from .reader import format_recorded_at, safe_text
from .registry import Registry, match_project_root
from .workspace import Workspace, WorkspaceError


def _directory(model):
    target = configuration_path(app_directory(model.app_dir))
    projects = Registry(target).projects if target.exists() else []
    return {project['id']: project for project in projects}


def _roots(project):
    return list(project['roots'].values())


def _same_directory(projects, path):
    match = match_project_root(projects.values(), path)
    return match[0] if match and match[1] == path else None


def _register(model, folder, name):
    path = Path(folder).expanduser()
    if not folder.strip() or not path.is_absolute() or not path.is_dir():
        raise ValueError('请输入已存在的本机绝对目录。')
    path = path.resolve()
    existing = _same_directory(_directory(model), path)
    if existing:
        return existing, False
    project_id = 'project-' + uuid.uuid4().hex[:16]
    options = dict(root=path, name=name.strip() or path.name or '项目', data_dir=model.data_dir)
    config = configuration_path(app_directory(model.app_dir))
    if not config.exists():
        try:
            return initialize(model.app_dir, project_id=project_id, **options)['project'], True
        except ValueError:
            if not config.exists():
                raise
            # A first-run writer may have initialized the registry concurrently.
    try:
        result = add_project(model.app_dir, project_id, **options)
    except ValueError:
        # Another client may have registered this exact directory under another ID.
        existing = _same_directory(_directory(model), path)
        if existing:
            return existing, False
        raise
    return result['project'], True


def _latest_titles(model, project_id):
    service = model._service()
    prefix = project_id + '\x1f'
    with service.store.connection() as db:
        rows = db.execute('''WITH newest AS (
            SELECT project_id, MAX(revision) AS revision FROM handoffs
            WHERE project_id=? OR substr(project_id,1,length(?))=?
            GROUP BY project_id
        ) SELECT h.project_id, json_extract(h.payload, '$.current_task') FROM handoffs h
          JOIN newest n ON h.project_id=n.project_id AND h.revision=n.revision''',
                          (project_id, prefix, prefix))
        return {service.store.scope_identity(scope).get('workline_id', 'default'): title
                for scope, title in rows if isinstance(title, str) and title}


def save_reading_position(model, record):
    """Save the record just opened, preserving preferences owned by other UIs."""
    position = {key: record[key] for key in ('id', 'project_id', 'workline_id', 'title')}
    model._service().store.save_preferences({'tui_reading_position': position})


class EntryForm(ModalScreen):
    BINDINGS = [('escape', 'cancel', '取消')]
    CSS = """
    EntryForm { align: center middle; }
    #entry-box { width: 74; max-width: 96%; height: auto; max-height: 100%; padding: 1 2;
                 background: $surface; border: round $accent; }
    #entry-title { text-style: bold; color: $accent; margin-bottom: 1; }
    #entry-status { height: auto; color: $warning; }
    #entry-buttons { height: auto; margin-top: 1; }
    #entry-buttons Button { margin-right: 1; }
    """

    def __init__(self, model, project=None):
        super().__init__()
        self.model = model
        self.project = project
        self.saving = False

    def actions_for_menu(self):
        return []

    def compose(self):
        with VerticalScroll(id='entry-box'):
            if self.project is None:
                yield Label('添加项目', id='entry-title')
                yield Label('已有项目目录')
                yield Input(placeholder='粘贴绝对路径，或选择当前目录', id='project-root')
                yield Button('使用当前目录', id='entry-cwd', compact=True)
                yield Label('显示名称')
                yield Input(placeholder='默认采用文件夹名称', id='project-name')
            else:
                yield Label(Text('新建任务 / ' + safe_text(self.project['name'])), id='entry-title')
                yield Label('任务名称')
                yield Input(id='task-name', placeholder='例如：实验复核')
            yield Static('', markup=False, id='entry-status')
            with Horizontal(id='entry-buttons'):
                yield Button('登记目录' if self.project is None else '创建任务',
                             id='entry-submit', variant='primary', compact=True)
                yield Button('取消', id='entry-cancel', compact=True)

    def on_mount(self):
        self.query_one(Input).focus()

    def action_cancel(self):
        if not self.saving:
            self.dismiss(None)

    def on_input_submitted(self, event):
        if event.input.id == 'project-root':
            self.query_one('#project-name', Input).focus()
            self.query_one('#entry-status', Static).update('核对显示名称，Enter 登记目录。')
        else:
            self.submit()

    def on_input_changed(self, event):
        if event.input.id == 'project-root':
            folder = event.value.strip()
            name = Path(folder).name
            self.query_one('#project-name', Input).placeholder = name or '默认采用文件夹名称'

    def on_button_pressed(self, event):
        if event.button.id == 'entry-cwd':
            self.query_one('#project-root', Input).value = str(Path.cwd())
            self.query_one('#project-name', Input).focus()
        elif event.button.id == 'entry-cancel':
            self.action_cancel()
        elif event.button.id == 'entry-submit':
            self.submit()

    def submit(self):
        if self.saving:
            return
        if self.project is None:
            folder = self.query_one('#project-root', Input).value
            name = self.query_one('#project-name', Input).value
            operation = asyncio.to_thread(_register, self.model, folder, name)
        else:
            name = self.query_one('#task-name', Input).value

            def create_task():
                return self.model._service().create_workline(self.project['id'], name)

            operation = asyncio.to_thread(create_task)
        self.saving = True
        for control in self.query('Button, Input'):
            control.disabled = True
        self.query_one('#entry-status', Static).update('正在保存…')
        self.run_worker(self.finish_submit(operation))

    async def finish_submit(self, operation):
        try:
            result = await operation
        except (ValueError, OSError, sqlite3.Error) as exc:
            if self.is_mounted:
                self.saving = False
                for control in self.query('Button, Input'):
                    control.disabled = False
                self.query_one('#entry-status', Static).update(safe_text(exc))
                self.query_one(Input).focus()
        else:
            if self.is_mounted:
                self.dismiss(result)


def make_project_screens(model):
    class ProjectHome(Screen):
        BINDINGS = [
            Binding('escape', 'back', '返回', show=False),
            Binding('plus', 'add', '添加', show=False),
            Binding('a', 'add', '添加', show=False),
            Binding('r', 'refresh', '刷新', show=False),
            Binding('slash', 'search', '搜索'),
            Binding('question_mark', 'app.help', '帮助'),
            Binding('j', 'step(1)', '下一项', show=False),
            Binding('k', 'step(-1)', '上一项', show=False),
        ]
        CSS = """
        ProjectHome { background: $background; }
        #nav-title { height: 2; padding: 0 1; color: $accent; text-style: bold; }
        #nav-section { height: 1; padding: 0 1; text-style: bold; }
        #nav-list { height: 1fr; margin: 0 1; border: none; padding: 0; }
        #nav-state { height: auto; max-height: 3; padding: 0 1; color: $text-muted; }
        #nav-hint { height: 1; padding: 0 1; }
        """

        def __init__(self, project_id=None, *, prefer_add=False):
            super().__init__()
            self.project_id = project_id
            self.directory = {}
            self.current_project_id = None
            self.statistics = {}
            self.latest_titles = {}
            self.position = None
            self.restore_id = 'add' if prefer_add else None
            self.suspended = False

        def compose(self):
            yield Static('HandoffDesk / 项目', markup=False, id='nav-title')
            yield Static('选择项目', id='nav-section')
            yield OptionList(id='nav-list')
            yield Static('正在读取项目…', id='nav-state', markup=False)
            yield Static('↑↓ 选择    Enter 打开', id='nav-hint', markup=False)
            yield Footer(compact=True)

        def on_mount(self):
            self.query_one('#nav-list').focus()
            self.action_refresh()

        def on_screen_suspend(self):
            self.suspended = True

        def on_screen_resume(self):
            if self.suspended and self.is_mounted:
                self.suspended = False
                self.action_refresh()

        def actions_for_menu(self):
            actions = [('add', '新建任务' if self.project_id else '添加项目', '+', '新建 创建 目录'),
                       ('all', '项目全部进展' if self.project_id else '全部进展', '', '查看 记录 列表'),
                       ('search', '搜索记录', '/', '查找 搜索'),
                       ('refresh', '刷新项目和任务', 'r', '刷新 更新')]
            if self.project_id:
                actions.append(('back', '返回项目首页', 'Esc', '返回'))
            return actions

        def action_step(self, amount):
            listing = self.query_one('#nav-list', OptionList)
            if amount > 0:
                listing.action_cursor_down()
            else:
                listing.action_cursor_up()

        def action_refresh(self):
            self.run_worker(self.read_directory(), exclusive=True, group='navigation')

        async def read_directory(self):
            try:
                self.directory = await asyncio.to_thread(_directory, model)
                if self.project_id and self.project_id not in self.directory:
                    self.notify('项目登记已移除，请重新选择。')
                    if len(self.app.screen_stack) > 1:
                        self.action_back()
                        return
                    self.project_id = None
                self.current_project_id = None
                if not self.project_id and self.directory:
                    workspace = Workspace(model.app_dir, model.data_dir)
                    try:
                        location = await asyncio.to_thread(workspace.inspect, Path.cwd(),
                                                           projects=self.directory.values(), include_checkout=False)
                    except WorkspaceError as exc:
                        self.notify(safe_text(exc), severity='warning')
                    else:
                        if location['connected']:
                            self.current_project_id = location['project']['id']
                self.render_choices()
                if self.directory:
                    projects = await asyncio.to_thread(model.projects)
                    self.statistics = {p['id']: p for p in projects}
                    if self.project_id:
                        self.latest_titles = await asyncio.to_thread(_latest_titles, model, self.project_id)
                    else:
                        prefs = await asyncio.to_thread(lambda: model._service().store.preferences())
                        position = prefs.get('tui_reading_position')
                        keys = ('id', 'project_id', 'workline_id', 'title')
                        self.position = position if isinstance(position, dict) and all(
                            isinstance(position.get(k), str) for k in keys) else None
                    self.render_choices()
                self.describe_state()
            except (ValueError, OSError, sqlite3.Error) as exc:
                if self.is_mounted:
                    self.query_one('#nav-state', Static).update('读取失败 · r 重试 · ' + safe_text(exc))

        def describe_state(self):
            message = ''
            if not self.directory:
                message = '尚无项目。选择“添加项目”，登记一个已有的工作目录。'
            elif self.project_id in self.statistics:
                lines = self.statistics[self.project_id]['worklines']
                if not any(line['record_count'] for line in lines):
                    message = '已登记，还没有进展。新建任务后可让 Agent 写入首条记录。'
            self.query_one('#nav-state', Static).update(message)

        def render_choices(self):
            listing = self.query_one('#nav-list', OptionList)
            selected = None
            if listing.highlighted is not None and listing.highlighted < listing.option_count:
                selected = listing.get_option_at_index(listing.highlighted).id
            options = []

            def add(key, label):
                options.append(Option(Text(safe_text(label)), id=key))

            if self.project_id:
                project = self.directory[self.project_id]
                self.query_one('#nav-title', Static).update('项目 / ' + safe_text(project['name']))
                self.query_one('#nav-section', Static).update('任务')
                add('all', '项目全部进展')
                add('add', '＋ 新建任务')
                for line in self.statistics.get(self.project_id, {}).get('worklines', []):
                    label = f"{line['name']}    {line['record_count']} 条 · {line['unread']} 未读"
                    title = self.latest_titles.get(line['id'])
                    if title:
                        stamp = format_recorded_at(line['latest_recorded_at']).split(' (')[0]
                        label += f"\n  {title} · {stamp}"
                    add('task:' + line['id'], label)
            else:
                names = Counter(p['name'] for p in self.directory.values())
                def latest(pid):
                    lines = self.statistics.get(pid, {}).get('worklines', [])
                    return max((line.get('latest_recorded_at') or '' for line in lines), default='')
                for pid in sorted(self.directory, key=latest, reverse=True):
                    project = self.directory[pid]
                    roots = _roots(project)
                    label = project['name']
                    if names[label] > 1:
                        label += ' · ' + roots[0]
                    if pid == self.current_project_id:
                        label += ' · 当前目录'
                    if pid in self.statistics:
                        stamp = latest(pid)
                        if stamp:
                            label += '    ' + format_recorded_at(stamp).split(' (')[0]
                        label += f"    {self.statistics[pid]['unread']} 未读"
                    add('project:' + pid, label)
                if isinstance(self.position, dict) and self.position.get('project_id') in self.directory:
                    add('resume', '继续上次阅读 · ' + str(self.position.get('title', '记录')))
                add('add', '＋ 添加项目')
                add('all', '全部进展')
            listing.clear_options().add_options(options)
            keys = [o.id for o in options]
            key = self.restore_id or selected
            if key is None and self.project_id is None and self.current_project_id is not None:
                key = 'project:' + self.current_project_id
            listing.highlighted = keys.index(key) if key in keys else 0
            if self.restore_id in keys:
                self.restore_id = None
            self.refresh_hint()

        def on_option_list_option_highlighted(self, event):
            self.refresh_hint()

        def refresh_hint(self):
            listing = self.query_one('#nav-list', OptionList)
            if listing.highlighted is None or listing.highlighted >= listing.option_count:
                return
            key = listing.get_option_at_index(listing.highlighted).id
            action = {'add': '新建任务' if self.project_id else '添加项目',
                      'all': '查看进展', 'resume': '继续阅读'}.get(key, '进入任务' if self.project_id else '进入项目')
            hint = '↑↓ 选择    Enter ' + action
            if self.project_id:
                hint += '    Esc 返回项目'
            self.query_one('#nav-hint', Static).update(hint)

        def on_option_list_option_selected(self, event):
            key = event.option.id
            if key == 'all':
                self.action_all()
            elif key == 'add':
                self.action_add()
            elif key == 'resume' and self.position:
                self.app.open_feed(self.position['project_id'], self.position['workline_id'], self.position['id'])
            elif key.startswith('project:'):
                self.app.push_screen(ProjectHome(key.split(':', 1)[1]))
            elif key.startswith('task:'):
                self.app.open_feed(self.project_id, key.split(':', 1)[1])

        def action_all(self):
            self.app.open_feed(self.project_id, None)

        def action_search(self):
            self.app.open_feed(self.project_id, None, search=True)

        def action_add(self):
            project = self.directory.get(self.project_id)
            if self.project_id and project is None:
                return
            self.app.push_screen(EntryForm(model, project), self.saved)

        def saved(self, result):
            if result is None:
                return
            if self.project_id:
                self.restore_id = 'task:' + result['id']
                self.app.open_feed(self.project_id, result['id'])
            else:
                project, created = result
                self.restore_id = 'project:' + project['id']
                self.notify('项目已登记' if created else '此目录已登记，正在打开已有项目。')
                self.app.push_screen(ProjectHome(project['id'], prefer_add=created))

        def action_back(self):
            if self.project_id and len(self.app.screen_stack) > 1:
                self.app.pop_screen()

    return ProjectHome
