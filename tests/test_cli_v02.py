import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from handoffdesk import __version__


class CLIIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cli-v02-', dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.app = self.folder/'app'
        self.source = self.folder/'business'
        self.source.mkdir()
        (self.app/'config').mkdir(parents=True)
        self.state = self.source/'state.json'
        self.state.write_text('{"status":"before"}', encoding='utf-8')
        configuration = {'schema_version': 1, 'projects': [{'id':'demo','name':'演示',
            'roots':{'main':str(self.source)}, 'sources':[{'id':'state','root':'main',
            'path':'state.json','format':'json','fields':{'status':'/status'}}], 'entries':[]}]}
        (self.app/'config/projects.json').write_text(json.dumps(configuration), encoding='utf-8')
        self.package = Path(__file__).resolve().parent.parent

    def command(self, *args):
        return subprocess.run([sys.executable, '-E', '-s', '-B', '-X', 'utf8', '-m', 'handoffdesk',
            '--app-dir',str(self.app),'--data-dir',str(self.folder/'data'),*args],
            cwd=self.package,capture_output=True,text=True,encoding='utf-8',timeout=10)

    def test_plan_changes_and_conflict_through_cli(self):
        self.assertEqual(self.command('refresh','demo','--json').returncode,0)
        first=json.loads(self.command('show','demo','--json').stdout)
        plan=self.folder/'plan.json'
        plan.write_text(json.dumps({'current_task':'开始工作','next_action':'核对登记状态',
            'source_snapshot_id':first['snapshot']['id']},ensure_ascii=False),encoding='utf-8')
        saved=self.command('plan','演示','--file',str(plan),'--expect-revision','0')
        self.assertEqual(saved.returncode,0,saved.stderr)
        self.assertEqual(json.loads(saved.stdout)['event_kind'],'plan')
        card=json.loads(self.command('show','demo','--json').stdout)
        self.assertIsNone(card['last_completion'])
        self.assertEqual(card['handoff']['last_done'],'')
        self.state.write_text('{"status":"after"}',encoding='utf-8')
        self.command('refresh','demo')
        changes=json.loads(self.command('changes','demo').stdout)
        self.assertTrue(changes['changes'])
        conflict=self.command('plan','demo','--file',str(plan),'--expect-revision','0')
        self.assertEqual(conflict.returncode,3,conflict.stdout+conflict.stderr)
        self.assertEqual(json.loads(conflict.stdout)['current']['revision'],1)

    def test_cli_version_and_rejects_oversized_input(self):
        result=self.command('--version')
        self.assertEqual(result.returncode,0)
        self.assertIn(__version__,result.stdout)
        source_before=self.state.read_bytes()
        too_large=self.folder/'too-large.json'
        too_large.write_text(' '*(262144+1))
        result=self.command('plan','demo','--file',str(too_large),'--expect-revision','0')
        self.assertEqual(result.returncode,2)
        self.assertEqual(self.state.read_bytes(),source_before)


if __name__=='__main__':
    unittest.main()
