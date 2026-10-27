"""Workflow regressions: independent contexts, explicit review, safe material actions."""
import sys
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from handoffdesk.service import Service, ConflictError


class V03ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='v03-core-', dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base/'business'
        self.source.mkdir()
        self.app = self.base/'app'
        (self.app/'config').mkdir(parents=True)
        self.file = self.source/'state.json'
        self.file.write_text('{"status":"initial"}', encoding='utf-8')
        (self.source/'guide.md').write_text('guide', encoding='utf-8')
        self.project = {'id':'test','name':'测试','roots':{'main':str(self.source)},
            'sources':[{'id':'state','root':'main','path':'state.json','format':'json','fields':{'status':'/status'}},
                       {'id':'guide','root':'main','path':'guide.md','format':'text'}],
            'entries':[{'id':'state','type':'open_source','source_id':'state'},
                       {'id':'guide','type':'open_source','source_id':'guide'}],
            'worklines':[{'id':'audit','name':'证据审计','entry_ids':['state']},
                         {'id':'analysis','name':'后续分析','entry_ids':['guide']}]}
        self.config = {'schema_version':1,'projects':[self.project]}
        self.write_config()
        self.service = Service(self.app)

    def write_config(self):
        (self.app/'config/projects.json').write_text(json.dumps(self.config), encoding='utf-8')

    def payload(self, sid=None, **changes):
        result = {'current_task':'测试','last_done':'实际完成','next_action':'检查材料','blockers':[],
                  'origin':'user_directed','source_snapshot_id':sid}
        result.update(changes)
        return result

    def draft(self, sid=None, kind='plan', **changes):
        result = self.payload(sid, draft_id=str(uuid.uuid4()),base_revision=0,event_kind=kind)
        result.update(changes)
        return result

    def test_worklines_isolate_snapshots_history_and_active_selection(self):
        default = self.service.refresh('test')['snapshot']
        old = self.service.save_plan('test', self.payload(default['id'],next_action='旧工作'),0)
        self.service.select_workline('test','audit')
        first = self.service.refresh('test')['snapshot']
        self.assertIsNone(self.service.show('test')['handoff'])
        a = self.service.save_plan('test',self.payload(first['id'],next_action='审计工作'),0)
        self.service.select_workline('test','analysis')
        second = self.service.refresh('test')['snapshot']
        self.assertNotEqual(first['id'],second['id'])
        self.assertEqual(self.service.history('test'),[])
        self.assertIsNone(self.service.show('test')['handoff'])
        self.assertEqual(self.service.show('test','default')['handoff'],old)
        self.assertEqual(self.service.show('test','audit')['handoff'],a)
        self.assertEqual(Service(self.app).show('test')['workline']['id'],'analysis')

    def test_explicit_editor_scope_survives_another_window_switch(self):
        first = self.service.refresh('test','audit')['snapshot']
        self.service.select_workline('test','analysis')
        saved = self.service.save_plan('test', self.payload(first['id']),0,workline_id='audit')
        self.assertEqual(saved['workline_id'],'audit')
        self.assertIsNone(self.service.show('test')['handoff'])
        self.assertEqual(self.service.show('test','audit')['handoff'],saved)

    def test_snapshot_from_another_workline_is_rejected(self):
        sid = self.service.refresh('test','audit')['snapshot']['id']
        for method, args in [(self.service.save_plan,(self.payload(sid),0)),
                             (self.service.save_draft,(self.draft(sid),0))]:
            with self.assertRaises(ValueError):
                method('test',*args,workline_id='analysis')

    def test_refresh_started_in_one_line_finishes_in_that_line(self):
        entered, release = threading.Event(), threading.Event()
        original = __import__('handoffdesk.service',fromlist=['collect_project']).collect_project
        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test timeout')
            return original(*args,**kwargs)
        errors = []
        def worker():
            try:
                self.service.refresh('test','audit')
            except Exception as error:
                errors.append(error)
        with patch('handoffdesk.service.collect_project',side_effect=delayed):
            thread = threading.Thread(target=worker)
            thread.start()
            self.assertTrue(entered.wait(5))
            self.service.select_workline('test','analysis')
            release.set()
            thread.join(5)
        self.assertFalse(errors)
        self.assertIsNone(self.service.show('test')['snapshot'])
        self.assertIsNotNone(self.service.show('test','audit')['snapshot'])

    def test_plan_and_handoff_drafts_are_independent_and_delete_is_specific(self):
        plan = self.service.save_draft('test', self.draft(),0,workline_id='audit')
        handoff = self.service.save_draft('test',self.draft(kind='handoff'),0,workline_id='audit')
        other = self.service.save_draft('test',self.draft(),0,workline_id='analysis')
        self.assertEqual(self.service.show('test','audit')['drafts'],{'plan':plan,'handoff':handoff})
        self.assertTrue(self.service.delete_draft('test',plan['draft_id'],plan['revision'],workline_id='audit'))
        self.assertEqual(self.service.load_draft('test',workline_id='audit'),handoff)
        self.assertEqual(self.service.load_draft('test','plan','analysis'),other)

    def test_imported_draft_preserves_assistant_origin_and_unbound_evidence(self):
        self.service.refresh('test','audit')
        saved = self.service.save_draft('test',self.draft(origin='assistant_suggested',entry_ids=[]),0,workline_id='audit')
        self.assertEqual(saved['origin'],'assistant_suggested')
        self.assertIsNone(saved['source_snapshot_id'])
        with self.assertRaises(ValueError):
            self.service.save_draft('test',self.draft(origin=[]),0)

    def test_material_binding_can_override_current_record_without_rewriting_it(self):
        sid = self.service.refresh('test','audit')['snapshot']['id']
        first = self.service.save_plan('test',self.payload(sid,entry_ids=['state']),0,workline_id='audit')
        self.service.set_workline_entries('test',['guide'],workline_id='audit')
        card = self.service.show('test','audit')
        self.assertEqual(card['bound_entry_ids'],['guide'])
        self.assertEqual(card['handoff'],first)
        self.service.save_plan('test',self.payload(sid,entry_ids=['state']),1,workline_id='audit')
        self.assertEqual(self.service.show('test','audit')['bound_entry_ids'],['state'])
        with self.assertRaises(ValueError):
            self.service.set_workline_entries('test',['state'],workline_id='analysis')

    def test_review_keeps_original_evidence_and_new_record_resets_baseline(self):
        first = self.service.refresh('test','audit')['snapshot']
        plan = self.service.save_plan('test',self.payload(first['id']),0,workline_id='audit')
        self.file.write_text('{"status":"changed"}', encoding='utf-8')
        changed = self.service.refresh('test','audit')
        self.assertTrue(changed['source_changes'])
        reviewed = self.service.acknowledge_changes('test',1,changed['snapshot']['id'],workline_id='audit')
        card = self.service.show('test','audit')
        self.assertEqual(card['review'],reviewed)
        self.assertEqual(card['handoff'],plan)
        self.assertEqual(card['source_changes'],[])
        self.assertEqual(card['change_comparison'],'review')
        self.service.save_plan('test',self.payload(first['id']),1,workline_id='audit')
        card = self.service.show('test','audit')
        self.assertIsNone(card['review'])
        self.assertTrue(card['source_changes'])
        self.assertEqual(len(self.service.review_history('test','audit')),1)

    def test_explicit_empty_material_binding_does_not_restore_preset(self):
        sid = self.service.refresh('test','audit')['snapshot']['id']
        self.assertEqual(self.service.show('test','audit')['bound_entry_ids'],['state'])
        self.service.save_plan('test',self.payload(sid,entry_ids=[]),0,workline_id='audit')
        self.assertEqual(self.service.show('test','audit')['bound_entry_ids'],[])
        self.service.set_workline_entries('test',['guide'],workline_id='audit')
        self.assertEqual(self.service.show('test','audit')['bound_entry_ids'],['guide'])
        self.service.set_workline_entries('test',[],workline_id='audit')
        self.assertEqual(self.service.show('test','audit')['bound_entry_ids'],[])

    def test_new_material_override_resolves_old_missing_material_warning(self):
        snap = self.service.refresh('test','audit')['snapshot']
        self.service.save_plan('test',self.payload(snap['id'],entry_ids=['state']),0,workline_id='audit')
        scope = self.service._scope('test','audit')[1]
        altered = copy.deepcopy(snap)
        altered['entries'] = [e for e in altered['entries'] if e['id']=='guide']
        generation = self.service.store.begin_refresh(scope)
        self.service.store.finish_refresh(scope,generation,altered)
        self.assertEqual(self.service.show('test','audit')['missing_entry_ids'],['state'])
        self.service.set_workline_entries('test',['guide'],workline_id='audit')
        card = self.service.show('test','audit')
        self.assertEqual(card['missing_entry_ids'],[])
        self.assertEqual(card['bound_entry_ids'],['guide'])
        self.assertFalse(any('绑定的部分材料' in warning for warning in card['warnings']))

    def test_review_rejects_stale_revision_stale_snapshot_and_failed_refresh(self):
        first = self.service.refresh('test','audit')['snapshot']
        self.service.save_plan('test',self.payload(first['id']),0,workline_id='audit')
        with self.assertRaises(ConflictError):
            self.service.acknowledge_changes('test',0,first['id'],workline_id='audit')
        self.service.refresh('test','audit')
        with self.assertRaises(ValueError):
            self.service.acknowledge_changes('test',1,first['id'],workline_id='audit')
        current = self.service.show('test','audit')['snapshot']
        self.file.unlink()
        self.service.refresh('test','audit')
        with self.assertRaises(ValueError):
            self.service.acknowledge_changes('test',1,current['id'],workline_id='audit')

    @unittest.skipUnless(sys.platform == "win32", "Windows API assertion; Linux dispatch covered separately")
    def test_source_open_copy_folder_and_removed_source_baseline(self):
        first = self.service.refresh('test','audit')['snapshot']
        self.service.save_plan('test',self.payload(first['id']),0,workline_id='audit')
        self.assertEqual(self.service.open_source('test','state','copy','audit'),str(self.file))
        self.file.unlink()
        self.service.refresh('test','audit')
        with patch('os.startfile') as opened:
            self.service.open_source('test','state','folder','audit')
        opened.assert_called_once_with(str(self.source))
        with self.assertRaises(ValueError):
            self.service.open_source('test','state','open','audit')
        with self.assertRaises(ValueError):
            self.service.open_source('test','not-registered','copy','audit')

    @unittest.skipUnless(sys.platform == "win32", "Windows API assertion; Linux dispatch covered separately")
    def test_model_default_is_folder_and_explicit_open_never_executes(self):
        model = self.source/'voice.pth'
        model.write_bytes(b'test weight not loaded')
        snap = self.service.refresh('test','audit')['snapshot']
        snap['entries'].append({'id':'output-weight','type':'file','label':model.name,'target':str(model)})
        scope = self.service._scope('test','audit')[1]
        generation = self.service.store.begin_refresh(scope)
        self.service.store.finish_refresh(scope,generation,snap)
        entry = next(e for e in self.service.show('test','audit')['entries'] if e['id']=='output-weight')
        self.assertEqual(entry['kind'],'模型权重')
        self.assertEqual(entry['default_action'],'folder')
        with patch('os.startfile') as opened:
            self.service.open_entry('test','output-weight',workline_id='audit')
        opened.assert_called_once_with(str(self.source))
        with patch('os.startfile') as opened, self.assertRaises(ValueError):
            self.service.open_entry('test','output-weight','open','audit')
        opened.assert_not_called()

    def test_workline_names_and_preferences_validation(self):
        created = self.service.create_workline('test','新的研究线')
        self.assertEqual(self.service.select_workline('test',created['id'])['workline']['name'],'新的研究线')
        for name in ('','\n','x'*101,'新的研究线'):
            with self.assertRaises(ValueError):
                self.service.create_workline('test',name)
        for value in ('yes',0,None):
            with self.assertRaises(ValueError):
                self.service.save_preferences({'auto_refresh':value})
        self.assertFalse(self.service.save_preferences({'auto_refresh':False})['auto_refresh'])

    def test_v2_draft_and_history_migration_does_not_rewrite_payloads(self):
        sid = self.service.refresh('test')['snapshot']['id']
        original = self.service.save_handoff('test',self.payload(sid),0)
        draft = self.service.save_draft('test',self.draft(sid,kind='plan',base_revision=1),0)
        with sqlite3.connect(self.service.store.path) as db:
            original_handoff = db.execute('SELECT payload FROM handoffs').fetchone()[0]
            original_snapshots = db.execute('SELECT payload FROM snapshots').fetchall()
            old_draft = db.execute('SELECT payload FROM drafts').fetchone()[0]
            db.execute('DROP TABLE drafts')
            db.execute('CREATE TABLE drafts(project_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,payload TEXT NOT NULL)')
            db.execute('INSERT INTO drafts VALUES(?,?,?)',('test',draft['revision'],old_draft))
            db.execute('PRAGMA user_version=2')
        db.close()
        reopened = Service(self.app)
        self.assertEqual(reopened.load_draft('test','plan'),draft)
        self.assertIsNone(reopened.load_draft('test','handoff'))
        self.assertEqual(reopened.show('test','default')['handoff'],original)
        with sqlite3.connect(self.service.store.path) as db:
            self.assertEqual(db.execute('SELECT payload FROM handoffs').fetchone()[0],original_handoff)
            self.assertEqual(db.execute('SELECT payload FROM snapshots').fetchall(),original_snapshots)
            self.assertEqual(db.execute('SELECT payload FROM drafts').fetchone()[0],old_draft)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],4)
        db.close()

    def test_rvc_job_switch_returns_to_its_own_plan(self):
        project = {'id':'rvc','name':'RVC','roots':{'main':str(self.source),'applio':str(self.source)},'sources':[],'entries':[]}
        self.config['projects'].append(project)
        for job in ('20261022_first','20261022_second'):
            folder = self.source/'workflows/jobs'/job
            folder.mkdir(parents=True)
            (folder/'job.json').write_text(json.dumps({'kind':'train','voice_name':'sample','created_at':'2026-10-22'}))
            (folder/'state.json').write_text('{"status":"completed","updated_at":"2026-10-22"}')
        self.write_config()
        service = Service(self.app)
        first_line = service.set_focus('rvc','20261022_first')['workline']['id']
        first_snap = service.refresh('rvc')['snapshot']['id']
        plan = service.save_plan('rvc',self.payload(first_snap,next_action='第一音色'),0)
        second = service.set_focus('rvc','20261022_second')
        self.assertNotEqual(second['workline']['id'],first_line)
        self.assertIsNone(second['handoff'])
        self.assertEqual(second['source_changes'],[])
        returned = service.set_focus('rvc','20261022_first')
        self.assertEqual(returned['handoff'],plan)
        self.assertEqual(returned['snapshot']['id'],first_snap)
        self.assertIn('sample · 训练',returned['workline']['name'])


if __name__ == '__main__':
    unittest.main()
