"""Existing configurations must preserve the same read-only project boundary."""
from itertools import product
import json
import os
from pathlib import Path
import tempfile
import unittest

from handoffdesk.service import Service


class ServicePathBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.counter = 0
        self.spellings = (False, True) if os.name == 'nt' else (False,)

    @staticmethod
    def form(path, extended):
        if path is None or not extended:
            return path
        name = str(path)
        return Path('\\\\?\\UNC\\' + name[2:] if name.startswith('\\\\')
                    else '\\\\?\\' + name)

    @staticmethod
    def snapshot(folder):
        return {str(path.relative_to(folder)): path.read_bytes() if path.is_file() else None
                for path in folder.rglob('*')}

    def fixture(self, layout, app_extended=False, root_extended=False, data_extended=False):
        self.counter += 1
        base = self.base / str(self.counter)
        app, source, data = base / 'state 中文', base / 'project 中文', None
        if layout == 'state_inside_project':
            app = source / 'state'
        elif layout == 'state_inside_project_with_external_data':
            app, data = source / 'state', base / 'external-data'
        elif layout == 'data_inside_project':
            data = source / 'data'
        elif layout == 'data_equals_project':
            data = source
        elif layout == 'project_inside_data':
            data = base / 'storage'
            source = data / 'project'
        elif layout == 'project_inside_config':
            source = app / 'config/project'
        elif layout == 'independent_data':
            data = base / 'storage'
        elif layout == 'demo':
            source = app / 'examples/demo-project'
        elif layout != 'independent_default':
            raise AssertionError('Unknown fixture layout')
        source.mkdir(parents=True)
        (source / 'README.md').write_text('Project contents must remain unchanged.\n', encoding='utf-8')
        (app / 'config').mkdir(parents=True, exist_ok=True)
        config = {'schema_version': 1, 'projects': [
            {'id': 'sample', 'name': 'Sample', 'roots': {'main': str(self.form(source, root_extended))},
             'sources': [], 'entries': []}]}
        (app / 'config/projects.json').write_text(json.dumps(config), encoding='utf-8')
        return (base, source, self.form(app, app_extended), self.form(data, data_extended))

    def test_existing_config_rejects_state_inside_project_even_with_external_data(self):
        for layout in ('state_inside_project', 'state_inside_project_with_external_data'):
            for flags in product(self.spellings, repeat=3):
                with self.subTest(layout=layout, forms=flags):
                    base, _, app, data = self.fixture(layout, *flags)
                    before = self.snapshot(base)
                    with self.assertRaises(ValueError):
                        Service(app, data)
                    self.assertEqual(self.snapshot(base), before)

    def test_existing_config_rejects_overlapping_data_or_config(self):
        for layout in ('data_inside_project', 'data_equals_project',
                       'project_inside_data', 'project_inside_config'):
            for flags in product(self.spellings, repeat=3):
                with self.subTest(layout=layout, forms=flags):
                    base, _, app, data = self.fixture(layout, *flags)
                    before = self.snapshot(base)
                    with self.assertRaises(ValueError):
                        Service(app, data)
                    self.assertEqual(self.snapshot(base), before)

    def test_independent_state_and_data_accept_all_path_forms(self):
        for layout in ('independent_default', 'independent_data'):
            for flags in product(self.spellings, repeat=3):
                with self.subTest(layout=layout, forms=flags):
                    _, source, app, data = self.fixture(layout, *flags)
                    before = self.snapshot(source)
                    service = Service(app, data)
                    service.store.save_preferences({'fixture': 'saved'})
                    self.assertEqual(Service(app, data).store.preferences(), {'fixture': 'saved'})
                    self.assertEqual(self.snapshot(source), before)

    def test_demo_project_under_state_examples_remains_supported(self):
        for flags in product(self.spellings, repeat=2):
            with self.subTest(forms=flags):
                _, source, app, data = self.fixture('demo', *flags)
                before = self.snapshot(source)
                service = Service(app, data)
                self.assertEqual(service.list_projects()[0]['id'], 'sample')
                self.assertEqual(self.snapshot(source), before)

    def test_write_path_accepts_contained_and_rejects_outside_across_path_forms(self):
        for flags in product(self.spellings, repeat=3):
            with self.subTest(forms=flags):
                base, source, app, data = self.fixture('independent_data', *flags)
                service = Service(app, data)
                stored = base / 'storage/note.txt'
                stored.write_text('Local state', encoding='utf-8')
                before = self.snapshot(source)
                for extended in self.spellings:
                    target = service._write_path(self.form(stored, extended))
                    self.assertEqual(target.read_text(encoding='utf-8'), 'Local state')
                    outside = self.form(source / 'forbidden.txt', extended)
                    with self.assertRaisesRegex(ValueError, '数据目录之外'):
                        service._write_path(outside)
                with self.assertRaisesRegex(ValueError, '数据目录之外'):
                    service._write_path('../escape.txt')
                self.assertEqual(self.snapshot(source), before)
                self.assertFalse((base / 'escape.txt').exists())


if __name__ == '__main__':
    unittest.main()
