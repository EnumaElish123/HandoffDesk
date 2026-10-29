"""User-observable upgrade, launcher ownership, and diagnosis contracts."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from handoffdesk import integration
from handoffdesk.paths import app_directory

spec = importlib.util.spec_from_file_location('source_installer', Path(__file__).resolve().parents[1] / 'scripts/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallationUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = self.root / 'registration/installation.json'
        self.state = self.root / 'existing 中文 state'
        self.prefix = self.root / 'application'
        self.prefix.mkdir()
        self.env = patch.dict(os.environ, {'HOME': str(self.root / 'home'), 'XDG_DATA_HOME': str(self.root / 'data'),
                                         'XDG_CONFIG_HOME': str(self.root / 'config'), 'HANDOFFDESK_HOME': ''})
        self.env.start()
        self.addCleanup(self.env.stop)
        os.environ.pop('HANDOFFDESK_HOME')

    def test_installer_uses_same_explicit_environment_registry_precedence(self):
        integration.register_installation(self.state, self.registry)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            installer.main(['--check', '--no-connect', '--registry-path', str(self.registry)])
        result = json.loads(out.getvalue())
        self.assertEqual(result['state'], str(self.state))
        self.assertTrue(result['reusing_records'])
        self.assertEqual(app_directory(registry_path=self.registry), self.state)
        os.environ['HANDOFFDESK_HOME'] = str(self.root / 'env-state')
        self.assertEqual(app_directory(registry_path=self.registry), self.root / 'env-state')
        self.assertEqual(app_directory(self.root / 'explicit', registry_path=self.registry), self.root / 'explicit')
        self.assertFalse((self.root / 'env-state').exists())

    def test_bad_registry_stops_install_before_package_or_state_changes(self):
        self.registry.parent.mkdir()
        self.registry.write_text('{bad')
        with patch.object(subprocess, 'run') as run, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                installer.main(['--state-dir', str(self.state), '--registry-path', str(self.registry), '--no-connect'])
        self.assertEqual(error.exception.code, 2)
        run.assert_not_called()
        self.assertEqual(self.registry.read_text(), '{bad')
        self.assertFalse(self.state.exists())
        with self.assertRaises(ValueError):
            integration.register_installation(self.state, self.registry)
        self.assertEqual(self.registry.read_text(), '{bad')

    def test_launcher_runs_space_path_and_preserves_user_replacements(self):
        command = self.root / '中文 path' / 'run'
        command.parent.mkdir()
        command.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        command.chmod(0o755)
        bin_dir = self.root / 'bin'
        first = installer.install_user_launcher(self.prefix, command, bin_dir)
        target = Path(first['path'])
        result = subprocess.run([str(target), 'hello space', '中文'], text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout, 'hello space\n中文\n')
        self.assertTrue(installer.install_user_launcher(self.prefix, command, bin_dir)['installed'])
        target.write_text('#!/bin/sh\necho user-owned\n')
        self.assertFalse(installer.install_user_launcher(self.prefix, command, bin_dir)['installed'])
        self.assertFalse(installer.remove_user_launcher(self.prefix))
        self.assertIn('user-owned', target.read_text())

    def test_launcher_never_replaces_external_symlink_and_removes_only_owned_entry(self):
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        target = bin_dir / 'handoffdesk'
        target.symlink_to(self.root / 'missing-other-program')
        self.assertFalse(installer.install_user_launcher(self.prefix, self.prefix / 'handoffdesk', bin_dir)['installed'])
        self.assertTrue(target.is_symlink())
        target.unlink()
        installer.install_user_launcher(self.prefix, self.prefix / 'handoffdesk', bin_dir)
        self.assertTrue(installer.remove_user_launcher(self.prefix))
        self.assertFalse(target.exists())
        self.assertTrue(self.prefix.exists())

    def test_new_prefix_takes_only_proven_old_launcher_ownership(self):
        second = self.root / 'new-prefix'
        second.mkdir()
        bin_dir = self.root / 'bin'
        one = installer.install_user_launcher(self.prefix, self.prefix / 'handoffdesk', bin_dir)
        target = Path(one['path'])
        old_bytes = target.read_bytes()
        new = installer.install_user_launcher(second, second / 'handoffdesk', bin_dir, previous_prefix=self.prefix)
        self.assertTrue(new['installed'])
        self.assertIn(str(second), target.read_text())
        self.assertFalse((self.prefix / 'launcher.json').exists())
        self.assertFalse(installer.remove_user_launcher(self.prefix))
        self.assertTrue(target.exists())
        self.assertTrue(installer.remove_user_launcher(second))
        self.assertFalse(target.exists())
        # A modified old entry cannot be claimed by a new installation.
        installer.install_user_launcher(self.prefix, self.prefix / 'handoffdesk', bin_dir)
        target.write_bytes(old_bytes + b'# user customization\n')
        result = installer.install_user_launcher(second, second / 'handoffdesk', bin_dir, previous_prefix=self.prefix)
        self.assertFalse(result['installed'])
        self.assertIn(b'user customization', target.read_bytes())
        self.assertFalse(installer.remove_user_launcher(second))

    def test_new_prefix_without_registry_owner_evidence_preserves_existing_command(self):
        second = self.root / 'new-prefix'
        second.mkdir()
        bin_dir = self.root / 'bin'
        installer.install_user_launcher(self.prefix, self.prefix / 'handoffdesk', bin_dir)
        before = (bin_dir / 'handoffdesk').read_bytes()
        self.assertFalse(installer.install_user_launcher(second, second / 'handoffdesk', bin_dir)['installed'])
        self.assertEqual((bin_dir / 'handoffdesk').read_bytes(), before)

    def connect(self, hooks=True):
        return integration.connect_codex(self.state, codex_home=self.root / 'custom-codex',
                                         skills_home=self.root / 'custom-skills', registry_path=self.registry, hooks=hooks)

    def test_diagnosis_uses_manifest_locations_and_explains_missing_host(self):
        self.connect()
        with patch('shutil.which', return_value=None):
            value = integration.doctor(self.state, probe=True)
        self.assertTrue(value['hooks_configured'])
        self.assertFalse(value['ok'])
        self.assertFalse(value['fully_verified'])
        self.assertEqual(value['issues'][0]['code'], 'host_executable_unavailable')
        self.assertIn('桌面', value['message'])
        self.assertIn('codex --version', value['next_action'])
        self.assertEqual(value['layers'][0]['status'], 'available')
        self.assertFalse((self.state / 'data/cards.sqlite3').exists())
        self.assertIn('下一步', integration.format_doctor(value))

    def test_probe_failure_is_actionable_before_unverified_trust_warning(self):
        self.connect()
        replies = [subprocess.CompletedProcess([], 0, 'codex test', ''), subprocess.CompletedProcess([], 0, 'hooks experimental true\n', '')]
        with patch('shutil.which', return_value='/codex'), patch('subprocess.run', side_effect=replies), patch.object(integration, 'permission_probe', return_value={'ok': False, 'message': 'denied'}):
            result = integration.doctor(self.state, probe=True)
        self.assertEqual(result['issues'][0]['code'], 'permission_denied')
        self.assertIn('写权限', result['next_action'])

    def test_invalid_hook_event_is_reported_without_a_type_error(self):
        self.connect()
        path = self.root / 'custom-codex/hooks.json'
        value = json.loads(path.read_text())
        value['hooks']['Stop'] = 1
        path.write_text(json.dumps(value))
        with patch('shutil.which', return_value=None):
            result = integration.doctor(self.state)
        self.assertFalse(result['hooks_configured'])
        self.assertIn('hooks_changed', {i['code'] for i in result['issues']})

    def test_diagnosis_explains_failed_host_disabled_hooks_probe_and_skill_only(self):
        self.connect()
        with patch('shutil.which', return_value='/codex'), patch('subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'failed')):
            self.assertEqual(integration.doctor(self.state)['host']['status'], 'executable_failed')
        replies = [subprocess.CompletedProcess([], 0, 'codex test', ''), subprocess.CompletedProcess([], 0, 'hooks experimental false\n', '')]
        with patch('shutil.which', return_value='/codex'), patch('subprocess.run', side_effect=replies), patch.object(integration, 'permission_probe', return_value={'ok': False, 'message': 'denied'}):
            value = integration.doctor(self.state, probe=True)
        codes = {i['code'] for i in value['issues']}
        self.assertIn('hooks_disabled', codes)
        self.assertIn('permission_denied', codes)
        self.assertFalse(value['ok'])
        self.connect(hooks=False)
        with patch('shutil.which', return_value='/codex'), patch('subprocess.run', side_effect=replies):
            value = integration.doctor(self.state)
        self.assertTrue(value['ok'])  # Disabled hooks do not invalidate Skill-only.
        self.assertIn('skill_only', {i['code'] for i in value['issues']})
        self.assertFalse(value['fully_verified'])


if __name__ == '__main__':
    unittest.main()
