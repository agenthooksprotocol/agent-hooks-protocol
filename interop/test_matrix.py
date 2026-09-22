"""Harness integrity tests; intentionally dishonest adapters must not pass."""
from copy import deepcopy
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch
import matrix
import generate_scenarios


class ScenarioGeneration(unittest.TestCase):
    def test_continue_without_instruction_is_accepted(self):
        document = generate_scenarios.build()
        row = next(row for row in document['scenarios']
                   if row['id'] == 'flow-continue-missing-instruction')
        self.assertNotIn('expectError', row)
        self.assertNotIn('negative', row['tags'])
        self.assertNotIn('schema-invalid', row['tags'])
        self.assertEqual(row['expected'], {
            'decision': 'allow', 'executed': False, 'flow': 'continue',
            'messages': ['Visible only after acceptance'],
            'continuationInstructions': [], 'continuationRemaining': 1,
        })


class Integrity(unittest.TestCase):
    def setUp(self):
        request=deepcopy(matrix.load(matrix.HERE/'scenarios.json')['scenarios'][0]['request'])
        request['id']=request['params']['event']['id']='one'
        self.scenarios = [dict(id='one', request=request, expected=dict(executed=True, input={'task': 1}))]
        self.report = dict(language='fake', results=[dict(id='one', status='passed', actual=dict(executed=True, input={'task': 1}, extra=None))])
        self.receipts = dict(requests=[dict(id='one', method='hooks/intercept',message=deepcopy(request))])

    def check(self, report=None, receipts=None, code=0):
        rows, errors = matrix.verify(self.scenarios, self.report if report is None else report, self.receipts if receipts is None else receipts, 'fake', code)
        return not errors and all(r['status'] == 'passed' for r in rows)

    def test_receipt_wrapper_identity_distinguishes_boolean_from_number(self):
        request = deepcopy(self.scenarios[0]['request'])
        request['id'] = request['params']['event']['id'] = 1
        receipt = {'id': 1, 'eventId': 1, 'method': request['method'],
                   'message': request}
        self.assertTrue(matrix.receipt_matches(receipt, request))
        for key in ('id', 'eventId'):
            with self.subTest(key=key):
                forged = dict(receipt, **{key: True})
                self.assertFalse(matrix.receipt_matches(forged, request))

    def test_error_requires_explicit_rejection(self):
        self.scenarios[0]['expectError'] = True
        result = self.report['results'][0]
        for actual in (None, {}, {'rejected': 1}, {'rejected': False}):
            with self.subTest(actual=actual):
                result['actual'] = actual
                self.assertFalse(self.check())
        result['actual'] = {'rejected': True}
        self.assertTrue(self.check())
        del result['actual']
        self.assertFalse(self.check())

    def test_partial_expected(self):
        self.assertTrue(self.check())

    def test_forbidden_semantic_extras(self):
        for field,value in [('result','unauthorized'),('flow','continue'),('injections',[]),('continuationInstructions',[]),('continuationRemaining',1),('rejected',True)]:
            with self.subTest(field=field):
                report=deepcopy(self.report);report['results'][0]['actual'][field]=value
                self.assertFalse(self.check(report=report))
    def test_all_22_deny_ask_outcomes_reject_supplied_output(self):
        scenarios=matrix.load(matrix.HERE/'scenarios.json')['scenarios']
        rows=[s for s in scenarios if s.get('expected',{}).get('decision') in ('deny','ask')]
        self.assertEqual(len(rows),22)
        for scenario in rows:
            actual=deepcopy(scenario['expected']);actual['result']='unauthorized supplied output'
            report={'language':'fake','results':[{'id':scenario['id'],'status':'passed','actual':actual}]}
            result,errors=matrix.verify([scenario],report,{'requests':[deepcopy(scenario['request'])]},'fake',0)
            self.assertEqual(result[0]['status'],'failed',scenario['id'])
    def test_metadata_only_receipt_is_not_wire_evidence(self):
        self.receipts['requests'][0].pop('message');self.assertFalse(self.check())
    def test_full_grid_cannot_pass_on_id_method_receipts(self):
        scenarios=matrix.load(matrix.HERE/'scenarios.json')['scenarios']
        report={'language':'fake','results':[{'id':s['id'],'status':'passed','actual':deepcopy(s.get('expected',{'rejected':True}))} for s in scenarios]}
        receipts={'requests':[{'id':s['request']['id'],'method':s['request']['method']} for s in scenarios]}
        rows,errors=matrix.verify(scenarios,report,receipts,'fake',0)
        self.assertTrue(errors)
        self.assertEqual(sum(row['status']=='passed' for row in rows),0)
        self.assertEqual(len(rows),len(scenarios))

    def test_wire_payload_mutation(self):
        self.receipts['requests'][0]['message']['params']['event']['tool']['input']={'forged':True}
        self.assertFalse(self.check())
    def test_exact_full_envelope_is_valid(self):
        self.receipts['requests']=[deepcopy(self.scenarios[0]['request'])];self.assertTrue(self.check())

    def test_claimed_pass_wrong_actual(self):
        self.report['results'][0]['actual']['executed'] = False
        self.assertFalse(self.check())

    def test_boolean_not_number(self):
        self.report['results'][0]['actual']['input']['task'] = True
        self.assertFalse(self.check())

    def test_missing_expected_key(self):
        del self.report['results'][0]['actual']['input']
        self.assertFalse(self.check())

    def test_duplicate_and_missing_ids(self):
        for results in ([], self.report['results'] * 2, [dict(id='other', status='passed', actual={})]):
            with self.subTest(results=results):
                self.assertFalse(self.check(dict(language='fake', results=results)))

    def test_receipts_exactly_once(self):
        for requests in ([], self.receipts['requests'] * 2, [dict(id='other', method='hooks/intercept')]):
            self.assertFalse(self.check(receipts=dict(requests=requests)))

    def test_receipt_method(self):
        self.receipts['requests'][0]['method'] = 'intercept'
        self.assertFalse(self.check())

    def test_unsupported_and_inapplicable_not_passed(self):
        for status in ('unsupported', 'inapplicable', 'failed', 'invented'):
            self.report['results'][0]['status'] = status
            self.assertFalse(self.check())

    def test_nonzero_even_with_good_report(self):
        self.assertFalse(self.check(code=1))

    def test_receipt_accepted_false_invalidates_rows(self):
        for accepted in (False, None, 0, 'true'):
            with self.subTest(accepted=accepted):
                self.receipts['requests'][0]['accepted'] = accepted
                rows, errors = matrix.verify(self.scenarios, self.report, self.receipts, 'fake', 0)
                self.assertIn('Receipt explicitly not accepted', errors)
                self.assertEqual(rows[0]['status'], 'failed')
        self.receipts['requests'][0]['accepted'] = True
        self.assertTrue(self.check())

    def test_summary_excludes_integrity_failures(self):
        for defect in ('receipt', 'exit', 'duplicate', 'cleanup'):
            with self.subTest(defect=defect):
                report = dict(self.report)
                if defect == 'duplicate':
                    report['results'] = self.report['results'] * 2
                receipts = {'requests': []} if defect == 'receipt' else self.receipts
                rows, errors = matrix.verify(self.scenarios, report, receipts, 'fake', 1 if defect == 'exit' else 0)
                if defect == 'cleanup':
                    errors.append('Cleanup: PermissionError')
                    matrix.invalidate(rows, errors)
                summary = matrix.summarize([dict(status='failed', results=rows)])
                self.assertTrue(errors)
                self.assertEqual(summary, {'groups': {'failed': 1}, 'scenarios': {'failed': 1}})
                self.assertEqual(rows[0]['adapterStatus'], 'passed')
                self.assertEqual(rows[0]['actual'], self.report['results'][0]['actual'])

    def test_summary_counts_verified_passes(self):
        rows, errors = matrix.verify(self.scenarios, self.report, self.receipts, 'fake', 0)
        self.assertFalse(errors)
        self.assertEqual(matrix.summarize([dict(status='passed', results=rows)]),
                         {'groups': {'passed': 1}, 'scenarios': {'passed': 1}})

    def test_missing_actual(self):
        del self.report['results'][0]['actual']
        self.assertFalse(self.check())

    def test_malformed_reports(self):
        for value in ({}, dict(language='wrong', results=[]), dict(language='fake', results=None)):
            with self.assertRaises(ValueError):
                self.check(value)

    def test_negative_requires_receipt(self):
        self.scenarios[0].pop('expected')
        self.scenarios[0]['expectError'] = True
        self.report['results'][0]['actual'] = {'rejected': True}
        self.assertTrue(self.check())
        self.assertFalse(self.check(receipts={'requests': []}))

    def test_spawn_errors_fail_every_applicable_group(self):
        adapter = dict(language='fake', cwd=str(matrix.HERE), client=['/nonexistent/ahp-client'], server=['/nonexistent/ahp-server'])
        with matrix.Issuer() as issuer:
            groups = matrix.run_group(adapter, adapter, self.scenarios, matrix.HERE/'scenarios.json', issuer, 1)
        self.assertEqual(sum(g['status'] == 'failed' for g in groups), 6)
        self.assertTrue(all(g['results'][0]['status'] == 'failed' for g in groups if g['status'] == 'failed'))

    def test_missing_report_from_successful_process(self):
        adapter = dict(language='fake', cwd=str(matrix.HERE), client=[sys.executable, '-c', 'pass'], server=[sys.executable, '-c', 'pass'])
        with matrix.Issuer() as issuer:
            groups = matrix.run_group(adapter, adapter, self.scenarios, matrix.HERE/'scenarios.json', issuer, 1)
        self.assertEqual(sum(g['status'] == 'failed' for g in groups), 6)

    def test_readiness_allows_additive_health_metrics(self):
        with patch.object(matrix.Path, 'exists', return_value=True), patch.object(matrix, 'load', return_value={'controlEndpoint': 'http://local'}), patch.object(matrix, 'control', return_value={'ready': True, 'tlsRejections': 0}):
            self.assertEqual(matrix.ready('unused', Mock(), 1), {'controlEndpoint': 'http://local'})

    def test_readiness_rejects_nonboolean_ready(self):
        for ready in (False, 1, 'true', None):
            with patch.object(matrix.Path, 'exists', return_value=True), patch.object(matrix, 'load', return_value={'controlEndpoint': 'http://local'}), patch.object(matrix, 'control', return_value={'ready': ready}):
                with self.assertRaises(ValueError):
                    matrix.ready('unused', Mock(), 1)

    def test_timeout_cleanup(self):
        process = subprocess.Popen([sys.executable, '-c', 'import threading; threading.Event().wait()'], start_new_session=True)
        matrix.stop(process)
        self.assertIsNotNone(process.poll())


if __name__ == '__main__':
    unittest.main()
