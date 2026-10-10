"""Real transports and all four independently implemented SDK runtimes."""
import copy, json, subprocess, sys, unittest
from unittest.mock import patch
import compaction_matrix
from concurrent.futures import ThreadPoolExecutor
from compaction_matrix import LANGUAGES, cases, run_pair, check, json_equal, text_parts, inline_text


class CompactionOracleTests(unittest.TestCase):
    def response(self):
        return {'jsonrpc':'2.0','id':'unchanged','result':{
            'instructions':text_parts('base', 'instructions'),'generated':True,'applied':True,'messages':[],
            'failures':[],'summary':text_parts('summary:base', 'logical-summary'),
            'provenance':{'kind':'generated'},'seen':[]}}

    def test_reject_numeric_booleans(self):
        response=self.response();check(cases()[0],response)
        for key in ('generated','applied'):
            bad=copy.deepcopy(response);bad['result'][key]=1
            with self.assertRaises(AssertionError):check(cases()[0],bad)
        bad=copy.deepcopy(response)
        bad['result']['seen']=[{'instructions':text_parts('base', 'instructions'),'summary':None,'bodies':{},
            'boundary':'before','capabilities':{'modify':{'instructions':{'replace':1,'merge':0}}}}]
        with self.assertRaises(AssertionError):check(cases()[0],bad)

    def test_ordered_inline_summary(self):
        response=self.response()
        response['result']['summary']=text_parts('summary:', 'first')+text_parts('base', 'second')
        check(cases()[0],response)
        response['result']['summary'].reverse()
        with self.assertRaises(AssertionError):check(cases()[0],response)

    def test_reject_legacy_summary_and_reference_text(self):
        for summary in ({'id':'logical-summary','ref':'opaque'}, 'summary:base',
                        [dict(text_parts('summary:base')[0], body={'ref':'opaque'})]):
            response=self.response();response['result']['summary']=summary
            with self.assertRaises(AssertionError):check(cases()[0],response)

    def test_response_count_rejected(self):
        output=subprocess.CompletedProcess([],0,stdout='[]',stderr='')
        with patch.object(compaction_matrix.subprocess,'run',return_value=output):
            result=run_pair(('python','python','stdio'))
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['error'],'response count')

    def test_nested_json_boolean_number_distinction(self):
        self.assertFalse(json_equal({'messages':[True]}, {'messages':[1]}))
        self.assertFalse(json_equal({'messages':[0]}, {'messages':[False]}))
        self.assertTrue(json_equal({'messages':[1]}, {'messages':[1.0]}))

    def test_validation_survives_optimization(self):
        bad=self.response();bad['result']['generated']=1
        result=subprocess.run([sys.executable,'-O','-c',
            'import json; from compaction_matrix import check,cases; check(cases()[0],json.loads('+repr(json.dumps(bad))+'))'],
            cwd=compaction_matrix.HERE,capture_output=True,text=True,timeout=10)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('AssertionError',result.stderr)

    def test_noisy_receiver_startup_is_bounded(self):
        command=[sys.executable,'-c',"import os,time; os.write(2,b'x'*200000+b'TAIL'); os.close(1); time.sleep(20)"]
        with patch.object(compaction_matrix,'commands',return_value={'python':command}):
            result=run_pair(('python','python','http'))
        self.assertEqual(result['status'],'failed')
        self.assertTrue(result['error'].endswith('TAIL'))
        self.assertLess(len(result['error']),1600)


class CompactionMatrixTests(unittest.TestCase):
    def test_all_http_and_stdio_pairs(self):
        pairs=[(s,r,t) for t in ('http','stdio') for s in LANGUAGES for r in LANGUAGES]
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(run_pair,pairs))
        self.assertEqual(len(results),32)
        for result in results:
            with self.subTest(sender=result['sender'],receiver=result['receiver'],transport=result['transport']):
                self.assertEqual(result['status'],'passed',result.get('error'))
                self.assertEqual(result['scenarios'],len(cases()))

    def test_oracle_never_sent_to_receiver(self):
        for row in cases():
            request=row['request']
            self.assertEqual(set(request),{'jsonrpc','id','method','params'})
            self.assertEqual(set(request['params']),{'instructions','itemId','before','after','observeOnly'})
            self.assertNotIn('expected',request['params'])

if __name__=='__main__':unittest.main()
