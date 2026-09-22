"""Canonical AHP wire, not the separate host-orchestration transport tests."""
import copy
import hashlib
import json
import subprocess
import sys
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from compaction_wire_matrix import LANGUAGES, cases, check, probe, run_pair, wire_equal


class WireValidationTests(unittest.TestCase):
    def fixture(self):
        ref={'ref':'receiver-assigned-opaque-ref', 'sha256':hashlib.sha256(b'x').hexdigest(), 'size':1}
        event={'type':'compaction.before','id':'case:before','instructions':{'id':'case:instructions','selection':'body','mediaType':'text/plain','body':ref},'trigger':'manual'}
        request={'method':'hooks/intercept','id':'case:before','params':{'event':event,'capabilities':{'modify':{'instructions':{'replace':True,'merge':False}}}}}
        reply={'jsonrpc':'2.0','id':'case:before','result':{'protocolVersion':'draft'}}
        trace={'subscription':'case-before-0','request':request,'response':reply}
        output={'name':'case','result':{'instructions':'x','generated':False,'applied':False,'failures':[],'messages':[],'summary':None,'bodies':{}},'downstream':[],'trace':[trace]}
        receipt=copy.deepcopy(trace);receipt['bodies']={'case:instructions':'x'}
        expected={'case':{'instructions':'x','generated':False,'applied':False,'failures':0,'final':None,'seen':['x']}}
        return [output],[receipt],expected

    def test_equal_json_numbers_remain_equal(self):
        self.assertTrue(wire_equal({'size': 1}, {'size': 1.0}))

    def test_valid_fixture(self):
        self.assertEqual(check(*self.fixture()),1)

    def test_wire_subscription_id_rejected(self):
        outputs,receipts,expected=self.fixture()
        for entry in (outputs[0]['trace'][0],receipts[0]):
            entry['request']['params']['subscriptionId']='caller-scope'
        with self.assertRaisesRegex(AssertionError,'wire subscription identity'):
            check(outputs,receipts,expected)

    def test_case_count(self):
        with self.assertRaisesRegex(AssertionError,'case count'):
            check([],[],{'missing':{}})

    def test_result_flags_are_booleans(self):
        for key in ('generated','applied'):
            with self.subTest(key=key):
                outputs,receipts,expected=self.fixture()
                outputs[0]['result'][key]=0
                with self.assertRaises(AssertionError):check(outputs,receipts,expected)

    def test_body_size_is_not_boolean(self):
        outputs,receipts,expected=self.fixture()
        for entry in (outputs[0]['trace'][0],receipts[0]):
            entry['request']['params']['event']['instructions']['body']['size']=True
        with self.assertRaises(AssertionError):check(outputs,receipts,expected)

    def test_capability_flags_are_booleans(self):
        for key,value in (('replace',1),('merge',0)):
            with self.subTest(key=key):
                outputs,receipts,expected=self.fixture()
                for entry in (outputs[0]['trace'][0],receipts[0]):
                    entry['request']['params']['capabilities']['modify']['instructions'][key]=value
                with self.assertRaises(AssertionError):check(outputs,receipts,expected)

    def test_receipt_evidence_uses_recursive_typed_equality(self):
        for field in ('request','response'):
            with self.subTest(field=field):
                outputs,receipts,expected=self.fixture()
                outputs[0]['trace'][0][field]['extra']=[{'flag':True}]
                receipts[0][field]['extra']=[{'flag':1}]
                with self.assertRaisesRegex(AssertionError,'receiver wire evidence'):
                    check(outputs,receipts,expected)

    def test_subscription_matching_is_type_sensitive(self):
        outputs,receipts,expected=self.fixture()
        outputs[0]['trace'][0]['subscription']=True
        receipts[0]['subscription']=1
        with self.assertRaisesRegex(AssertionError,'receiver receipt count'):
            check(outputs,receipts,expected)

    def test_probe_uses_credential_scoped_endpoint(self):
        outputs,_,_=self.fixture()
        replies=[{'error':{'code':-32602}}]*4
        with patch('compaction_wire_matrix.transport_commands',return_value={'python':['unused']}), patch('compaction_wire_matrix.subprocess.run',return_value=subprocess.CompletedProcess([],0,json.dumps(replies),'')) as send:
            self.assertEqual(probe('python','python','http','http://unused','scope-token',[],outputs,{}),4)
        plan=json.loads(send.call_args.kwargs['input'])
        self.assertEqual(plan['endpoint'],'http://unused/hooks/intercept')
        self.assertEqual(plan['token'],'scope-token')
        for request in plan['requests']:
            self.assertNotIn('subscriptionId',request['params'])

    def test_negative_probe_validates_count_and_codes(self):
        outputs,_,_=self.fixture()
        for replies in ([],[{'error':{'code':-32602}}]*3,[{'result':{}}]*4,[{'error':{'code':True}}]*4):
            with self.subTest(replies=replies), patch('compaction_wire_matrix.transport_commands',return_value={'python':['unused']}), patch('compaction_wire_matrix.subprocess.run',return_value=subprocess.CompletedProcess([],0,json.dumps(replies),'')):
                with self.assertRaises(AssertionError):
                    probe('python','python','http','http://unused','token',[],outputs,{})

    def test_noisy_receiver_startup_failure_has_bounded_tail_and_closes_file(self):
        command=[sys.executable,'-c',"import sys; sys.stderr.write('x'*100000+'TAIL'); sys.stderr.flush()"]
        popen=subprocess.Popen
        processes=[]
        def start(*args,**kwargs):
            process=popen(*args,**kwargs)
            processes.append((process,kwargs['stderr']))
            return process
        with patch('compaction_wire_matrix.commands',return_value={'python':command}), patch('compaction_wire_matrix.subprocess.Popen',side_effect=start):
            result=run_pair(('python','python','http'))
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['error'],'receiver startup failed: '+'x'*1496+'TAIL')
        self.assertEqual(len(processes),1)
        process,stderr=processes[0]
        self.assertIsNotNone(process.returncode)
        self.assertTrue(stderr.closed)


class CanonicalCompactionMatrixTests(unittest.TestCase):
    def test_all_http_and_stdio_pairs(self):
        pairs=[(s,r,t) for t in ('http','stdio') for s in LANGUAGES for r in LANGUAGES]
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(run_pair,pairs))
        self.assertEqual(len(results),32)
        for result in results:
            with self.subTest(sender=result['sender'],receiver=result['receiver'],transport=result['transport']):
                self.assertEqual(result['status'],'passed',result.get('error'))
                self.assertEqual(result['scenarios'],8)
                self.assertEqual(result['receiverRejections'],4)
                self.assertEqual(result['canonicalExchanges'],27)
                self.assertGreaterEqual(result['preuploadedBodies'],result['referencedBodies'])
                self.assertEqual(result['referencedBodies'],27)

    def test_expectations_are_not_subscription_configuration(self):
        rows,config,expected=cases()
        self.assertEqual(len(rows),8)
        self.assertEqual(set(expected),{r['name'] for r in rows})
        for row in rows:
            self.assertEqual(set(row),{'name','before','after'})
            for hook in row['before']+row['after']:
                self.assertEqual(set(hook),{'supplier','failurePolicy'})
                action=config[hook['supplier']]
                self.assertIn(action['kind'],('append','effects'))
                self.assertNotIn('expected',action)

if __name__=='__main__':unittest.main()
