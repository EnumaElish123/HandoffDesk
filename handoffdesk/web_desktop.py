"""Local Web reading surface. Importing it needs neither Tk nor a Web engine.

The native window exposes a deliberately small reader API. Browser previews use
the same API on loopback with a per-process secret and exact origin/Host checks.
Neither transport accepts a filesystem path from JavaScript.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import sqlite3
import sys
import threading
import webbrowser


ASSETS = Path(__file__).with_name('web')
MAX_REQUEST_BYTES = 64 * 1024
METHODS = frozenset({'projects', 'records', 'detail', 'ack', 'mark_unread', 'changes', 'preview',
                     'open_file', 'freeze_file', 'export', 'export_filename', 'connection_status'})
STATIC = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/index.html': ('index.html', 'text/html; charset=utf-8'),
          '/app.css': ('app.css', 'text/css; charset=utf-8'),
          '/app.js': ('app.js', 'text/javascript; charset=utf-8')}


class ReaderAPI:
    """Only public methods below are exposed to the trusted local document."""

    def __init__(self, app_dir, data_dir=None, *, reader_factory=None):
        self._app_dir, self._data_dir = app_dir, data_dir
        self._reader_factory = reader_factory
        self._window = None

    def _reader(self):
        if self._reader_factory:
            return self._reader_factory()
        from .reader import ReaderModel
        return ReaderModel(self._app_dir, self._data_dir)

    def _call(self, method, *args, **kwargs):
        try:
            value = getattr(self._reader(), method)(*args, **kwargs)
            return {'ok': True, 'data': value}
        except (ValueError, OSError, KeyError, sqlite3.Error) as exc:
            code = ('storage_unavailable' if isinstance(exc, sqlite3.Error)
                    else 'file_unavailable' if method in {'preview', 'open_file', 'freeze_file'}
                    else 'operation_failed')
            return {'ok': False, 'error': str(exc), 'code': code, 'category': 'operation'}

    def projects(self):
        return self._call('projects')

    def records(self, project=None, workline=None, limit=50, cursor=None, unread_only=False, query=''):
        if type(limit) is not int or not 1 <= limit <= 100:
            return {'ok': False, 'error': '每次读取 1 至 100 条记录。'}
        if not isinstance(query, str) or len(query) > 200:
            return {'ok': False, 'error': '搜索内容最多 200 字。'}
        return self._call('records', project=project, workline=workline, limit=limit,
                          cursor=cursor, unread_only=bool(unread_only), query=query)

    def detail(self, record_id):
        return self._call('detail', record_id)

    def ack(self, record_ids):
        if not isinstance(record_ids, list) or len(record_ids) > 100:
            return {'ok': False, 'error': '每次最多标记 100 条已读记录。'}
        return self._call('ack', record_ids)

    def changes(self, cursor=None):
        return self._call('changes', cursor)

    def mark_unread(self, record_ids):
        if not isinstance(record_ids, list) or len(record_ids) > 100:
            return {'ok': False, 'error': '每次最多标记 100 条未读记录。', 'category': 'operation'}
        return self._call('mark_unread', record_ids)

    def preview(self, record_id, file_id):
        return self._call('preview', record_id, file_id)

    def open_file(self, record_id, file_id, action='open'):
        if action not in {'open', 'reveal', 'copy'}:
            return {'ok': False, 'error': '不支持此文件操作。'}
        return self._call('open_file', record_id, file_id, action=action)

    def export(self, record_id):
        return self._call('export', record_id)

    def export_filename(self, record_id):
        return self._call('export_filename', record_id)

    def freeze_file(self, record_id, file_id):
        return self._call('freeze_file', record_id, file_id)

    def connection_status(self):
        from .integration import _prefix, doctor, shell_command
        from .paths import app_directory
        try:
            app = app_directory(self._app_dir)
            result = doctor(app, probe=False)
            argv = _prefix(app) + ['connect', 'codex']
            if sys.platform == 'win32':
                command = '& ' + ' '.join("'" + item.replace("'", "''") + "'" for item in argv)
            else:
                command = shell_command(argv)
            custom_data = self._data_dir is not None
            result.update(connect_command='' if custom_data else command,
                          native_connect=self._window is not None and not custom_data,
                          reader_uses_custom_data=custom_data,
                          reader_data_dir=str(Path(self._data_dir).resolve()) if custom_data else str(app / 'data'))
            return {'ok': True, 'data': result}
        except (ValueError, OSError) as exc:
            return {'ok': False, 'error': str(exc)}

    def connect_codex(self):
        """A deliberate native button click installs the managed integration."""
        if self._data_dir is not None:
            return {'ok': False, 'error': '当前阅读使用独立数据目录。请先用统一的 --app-dir 重新打开 HandoffDesk，再连接 Codex，避免记录写到其他目录。'}
        if self._window is None:
            return {'ok': False, 'error': '请在桌面窗口连接 Codex，或复制连接命令到本机终端。'}
        from .integration import connect_codex
        try:
            return {'ok': True, 'data': connect_codex(self._app_dir)}
        except (ValueError, OSError) as exc:
            return {'ok': False, 'error': str(exc)}

    def save_export(self, record_id):
        """Native export can write only the path chosen in a system dialog."""
        if self._window is None:
            return {'ok': False, 'error': '浏览器中请使用下载按钮。'}
        result = self.export(record_id)
        if not result['ok']:
            return result
        filename = self.export_filename(record_id)
        if not filename['ok']:
            return filename
        try:
            import webview
            selected = self._window.create_file_dialog(
                webview.FileDialog.SAVE, save_filename=filename['data'],
                file_types=('Markdown (*.md)',))
            if not selected:
                return {'ok': True, 'data': {'saved': False}}
            target = Path(selected if isinstance(selected, str) else selected[0])
            target.write_text(result['data'], encoding='utf-8')
            return {'ok': True, 'data': {'saved': True, 'path': str(target)}}
        except (ValueError, OSError) as exc:
            return {'ok': False, 'error': str(exc)}


class LocalReaderServer:
    """Owned, stoppable loopback server; public files are an explicit allowlist."""

    def __init__(self, api, *, browser_api=True):
        self.api, self.browser_api = api, browser_api
        self.token = secrets.token_urlsafe(32)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                # Never put record names, request bodies, or tokens in access logs.
                pass

            def _reply(self, status, body, content_type='application/json; charset=utf-8'):
                try:
                    self._write_reply(status, body, content_type)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    # A refresh/closed tab may cancel either headers or body on
                    # Windows. Only transport cancellation is suppressed.
                    self.close_connection = True

            def _write_reply(self, status, body, content_type):
                if not isinstance(body, bytes):
                    body = json.dumps(body, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('X-Frame-Options', 'DENY')
                # pywebview generates bridge functions with Function() and returns
                # promises through evaluate_js. Its native-only document needs
                # eval; the token-authenticated browser surface does not.
                script_policy = "'self'" if owner.browser_api else "'self' 'unsafe-eval'"
                style_policy = "'self'" if owner.browser_api else "'self' 'unsafe-inline'"
                self.send_header('Content-Security-Policy', f"default-src 'self'; script-src {script_policy}; style-src {style_policy}; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
                self.end_headers()
                self.wfile.write(body)

            def _host_ok(self):
                return self.headers.get('Host') == owner.host

            def do_GET(self):
                if not self._host_ok():
                    self._reply(403, {'ok': False, 'error': '仅允许本机访问。'})
                    return
                asset = STATIC.get(self.path)
                if not asset:
                    self._reply(404, {'ok': False, 'error': '页面不存在。'})
                    return
                self._reply(200, (ASSETS / asset[0]).read_bytes(), asset[1])

            def do_POST(self):
                # Exact origin checks prevent DNS rebinding and cross-site requests.
                supplied = self.headers.get('X-HandoffDesk-Token', '')
                if (not owner.browser_api or not self._host_ok()
                        or self.headers.get('Origin') != owner.origin
                        or not secrets.compare_digest(supplied, owner.token)):
                    self._reply(403, {'ok': False, 'error': '阅读会话已失效，请从 HandoffDesk 重新打开。'})
                    return
                if self.path != '/api' or self.headers.get_content_type() != 'application/json':
                    self._reply(400, {'ok': False, 'error': '不支持此请求。'})
                    return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= MAX_REQUEST_BYTES:
                        raise ValueError('请求大小超出限制。')
                    payload = json.loads(self.rfile.read(length))
                    if not isinstance(payload, dict) or set(payload) != {'method', 'args'}:
                        raise ValueError('请求格式错误。')
                    method, args = payload['method'], payload['args']
                    if not isinstance(method, str) or method not in METHODS or not isinstance(args, list) or len(args) > 6:
                        raise ValueError('不支持此读取操作。')
                    result = getattr(owner.api, method)(*args)
                except (ValueError, TypeError, UnicodeError) as exc:
                    self._reply(400, {'ok': False, 'error': str(exc)})
                    return
                except Exception:
                    self._reply(500, {'ok': False, 'error': '读取失败，请重试或查看本机诊断。'})
                    return
                self._reply(200, result)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.host = f'127.0.0.1:{self.server.server_port}'
        self.origin = f'http://{self.host}'
        self.url = f'{self.origin}/#token={self.token}'
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.server.serve_forever, name='handoffdesk-reader', daemon=True)
        self.thread.start()
        return self.url

    def close(self):
        if self.thread:
            self.server.shutdown()
            self.thread.join(timeout=3)
        self.server.server_close()


def run(app_dir, data_dir=None, *, browser=False):
    """Launch a local desktop window, or an explicitly selected browser reader."""
    if not browser:
        try:
            import webview
        except ImportError as exc:
            raise ValueError('Web 桌面组件尚未安装。请安装 HandoffDesk Windows 版本，或运行 pip install "handoffdesk[desktop]"；Linux 可使用 handoffdesk tui。') from exc
        if sys.platform != 'win32':
            from .platform_io import ensure_desktop
            ensure_desktop()
    api = ReaderAPI(app_dir, data_dir)
    check = api.projects()
    if not check['ok']:
        raise ValueError(check['error'])
    server = LocalReaderServer(api, browser_api=browser)
    try:
        server.start()
        if browser:
            print('HandoffDesk 本机阅读地址：' + server.url, flush=True)
            print('此窗口保持运行；按 Ctrl+C 关闭阅读服务。', flush=True)
            webbrowser.open(server.url)
            try:
                server.thread.join()
            except KeyboardInterrupt:
                pass
        else:
            webview.settings['ALLOW_FILE_URLS'] = False
            webview.settings['ALLOW_DOWNLOADS'] = False
            window = webview.create_window(
                'HandoffDesk · 工作进展', server.origin,
                width=1280, height=860, min_size=(680, 500),
                background_color='#f6f7f9', text_select=True, zoomable=True)
            api._window = window
            # Do not pass the API object: pywebview's callback resolver accepts
            # dotted names, including private attributes. Only these bound
            # functions belong in its exposed-function dictionary.
            window.expose(*(getattr(api, method) for method in sorted(METHODS | {'save_export', 'connect_codex'})))
            try:
                webview.start(gui='edgechromium' if sys.platform == 'win32' else None, debug=False)
            except Exception as exc:
                raise ValueError('无法启动 Web 桌面窗口。Windows 请确认已安装 Microsoft Edge WebView2 Runtime。' + str(exc)) from exc
    finally:
        server.close()
    return 0
