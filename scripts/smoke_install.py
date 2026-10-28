"""Run from outside the checkout after installing a wheel or source archive."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid


def main():
    with tempfile.TemporaryDirectory(prefix='handoffdesk-install-') as temporary:
        base = Path(temporary)
        source = base / 'project 中文 space'
        source.mkdir()
        note = source / '阶段报告, sample.txt'
        note.write_text('Example project artifact.\n', encoding='utf-8')
        before = hashlib.sha256(note.read_bytes()).hexdigest()
        env = dict(os.environ, HANDOFFDESK_HOME=str(base / 'state'), PYTHONUTF8='1')
        env.pop('PYTHONPATH', None)

        def run(*args, payload=None, expected=0):
            result = subprocess.run([sys.executable, '-s', '-B', '-X', 'utf8', '-m', 'handoffdesk', *args],
                cwd=base, env=env, input=json.dumps(payload) if payload is not None else None,
                capture_output=True, text=True, encoding='utf-8', timeout=30)
            if result.returncode != expected:
                raise AssertionError(f'{args}: exit {result.returncode}, expected {expected}\n{result.stdout}\n{result.stderr}')
            return json.loads(result.stdout)

        run('record', 'schema')
        assert not (base / 'state').exists(), 'Schema must not create user state'
        run('init', '--project', 'sample', '--name', 'Sample project', '--root', str(source))
        context = run('record', 'context', 'sample', '--workline', 'default')
        payload = {'schema_version': 1, 'project': 'sample', 'workline': 'default',
            'operation_id': str(uuid.uuid4()), 'expected_revision': context['current_revision'],
            'record': {'title': 'Example milestone', 'event_kind': 'handoff',
                       'last_done': 'Prepared the example artifact.', 'next_action': 'Review the artifact.', 'blockers': []},
            'actor': {'name': 'Example agent'},
            'verification': {'status': 'passed', 'summary': 'Example artifact was created and read.'},
            'files': [{'path': str(note), 'purpose': 'Milestone report'}], 'source_snapshot_id': None}
        run('record', 'validate', '--stdin', payload=payload)
        receipt = run('record', 'save', '--stdin', payload=payload)
        replay = run('record', 'save', '--stdin', payload=payload)
        assert replay['replayed'] and replay['record_id'] == receipt['record_id']
        manifest = run('files', 'sample', '--node', receipt['node_id'])
        assert receipt['file_count'] == 1
        assert any(Path(item['path']) == note for item in manifest['files'])
        conflict = dict(payload, operation_id=str(uuid.uuid4()))
        assert run('record', 'save', '--stdin', payload=conflict, expected=3)['error']['code'] == 'revision_conflict'
        assert hashlib.sha256(note.read_bytes()).hexdigest() == before
        import handoffdesk
        assets = Path(handoffdesk.__file__).parent / 'assets'
        assert all((assets / name).is_file() for name in ['app.png', 'app.ico', 'app.svg'])
        package = assets.parent
        assert all((package / 'web' / name).is_file() for name in ['index.html', 'app.css', 'app.js'])
        assert (package / 'integrations/handoffdesk/SKILL.md').is_file()
        assert (package / 'integrations/handoffdesk/agents/openai.yaml').is_file()
        session = run('agent', 'begin', '--cwd', str(source), '--task', 'Installed package check', '--actor', 'Smoke agent')
        semantic = {'kind': 'handoff', 'title': 'Installed session workflow',
                    'actual_done': 'Checked packaged reader resources and automatic task binding.',
                    'next_action': 'Read this result.', 'files': [{'path': note.name, 'purpose': 'Package test artifact'}]}
        checkpoint = run('agent', 'checkpoint', '--session', session['session_ref'],
                         '--checkpoint', session['checkpoint_token'], '--stdin', payload=semantic)
        repeated = run('agent', 'checkpoint', '--session', session['session_ref'],
                       '--checkpoint', session['checkpoint_token'], '--stdin', payload=semantic)
        assert repeated['replayed'] and repeated['record_id'] == checkpoint['record_id']
        read = run('read', checkpoint['record_id'])
        assert read['files'][0]['path'] == str(note)
        assert read['verification']['status'] == 'not_run'
        assert run('feed')['items'][0]['id'] == checkpoint['record_id']
        print(json.dumps({'installed_package': True, 'init': True, 'agent_save': True, 'idempotency': True,
                          'conflict': True, 'file_manifest': True, 'source_preserved': True,
                          'agent_session': True, 'reader_assets': True, 'shared_feed': True,
                          'tkinter_imported': 'tkinter' in sys.modules}))


if __name__ == '__main__':
    main()
