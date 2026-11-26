import json
import subprocess
import sys
import tempfile
import unittest
import shutil
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT / 'examples' / 'fixtures'


class ExampleTests(unittest.TestCase):
    def run_cli(self, arguments, output):
        result = subprocess.run([sys.executable, '-B', *arguments, '--output-dir', str(output)],
                                cwd=PROJECT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads((output / 'summary.json').read_text())

    def test_agentx_branch_snapshot_and_conversation_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self.run_cli(['examples/agentx.py', '--demo'], Path(tmp) / 'agentx')
            self.assertEqual(s['request_count'], 6)
            self.assertEqual(s['subagent_requests'], 1)
            self.assertEqual(s['input_tokens'], 960)
            self.assertEqual(s['cached_input_tokens'], 384)
            self.assertEqual(s['cached_input_rate'], 0.4)
            self.assertEqual(s['example_data_mode'], 'synthetic-demo')

    def test_sage_rule_session_example(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self.run_cli(['examples/sage_session_id.py', '--demo'], Path(tmp) / 'session')
            self.assertEqual(s['conversations'], 3)
            self.assertEqual(s['input_tokens'], 896)
            self.assertEqual(s['cached_input_tokens'], 256)

    def test_sage_user_example_and_complete_pilot_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            common = Path(tmp) / 'common'
            s = self.run_cli(['examples/sage_user_id.py', '--demo'], common)
            self.assertEqual(s['totals']['requests'], 5)
            self.assertEqual(s['totals']['input_tokens'], 896)
            self.assertEqual(s['totals']['user_daily_cached_tokens'], 448)
            self.assertEqual(s['totals']['global_daily_cached_tokens'], 576)
            pilot = self.run_cli(['-m', 'experiments.request_chains', '--common-dir', str(common)],
                                 Path(tmp) / 'pilot')
            self.assertEqual(pilot['baseline_user_model']['daily_cached_tokens'], 448)
            self.assertEqual(pilot['profiles'][0]['score']['daily_cached_tokens'], 0)
            self.assertEqual(pilot['profiles'][0]['graph']['unassigned_singleton_requests'], 5)

    def test_direct_cache_aware_example(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self.run_cli(['examples/sage_cache_aware.py', '--demo'], Path(tmp) / 'cache')
            methods = {r['label']:r for r in s['results']}
            self.assertEqual(methods['shared-unlimited']['cached_input_tokens'], 576)
            self.assertEqual(methods['shared-lru']['cached_input_tokens'], 576)
            self.assertEqual(methods['replicas-round-robin']['cached_input_tokens'], 384)
            self.assertEqual(methods['replicas-cache-aware']['cached_input_tokens'], 576)
            self.assertEqual(methods['replicas-cache-aware']['replica_requests'], [5, 0])

    def test_default_mode_never_falls_back_to_synthetic_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkout=Path(tmp)/'checkout'
            shutil.copytree(PROJECT,checkout,ignore=shutil.ignore_patterns('results','data','__pycache__'))
            for name in ['agentx','sage_session_id','sage_user_id','sage_cache_aware','run_all']:
                result=subprocess.run([sys.executable,'-B',str(checkout/'examples'/f'{name}.py')],
                                      cwd=checkout,capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,2,result.stdout+result.stderr)
                self.assertIn('--demo',result.stderr)
            self.assertFalse((checkout/'results').exists())

    def test_explicit_dataset_uses_full_agentx_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=self.run_cli(['examples/agentx.py','--input',str(FIXTURES/'agentx.jsonl')],Path(tmp)/'actual')
            self.assertEqual(s['conversation_limit'],393)
            self.assertEqual(s['example_data_mode'],'dataset')

    def test_real_cache_preset_is_four_replicas_500k_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=self.run_cli(['examples/sage_cache_aware.py','--day-dir',str(FIXTURES/'sage'/'2026-11-18'/'Sage-S2.1-flash')],Path(tmp)/'actual')
            row=next(r for r in s['results'] if r['label']=='replicas-cache-aware')
            self.assertEqual((row['replicas'],row['capacity_blocks_per_replica']),(4,500000))
            self.assertEqual(s['primary_result_label'],'replicas-cache-aware')
            self.assertEqual(s['example_data_mode'],'dataset')

    def test_real_user_preset_requires_all_three_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ['sage_session_id', 'sage_user_id', 'sage_cache_aware']:
                with self.subTest(example=name):
                    result=subprocess.run([sys.executable,'-B',f'examples/{name}.py','--data-root',str(FIXTURES/'sage'),'--output-dir',str(Path(tmp)/name)],cwd=PROJECT,capture_output=True,text=True,timeout=30)
                    self.assertEqual(result.returncode,2)
                    self.assertIn('2026-11-16',result.stderr)

    def test_all_sage_examples_default_to_full_dates_and_keep_cross_day_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'sage'
            for day in ['2026-11-16', '2026-11-17', '2026-11-18']:
                shutil.copytree(FIXTURES/'sage'/'2026-11-18',root/day)
            reports={name:self.run_cli([f'examples/{name}.py','--data-root',str(root)],Path(tmp)/name)
                     for name in ['sage_session_id','sage_user_id','sage_cache_aware']}
            for s in reports.values():
                self.assertEqual(s['days'],['2026-11-16','2026-11-17','2026-11-18'])
            session=reports['sage_session_id']
            self.assertEqual((session['request_count'],session['input_tokens'],session['cached_input_tokens']),(15,2688,768))
            user=reports['sage_user_id']
            self.assertEqual((user['totals']['requests'],user['totals']['input_tokens']),(15,2688))
            self.assertEqual(user['totals']['user_daily_cached_tokens'],3*448)
            self.assertGreater(user['primary_cached_input_tokens'],user['totals']['user_daily_cached_tokens'])
            self.assertEqual(user['primary_result_label'],'user_continuous')
            cache=reports['sage_cache_aware']
            primary=next(r for r in cache['results'] if r['label']=='replicas-cache-aware')
            cold=next(r for r in cache['daily_cold_starts_weighted_results'] if r['label']=='replicas-cache-aware')
            self.assertEqual((primary['requests'],primary['input_tokens']),(15,2688))
            self.assertEqual(cold['cached_input_tokens'],3*576)
            self.assertGreater(primary['cached_input_tokens'],cold['cached_input_tokens'])

    def test_repeated_dates_rejected_before_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ['sage_session_id','sage_user_id','sage_cache_aware']:
                with self.subTest(example=name):
                    output=Path(tmp)/name
                    result=subprocess.run([sys.executable,'-B',f'examples/{name}.py','--demo','--days','2026-11-18','2026-11-18','--output-dir',str(output)],cwd=PROJECT,capture_output=True,text=True,timeout=30)
                    self.assertEqual(result.returncode,2)
                    self.assertFalse(output.exists())

    def test_full_demo_suite_is_explicit_and_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'demo'
            result=subprocess.run([sys.executable,'-B','examples/run_all.py','--demo','--output-dir',str(output)],cwd=PROJECT,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            s=json.loads((output/'comparison.json').read_text())
            self.assertEqual(s['data_mode'],'synthetic-demo')
            self.assertTrue(all(r['reference_matches'] for r in s['results']))
            self.assertEqual([r['cached_input_tokens'] for r in s['results']],[384,256,448,576])

    def test_portable_schedule_and_trace_entrypoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = ['--data-root', str(FIXTURES / 'sage'), '--days', '2026-11-18']
            schedule = self.run_cli(['-m', 'experiments.schedule', *args], Path(tmp) / 'schedule')
            self.assertEqual(schedule['continuous_three_day_run']['cached_input_tokens'], 576)
            trace = self.run_cli(['-m', 'experiments.trace_users', *args], Path(tmp) / 'trace')
            self.assertEqual(trace['totals']['rule_group_cached_tokens'], 256)
            self.assertEqual(trace['totals']['user_model_daily_cached_tokens'], 448)


if __name__ == '__main__':
    unittest.main()
