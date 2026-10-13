"""Conformance aggregation must fail closed without failing cancelled workflows."""
import json
import os
from pathlib import Path
import textwrap
import unittest
from unittest.mock import patch

WORKFLOW = Path(__file__).resolve().parents[2] / '.github/workflows/ci.yml'


class ConformanceWorkflowTests(unittest.TestCase):
    def test_cancelled_runs_do_not_start_the_aggregate(self):
        aggregate = WORKFLOW.read_text().split('  conformance:\n', 1)[1]
        self.assertIn('    if: ${{ !cancelled() }}', aggregate)
        self.assertNotIn('    if: always()', aggregate)

    def test_non_cancelled_aggregate_requires_every_job_to_succeed(self):
        aggregate = WORKFLOW.read_text().split('  conformance:\n', 1)[1]
        script = textwrap.dedent(aggregate.split("python3 - <<'PYTHON'\n", 1)[1].split('          PYTHON', 1)[0])
        jobs = {name: {'result': 'success'} for name in ('snapshots', 'generator', 'smoke')}
        with patch.dict(os.environ, RESULTS=json.dumps(jobs)):
            exec(compile(script, str(WORKFLOW), 'exec'), {})
        for name in jobs:
            for result in ('failure', 'skipped', 'cancelled'):
                failed = {key: dict(value) for key, value in jobs.items()}
                failed[name]['result'] = result
                with self.subTest(job=name, result=result), patch.dict(os.environ, RESULTS=json.dumps(failed)), self.assertRaises(AssertionError):
                    exec(compile(script, str(WORKFLOW), 'exec'), {})
