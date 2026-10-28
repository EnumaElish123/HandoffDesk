import sys
import concurrent.futures
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from handoffdesk.service import Service


class NodeFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='node-files-', dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.app = self.base / 'app'
        self.root = self.base / '项目 甲'
        self.external = self.base / '外部材料'
        self.other_root = self.base / '项目 乙'
        for folder in (self.root, self.external, self.other_root, self.app / 'config'):
            folder.mkdir(parents=True)
        self.state = self.root / '进度 状态.json'
        self.state.write_text('{"status":"first"}', encoding='utf-8')
        self.guide = self.root / '结果, 第二版.md'
        self.guide.write_text('说明', encoding='utf-8')
        self.script = self.root / '运行 实验.py'
        self.script.write_text('raise RuntimeError("must never run")', encoding='utf-8')
        self.weight = self.root / '模型.pth'
        self.weight.write_bytes(b'model')
        (self.root / '材料目录').mkdir()
        (self.other_root / '记录.txt').write_text('other', encoding='utf-8')
        project = {'id':'alpha', 'name':'甲', 'roots':{'main':str(self.root)},
                   'sources':[{'id':'alpha.state','path':self.state.name,'format':'json','fields':{'status':'/status'}},
                              {'id':'alpha.guide','path':self.guide.name,'format':'text','label':'结果说明'}],
                   'entries':[{'id':'guide','type':'open_source','source_id':'alpha.guide','label':'结果说明'},
                              {'id':'folder','type':'open_folder','path':'材料目录'},
                              {'id':'web','type':'url','url':'https://example.org'}],
                   'worklines':[{'id':'experiment','name':'独立实验','entry_ids':[]}]}
        other = {'id':'beta','name':'乙','roots':{'main':str(self.other_root)},
                 'sources':[{'id':'beta.state','path':'记录.txt','format':'text'}], 'entries':[]}
        self.config = {'schema_version':1,'projects':[project,other]}
        self.config_path = self.app / 'config/projects.json'
        self.config_path.write_text(json.dumps(self.config, ensure_ascii=False), encoding='utf-8')
        self.service = Service(self.app)

    def paths(self, node='current:default', project='alpha'):
        return {item['path'] for item in self.service.get_node_files(project,node)['files']}

    def payload(self, snapshot=None, **updates):
        result = {'last_done':'完成数据核对','next_action':'继续实验','current_task':'首轮结果',
                  'source_snapshot_id':snapshot,'origin':'user_directed'}
        result.update(updates)
        return result

    def external_file(self, name='实验结果.txt'):
        path = self.external / name
        path.write_text(name, encoding='utf-8')
        return path

    def selected(self, path, node='current:default'):
        return next(item for item in self.service.get_node_files('alpha',node)['files'] if item['path'] == str(path))

    def test_current_lists_registered_sources_and_entries_before_refresh_and_excludes_url(self):
        values = self.service.get_node_files('alpha','current:default')
        self.assertEqual(self.paths(), {str(self.state),str(self.guide),str(self.root/'材料目录')})
        self.assertEqual(len(values['files']),3)
        self.assertTrue(all(item['exists'] for item in values['files']))
        self.assertEqual(self.selected(self.guide)['purpose'],'结果说明')

    def test_node_list_all_worklines_and_explicit_filter(self):
        self.service.select_workline('alpha','experiment')
        nodes = self.service.list_file_nodes('alpha')
        self.assertEqual({node['workline_id'] for node in nodes},{'default','experiment'})
        self.assertEqual({node['id'] for node in self.service.list_file_nodes('alpha','experiment')},{'current:experiment'})
        with self.assertRaises(ValueError):
            self.service.list_file_nodes('alpha','missing')

    def test_add_many_files_external_folder_and_deduplicate(self):
        paths = [self.external_file(f'文件 {i}.txt') for i in range(8)] + [self.external]
        result = self.service.add_node_files('alpha','current:default',paths,purpose='本轮产物')
        self.assertEqual(len(result['files']),12)
        self.assertTrue(all(item['origin']=='manual' and item['purpose']=='本轮产物'
                            for item in result['files'] if item['path'] in map(str,paths)))
        result = self.service.add_node_files('alpha','current:default',[paths[0],paths[0]])
        self.assertEqual(len(result['files']),12)
        self.assertEqual(self.selected(self.external)['kind'],'文件夹')

    def test_associations_project_and_workline_isolation(self):
        added = self.external_file()
        self.service.add_node_files('alpha','current:experiment',[added])
        self.assertNotIn(str(added),self.paths())
        self.assertIn(str(added),self.paths('current:experiment'))
        self.assertNotIn(str(added),self.paths(project='beta'))
        node = self.service.create_file_node('alpha','实验节点',workline_id='experiment')
        with self.assertRaises(ValueError):
            self.service.get_node_files('beta',node['id'])
        foreign_id = self.selected(added,'current:experiment')['id']
        with self.assertRaises(ValueError):
            self.service.open_node_file('alpha','current:default',foreign_id,'copy')

    def test_file_ids_stable_across_refresh_and_distinct_between_nodes(self):
        initial = self.selected(self.guide)['id']
        self.service.refresh('alpha')
        self.assertEqual(self.selected(self.guide)['id'],initial)
        self.assertNotEqual(self.selected(self.guide,'current:experiment')['id'],initial)

    def test_remove_auto_hides_persistently_without_deleting_file(self):
        identity = self.selected(self.guide)['id']
        self.service.remove_node_file('alpha','current:default',identity)
        self.service.refresh('alpha')
        self.service = Service(self.app)
        self.assertNotIn(str(self.guide),self.paths())
        self.assertTrue(self.guide.is_file())
        self.service.add_node_files('alpha','current:default',[self.guide])
        self.assertIn(str(self.guide),self.paths())

    def test_remove_manual_only_changes_association_and_unknown_file_is_rejected(self):
        added = self.external_file()
        self.service.add_node_files('alpha','current:default',[added])
        identity = self.selected(added)['id']
        self.service.remove_node_file('alpha','current:default',identity)
        self.assertTrue(added.is_file())
        self.assertNotIn(str(added),self.paths())
        with self.assertRaises(ValueError):
            self.service.remove_node_file('alpha','current:default',identity)

    def test_manual_nodes_freeze_current_and_empty_creation(self):
        added = self.external_file()
        node = self.service.create_file_node('alpha','第一阶段',description='已完成核对')
        self.assertEqual(self.paths(node['id']),self.paths())
        self.service.add_node_files('alpha','current:default',[added])
        self.assertNotIn(str(added),self.paths(node['id']))
        empty = self.service.create_file_node('alpha','空节点',include_current=False)
        self.assertEqual(self.paths(empty['id']),set())
        self.assertEqual(empty['file_count'],0)

    def test_handoff_and_plan_freeze_current_associations(self):
        sid = self.service.refresh('alpha')['snapshot']['id']
        added = self.external_file()
        self.service.add_node_files('alpha','current:default',[added])
        saved = self.service.save_handoff('alpha',self.payload(sid),0)
        original = self.paths('record:default:1')
        self.assertIn(str(added),original)
        self.assertEqual({item['path'] for item in saved['file_manifest']},original)
        self.service.remove_node_file('alpha','current:default',self.selected(added)['id'])
        self.service.refresh('alpha')
        self.service.save_plan('alpha',self.payload(sid,last_done=''),1)
        self.assertEqual(self.paths('record:default:1'),original)
        self.assertNotIn(str(added),self.paths('record:default:2'))
        nodes = self.service.list_file_nodes('alpha','default')
        self.assertEqual({node['kind'] for node in nodes},{'current','plan','handoff'})

    def test_older_bound_snapshot_does_not_use_new_config_or_newest_snapshot_files(self):
        sid = self.service.refresh('alpha')['snapshot']['id']
        changed = self.root / '新方案.md'
        changed.write_text('new',encoding='utf-8')
        self.config['projects'][0]['sources'][1]['path'] = changed.name
        self.config_path.write_text(json.dumps(self.config),encoding='utf-8')
        self.service = Service(self.app)
        self.service.refresh('alpha')
        self.service.save_handoff('alpha',self.payload(sid),0)
        self.assertIn(str(self.guide),self.paths('record:default:1'))
        self.assertNotIn(str(changed),self.paths('record:default:1'))

    def test_legacy_history_uses_only_original_snapshot_and_reports_missing_provenance(self):
        sid = self.service.refresh('alpha')['snapshot']['id']
        self.service.store.save_handoff('alpha',self.payload(sid),0)
        added = self.external_file()
        self.service.add_node_files('alpha','current:default',[added])
        self.assertNotIn(str(added),self.paths('record:default:1'))
        self.assertIn('旧记录',self.service.get_node_files('alpha','record:default:1')['notice'])
        self.service.store.save_handoff('alpha',self.payload(None),1)
        self.assertEqual(self.paths('record:default:2'),set())
        self.assertIn('没有文件清单',self.service.get_node_files('alpha','record:default:2')['notice'])

    def test_retry_same_operation_after_file_changes_keeps_first_manifest(self):
        first = self.service.save_handoff('alpha',self.payload(),0,'same-operation')
        self.service.add_node_files('alpha','current:default',[self.external_file()])
        repeated = self.service.save_handoff('alpha',self.payload(),0,'same-operation')
        self.assertEqual(first,repeated)
        with self.assertRaises(ValueError):
            self.service.save_handoff('alpha',self.payload(next_action='不同内容'),1,'same-operation')

    def test_restore_carries_original_files_not_current_and_ignores_injected_manifest(self):
        first = self.service.save_handoff('alpha',self.payload(file_manifest=[{'path':'unsafe'}]),0)
        self.service.add_node_files('alpha','current:default',[self.external_file()])
        self.service.save_handoff('alpha',self.payload(next_action='第二步'),1)
        restored = self.service.restore_handoff('alpha',1,2)
        self.assertEqual(first['file_manifest'],restored['file_manifest'])
        self.assertEqual(self.paths('record:default:1'),self.paths('record:default:3'))

    @unittest.skipUnless(sys.platform == "win32", "Windows API assertion; Linux dispatch covered separately")
    def test_missing_file_keeps_path_and_reveals_nearest_existing_parent(self):
        folder = self.external / '临时子目录'
        folder.mkdir()
        added = folder / '移动了.txt'
        added.write_text('moved',encoding='utf-8')
        self.service.add_node_files('alpha','current:default',[added])
        identity = self.selected(added)['id']
        added.unlink()
        folder.rmdir()
        item = self.selected(added)
        self.assertFalse(item['exists'])
        self.assertEqual(item['status'],'文件已缺失')
        self.assertEqual(self.service.open_node_file('alpha','current:default',identity,'copy'),str(added))
        with self.assertRaises(ValueError):
            self.service.open_node_file('alpha','current:default',identity,'open')
        with patch('handoffdesk.node_files.os.startfile') as start:
            self.service.open_node_file('alpha','current:default',identity,'reveal')
        start.assert_called_once_with(str(self.external))

    @unittest.skipUnless(sys.platform == "win32", "Windows API assertion; Linux dispatch covered separately")
    def test_safe_open_document_script_weights_executable_and_folder(self):
        executable = self.external_file('软件.exe')
        self.service.add_node_files('alpha','current:default',[self.script,self.weight,executable,self.external])
        with patch('handoffdesk.node_files.os.startfile') as start, patch('handoffdesk.node_files.subprocess.Popen') as process:
            self.service.open_node_file('alpha','current:default',self.selected(self.guide)['id'])
            start.assert_called_once_with(str(self.guide))
            self.service.open_node_file('alpha','current:default',self.selected(self.script)['id'])
            args = process.call_args.args[0]
            self.assertEqual(Path(args[0]).name.lower(),'notepad.exe')
            self.assertEqual(args[1],str(self.script))
            self.assertFalse(process.call_args.kwargs['shell'])
            for path in (self.weight,executable):
                self.service.open_node_file('alpha','current:default',self.selected(path)['id'])
                self.assertIn(' /select,"' + str(path) + '"',process.call_args.args[0])
                self.assertFalse(process.call_args.kwargs['shell'])
            self.service.open_node_file('alpha','current:default',self.selected(self.external)['id'])
            self.assertEqual(start.call_args.args[0],str(self.external))

    @unittest.skipUnless(sys.platform == "win32", "Windows API assertion; Linux dispatch covered separately")
    def test_explorer_select_quotes_chinese_space_and_comma(self):
        with patch('handoffdesk.node_files.subprocess.Popen') as process:
            self.service.open_node_file('alpha','current:default',self.selected(self.guide)['id'],'reveal')
        command = process.call_args.args[0]
        self.assertIn(' /select,"' + str(self.guide) + '"',command)
        self.assertFalse(process.call_args.kwargs['shell'])

    def test_invalid_paths_are_rejected_atomically(self):
        good = self.external_file()
        invalid = ['relative.txt','https://example.com/file','file:///C:/file','C:relative.txt',
                   r'\\server\share\file',r'\\?\C:\file',r'\\.\NUL',r'C:\NUL.txt',
                   r'C:\CON .txt',r'C:\COM¹.txt',r'C:\con\file.txt',
                   r'C:\file.txt:stream',r'C:\bad"file.txt','C:\\bad\x00file']
        initial = self.paths()
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.service.add_node_files('alpha','current:default',[good,value])
            self.assertEqual(self.paths(),initial)
        with self.assertRaises(ValueError):
            self.service.add_node_files('alpha','current:default',[self.external/'missing.txt'])

    def test_action_and_node_input_types_are_validated(self):
        identity = self.selected(self.guide)['id']
        for action in ('delete','execute',None,[],{},1):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.service.open_node_file('alpha','current:default',identity,action)
        for node in (None,[],{},1,'missing:node'):
            with self.subTest(node=node), self.assertRaises(ValueError):
                self.service.get_node_files('alpha',node)
        for title in ('','a'*121,'bad\nname',None):
            with self.subTest(title=title), self.assertRaises(ValueError):
                self.service.create_file_node('alpha',title)

    def test_concurrent_association_additions_do_not_overwrite_each_other(self):
        files = [self.external_file(f'parallel {n}.txt') for n in range(10)]
        services = [Service(self.app) for _ in files]
        def add(pair):
            service, path = pair
            service.add_node_files('alpha','current:default',[path])
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(add,zip(services,files)))
        self.assertTrue(set(map(str,files)).issubset(self.paths()))

    def test_limit_is_500_links_and_add_is_atomic(self):
        node = self.service.create_file_node('alpha','全部文件',include_current=False)
        files = [self.external_file(f'item-{n}.txt') for n in range(501)]
        self.service.add_node_files('alpha',node['id'],files[:500])
        with self.assertRaises(ValueError):
            self.service.add_node_files('alpha',node['id'],files[500:])
        self.assertEqual(len(self.paths(node['id'])),500)

    def test_listing_and_link_management_never_write_project_files(self):
        before = {str(path):(path.read_bytes(),path.stat().st_mtime_ns) for path in self.root.rglob('*') if path.is_file()}
        self.service.refresh('alpha')
        self.service.list_file_nodes('alpha')
        node = self.service.create_file_node('alpha','只读检查')
        self.service.add_node_files('alpha',node['id'],[self.script,self.weight])
        self.service.remove_node_file('alpha',node['id'],self.selected(self.script,node['id'])['id'])
        self.service.save_handoff('alpha',self.payload(),0)
        after = {str(path):(path.read_bytes(),path.stat().st_mtime_ns) for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(before,after)

    def test_legacy_paths_preserve_recorded_location_if_resolve_changes(self):
        sid = self.service.refresh('alpha')['snapshot']['id']
        self.service.store.save_handoff('alpha',self.payload(sid),0)
        real_resolve = Path.resolve
        redirected = self.external_file('redirected.md')
        def resolve(path, *args, **kwargs):
            return redirected if path == self.guide else real_resolve(path,*args,**kwargs)
        with patch.object(Path,'resolve',resolve):
            result = self.service.get_node_files('alpha','record:default:1')
            guide = next(item for item in result['files'] if item['path']==str(self.guide))
            self.assertEqual(guide['status'],'位置不可访问')
            with self.assertRaises(ValueError):
                self.service.open_node_file('alpha','record:default:1',guide['id'],'reveal')
        self.assertNotIn(str(redirected),{item['path'] for item in result['files']})

    def test_auto_snapshot_files_outside_root_are_never_openable(self):
        foreign = self.external_file()
        generation = self.service.store.begin_refresh('alpha')
        sid = self.service.store.finish_refresh('alpha',generation,{'observations':[],
                    'entries':[{'id':'bad','type':'file','target':str(foreign)}]})
        self.assertNotIn(str(foreign),self.paths())
        self.service.store.save_handoff('alpha',self.payload(sid),0)
        item = self.selected(foreign,'record:default:1')
        self.assertEqual(item['status'],'位置不可访问')
        with self.assertRaises(ValueError):
            self.service.open_node_file('alpha','record:default:1',item['id'],'open')

    def test_migration_keeps_old_rows_byte_for_byte(self):
        sid = self.service.refresh('alpha')['snapshot']['id']
        self.service.store.save_handoff('alpha',self.payload(sid),0)
        db_path = self.app / 'data/cards.sqlite3'
        with closing(sqlite3.connect(db_path)) as db:
            before = db.execute('SELECT payload FROM handoffs').fetchall(),db.execute('SELECT payload FROM snapshots').fetchall()
            db.execute('DROP TABLE file_nodes')
            db.execute('DROP TABLE node_file_state')
            db.execute('PRAGMA user_version=3')
            db.commit()
        self.service = Service(self.app)
        with closing(sqlite3.connect(db_path)) as db:
            after = db.execute('SELECT payload FROM handoffs').fetchall(),db.execute('SELECT payload FROM snapshots').fetchall()
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],5)
        self.assertEqual(before,after)


if __name__ == '__main__':
    unittest.main()
