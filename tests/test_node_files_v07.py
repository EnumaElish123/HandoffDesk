"""Node-link corrections stay local, preserve history, and merge concurrent edits."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from handoffdesk.service import Service


class NodeFileCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='node-correction-', dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / '原始项目'
        self.other = self.base / '另一个项目'
        self.external = self.base / '手动关联的资料'
        self.app = self.base / 'app'
        for folder in (self.source,self.other,self.external,self.app/'config'):
            folder.mkdir(parents=True)
        self.guide = self.source / '原始说明.md'
        self.guide.write_text('必须保留的原始说明。',encoding='utf-8')
        (self.other/'说明.txt').write_text('另一个项目',encoding='utf-8')
        config = {'schema_version':1,'projects':[
            {'id':'alpha','name':'项目甲','roots':{'main':str(self.source)},
             'sources':[{'id':'guide','path':self.guide.name,'format':'text'}],
             'entries':[{'id':'guide','type':'open_source','source_id':'guide','label':'原始用途'}],
             'worklines':[{'id':'audit','name':'独立审计'}]},
            {'id':'beta','name':'项目乙','roots':{'main':str(self.other)},
             'sources':[{'id':'guide','path':'说明.txt','format':'text'}], 'entries':[]}]}
        (self.app/'config/projects.json').write_text(json.dumps(config),encoding='utf-8')
        self.service = Service(self.app)
        self.originals = self.source_state()

    def tearDown(self):
        self.assertEqual(self.originals,self.source_state(),'Managing links changed original project files')

    def source_state(self):
        return {str(path):(hashlib.sha256(path.read_bytes()).hexdigest(),path.stat().st_mtime_ns)
                for folder in (self.source,self.other) for path in folder.rglob('*') if path.is_file()}

    def attachment(self,name='待复核材料.txt'):
        path = self.external/name
        path.write_text('只用于本次隔离测试。',encoding='utf-8')
        return path

    def files(self,node='current:default'):
        return self.service.get_node_files('alpha',node)['files']

    def item(self,path,node='current:default'):
        return next(item for item in self.files(node) if item['path']==str(path))

    def save(self):
        return self.service.save_plan('alpha',{'next_action':'继续复核','origin':'user_directed'},
                                      len(self.service.history('alpha')))

    def test_duplicate_picker_selection_preserves_notes_and_explicit_edit_can_clear(self):
        path = self.attachment()
        self.service.add_node_files('alpha','current:default',[path],purpose='已经写好的用途')
        self.service.add_node_files('alpha','current:default',[path,path],purpose='  ')
        self.assertEqual(self.item(path)['purpose'],'已经写好的用途')
        original = self.item(self.guide)
        self.service.add_node_files('alpha','current:default',[self.guide])
        self.assertEqual(self.item(self.guide)['purpose'],original['purpose'])
        self.assertEqual(self.item(self.guide)['origin'],original['origin'])
        self.service.update_node_file('alpha','current:default',self.item(path)['id'],purpose=' ')
        self.assertEqual(self.item(path)['purpose'],'')

    def test_purpose_correction_keeps_provenance_other_nodes_and_frozen_manifest(self):
        saved = self.save()
        original = self.item(self.guide,'record:default:1')
        self.service.update_node_file('alpha','record:default:1',original['id'],purpose=' 补充的阶段用途 ')
        changed = self.item(self.guide,'record:default:1')
        self.assertEqual(changed['purpose'],'补充的阶段用途')
        self.assertEqual(changed['origin'],original['origin'])
        self.assertEqual(changed['id'],original['id'])
        self.assertEqual(self.item(self.guide)['purpose'],'原始用途')
        self.assertEqual(self.service.history('alpha')[0],saved)
        self.assertEqual(self.service.store.file_nodes('alpha'),[])

    def test_relocating_missing_history_link_preserves_note_and_original_record(self):
        old = self.attachment()
        self.service.add_node_files('alpha','current:default',[old],purpose='本阶段的结果')
        saved = self.save()
        old_id = self.item(old,'record:default:1')['id']
        old.unlink()  # Disposable fixture only, never a registered business source.
        new = self.attachment('报告, 新位置.txt')
        result = self.service.relocate_node_file('alpha','record:default:1',old_id,new)
        self.assertNotIn(str(old),{item['path'] for item in result['files']})
        changed = self.item(new,'record:default:1')
        self.assertEqual(changed['purpose'],'本阶段的结果')
        self.assertEqual(changed['relinked_from'],str(old))
        self.assertEqual(changed['origin'],'manual')
        self.assertNotEqual(changed['id'],old_id)
        self.assertTrue(changed['exists'])
        self.assertFalse(self.item(old)['exists'])
        self.assertEqual(self.service.history('alpha')[0],saved)
        self.assertTrue(new.is_file())

    def test_relocation_rejects_existing_target_and_invalid_paths_without_partial_changes(self):
        first,second = self.attachment('第一份.txt'),self.attachment('第二份.txt')
        self.service.add_node_files('alpha','current:default',[first,second],purpose='保留说明')
        identity = self.item(first)['id']
        before = self.service.get_node_files('alpha','current:default')
        for target in (second,'relative.txt','https://example.org/file',self.external/'不存在.txt'):
            with self.subTest(target=target),self.assertRaises(ValueError):
                self.service.relocate_node_file('alpha','current:default',identity,target)
            self.assertEqual(self.service.get_node_files('alpha','current:default'),before)
        self.assertEqual(self.service.relocate_node_file('alpha','current:default',identity,first),before)

    def test_missing_folder_retains_kind_and_relocation_accepts_directory(self):
        old = self.external/'旧成果目录'
        old.mkdir()
        self.service.add_node_files('alpha','current:default',[old],purpose='本轮产物目录')
        saved = self.service.create_file_node('alpha','成果节点')
        old.rmdir()  # Empty disposable directory only.
        item = self.item(old,saved['id'])
        self.assertEqual(item['kind'],'文件夹')
        self.assertFalse(item['exists'])
        new = self.external/'新成果目录'
        new.mkdir()
        self.service.relocate_node_file('alpha',saved['id'],item['id'],new)
        self.assertEqual(self.item(new,saved['id'])['kind'],'文件夹')
        self.assertEqual(self.item(new,saved['id'])['purpose'],'本轮产物目录')

    def test_manual_rename_retains_files_description_scope_and_survives_reopen(self):
        node = self.service.create_file_node('alpha',' 初步节点 ',workline_id='audit',description='原说明')
        file = self.attachment()
        self.service.add_node_files('alpha',node['id'],[file],purpose='补充文件')
        before = self.files(node['id'])
        changed = self.service.rename_file_node('alpha',node['id'],' 最终复核节点 ')
        self.assertEqual(changed['title'],'最终复核节点')
        self.assertEqual(changed['description'],'原说明')
        self.assertEqual(changed['workline_id'],'audit')
        self.assertEqual(self.files(node['id']),before)
        changed = self.service.rename_file_node('alpha',node['id'],'最终复核节点',description=' 新说明 ')
        self.assertEqual(changed['description'],'新说明')
        self.service = Service(self.app)
        self.assertEqual(self.service.get_node_files('alpha',node['id'])['node']['title'],'最终复核节点')
        self.assertEqual(self.files(node['id']),before)

    def test_validation_and_cross_scope_errors_leave_associations_and_metadata_unchanged(self):
        node = self.service.create_file_node('alpha','合法节点')
        before = self.service.get_node_files('alpha',node['id'])
        for title in ('','a'*121,'控制\n字符',None):
            with self.subTest(title=title),self.assertRaises(ValueError):
                self.service.rename_file_node('alpha',node['id'],title)
        for target in ('current:default','record:default:1'):
            with self.subTest(node=target),self.assertRaises(ValueError):
                self.service.rename_file_node('alpha',target,'不应修改')
        for purpose in (None,123,'a'*2001):
            with self.subTest(purpose=purpose),self.assertRaises(ValueError):
                self.service.update_node_file('alpha',node['id'],self.item(self.guide,node['id'])['id'],purpose)
        with self.assertRaises(ValueError):
            self.service.rename_file_node('beta',node['id'],'串到其他项目')
        with self.assertRaises(ValueError):
            self.service.relocate_node_file('alpha','current:audit',self.item(self.guide)['id'],self.guide)
        self.assertEqual(self.service.get_node_files('alpha',node['id']),before)

    def test_concurrent_note_edits_and_new_associations_merge_without_lost_files(self):
        files = [self.attachment(f'并行 {i}.txt') for i in range(6)]
        self.service.add_node_files('alpha','current:default',files[:3],purpose='初始用途')
        identities = [self.item(path)['id'] for path in files[:3]]
        services = [Service(self.app) for _ in files]
        def change(index):
            if index<3:
                services[index].update_node_file('alpha','current:default',identities[index],f'更新用途 {index}')
            else:
                services[index].add_node_files('alpha','current:default',[files[index]],purpose=f'新关联 {index}')
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(change,range(6)))
        for index,path in enumerate(files):
            self.assertEqual(self.item(path)['purpose'],f'更新用途 {index}' if index<3 else f'新关联 {index}')

    def test_stale_selection_cannot_recreate_link_after_another_window_relocates(self):
        first,second,third = [self.attachment(name) for name in ('旧位置.txt','新位置.txt','另一个位置.txt')]
        self.service.add_node_files('alpha','current:default',[first],purpose='需要保留')
        old_id = self.item(first)['id']
        other = Service(self.app)
        self.service.relocate_node_file('alpha','current:default',old_id,second)
        before = self.service.get_node_files('alpha','current:default')
        for action in (lambda: other.update_node_file('alpha','current:default',old_id,'过期说明'),
                       lambda: other.relocate_node_file('alpha','current:default',old_id,third)):
            with self.assertRaisesRegex(ValueError,'刷新'):
                action()
        self.assertEqual(self.service.get_node_files('alpha','current:default'),before)

    def test_note_edit_does_not_turn_automatic_source_into_unrestricted_manual_link(self):
        self.save()
        identity = self.item(self.guide,'record:default:1')['id']
        self.service.update_node_file('alpha','record:default:1',identity,purpose='修改了用途')
        redirected = self.attachment('外部目标.md')
        original_resolve = Path.resolve
        def resolve(path,*args,**kwargs):
            return redirected if path==self.guide else original_resolve(path,*args,**kwargs)
        with patch.object(Path,'resolve',resolve):
            selected = self.item(self.guide,'record:default:1')
            self.assertEqual(selected['status'],'位置不可访问')
            with self.assertRaises(ValueError):
                self.service.open_node_file('alpha','record:default:1',identity,'reveal')

    def test_many_progress_nodes_read_history_once_per_workline_and_keep_fresh_file_counts(self):
        for line in ('default','audit'):
            for revision in range(24):
                self.service.save_plan('alpha',{'next_action':f'{line} 的第 {revision+1} 步'},
                                       revision,workline_id=line)
        custom = self.service.create_file_node('alpha','阶段材料',workline_id='audit',include_current=False)
        added = self.attachment()
        with patch.object(self.service.store,'history',wraps=self.service.store.history) as history:
            nodes = self.service.list_file_nodes('alpha')
        self.assertEqual(history.call_count,2)
        self.assertEqual({call.args[0] for call in history.call_args_list},{'alpha','alpha\x1faudit'})
        self.assertEqual(len(nodes),51)
        self.assertEqual(sum(node['kind']=='plan' for node in nodes),48)
        self.assertTrue(all('file_manifest' not in node and not any(key.startswith('_') for key in node) for node in nodes))
        before = next(node for node in nodes if node['id']==custom['id'])
        self.assertEqual(before['file_count'],0)
        self.service.add_node_files('alpha',custom['id'],[added],purpose='新增的阶段证据')
        with patch.object(self.service.store,'history',wraps=self.service.store.history) as history:
            changed = self.service.list_file_nodes('alpha')
        self.assertEqual(history.call_count,2)
        self.assertEqual(next(node for node in changed if node['id']==custom['id'])['file_count'],1)
        for node in changed:
            if node['kind']=='plan':
                self.assertEqual(node['file_count'],1)
                self.assertEqual(self.files(node['id'])[0]['path'],str(self.guide))


if __name__ == '__main__':
    unittest.main()
