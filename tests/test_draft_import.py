import json
import unittest
from handoffdesk.draft_import import parse_summary


class DraftImportTests(unittest.TestCase):
    def test_chinese_sections_preserve_claims_without_inference(self):
        value = parse_summary('总结说明\n## 本次完成\n只阅读了报告，未运行实验。\n## 下一步\n核对审计记录。\n## 阻塞项\n- 缺少一份原始记录\n- 待人工试听')
        self.assertEqual(value['last_done'], '只阅读了报告，未运行实验。')
        self.assertEqual(value['next_action'], '核对审计记录。')
        self.assertEqual(len(value['blockers']), 2)
        self.assertIsNone(value['source_snapshot_id'])
        self.assertEqual(value['origin'], 'assistant_suggested')

    def test_json_preserves_explicit_evidence_but_not_claimed_origin(self):
        value = parse_summary('```json\n' + json.dumps({'next_action':'核对', 'source_snapshot_id':7, 'entry_ids':['report'], 'origin':'user_directed'}) + '\n```')
        self.assertEqual(value['source_snapshot_id'], 7)
        self.assertEqual(value['entry_ids'], ['report'])
        self.assertEqual(value['origin'], 'assistant_suggested')
        self.assertEqual(value['last_done'], '')

    def test_inline_and_markdown_field_names(self):
        value = parse_summary('**当前工作线：** 证据复核\n- **本次完成**：看过原文\n**下一步：** 补充记录')
        self.assertEqual(value['current_task'], '证据复核')
        self.assertEqual(value['last_done'], '看过原文')
        self.assertEqual(value['next_action'], '补充记录')

    def test_unrelated_heading_is_not_imported_into_accomplishments(self):
        value = parse_summary('## 本次完成\n看过原文\n## 无关说明\n后续应上传所有文件\n## 下一步\n继续核对')
        self.assertEqual(value['last_done'], '看过原文')
        self.assertEqual(value['next_action'], '继续核对')
        self.assertNotIn('上传', json.dumps({k:v for k,v in value.items() if k!='_import_warnings'},ensure_ascii=False))

    def test_ambiguous_or_invalid_input_is_rejected(self):
        for text in ('', '我今天做了许多工作', '{bad}', '[]', '下一步：a\n下一步：b', json.dumps({'next_action':'a','下一步':'b'}), json.dumps({'next_action':8}), json.dumps({'next_action':'x','source_snapshot_id':True}), json.dumps({'next_action':'x','entry_ids':['a','b','c','d']}), 'a'*32769):
            with self.subTest(text=text[:60]), self.assertRaises(ValueError):
                parse_summary(text)

    def test_unknown_json_fields_do_not_become_instructions(self):
        value = parse_summary(json.dumps({'next_action':'本地复核','upload':'all files','command':'run.exe'}))
        self.assertNotIn('command', value)
        self.assertNotIn('upload', value)
        self.assertTrue(value['_import_warnings'])

    def test_named_workline_is_not_confused_with_optional_subject(self):
        value = parse_summary('工作线：V7 分析\n本次主题补充：核对边界条件\n上次完成：只阅读了指南\n下一步：检查一个反例')
        self.assertEqual(value['current_task'], '核对边界条件')
        self.assertEqual(value['last_done'], '只阅读了指南')
        named_only = parse_summary('工作线：V7 分析\n下一步：检查一个反例')
        self.assertEqual(named_only['current_task'], '')
        json_value = parse_summary(json.dumps({'工作线':'V7 分析','本次主题补充':'核对条件','下一步':'继续检查'}))
        self.assertEqual(json_value['current_task'], '核对条件')

    def test_export_metadata_ends_fields_without_binding_text_snapshot(self):
        value = parse_summary('上次完成：核对过一份材料\n交接：第 3 版\n不属于完成项\n下一步：复核结果\n'
                              '阻塞项：缺少原记录\n依据快照：27\n来源读取：2026-10-22\n'
                              '材料入口：\n- D:\\project\\report.md\n需要留意：\n- 历史状态并非当前状态')
        self.assertEqual(value['last_done'], '核对过一份材料')
        self.assertEqual(value['next_action'], '复核结果')
        self.assertEqual(value['blockers'], ['缺少原记录'])
        self.assertIsNone(value['source_snapshot_id'])
        self.assertEqual(value['entry_ids'], [])

    def test_plain_and_markdown_metadata_headings_stop_claim_continuation(self):
        for title in ('工作线','交接','起步计划','依据快照','来源读取','材料入口','需要留意','source_snapshot_id'):
            for heading in (title+'：metadata', '## '+title, '**'+title+'**：metadata'):
                with self.subTest(heading=heading):
                    value = parse_summary('本次完成：只查看文件\n'+heading+'\n不应导入的附注\n下一步：继续核对')
                    self.assertEqual(value['last_done'], '只查看文件')
                    self.assertEqual(value['current_task'], '')
                    self.assertIsNone(value['source_snapshot_id'])


if __name__ == '__main__':
    unittest.main()
