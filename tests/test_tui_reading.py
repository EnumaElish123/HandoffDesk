"""Keyboard and terminal fallback coverage for the optional reading dialogs."""
import importlib.util
import unittest


@unittest.skipUnless(importlib.util.find_spec('textual'), 'Textual optional dependency')
class ReadingDialogTests(unittest.IsolatedAsyncioTestCase):
    async def test_action_filter_selection_and_modal_focus_return(self):
        from textual.app import App
        from textual.widgets import Button, Input, OptionList, TextArea
        from handoffdesk.tui_reading import ActionMenu, HelpScreen

        choices = [('files', '查看附件', 'f', '材料'), ('export', '导出记录', 'e', 'Markdown')]
        class Harness(App):
            def compose(self):
                yield Button('操作')
        app = Harness()
        received = []
        async with app.run_test(size=(60, 18)) as pilot:
            await app.push_screen(ActionMenu('记录操作', choices), received.append)
            query = app.screen.query_one(Input)
            query.value = 'ｍａｒｋ'
            await pilot.pause()
            self.assertEqual(app.screen.query_one(OptionList).option_count, 1)
            await pilot.press('enter')
            self.assertEqual(received, ['export'])
            await app.push_screen(ActionMenu('记录操作', choices), received.append)
            await pilot.press('down', 'enter')
            self.assertEqual(received, ['export', 'export'])
            await app.push_screen(ActionMenu('记录操作', choices), received.append)
            app.screen.query_one(Input).value = 'no match'
            await pilot.press('enter')
            self.assertIsInstance(app.screen, ActionMenu)
            await pilot.press('escape')
            self.assertIsNone(received[-1])
            await app.push_screen(HelpScreen('记录', choices))
            listing = app.screen.query_one(OptionList)
            self.assertIn('导出记录', listing.get_option('export').prompt.plain)
            await pilot.press('enter')
            self.assertIn('Ctrl+P', app.screen.query_one(TextArea).text)
            await pilot.press('escape', 'c')
            self.assertEqual(listing.highlighted_option.prompt.plain.splitlines()[0], 'handoffdesk --help')
            names = [item.prompt.plain.splitlines()[0] for item in listing.options
                     if item.id and item.id.startswith('cli-')]
            self.assertIn('handoffdesk agent checkpoint --help', names)
            self.assertIn('handoffdesk worklines PROJECT bind --help', names)
            self.assertFalse(any('integration-hook' in name for name in names))
            await pilot.press('end', 'enter')
            self.assertIn('--probe', app.screen.query_one(TextArea).text)
            await pilot.press('escape')
            self.assertIn('handoffdesk doctor --help', listing.highlighted_option.prompt.plain)
            self.assertIs(app.focused, listing)
            await pilot.press('escape')
            self.assertIsInstance(app.focused, Button)

            from handoffdesk.tui_reading import AdvancedScreen
            await app.push_screen(AdvancedScreen('demo', 'review', app_dir='/tmp/带 空格的目录'))
            instructions = app.screen.query_one(TextArea).text
            self.assertIn("handoffdesk --app-dir '/tmp/带 空格的目录' history demo --workline review", instructions)
            await pilot.press('escape')
            self.assertIsInstance(app.focused, Button)

    async def test_detail_structure_and_manual_copy(self):
        from textual.app import App
        from textual.widgets import TextArea
        from handoffdesk.tui_reading import CopyScreen, continuation_text, render_detail, render_preview, render_record_option

        record = {'id':'p:t:1', 'project_id':'p', 'project_name':'项目',
                  'workline_id':'t', 'workline_name':'任务', 'title':'试验', 'kind':'plan',
                  'done':'', 'next_action':'复核路径', 'blockers':['等待样本'],
                  'recorded_at':'2026-11-12T00:00:00Z', 'actor':{'name':'Operator'},
                  'verification':{'status':'not_run','summary':'尚待执行'},
                  'file_count':0, 'files':[], 'read':False}
        text = render_detail(record, 'UTC')
        self.assertLess(text.index('复核路径'), text.index('等待样本'))
        self.assertNotIn('实际完成', text)
        self.assertNotIn('p:t:1', text)
        self.assertIn('p:t:1', render_detail(record, 'UTC', metadata=True))
        self.assertIn('复核路径', render_preview(record, 'UTC'))
        row = render_record_option(record, '', 'UTC').plain
        self.assertIn('未读  试验', row)
        self.assertIn('有阻塞', row)
        record['match_excerpt'] = '附件用途 · 复核样本'
        self.assertIn('附件用途 · 复核样本', render_record_option(record, '样本', 'UTC').plain)
        self.assertNotIn('附件用途', render_record_option(record, '', 'UTC').plain)
        prompt = continuation_text(record)
        app = App()
        async with app.run_test(size=(60,18)) as pilot:
            await app.push_screen(CopyScreen('复制续接说明', prompt))
            self.assertIn('p:t:1', app.clipboard)
            self.assertEqual(app.screen.query_one(TextArea).text, prompt)
            await pilot.click('#copy-close')
            self.assertNotIsInstance(app.screen, CopyScreen)
