"""Run reader UI regressions with an explicitly provided Node/Playwright/browser.

This is an opt-in real-browser acceptance test, separate from the Python suite.
All records and attachments are isolated in a temporary directory.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from handoffdesk.agent_session import AgentSessions
from handoffdesk.web_desktop import LocalReaderServer, ReaderAPI


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', default='node')
    parser.add_argument('--playwright-module', default='playwright')
    parser.add_argument('--browser', required=True, help='Chromium-compatible executable')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--focus', default='all')
    options = parser.parse_args()
    options.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='handoffdesk-reader-regression-') as folder:
        base = Path(folder)
        project = base / '中文项目 space'
        project.mkdir()
        attachment = project / '验证报告 中文.txt'
        attachment.write_text('中文验收正文\n检查实际保存的文件内容。\n', encoding='utf-8')
        state = base / 'state'
        sessions = AgentSessions(state)
        begun = sessions.begin(project, 'Windows 修复交接', actor='Regression Agent')
        older = sessions.checkpoint(begun['session_ref'], begun['checkpoint_token'], {
            'title': '较早的交接', 'actual_done': '保留另一条未读记录。',
            'next_action': '检查阅读状态。', 'verification': {'status': 'not_run', 'summary': '仅用于测试。'},
            'files': []})
        latest = sessions.checkpoint(begun['session_ref'], older['next_checkpoint_token'], {
            'title': '中文导出 / Windows: 修复交接', 'actual_done': '已完成中文路径修复。',
            'next_action': '预览验证报告并继续工作。',
            'verification': {'status': 'passed', 'summary': '记录者提供的测试结果。'},
            'files': [{'path': str(attachment), 'purpose': '中文验收证据'}]})
        sessions.begin(project, 'Windows 修复交接', actor='Another Regression Agent', new=True)
        server = LocalReaderServer(ReaderAPI(state))
        server.start()
        try:
            env = dict(os.environ, HANDOFFDESK_READER_TEST=json.dumps({
                'url': server.url, 'record': latest['record_id'], 'older': older['record_id'],
                'project': begun['project'], 'attachment': str(attachment),
                'output': str(options.output.resolve()), 'browser': options.browser,
                'playwright': options.playwright_module, 'focus': options.focus,
            }, ensure_ascii=False))
            result = subprocess.run([options.node, str(ROOT / 'tests' / 'browser_reader_regressions.cjs')],
                                    env=env, cwd=ROOT, timeout=180)
            return result.returncode
        finally:
            server.close()


if __name__ == '__main__':
    raise SystemExit(main())
