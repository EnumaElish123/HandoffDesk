import sys
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from handoffdesk.service import Service, ConflictError


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='service-fixture-', dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.app = self.base / 'app'
        self.source = self.base / 'project'
        self.source.mkdir()
        (self.app / 'config').mkdir(parents=True)
        self.file = self.source / 'state.json'
        self.file.write_text('{"status":"initial"}', encoding='utf-8')
        config = {'schema_version':1, 'projects':[{'id':'test', 'name':'测试', 'roots':{'main':str(self.source)},
                  'sources':[{'id':'test.state', 'root':'main', 'path':'state.json', 'format':'json', 'fields':{'status':'/status'}}],
                  'entries':[{'id':'state', 'type':'open_source', 'source_id':'test.state'}]}]}
        (self.app/'config/projects.json').write_text(json.dumps(config),encoding='utf-8')
        self.service = Service(self.app)

    def handoff(self, sid=None, next_action='核对状态'):
        return {'current_task':'测试流程', 'last_done':'读取状态', 'next_action':next_action,
                'blockers':[], 'origin':'user_directed', 'source_snapshot_id':sid}

    def test_refresh_does_not_write_source_or_invent_handoff(self):
        before = self.file.read_bytes(), self.file.stat().st_mtime_ns
        card = self.service.refresh('test')
        self.assertIsNone(card['handoff'])
        self.assertEqual(card['snapshot']['observations'][0]['status'], 'ok')
        self.assertEqual(before, (self.file.read_bytes(), self.file.stat().st_mtime_ns))

    def test_conflict_and_history_restore(self):
        first = self.service.save_handoff('test',self.handoff(),0)
        with self.assertRaises(ConflictError) as conflict:
            self.service.save_handoff('test', self.handoff(next_action='另一条'),0)
        self.assertEqual(conflict.exception.current['revision'], 1)
        self.service.save_handoff('test',self.handoff(next_action='第二步'),1)
        restored = self.service.restore_handoff('test',1,2)
        self.assertEqual(restored['revision'],3)
        self.assertEqual(restored['next_action'],first['next_action'])
        self.assertEqual(len(self.service.history('test')),3)

    def test_idempotency_rejects_reused_key_with_different_content(self):
        first = self.service.save_handoff('test',self.handoff(),0,'op1')
        repeated = self.service.save_handoff('test',self.handoff(),0,'op1')
        self.assertEqual(first,repeated)
        with self.assertRaises(ValueError):
            self.service.save_handoff('test',self.handoff(next_action='不同'),1,'op1')

    def test_handoff_keeps_snapshot_seen_before_new_refresh(self):
        original = self.service.refresh('test')['snapshot']['id']
        self.file.write_text('{"status":"changed"}',encoding='utf-8')
        self.service.refresh('test')
        saved = self.service.save_handoff('test',self.handoff(original),0)
        self.assertEqual(saved['source_snapshot_id'],original)
        self.assertTrue(any('依据已发生变化' in w for w in self.service.show('test')['warnings']))

    def test_failed_refresh_preserves_cache_with_explicit_error(self):
        original = self.service.refresh('test')['snapshot']['id']
        self.file.unlink()
        card = self.service.refresh('test')
        self.assertEqual(card['snapshot']['id'],original)
        self.assertFalse(card['last_attempt']['usable'])
        self.assertTrue(any('上一次有效快照' in w for w in card['warnings']))
        self.assertIn('无法读取', card['latest_attempt_snapshot']['observations'][0]['error'])

    def test_alias_uses_same_card_and_handoff(self):
        self.service.refresh('测试')
        self.assertIsNotNone(self.service.show('TEST')['snapshot'])
        self.service.save_handoff('TEST',self.handoff(),0)
        self.assertEqual(self.service.show('测试')['handoff']['revision'],1)
        self.assertEqual(len(self.service.history('test')),1)

    def test_changed_source_binding_does_not_receive_old_cache(self):
        self.service.refresh('test')
        config = self.app/'config/projects.json'
        data = json.loads(config.read_text())
        data['projects'][0]['sources'][0]['path'] = 'missing-new-file.json'
        config.write_text(json.dumps(data))
        other = Service(self.app)
        card = other.refresh('test')
        failed = card['latest_attempt_snapshot']['observations'][0]
        self.assertNotIn('last_success', failed)
        self.assertTrue(any('接入配置已变化' in w for w in card['warnings']))

    def test_partial_failure_retains_previous_value_as_stale(self):
        good = {'source_id':'a','status':'ok','value':{'n':1},'fingerprint':'a','observed_at':'old'}
        second = {'source_id':'b','status':'ok','value':{'n':2},'fingerprint':'b','observed_at':'old'}
        with patch('handoffdesk.service.collect_project',return_value={'observations':[good,second]}):
            self.service.refresh('test')
        bad = {'source_id':'a','status':'error','value':{},'error':'missing'}
        with patch('handoffdesk.service.collect_project',return_value={'observations':[bad,second]}):
            card = self.service.refresh('test')
        observation = card['snapshot']['observations'][0]
        self.assertEqual(observation['status'],'error')
        self.assertEqual(observation['last_success']['value'],{'n':1})

    def test_slow_old_refresh_cannot_replace_newer_result(self):
        entered, release = threading.Event(), threading.Event()
        def collect(*args, **kwargs):
            if threading.current_thread().name == 'old-refresh':
                entered.set()
                release.wait(5)
                name = 'old'
            else:
                name = 'new'
            return {'facts':[name], 'observations':[{'source_id':'x','status':'ok','value':{},'fingerprint':name}]}
        with patch('handoffdesk.service.collect_project',side_effect=collect):
            worker = threading.Thread(target=self.service.refresh,args=('test',),name='old-refresh')
            worker.start()
            self.assertTrue(entered.wait(5))
            self.service.refresh('test')
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.service.show('test')['snapshot']['facts'],['new'])

    def test_latest_failed_refresh_does_not_reactivate_older_pending(self):
        generation1 = self.service.store.begin_refresh('test')
        generation2 = self.service.store.begin_refresh('test')
        self.service.store.finish_refresh('test',generation2,{'error':'failed'},False)
        self.service.store.finish_refresh('test',generation1,{'facts':['late']},True)
        self.assertIsNone(self.service.show('test')['snapshot'])
        self.assertEqual(self.service.show('test')['last_attempt']['message'],'failed')

    def test_parallel_handoff_writers_have_one_winner(self):
        barrier = threading.Barrier(2)
        results = []
        def writer():
            barrier.wait(5)
            try:
                self.service.save_handoff('test',self.handoff(),0)
                results.append('saved')
            except ConflictError:
                results.append('conflict')
        threads = [threading.Thread(target=writer) for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join(5)
        self.assertCountEqual(results,['saved','conflict'])

    def test_data_dir_cannot_be_inside_source(self):
        with self.assertRaises(ValueError):
            Service(self.app,self.source/'tool-data')
        self.assertFalse((self.source/'tool-data').exists())

    def test_redirected_export_path_rejected(self):
        actual_resolve = Path.resolve
        export_dir = self.service.data_dir/'exports'
        def redirected(path, *args, **kwargs):
            if path == export_dir:
                return self.source
            return actual_resolve(path, *args, **kwargs)
        with patch.object(Path,'resolve',redirected):
            with self.assertRaises(ValueError):
                self.service.export('test')
        self.assertEqual(list(self.source.iterdir()),[self.file])

    def test_focus_change_invalidates_pending_refresh(self):
        generation = self.service.store.begin_refresh('test')
        self.service.store.set_setting('test','focus_job','new-focus')
        self.service.store.finish_refresh('test',generation,{'facts':['old-focus']},True)
        self.assertIsNone(self.service.show('test')['snapshot'])

    @unittest.skipUnless(sys.platform == "win32", "Windows API assertion; Linux dispatch covered separately")
    def test_open_rechecks_containment_and_blocks_executable(self):
        snap = self.service.refresh('test')['snapshot']
        outside = self.base/'outside.txt'
        outside.write_text('outside')
        for target in [outside,self.source/'unsafe.exe']:
            target.touch()
            payload = copy.deepcopy(snap)
            payload['entries'] = [{'id':'bad','type':'file','label':'bad','target':str(target)}]
            generation = self.service.store.begin_refresh('test')
            self.service.store.finish_refresh('test',generation,payload,True)
            with self.assertRaises(ValueError), patch('os.startfile') as opener:
                self.service.open_entry('test','bad',action='open')
            opener.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Windows API assertion; Linux dispatch covered separately")
    def test_script_opens_fixed_editor_without_execution(self):
        target = self.source/'script.ps1'
        target.write_text('do not execute')
        payload = {'registry_fingerprint':self.service.registry.fingerprint,
                   'entries':[{'id':'script','type':'file','label':'script','target':str(target)}]}
        generation = self.service.store.begin_refresh('test')
        self.service.store.finish_refresh('test',generation,payload,True)
        with patch('subprocess.Popen') as popen:
            self.service.open_entry('test','script')
        self.assertEqual(Path(popen.call_args.args[0][0]).name.lower(),'notepad.exe')
        self.assertFalse(popen.call_args.kwargs['shell'])

    def test_export_is_local_and_persists_across_reopen(self):
        self.service.save_handoff('test',self.handoff(),0)
        path = self.service.export('test')
        self.assertTrue(path.is_relative_to(self.service.data_dir))
        other = Service(self.app)
        self.assertEqual(other.show('test')['handoff']['next_action'],'核对状态')
        self.assertIn('用户指定',path.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
