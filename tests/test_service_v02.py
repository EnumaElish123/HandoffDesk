import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from handoffdesk.service import Service, ConflictError


class V02ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='v02-fixture-', dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.app, self.source = self.base/'app', self.base/'business'
        self.source.mkdir()
        (self.app/'config').mkdir(parents=True)
        self.file = self.source/'state.json'
        self.file.write_text('{"status":"initial","optional":null}', encoding='utf-8')
        project = {'id':'test', 'name':'测试', 'aliases':['样例'], 'roots':{'main':str(self.source)},
                   'sources':[{'id':'test.state', 'root':'main','path':'state.json','format':'json',
                               'fields':{'status':'/status','optional':'/optional'},'optional_fields':['optional']}],
                   'entries':[{'id':'state','type':'open_source','source_id':'test.state'}]}
        self.config = {'schema_version':1,'projects':[project]}
        self.config_path = self.app/'config/projects.json'
        self.config_path.write_text(json.dumps(self.config), encoding='utf-8')
        self.service = Service(self.app)

    def payload(self, sid=None, **patches):
        value = {'current_task':'测试问题', 'last_done':'实测已完成', 'next_action':'核对下一步',
                 'blockers':[], 'origin':'user_directed', 'source_snapshot_id':sid}
        value.update(patches)
        return value

    def draft(self, sid=None, **patches):
        value = self.payload(sid, draft_id=str(uuid.uuid4()), base_revision=0, event_kind='handoff')
        value.pop('origin')
        value.update(patches)
        return value

    def test_first_plan_does_not_invent_completed_work(self):
        self.service.save_plan('样例', self.payload(last_done='不应写成完成'), 0)
        card = self.service.show('test')
        self.assertEqual(card['handoff']['event_kind'], 'plan')
        self.assertEqual(card['handoff']['last_done'], '')
        self.assertIsNone(card['last_completion'])
        self.assertIn('尚无完成记录', self.service.summary('test'))
        self.assertIn('起步计划', self.service.summary('test'))
        with self.assertRaises(ValueError):
            self.service.save_handoff('test', self.payload(last_done=''), 1)

    def test_plan_retains_previous_completion_and_shares_revision_conflicts(self):
        first = self.service.save_handoff('test', self.payload(), 0)
        plan = self.service.save_plan('test', self.payload(next_action='新的目标'), 1)
        self.assertEqual(plan['revision'], 2)
        self.assertEqual(self.service.show('test')['last_completion'], first)
        with self.assertRaises(ConflictError):
            self.service.save_handoff('test', self.payload(), 1)
        restored = self.service.restore_handoff('test', 2, 2)
        self.assertEqual(restored['event_kind'], 'plan')
        self.assertEqual(restored['last_done'], '')
        self.assertEqual(restored['revision'], 3)
        self.assertEqual(self.service.show('test')['last_completion'], first)

    def test_preferences_merge_and_do_not_invalidate_observations(self):
        card = self.service.refresh('test')
        before = self.service.store.state('test')
        self.service.save_preferences({'last_project':'样例','geometry':'1200x800+30+40'})
        self.service.save_preferences({'entries':{'test':'state'}})
        prefs = Service(self.app).preferences()
        self.assertEqual(prefs['last_project'],'test')
        self.assertEqual(prefs['geometry'],'1200x800+30+40')
        self.assertEqual(prefs['entries'],{'test':'state'})
        self.assertEqual(self.service.store.state('test'), before)
        self.assertEqual(self.service.show('test')['snapshot']['id'], card['snapshot']['id'])

    def test_invalid_preferences_are_rejected(self):
        for value in ({'geometry':'bad'}, {'geometry':'5x5'}, {'entries':{'other':'state'}}, {'last_project':'missing'}, {'focus_job':'bad'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.service.save_preferences(value)

    def test_draft_restores_old_source_and_version_after_refresh_and_new_handoff(self):
        old_sid = self.service.refresh('test')['snapshot']['id']
        draft = self.draft(old_sid, next_action='未写完的下一步')
        saved = self.service.save_draft('test',draft,0)
        self.file.write_text('{"status":"new"}',encoding='utf-8')
        self.service.refresh('test')
        self.service.save_handoff('test',self.payload(),0)
        loaded = Service(self.app).load_draft('样例')
        self.assertEqual(loaded['source_snapshot_id'],old_sid)
        self.assertEqual(loaded['base_revision'],0)
        self.assertEqual(loaded['next_action'],'未写完的下一步')
        with self.assertRaises(ConflictError):
            self.service.save_handoff('test',loaded,loaded['base_revision'])
        self.assertEqual(self.service.load_draft('test'),saved)

    def test_draft_cas_ownership_and_conditional_delete(self):
        draft = self.draft()
        saved = self.service.save_draft('test',draft,0)
        with self.assertRaises(ConflictError):
            self.service.save_draft('test',dict(draft,next_action='stale'),0)
        with self.assertRaises(ConflictError):
            self.service.save_draft('test',self.draft(),saved['revision'])
        self.assertFalse(self.service.delete_draft('test',str(uuid.uuid4()),saved['revision']))
        self.assertFalse(self.service.delete_draft('test',draft['draft_id'],0))
        self.assertTrue(self.service.delete_draft('test',draft['draft_id'],saved['revision']))
        self.assertIsNone(self.service.load_draft('test'))

    def test_draft_cannot_silently_rebind_its_evidence(self):
        first = self.service.refresh('test')['snapshot']['id']
        second = self.service.refresh('test')['snapshot']['id']
        draft = self.draft(first)
        saved = self.service.save_draft('test',draft,0)
        with self.assertRaises(ValueError):
            self.service.save_draft('test',dict(draft,source_snapshot_id=second),saved['revision'])
        with self.assertRaises(ValueError):
            self.service.save_draft('test',dict(draft,event_kind='plan'),saved['revision'])
        self.assertEqual(self.service.load_draft('test')['source_snapshot_id'],first)

    def test_parallel_draft_writers_have_one_winner(self):
        barrier = threading.Barrier(2)
        outcomes = []
        draft = self.draft()
        def writer():
            barrier.wait(5)
            try:
                self.service.save_draft('test',draft,0)
                outcomes.append('saved')
            except ConflictError:
                outcomes.append('conflict')
        threads = [threading.Thread(target=writer) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(5)
        self.assertCountEqual(outcomes,['saved','conflict'])

    def test_draft_validation_rejects_foreign_or_invalid_bindings(self):
        for payload, version in [(self.draft(draft_id='bad'),0),(self.draft(base_revision=99),0),
                                 (self.draft(source_snapshot_id=999),0),(self.draft(),True),
                                 (self.draft(next_action='x'*12001),0)]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.service.save_draft('test',payload,version)

    def test_source_changes_use_previous_refresh_and_distinguish_null_from_missing(self):
        initial = self.service.refresh('test')
        self.assertEqual(initial['source_changes'],[])
        self.file.write_text('{"status":"changed"}',encoding='utf-8')
        changed = self.service.refresh('test')
        self.assertEqual(changed['change_comparison'],'previous_refresh')
        change = changed['source_changes'][0]
        self.assertEqual(change['status'],'changed')
        fields = {field['field']:field for field in change['fields']}
        self.assertEqual(fields['status']['before'],'initial')
        self.assertEqual(fields['status']['after'],'changed')
        self.assertIsNone(fields['optional']['before'])
        self.assertEqual(fields['optional']['after'],{'missing':True})

    def test_source_change_baseline_stays_at_handoff_and_exposes_failed_attempt(self):
        initial = self.service.refresh('test')['snapshot']['id']
        self.service.save_plan('test',self.payload(initial),0)
        self.file.write_text('{"status":"changed"}',encoding='utf-8')
        self.service.refresh('test')
        self.service.refresh('test')
        card = self.service.show('test')
        self.assertEqual(card['change_comparison'],'handoff')
        self.assertTrue(card['source_changes'])
        self.file.unlink()
        failed = self.service.refresh('test')
        self.assertEqual(failed['source_changes'][0]['status'],'unavailable')
        self.assertEqual(failed['snapshot']['id'],card['snapshot']['id'])

    def test_text_change_is_bounded_and_does_not_emit_full_text(self):
        old = {'observations':[{'source_id':'text','status':'ok','source_ref':'file.md','fingerprint':'a',
                                'value':{'excerpt':'before'*1000}}]}
        new = {'observations':[{'source_id':'text','status':'ok','source_ref':'file.md','fingerprint':'b',
                                'value':{'excerpt':'after'*1000}}]}
        changes = self.service._source_changes(self.config['projects'][0],old,new)
        self.assertEqual(changes[0]['fields'][0]['field'],'内容')
        self.assertLess(len(json.dumps(changes)),1000)
        self.assertNotIn('beforebefore',json.dumps(changes))

    def test_overall_failure_does_not_claim_sources_were_removed(self):
        self.service.refresh('test')
        with patch('handoffdesk.service.collect_project',side_effect=ValueError('collector unavailable')):
            card = self.service.refresh('test')
        self.assertEqual(len(card['source_changes']),1)
        self.assertEqual(card['source_changes'][0]['source_id'],'__refresh__')
        self.assertEqual(card['source_changes'][0]['status'],'unavailable')

    def test_recovered_read_uses_last_success_without_inventing_new_fields(self):
        value = {'source_id':'one','status':'ok','source_ref':'same.json','value':{'status':'completed'},'fingerprint':'same'}
        failed = dict(value,status='error',value={},fingerprint=None,last_success=value)
        recovered = self.service._source_changes(self.config['projects'][0],{'observations':[failed]},{'observations':[value]})
        self.assertEqual(recovered[0]['fields'],[{'field':'读取状态','before':'error','after':'ok'}])
        changed = dict(value,value={'status':'paused'},fingerprint='different')
        changes = self.service._source_changes(self.config['projects'][0],{'observations':[failed]},{'observations':[changed]})
        fields = {field['field']:field for field in changes[0]['fields']}
        self.assertEqual(fields['status']['before'],'completed')
        self.assertEqual(fields['status']['after'],'paused')

    def test_recovered_read_without_history_does_not_infer_missing_fields(self):
        old = {'source_id':'one','status':'error','source_ref':'same.json','value':{},'fingerprint':None}
        new = dict(old,status='ok',value={'status':'completed'},fingerprint='new')
        changes = self.service._source_changes(self.config['projects'][0],{'observations':[old]},{'observations':[new]})
        self.assertEqual(changes[0]['fields'],[{'field':'读取状态','before':'error','after':'ok'}])

    def test_recovered_text_read_does_not_claim_unchanged_content_changed(self):
        good = {'source_id':'readme','status':'ok','source_ref':'README.md','value':{'excerpt':'same contents'},'fingerprint':'same'}
        failed = dict(good,status='error',value={},fingerprint=None,last_success=good)
        changes = self.service._source_changes(self.config['projects'][0],{'observations':[failed]},{'observations':[good]})
        self.assertEqual(changes[0]['fields'],[{'field':'读取状态','before':'error','after':'ok'}])
        modified = dict(good,value={'excerpt':'new contents'},fingerprint='new')
        changes = self.service._source_changes(self.config['projects'][0],{'observations':[failed]},{'observations':[modified]})
        self.assertEqual([field['field'] for field in changes[0]['fields']],['读取状态','内容'])

    def test_focus_options_expose_readonly_metadata(self):
        project = {'id':'rvc','name':'RVC','roots':{'main':str(self.source),'applio':str(self.source)},'sources':[],'entries':[]}
        self.config['projects'].append(project)
        self.config_path.write_text(json.dumps(self.config),encoding='utf-8')
        folder = self.source/'workflows/jobs/20261022_train'
        folder.mkdir(parents=True)
        (folder/'job.json').write_text('{"id":"20261022_train","kind":"train","voice_name":"sample","created_at":"2026-10-22"}')
        (folder/'state.json').write_text('{"status":"completed","updated_at":"2026-10-22T12:00:00+08:00"}')
        before = {file.name:(file.read_bytes(),file.stat().st_mtime_ns) for file in folder.iterdir()}
        option = Service(self.app).focus_options('rvc')[1]
        self.assertEqual(option['kind'],'train')
        self.assertEqual(option['voice_name'],'sample')
        self.assertEqual(option['status'],'completed')
        self.assertEqual(option['recorded_at'],'2026-10-22T12:00:00+08:00')
        self.assertEqual(before,{file.name:(file.read_bytes(),file.stat().st_mtime_ns) for file in folder.iterdir()})

    def test_v01_database_migrates_without_rewriting_history_or_cache(self):
        snap = self.service.refresh('test')['snapshot']
        original = self.service.save_handoff('test',self.payload(snap['id']),0,'old-op')
        legacy = dict(original)
        legacy.pop('event_kind')
        with closing(sqlite3.connect(self.service.store.path)) as db, db:
            db.execute('UPDATE handoffs SET payload=?',(json.dumps(legacy),))
            db.execute('DROP TABLE drafts')
            db.execute('DROP TABLE app_preferences')
            db.execute('PRAGMA user_version=1')
        reopened = Service(self.app)
        self.assertEqual(reopened.show('test')['handoff'],legacy)
        self.assertEqual(reopened.show('test')['last_completion'],legacy)
        self.assertEqual(reopened.show('test')['snapshot'],snap)
        self.assertEqual(reopened.save_handoff('test',self.payload(snap['id']),0,'old-op'),legacy)
        self.assertEqual(reopened.preferences(),{})
        self.assertIsNone(reopened.load_draft('test'))

    def test_future_database_version_is_not_downgraded(self):
        with closing(sqlite3.connect(self.service.store.path)) as db, db:
            db.execute('PRAGMA user_version=99')
        with self.assertRaises(ValueError):
            Service(self.app)
        with closing(sqlite3.connect(self.service.store.path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],99)


if __name__ == '__main__':
    unittest.main()
