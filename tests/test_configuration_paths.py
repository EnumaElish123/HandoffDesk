"""Windows realpath spellings must not change configuration containment."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from handoffdesk.bootstrap import configuration_lock
from handoffdesk.paths import _resolve_path, app_directory, configuration_path, validate_configuration_writes


@unittest.skipUnless(os.name == 'nt', 'Windows extended-length path spelling')
class WindowsConfigurationPathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app = self.root / 'state'

    @staticmethod
    def extended(path):
        name = str(path)
        return Path('\\\\?\\UNC\\' + name[2:] if name.startswith('\\\\')
                    else '\\\\?\\' + name)

    def test_concurrent_creator_device_prefix_does_not_change_state_paths(self):
        original_resolve = Path.resolve
        for component in ('state', 'config', 'projects.json', '.projects.guard'):
            with self.subTest(component=component):
                def changing_spelling(path, *args, **kwargs):
                    resolved = original_resolve(path, *args, **kwargs)
                    return self.extended(resolved) if path.name == component else resolved

                with patch.object(Path, 'resolve', changing_spelling):
                    self.assertEqual(app_directory(self.app), self.app)
                    self.assertEqual(configuration_path(self.app), self.app / 'config/projects.json')
                    with configuration_lock(self.app):
                        self.assertTrue((self.app / 'config').is_dir())

    def test_explicit_device_path_is_preserved(self):
        self.app.mkdir()
        explicit = self.extended(self.app)
        self.assertEqual(_resolve_path(explicit), explicit)
        self.assertEqual(configuration_path(explicit), explicit / 'config/projects.json')

    def test_unc_resolution_preserves_the_callers_path_form(self):
        ordinary = Path(r'\\server\share\state')
        explicit = self.extended(ordinary)
        with patch.object(Path, 'resolve', return_value=explicit):
            self.assertEqual(_resolve_path(ordinary), ordinary)
            self.assertEqual(_resolve_path(explicit), explicit)

    def test_device_prefix_does_not_allow_redirected_configuration(self):
        original_resolve = Path.resolve
        outside = self.root / 'outside'
        for component in ('config', 'projects.json'):
            with self.subTest(component=component):
                def redirected(path, *args, **kwargs):
                    return (self.extended(outside) if path.name == component
                            else original_resolve(path, *args, **kwargs))

                with patch.object(Path, 'resolve', redirected), self.assertRaisesRegex(ValueError, '符号链接'):
                    configuration_path(self.app)

    def test_mixed_path_forms_reject_overlapping_state_configuration_and_data(self):
        project = self.root / 'project'
        storage = self.root / 'storage'
        cases = [
            ('state_inside_project', project / 'state', project, None),
            ('project_inside_config', self.app, self.app / 'config/project', None),
            ('project_equals_config', self.app, self.app / 'config', None),
            ('data_inside_project', self.app, project, project / 'data'),
            ('project_inside_data', self.app, storage / 'project', storage),
            ('data_equals_project', self.app, project, project),
        ]
        for name, app, root, data in cases:
            root.mkdir(parents=True, exist_ok=True)
            for app_extended in (False, True):
                for root_extended in (False, True):
                    for data_extended in (False, True):
                        with self.subTest(case=name, app=app_extended, root=root_extended, data=data_extended):
                            state = self.extended(app) if app_extended else app
                            source = self.extended(root) if root_extended else root
                            folder = self.extended(data) if data is not None and data_extended else data
                            with self.assertRaisesRegex(ValueError, '重叠'):
                                validate_configuration_writes(state, [{'roots': {'main': str(source)}}], folder)

    def test_mixed_path_forms_allow_independent_state_project_and_data(self):
        project = self.root / 'project'
        project.mkdir()
        storage = self.root / 'storage'
        for app_extended in (False, True):
            for root_extended in (False, True):
                for data_extended in (False, True):
                    with self.subTest(app=app_extended, root=root_extended, data=data_extended):
                        state = self.extended(self.app) if app_extended else self.app
                        source = self.extended(project) if root_extended else project
                        folder = self.extended(storage) if data_extended else storage
                        validate_configuration_writes(state, [{'roots': {'main': str(source)}}], folder)

    def test_mixed_path_forms_allow_contained_configuration_targets(self):
        self.app.mkdir()
        explicit = self.extended(self.app)
        target = self.app / 'config/projects.json'
        original_resolve = Path.resolve

        def ordinary_target(path, *args, **kwargs):
            return target if path.name == 'projects.json' else original_resolve(path, *args, **kwargs)

        with patch.object(Path, 'resolve', ordinary_target):
            self.assertEqual(configuration_path(explicit), explicit / 'config/projects.json')

    def test_mixed_unc_path_forms_enforce_the_same_overlap_boundary(self):
        project = Path(r'\\server\share\project')
        separate = Path(r'\\server\share\state')
        # No network access: this seam supplies already resolved UNC paths.
        with patch.object(Path, 'resolve', lambda path: path):
            for extended_project in (False, True):
                source = self.extended(project) if extended_project else project
                state = project / 'state' if extended_project else self.extended(project / 'state')
                with self.subTest(root_extended=extended_project), self.assertRaisesRegex(ValueError, '重叠'):
                    validate_configuration_writes(state, [{'roots': {'main': str(source)}}])
                validate_configuration_writes(self.extended(separate), [{'roots': {'main': str(source)}}])

    def test_empty_guard_waits_for_owner_without_initializing_a_locked_byte(self):
        import msvcrt

        config = self.app / 'config'
        config.mkdir(parents=True)
        attempted = threading.Event()
        original_locking = msvcrt.locking

        def observe_attempt(descriptor, mode, size):
            try:
                return original_locking(descriptor, mode, size)
            finally:
                if mode == msvcrt.LK_NBLCK:
                    attempted.set()

        def acquire():
            with configuration_lock(self.app, timeout=3):
                return 'acquired'

        with (config / '.projects.guard').open('a+b') as owner:
            # Windows byte-range locks can cover an empty file. Another first
            # writer must wait for ownership before touching the locked range.
            original_locking(owner.fileno(), msvcrt.LK_NBLCK, 1)
            with patch('msvcrt.locking', observe_attempt), ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(acquire)
                future.add_done_callback(lambda _: attempted.set())
                try:
                    self.assertTrue(attempted.wait(3), 'configuration lock never attempted acquisition')
                finally:
                    owner.seek(0)
                    original_locking(owner.fileno(), msvcrt.LK_UNLCK, 1)
                self.assertEqual(future.result(timeout=3), 'acquired')


if __name__ == '__main__':
    unittest.main()
