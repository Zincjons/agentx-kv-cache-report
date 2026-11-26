import json
import subprocess
import sys
import tempfile
import unittest
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
            s = self.run_cli(['examples/agentx.py'], Path(tmp) / 'agentx')
            self.assertEqual(s['request_count'], 6)
            self.assertEqual(s['subagent_requests'], 1)
            self.assertEqual(s['input_tokens'], 960)
            self.assertEqual(s['cached_input_tokens'], 384)
            self.assertEqual(s['cached_input_rate'], 0.4)

    def test_sage_rule_session_example(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self.run_cli(['examples/sage_session_id.py'], Path(tmp) / 'session')
            self.assertEqual(s['conversations'], 3)
            self.assertEqual(s['input_tokens'], 896)
            self.assertEqual(s['cached_input_tokens'], 256)

    def test_sage_user_example_and_complete_pilot_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            common = Path(tmp) / 'common'
            s = self.run_cli(['examples/sage_user_id.py'], common)
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
            s = self.run_cli(['examples/sage_cache_aware.py'], Path(tmp) / 'cache')
            methods = {r['label']:r for r in s['results']}
            self.assertEqual(methods['shared-unlimited']['cached_input_tokens'], 576)
            self.assertEqual(methods['shared-lru']['cached_input_tokens'], 576)
            self.assertEqual(methods['replicas-round-robin']['cached_input_tokens'], 384)
            self.assertEqual(methods['replicas-cache-aware']['cached_input_tokens'], 576)
            self.assertEqual(methods['replicas-cache-aware']['replica_requests'], [5, 0])

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
