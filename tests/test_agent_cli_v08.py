"""Public agent commands run in separate processes without desktop interaction."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_cli_v03 as previous


class AgentCLITests(unittest.TestCase):
    setUp = previous.V03CLIIntegrationTests.setUp

    def command(self, *args, stdin=None):
        return subprocess.run([sys.executable, '-E', '-s', '-B', '-X', 'utf8', '-m', 'handoffdesk',
                               '--app-dir', str(self.app), '--data-dir', str(self.folder / 'data'), *args],
                              cwd=self.package, input=stdin, capture_output=True, text=True,
                              encoding='utf-8', timeout=15)

    def result(self, *args, stdin=None, code=0):
        result = self.command(*args, stdin=stdin)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '')
        return json.loads(result.stdout)

    def payload(self):
        return {'schema_version': 1, 'project': 'demo', 'workline': 'alpha',
                'operation_id': 'cli-stage-1', 'expected_revision': 0,
                'record': {'title': '阶段复核', 'last_done': '核对两份文件', 'next_action': '查看核对结果'},
                'actor': {'name': 'Codex', 'session_id': 'isolated-test'},
                'verification': {'status': 'partial', 'summary': '仅检查试用文件，不代表项目结论'},
                'files': [{'path': str(self.state), 'purpose': '运行状态', 'role': 'evidence'},
                          {'path': str(self.model), 'purpose': '待核对的模型', 'role': 'output'}]}

    def test_schema_does_not_require_configuration_database_or_tk(self):
        (self.app / 'config/projects.json').write_text('{}', encoding='utf-8')
        value = self.result('record', 'schema')
        self.assertEqual(value['properties']['schema_version']['const'], 1)
        self.assertFalse((self.folder / 'data').exists())

    def test_headless_validate_save_retry_and_node_file_readback(self):
        data = json.dumps(self.payload(), ensure_ascii=False)
        self.assertTrue(self.result('record', 'validate', '--stdin', stdin=data)['valid'])
        context = self.result('record', 'context', 'demo', '--workline', 'alpha')
        self.assertEqual(context['current_revision'], 0)
        saved = self.result('record', 'save', '--stdin', stdin=data)
        self.assertEqual((saved['revision'], saved['replayed'], saved['file_count']), (1, False, 2))
        retry = self.result('record', 'save', '--stdin', stdin=data)
        self.assertTrue(retry['replayed'])
        self.assertEqual(saved['record_id'], retry['record_id'])
        nodes = self.result('nodes', 'demo', '--workline', 'alpha')['nodes']
        self.assertIn(saved['node_id'], {node['id'] for node in nodes})
        files = self.result('files', 'demo', '--node', saved['node_id'])['files']
        self.assertEqual({file['purpose'] for file in files}, {'运行状态', '待核对的模型'})
        self.assertEqual(self.result('record', 'context', 'demo', '--workline', 'alpha')['current_revision'], 1)
        self.assertEqual(self.result('record', 'context', 'demo', '--workline', 'beta')['current_revision'], 0)

    def test_file_input_unknown_fields_conflict_and_malformed_input_are_json(self):
        payload = self.payload()
        path = self.folder / '提交 中文 空格.json'
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8-sig')
        self.result('record', 'save', '--file', str(path))
        stale = copy.deepcopy(payload)
        stale['operation_id'] = 'new-operation'
        failure = self.result('record', 'save', '--stdin', stdin=json.dumps(stale), code=3)
        self.assertEqual(failure['error']['code'], 'revision_conflict')
        unknown = dict(payload, unsupported=True)
        self.assertEqual(self.result('record', 'validate', '--stdin', stdin=json.dumps(unknown), code=2)['error']['code'], 'invalid_input')
        self.assertEqual(self.result('record', 'save', '--stdin', stdin='{broken', code=2)['error']['code'], 'invalid_input')
        self.assertEqual(self.result('record', 'context', 'demo', code=2)['error']['code'], 'invalid_arguments')

    def test_roots_setup_is_validated_before_replacement_and_does_not_create_data(self):
        original = (self.app / 'config/projects.json').read_bytes()
        shown = self.result('configure-roots', '--show')
        self.assertEqual(shown['projects']['demo']['main'], str(self.source))
        moved = self.folder / '另一台机器 中文路径'
        moved.mkdir()
        path = self.folder / 'roots.json'
        path.write_text(json.dumps({'schema_version': 1, 'projects': {'demo': {'main': str(moved)}}}), encoding='utf-8')
        self.assertTrue(self.result('configure-roots', '--file', str(path))['saved'])
        previous_mapping = (self.app / 'config/roots.local.json').read_bytes()
        path.write_text(json.dumps({'schema_version': 1, 'projects': {'unknown': {'main': str(moved)}}}), encoding='utf-8')
        self.result('configure-roots', '--file', str(path), code=2)
        self.assertEqual((self.app / 'config/roots.local.json').read_bytes(), previous_mapping)
        self.assertEqual((self.app / 'config/projects.json').read_bytes(), original)
        self.assertFalse((self.folder / 'data').exists())

    def test_bad_attachment_never_creates_partial_record(self):
        payload = self.payload()
        payload['files'].append({'path': str(self.source / 'missing.txt'), 'purpose': '不存在'})
        self.result('record', 'save', '--stdin', stdin=json.dumps(payload), code=2)
        self.assertEqual(self.result('record', 'context', 'demo', '--workline', 'alpha')['current_revision'], 0)

    def test_restore_keeps_agent_attribution_and_original_manifest(self):
        saved = self.result('record', 'save', '--stdin', stdin=json.dumps(self.payload()))
        restored = self.result('restore', 'demo', '--workline', 'alpha', '--revision', '1', '--expect-revision', '1')
        self.assertEqual(restored['revision'], 2)
        for field in ('actor', 'verification', 'file_manifest'):
            self.assertEqual(restored[field], saved['record'][field])
        self.assertEqual(restored['restored_from'], 1)

    def test_deep_json_returns_structured_error_without_traceback(self):
        deeply_nested = '{"value":' + '[' * 10000 + '0' + ']' * 10000 + '}'
        error = self.result('record', 'save', '--stdin', stdin=deeply_nested, code=2)
        self.assertEqual(error['error']['code'], 'invalid_input')

    def test_mapping_final_serialized_size_is_rejected_before_saving(self):
        root = str(Path(self.source.anchor) / 'example-project')
        mapping = None
        for count in range(3000, 11000, 250):
            candidate = {'schema_version': 1, 'projects': {'demo': {f'k{i:04d}': root for i in range(count)}}}
            compact = len(json.dumps(candidate, ensure_ascii=False, sort_keys=True).encode('utf-8'))
            pretty = len((json.dumps(candidate, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
            if compact <= 262144 < pretty:
                mapping = candidate
                break
        self.assertIsNotNone(mapping)
        config_path = self.app / 'config/projects.json'
        configuration = json.loads(config_path.read_text('utf-8'))
        configuration['projects'][0]['roots'].update(mapping['projects']['demo'])
        config_path.write_text(json.dumps(configuration), encoding='utf-8')
        path = self.folder / 'large-roots.json'
        path.write_text(json.dumps(mapping), encoding='utf-8')
        result = self.result('configure-roots', '--file', str(path), code=2)
        self.assertIn('256 KiB', result['error']['message'])
        self.assertFalse((self.app / 'config/roots.local.json').exists())


if __name__ == '__main__':
    unittest.main()
