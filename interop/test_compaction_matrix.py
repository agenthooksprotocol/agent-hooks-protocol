"""Real transports and all four independently implemented SDK runtimes."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from compaction_matrix import LANGUAGES, cases, run_pair


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
