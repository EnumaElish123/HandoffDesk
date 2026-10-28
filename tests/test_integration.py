import base64
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from handoffdesk import integration
from handoffdesk.agent_session import AgentSessions


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = self.root / '应用 状态'
        self.codex = self.root / 'codex'
        self.skills = self.root / 'skills'
        self.registry = self.root / 'config' / 'installation.json'
        self.project = self.root / 'project'
        self.project.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def connect(self, **kwargs):
        return integration.connect_codex(self.app, codex_home=self.codex,
                                         skills_home=self.skills, registry_path=self.registry, **kwargs)

    def test_connect_update_disconnect_preserves_unrelated_configuration(self):
        self.codex.mkdir()
        original_hook = {'hooks': [{'type': 'command', 'command': 'echo existing'}]}
        original = {'description': 'my hooks', 'hooks': {'Stop': [original_hook]}}
        hook_path = self.codex / 'hooks.json'
        hook_path.write_text(json.dumps(original), encoding='utf-8')
        (self.codex / 'AGENTS.md').write_text('my instructions', encoding='utf-8')
        first = self.connect()
        before = hook_path.read_bytes()
        second = self.connect()
        self.assertEqual(before, hook_path.read_bytes())
        self.assertEqual(first['installation']['state_id'], second['installation']['state_id'])
        self.assertEqual(first['installation']['installation_id'], second['installation']['installation_id'])
        self.assertEqual(first['hook_status'], 'awaiting_host_trust')
        self.assertIn(original_hook, json.loads(hook_path.read_text())['hooks']['Stop'])
        result = integration.disconnect_codex(self.app)
        self.assertTrue(result['disconnected'])
        self.assertEqual(json.loads(hook_path.read_text()), original)
        self.assertEqual((self.codex / 'AGENTS.md').read_text(), 'my instructions')
        self.assertTrue(self.registry.exists())
        self.assertTrue((self.app / 'integrations' / 'backups').is_dir())

    def test_user_skill_collision_and_modified_file_are_never_overwritten(self):
        target = self.skills / 'handoffdesk' / 'SKILL.md'
        target.parent.mkdir(parents=True)
        target.write_text('user owns this', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '同名 Skill'):
            self.connect()
        self.assertEqual(target.read_text(), 'user owns this')
        self.assertFalse((self.codex / 'hooks.json').exists())
        target.unlink()
        self.connect()
        target.write_text('user changed this', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.connect()
        result = integration.disconnect_codex(self.app)
        self.assertEqual(target.read_text(), 'user changed this')
        self.assertIn(str(target), result['preserved_modified_files'])

    def test_failed_registration_rolls_back_hook_skill_and_manifest(self):
        self.codex.mkdir()
        path = self.codex / 'hooks.json'
        original = b'{"hooks": {}, "description": "retain exact bytes"}\n'
        path.write_bytes(original)
        with patch.object(integration, 'register_installation', side_effect=OSError('read only registry')):
            with self.assertRaisesRegex(OSError, 'registry'):
                self.connect()
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((self.skills / 'handoffdesk' / 'SKILL.md').exists())
        self.assertFalse((self.app / 'integrations' / 'codex.json').exists())
        self.assertFalse((self.codex / 'handoffdesk-installation.json').exists())

    def test_second_installation_cannot_attach_another_recorder_to_same_codex(self):
        self.connect()
        original = (self.codex / 'hooks.json').read_bytes()
        with self.assertRaisesRegex(ValueError, '另一份'):
            integration.connect_codex(self.root / 'second-state', codex_home=self.codex,
                                      skills_home=self.root / 'other-skills', registry_path=self.registry)
        self.assertEqual((self.codex / 'hooks.json').read_bytes(), original)
        self.assertEqual(integration.registered_app_directory(self.registry), self.app)
        integration.disconnect_codex(self.app)
        changed = integration.connect_codex(self.root / 'second-state', codex_home=self.codex,
                                           skills_home=self.root / 'other-skills', registry_path=self.registry)
        self.assertTrue(changed['ok'])

    def test_old_program_uninstaller_cannot_disconnect_new_program(self):
        self.connect()
        result = integration.disconnect_codex(self.app, program_dir=self.root / 'other-program')
        self.assertFalse(result['disconnected'])
        self.assertTrue((self.app / 'integrations' / 'codex.json').exists())

    def test_skill_only_and_probe_do_not_create_progress_database(self):
        result = self.connect(hooks=False)
        self.assertEqual(result['hook_status'], 'skill_only')
        self.assertFalse((self.codex / 'hooks.json').exists())
        self.assertTrue(integration.permission_probe(self.app)['ok'])
        self.assertFalse((self.app / 'data' / 'cards.sqlite3').exists())
        self.assertEqual(list(self.app.glob('.permission-probe-*')), [])
        with patch('shutil.which', return_value=None):
            status = integration.doctor(self.app, codex_home=self.codex, skills_home=self.skills)
        self.assertEqual(status['host']['status'], 'executable_unavailable')
        self.assertFalse(status['ok'])
        self.assertFalse(status['fully_verified'])
        self.assertEqual(status['host_trust'], 'not_verified')
        self.assertFalse(status['hooks_configured'])

    def test_registry_validation_never_silently_uses_another_state(self):
        self.assertIsNone(integration.registered_app_directory(self.registry))
        info = integration.register_installation(self.app, self.registry)
        self.assertEqual(integration.registered_app_directory(self.registry), self.app)
        self.assertTrue(info['state_id'])
        self.registry.write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            integration.registered_app_directory(self.registry)

    def test_shell_arguments_are_not_shell_code(self):
        argv = ['/some path/py`$(test)', '-m', 'handoffdesk', '--app-dir', '/state with \'quote\'']
        self.assertEqual(shlex.split(integration.shell_command(argv)), argv)
        windows = integration.shell_command(argv, windows=True)
        script = base64.b64decode(windows.split()[-1]).decode('utf-16-le')
        self.assertIn("& '/some path/py`$(test)'", script)
        self.assertIn("'/state with ''quote'''", script)

    def test_windows_gui_connection_uses_console_python_and_utf8(self):
        pythonw = self.root / 'pythonw.exe'
        python = self.root / 'python.exe'
        python.touch()
        pythonw.touch()
        with patch.object(sys, 'platform', 'win32'), patch.object(sys, 'executable', str(pythonw)):
            prefix = integration._prefix(self.app)
        self.assertEqual(prefix[:3], [str(python), '-X', 'utf8'])

    def test_symlink_target_is_rejected(self):
        elsewhere = self.root / 'elsewhere'
        elsewhere.mkdir()
        try:
            self.skills.symlink_to(elsewhere, target_is_directory=True)
        except OSError:
            self.skipTest('symlinks unavailable')
        # _locations intentionally canonicalizes the requested root, but an
        # individual managed file may never redirect elsewhere.
        folder = elsewhere / 'handoffdesk'
        folder.mkdir()
        target = self.root / 'private'
        target.write_text('preserve', encoding='utf-8')
        (folder / 'SKILL.md').symlink_to(target)
        with self.assertRaises(ValueError):
            self.connect()
        self.assertEqual(target.read_text(), 'preserve')

    def hook(self, event, **payload):
        values = {'session_id': 'real-host-session', 'turn_id': 'turn-1', 'cwd': str(self.project)}
        values.update(payload)
        return integration.handle_hook(self.app, event, values)

    def turn(self, host_turn='turn-1'):
        return AgentSessions(self.app).observe_turn('codex', 'real-host-session', host_turn)

    def test_pure_question_creates_no_project_and_no_stop_loop(self):
        prompt = self.hook('UserPromptSubmit')
        self.assertEqual(prompt['hookSpecificOutput']['hookEventName'], 'UserPromptSubmit')
        turn = self.turn()
        AgentSessions(self.app).conclude_turn(turn['turn_ref'], 'no_record', reason='Pure explanation.')
        self.assertEqual(self.hook('Stop'), {})
        self.assertFalse((self.app / 'config' / 'projects.json').exists())

    def test_stop_budget_survives_a_new_continuation_turn(self):
        self.hook('UserPromptSubmit')
        first = self.hook('Stop')
        self.assertEqual(first['decision'], 'block')
        second = self.hook('Stop')
        self.assertNotIn('decision', second)
        self.hook('UserPromptSubmit', turn_id='turn-2', prompt=first['reason'])
        self.assertEqual(self.turn('turn-2')['original_turn_ref'], self.turn()['turn_ref'])
        third = self.hook('Stop', turn_id='turn-2', stop_hook_active=False)
        self.assertNotIn('decision', third)

    def test_interrupt_retains_pending_report_and_does_not_restart(self):
        self.hook('UserPromptSubmit')
        sessions = AgentSessions(self.app)
        turn = self.turn()
        session = sessions.begin(self.project, 'Implementation', turn_ref=turn['turn_ref'])
        saved = sessions.checkpoint(session['session_ref'], session['checkpoint_token'], {
            'title': 'Saved work', 'actual_done': 'Completed the feature', 'next_action': 'Review it'})
        result = self.hook('Interrupt')
        self.assertEqual(result, {})
        status = sessions.turn_status(turn['turn_ref'])
        self.assertEqual(status['disposition'], 'interrupted')
        self.assertEqual(status['pending_reports'][0]['record_id'], saved['record_id'])
        self.assertEqual(self.hook('Stop'), {})

    def test_final_receipt_observation_does_not_claim_user_delivery(self):
        self.hook('UserPromptSubmit')
        sessions = AgentSessions(self.app)
        turn = self.turn()
        session = sessions.begin(self.project, 'Feature', turn_ref=turn['turn_ref'])
        saved = sessions.checkpoint(session['session_ref'], session['checkpoint_token'], {
            'title': 'Feature', 'actual_done': 'Done', 'next_action': 'Review'})
        sessions.ack_report(turn['turn_ref'], saved['record_id'], 'agent_acknowledged', 'already reported mid-turn')
        sessions.conclude_turn(turn['turn_ref'], 'recorded', [saved['record_id']])
        self.assertEqual(self.hook('Stop', last_assistant_message='已保存 ' + saved['record_id'] + '。'), {})
        status = sessions.turn_status(turn['turn_ref'])
        self.assertEqual(status['pending_reports'], [])
        self.assertFalse(status['delivery_verified'])
        self.assertEqual(status['receipts'][0]['report_status'], 'observed_in_final')

    def test_hook_cli_keeps_stdout_machine_readable(self):
        command = [sys.executable, '-m', 'handoffdesk.integration_cli', '--app-dir', str(self.app),
                   'integration-hook', '--event', 'SessionStart']
        result = subprocess.run(command, input=json.dumps({'session_id': 'host', 'cwd': str(self.project)}),
                                text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout)['hookSpecificOutput']['hookEventName'], 'SessionStart')
        self.assertNotIn('tkinter', result.stdout)


if __name__ == '__main__':
    unittest.main()
