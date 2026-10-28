"""The local reader must not expose user data to other browser origins."""
import http.client
import json
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from handoffdesk.web_desktop import LocalReaderServer, METHODS, ReaderAPI, run


class WebReaderTests(unittest.TestCase):
    def setUp(self):
        self.reader = Mock()
        self.reader.projects.return_value = [{'id': 'sample', 'name': '中文项目', 'unread': 1}]
        self.reader.export.return_value = '# 中文记录\n\n已完成内容。\n'
        self.api = ReaderAPI(None, reader_factory=lambda: self.reader)
        self.server = LocalReaderServer(self.api)
        self.server.start()
        self.addCleanup(self.server.close)

    def request(self, method='POST', path='/api', body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server.server_port, timeout=3)
        self.addCleanup(connection.close)
        if body is None:
            body = json.dumps({'method': 'projects', 'args': []})
        valid = {'Content-Type': 'application/json', 'Origin': self.server.origin,
                 'X-HandoffDesk-Token': self.server.token}
        valid.update(headers or {})
        connection.request(method, path, body=body if method == 'POST' else None, headers=valid)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()

    def test_authenticated_same_origin_can_read(self):
        status, headers, body = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['data'][0]['name'], '中文项目')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertNotIn('Access-Control-Allow-Origin', headers)

    def test_cross_origin_missing_secret_and_dns_rebinding_are_denied(self):
        for headers in ({'Origin': 'https://attacker.example'},
                        {'Origin': 'null'}, {'Origin': ''},
                        {'X-HandoffDesk-Token': ''}, {'X-HandoffDesk-Token': 'wrong'},
                        {'Host': 'attacker.example'}, {'Host': 'localhost:' + str(self.server.server.server_port)}):
            with self.subTest(headers=headers):
                status, _, _ = self.request(headers=headers)
                self.assertEqual(status, 403)
        self.reader.projects.assert_not_called()

    def test_only_bundled_assets_can_be_requested(self):
        for path in ('/', '/app.css', '/app.js'):
            with self.subTest(path=path):
                status, headers, body = self.request('GET', path)
                self.assertEqual(status, 200)
                self.assertTrue(body)
                self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        for path in ('/../store.py', '/%2e%2e/store.py', '/data/cards.sqlite3', '/api', '/app.js?path=/etc/passwd'):
            with self.subTest(path=path):
                self.assertEqual(self.request('GET', path)[0], 404)

    def test_no_arbitrary_reader_or_native_export_method_is_dispatched(self):
        for method in ('_reader', '__dict__', 'save_export', 'connect_codex', 'connection', 'delete', ['projects']):
            with self.subTest(method=method):
                body = json.dumps({'method': method, 'args': []})
                self.assertEqual(self.request(body=body)[0], 400)
        self.reader.projects.assert_not_called()

    def test_invalid_envelopes_are_rejected_without_writes(self):
        for value in ({'method': 'projects', 'args': [], 'path': '/etc/passwd'},
                      {'method': 'projects', 'args': {}}, [], {'method': 'projects'}, None):
            with self.subTest(value=value):
                self.assertEqual(self.request(body=json.dumps(value))[0], 400)
        self.assertEqual(self.request(body='not json')[0], 400)
        self.reader.projects.assert_not_called()

    def test_native_window_disables_http_data_api(self):
        self.server.browser_api = False
        self.assertEqual(self.request()[0], 403)
        self.reader.projects.assert_not_called()
        native_csp = self.request('GET', '/')[1]['Content-Security-Policy']
        self.assertIn("'unsafe-eval'", native_csp)
        self.server.browser_api = True
        browser_csp = self.request('GET', '/')[1]['Content-Security-Policy']
        self.assertNotIn("'unsafe-eval'", browser_csp)

    def test_native_export_uses_only_user_selected_path(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'selected.md'
            window = Mock()
            window.create_file_dialog.return_value = (str(destination),)
            self.api._window = window
            webview = Mock()
            with patch.dict('sys.modules', {'webview': webview}):
                result = self.api.save_export('sample:default:1')
            self.assertTrue(result['data']['saved'])
            self.assertEqual(destination.read_text(encoding='utf-8'), '# 中文记录\n\n已完成内容。\n')
            window.create_file_dialog.assert_called_once()
            self.reader.export.assert_called_once_with('sample:default:1')

    def test_cancelled_export_does_not_claim_success(self):
        self.api._window = Mock()
        self.api._window.create_file_dialog.return_value = None
        with patch.dict('sys.modules', {'webview': Mock()}):
            result = self.api.save_export('sample:default:1')
        self.assertFalse(result['data']['saved'])

    def test_failed_reader_operation_is_an_explicit_error(self):
        self.reader.preview.side_effect = ValueError('文件不存在。')
        result = self.api.preview('sample:default:1', 'file-id')
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], '文件不存在。')
        self.reader.projects.side_effect = sqlite3.OperationalError('database is locked')
        self.assertFalse(self.api.projects()['ok'])

    def test_frontend_cannot_request_unbounded_pages_or_execute_files(self):
        self.assertFalse(self.api.records(limit=10001)['ok'])
        self.assertFalse(self.api.open_file('sample:default:1', 'file-id', 'execute')['ok'])
        self.reader.records.assert_not_called()
        self.reader.open_file.assert_not_called()

    def test_browser_connection_status_cannot_install_integration(self):
        with patch('handoffdesk.integration.connect_codex') as connect:
            self.assertFalse(self.api.connect_codex()['ok'])
            connect.assert_not_called()

    def test_connection_status_is_honest_about_host_trust(self):
        with tempfile.TemporaryDirectory() as folder:
            self.api._app_dir = Path(folder)
            status = {'ok': True, 'connected': True, 'hooks_configured': True,
                      'host_trust': 'not_verified', 'app_dir': folder, 'version': 'test'}
            with patch('handoffdesk.integration.doctor', return_value=status) as doctor:
                value = self.api.connection_status()['data']
            doctor.assert_called_once_with(Path(folder), probe=False)
            self.assertEqual(value['host_trust'], 'not_verified')
            self.assertFalse(value['native_connect'])
            self.assertIn('connect codex', value['connect_command'])

    def test_native_bridge_exposes_functions_without_private_object_graph(self):
        webview = Mock()
        webview.settings = {}
        with patch.dict('sys.modules', {'webview': webview}), \
                patch('handoffdesk.web_desktop.sys.platform', 'win32'), \
                patch.object(ReaderAPI, 'projects', return_value={'ok': True, 'data': []}):
            self.assertEqual(run(None), 0)
        self.assertNotIn('js_api', webview.create_window.call_args.kwargs)
        exposed = webview.create_window.return_value.expose.call_args.args
        names = {method.__name__ for method in exposed if hasattr(method, '__name__')}
        # The projects mock has no method name; all other methods are real.
        self.assertEqual(names, (METHODS | {'save_export', 'connect_codex'}) - {'projects'})
        self.assertFalse(any(name.startswith('_') for name in names))

    def test_custom_data_directory_cannot_silently_connect_another_database(self):
        self.api._data_dir = Path('/custom/reader/data')
        self.api._window = Mock()
        with patch('handoffdesk.integration.connect_codex') as connect:
            result = self.api.connect_codex()
        self.assertFalse(result['ok'])
        self.assertIn('--app-dir', result['error'])
        connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
