"""Canonical AHP wire, not the separate host-orchestration transport tests."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from compaction_wire_matrix import LANGUAGES, cases, run_pair


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
                self.assertEqual(result['preuploadedBodies'],41)

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
