"""Shared canonical lineage checks; these do not claim language runtime coverage."""
from copy import deepcopy
import json
from unittest import TestCase, main
from task_lineage import TaskLineage, LineageError, ROOT


def message(kind, identity, parent=None, source='urn:test:harness'):
    value=json.loads((ROOT/'fixtures/draft/http'/('catalogue-'+kind+'.valid.json')).read_text())
    event=value['params']['event']; event.update(id=identity,source=source)
    if parent is not None: event['parentEventId']=parent
    return value


class LineageTests(TestCase):
    def setUp(self): self.tracker=TaskLineage()
    def accept(self,value): return self.tracker.accept_wire(json.dumps(value).encode())

    def test_known_task_pair_preserves_identity_not_proposed_values(self):
        self.accept(message('task.change.before','before'))
        after=message('task.change.after','after','before'); after['params']['event']['task']['change']['status']='done'
        self.accept(after); self.assertEqual(len(self.tracker.events),2)

    def test_wrong_task_pair_rejected_without_mutation(self):
        self.accept(message('task.change.before','before')); before=deepcopy(self.tracker.events)
        after=message('task.change.after','after','before'); after['params']['event']['task']['id']='other'
        with self.assertRaises(LineageError): self.accept(after)
        self.assertEqual(self.tracker.events,before)

    def test_unknown_parent_allowed_for_filtered_subscriptions(self):
        self.accept(message('task.change.after','after','filtered-out'))
        self.assertEqual(len(self.tracker.events),1)

    def test_producer_requires_known_parent(self):
        self.tracker=TaskLineage(require_known_parents=True)
        with self.assertRaises(LineageError): self.accept(message('task.change.after','after','missing'))

    def test_source_local_parent_never_crosses_source(self):
        self.accept(message('task.change.before','same',source='urn:source:a'))
        after=message('task.change.after','after','same',source='urn:source:b')
        after['params']['event']['task']['id']='different-task'; self.accept(after)

    def test_late_parent_cycle_rejected(self):
        self.accept(message('task.change.before','a','b'))
        with self.assertRaises(LineageError): self.accept(message('task.change.before','b','a'))
        self.assertEqual(len(self.tracker.events),1)

    def test_duplicate_wire_members_rejected(self):
        with self.assertRaises(LineageError): self.tracker.accept_wire('{"jsonrpc":"2.0","jsonrpc":"2.0"}')
        self.assertEqual(self.tracker.events,{})

    def test_noop_actual_task_change_rejected(self):
        after=message('task.change.after','after'); task=after['params']['event']['task']
        task.update(operation='update',prior={'status':'open'},change={'status':'open'})
        with self.assertRaises(LineageError): self.accept(after)


if __name__ == '__main__': main()
