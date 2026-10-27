"""User-flow regressions with actual Tk and SQLite, isolated project fixtures."""
import sys
import copy
import json
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from handoffdesk.desktop import App
from handoffdesk.service import Service


class DesktopV03Tests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='desktop-v03-', dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        self.project = base/'source'
        self.project.mkdir()
        self.state_file = self.project/'state.json'
        self.state_file.write_text('{"status":"paused"}', encoding='utf-8')
        (self.project/'guide.md').write_text('fixture guide', encoding='utf-8')
        (self.project/'model.pth').write_bytes(b'fixture only, not a real model')
        appdir=base/'app';(appdir/'config').mkdir(parents=True)
        config={'schema_version':1,'projects':[{'id':'test','name':'测试项目','roots':{'main':str(self.project)},
            'worklines':[{'id':'a','name':'工作线甲','entry_ids':['guide']},{'id':'b','name':'工作线乙','entry_ids':['weight']}],
            'sources':[{'id':'test.state','path':'state.json','format':'json','fields':{'recorded_status':'/status'}},
                       {'id':'test.guide','path':'guide.md','format':'text'},
                       {'id':'test.weight','path':'model.pth','format':'binary_entry_only'}],
            'entries':[{'id':'state','type':'open_source','source_id':'test.state'},
                       {'id':'guide','type':'open_source','source_id':'test.guide'},
                       {'id':'weight','type':'open_source','source_id':'test.weight'}]}]}
        (appdir/'config/projects.json').write_text(json.dumps(config),encoding='utf-8')
        self.service=Service(appdir)
        self.service.save_preferences({'auto_refresh':False})
        self.service.refresh('test')
        try:self.window=tk.Tk()
        except tk.TclError as exc:self.skipTest(str(exc))
        self.window.withdraw()
        self.app=App(self.service,self.window)
        self.window.update_idletasks()

    def tearDown(self):
        if hasattr(self,'app') and not self.app.closed:
            if self.app.last_dialog and not self.app.last_dialog.closed:self.app.last_dialog._destroy()
            self.app.close()

    def pump(self,predicate,timeout=4):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            self.window.update()
            if predicate():return
            time.sleep(.01)
        self.fail('GUI callback timed out')

    def switch(self,line):
        index=next(i for i,w in enumerate(self.app.worklines) if w['id']==line)
        self.app.workline_combo.current(index)
        self.app._workline_selected()

    def plan(self,next_action='核对状态',**extra):
        state=self.service.show('test',workline_id=self.app.workline_id)
        return self.service.save_plan('test',{'next_action':next_action,'source_snapshot_id':state['snapshot']['id'],**extra},
            (state['handoff'] or {}).get('revision',0),workline_id=self.app.workline_id)

    def test_plan_draft_does_not_hide_handoff_field_and_both_recover(self):
        plan=self.app.create_plan_dialog()
        plan.last_scroll.set(0,.5)
        self.assertEqual(plan.last_scroll.grid_info(),{})
        plan.next_text.insert('1.0','草稿下一步');self.assertTrue(plan.close())
        self.assertTrue(self.app.draft_buttons['plan'].winfo_manager())
        handoff=self.app.create_handoff_dialog()
        self.assertEqual(handoff.event_kind,'handoff')
        self.assertTrue(handoff.last_done_text.grid_info())
        handoff.last_done_text.insert('1.0','草稿完成项');self.assertTrue(handoff.close())
        self.assertTrue(self.app.draft_buttons['handoff'].winfo_manager())
        state=self.service.show('test')
        self.assertEqual(state['drafts']['plan']['next_action'],'草稿下一步')
        self.assertEqual(state['drafts']['handoff']['last_done'],'草稿完成项')
        self.assertIsNone(state['handoff'])

    def test_blockers_have_dedicated_count_complete_text_and_scroll(self):
        blockers=['需要确认音色版本','需要核对输出位置','需要准备试听材料','需要确认第四个条件']
        self.plan(blockers=blockers);self.app.load_current();self.window.update_idletasks()
        self.assertEqual(self.app.blocker_var.get(),'当前阻塞 · 4 项')
        self.assertTrue(self.app.blocker_frame.grid_info())
        self.assertNotIn(blockers[0],self.app.warning_text.get('1.0','end'))
        self.assertIn(blockers[3],self.app.blocker_text.get('1.0','end'))
        self.app.toggle_blockers()
        self.assertEqual(int(self.app.blocker_text['height']),4)

    @unittest.skipUnless(sys.platform == "win32", "Windows API assertion; Linux dispatch covered separately")
    def test_weight_action_and_current_binding_override_historical_plan(self):
        saved=self.plan(entry_ids=['state'])
        self.service.set_workline_entries('test',['weight'])
        self.app.load_current()
        self.assertEqual([e['id'] for e in self.app._bound_entries()],['weight'])
        self.assertIn('定位',self.app.continue_button['text'])
        with patch('os.startfile') as opened:self.app.continue_work()
        opened.assert_called_once_with(str(self.project))
        self.assertEqual(self.service.history('test')[0]['entry_ids'],saved['entry_ids'])

    @unittest.skipUnless(sys.platform == "win32", "Windows API assertion; Linux dispatch covered separately")
    def test_explicit_empty_binding_does_not_reopen_old_plan_material(self):
        self.plan(entry_ids=['state'])
        self.service.set_workline_entries('test',[])
        self.app.load_current()
        self.assertEqual(self.app._bound_entries(),[])
        with patch.object(self.app,'bind_materials') as picker,patch('os.startfile') as opened:
            self.app.continue_work()
        picker.assert_called_once();opened.assert_not_called()

    def test_failed_material_open_does_not_persist_selection(self):
        self.app.entry_combo.current(next(i for i,e in enumerate(self.app.entries) if e['id']=='weight'))
        with patch.object(self.service,'open_entry',side_effect=OSError('fixture unavailable')),\
             patch.object(self.app,'_entry_selected') as persist,\
             patch('handoffdesk.desktop.messagebox.showerror'):
            self.app.open_selected_entry()
        persist.assert_not_called()

    def test_worklines_restore_their_own_plan_and_draft(self):
        self.switch('a');self.service.refresh('test',workline_id='a');self.app.load_current()
        self.plan('甲的下一步');self.app.load_current()
        editor=self.app.create_handoff_dialog();editor.last_done_text.insert('1.0','甲的草稿');editor.close()
        self.switch('b');self.service.refresh('test',workline_id='b');self.app.load_current()
        self.assertIsNone(self.app.current['handoff'])
        self.assertFalse(self.app.current['drafts']['handoff'])
        self.plan('乙的下一步');self.app.load_current();self.switch('a')
        self.assertEqual(self.app.current['handoff']['next_action'],'甲的下一步')
        self.assertEqual(self.app.current['drafts']['handoff']['last_done'],'甲的草稿')

    def test_background_refresh_cannot_replace_a_new_workline(self):
        release=threading.Event();started=threading.Event();original=self.service.refresh
        def delayed(project_id,workline_id=None):
            started.set();release.wait(3);return original(project_id,workline_id=workline_id)
        with patch.object(self.service,'refresh',side_effect=delayed):
            self.app.refresh();self.assertTrue(started.wait(1));self.switch('b');release.set()
            self.pump(lambda:self.app.busy_count==0)
        self.assertEqual(self.app.workline_id,'b')
        self.assertIsNone(self.app.current['snapshot'])

    @unittest.skipUnless(sys.platform == "win32", "Windows API assertion; Linux dispatch covered separately")
    def test_source_open_copy_and_review_are_available_without_returning_home(self):
        original=self.plan();self.app.load_current()
        self.state_file.write_text('{"status":"completed"}',encoding='utf-8')
        self.service.refresh('test');self.app.load_current()
        self.assertTrue(self.app.source_changes)
        self.app.changes_tree.selection_set('0')
        with patch('os.startfile') as opened:self.app.source_action('open',changed=True)
        opened.assert_called_once_with(str(self.state_file))
        self.app.source_action('copy',changed=True)
        self.assertEqual(self.window.clipboard_get(),str(self.state_file))
        self.app.acknowledge_changes()
        self.assertEqual(self.app.source_changes,[])
        self.assertEqual(self.service.show('test')['handoff'],original)
        self.assertIn('最近复核',self.app.review_var.get())

    def test_imported_advice_keeps_unknown_evidence_and_origin_after_recovery(self):
        imported={'last_done':'助手记录的完成项','next_action':'待用户确认的下一步','blockers':[],
            'current_task':'','origin':'assistant_suggested','source_snapshot_id':None,'entry_ids':[]}
        dialog=self.app.create_handoff_dialog(imported=imported)
        self.assertEqual(dialog.origin,'assistant_suggested')
        self.assertIsNone(dialog.source_snapshot_id)
        self.assertEqual(dialog.save_button['text'],'确认并保存')
        self.assertIn('尚未绑定材料',dialog.material_var.get())
        self.assertIsNone(self.service.show('test')['handoff'])
        dialog.close();recovered=self.app.create_handoff_dialog()
        self.assertEqual(recovered.origin,'assistant_suggested')
        self.assertIsNone(recovered.source_snapshot_id)
        self.assertTrue(recovered.save())
        self.assertEqual(self.service.show('test')['handoff']['origin'],'assistant_suggested')

    def test_import_never_overwrites_existing_same_kind_draft(self):
        plan=self.app.create_plan_dialog();plan.next_text.insert('1.0','原草稿');plan.close()
        before=copy.deepcopy(self.service.load_draft('test',event_kind='plan'))
        with patch('handoffdesk.desktop.messagebox.showerror') as error:
            result=self.app.create_handoff_dialog('plan',imported={'next_action':'导入内容','source_snapshot_id':None})
        self.assertIsNone(result);error.assert_called_once()
        self.assertEqual(self.service.load_draft('test',event_kind='plan'),before)

    def test_failure_alert_is_visible_separately_from_general_notes(self):
        self.state_file.unlink();self.service.refresh('test');self.app.load_current()
        self.assertTrue(self.app.alert_button.grid_info())
        self.assertIn('来源读取失败',self.app.alert_button['text'])
        self.assertIn('失败',self.app.source_tree.item('0')['values'][1])

    def test_search_hides_non_matches_and_shows_empty_message(self):
        self.app.search_var.set('not-a-project');self.window.update_idletasks()
        self.assertEqual(self.app.project_buttons['test'].grid_info(),{})
        self.assertTrue(self.app.no_results.grid_info())

    def test_material_action_has_one_verb_and_sidebar_keeps_subject_before_date(self):
        self.app.entries=[{'id':'url','label':'打开保存的服务地址（未探测）','kind':'url',
                           'target':'http://127.0.0.1:6969','default_action':'open'}]
        self.app.current['bound_entry_ids']=['url'];self.app._update_continue_label()
        self.assertEqual(self.app.continue_button['text'],'打开 保存的服务地址（未探测）')
        self.app.overviews['test']={'workline_name':'yq1012 · 训练 · 2026-10-12 · abcdef'}
        self.app._update_project_values()
        self.assertEqual(self.app.project_buttons['test']['text'].splitlines()[1],'yq1012 · 训练…')

    def test_empty_card_compact_and_actions_fit_small_and_large_windows(self):
        self.window.deiconify()
        for width,height in ((1080,760),(840,620)):
            self.window.geometry(f'{width}x{height}');self.window.update()
            self.assertEqual(self.app.last_frame.grid_info(),{})
            self.assertLess(self.app.next_text.winfo_height(),130)
            for button in (self.app.continue_button,self.app.plan_button,self.app.handoff_button):
                self.assertLess(button.winfo_rooty()+button.winfo_height(),self.window.winfo_rooty()+height)
                self.assertLessEqual(button.winfo_rootx()+button.winfo_width(),self.window.winfo_rootx()+width)

    def test_small_editors_keep_save_visible_with_expanded_scrollable_blockers(self):
        self.window.deiconify();self.window.geometry('840x620');self.window.update()
        for kind in ('plan','handoff'):
            dialog=self.app.create_handoff_dialog(kind)
            dialog.toggle_extra()
            dialog.blockers_text.insert('1.0','\n'.join(f'阻塞项 {i}' for i in range(40)))
            dialog.window.update()
            self.assertLessEqual(dialog.window.winfo_height(),self.window.winfo_height())
            self.assertLessEqual(dialog.save_button.winfo_rooty()+dialog.save_button.winfo_height(),
                                 dialog.window.winfo_rooty()+dialog.window.winfo_height())
            dialog.blockers_text.yview_moveto(1)
            self.assertEqual(dialog.blockers_text.yview()[1],1.0)
            self.assertIn('阻塞项 39',dialog.blockers_text.get('1.0','end'))
            dialog.close()


if __name__=='__main__':unittest.main()
