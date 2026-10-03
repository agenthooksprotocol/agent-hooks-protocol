"""Canonical wire validation and source-local lineage checks, without adapter policy.

One tracker is one receiving connection. Unknown parents are legal for filtered
subscriptions. Producers can enable require_known_parents when their full event
history is available. No task inventory or inference from timestamps is used.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import check_conformance as checker

OWNED_TYPES = frozenset(('task.change.before', 'task.change.after',
                        'workspace.change.before', 'workspace.change.after',
                        'file.changed'))


class LineageError(ValueError):
    pass


class TaskLineage:
    def __init__(self, *, require_known_parents=False):
        self.require_known_parents = require_known_parents
        self.snapshot = checker.Snapshot.resolve(ROOT)
        self.store = checker.SchemaStore(self.snapshot)
        self.validator = checker.SubsetValidator(self.store)
        self.events = {}
        self.parents = {}

    def _validate(self, value, name):
        path = self.snapshot.schema_dir / name
        errors = self.validator.validate(value, self.store.load(path), path)
        if errors:
            raise LineageError(str(errors))

    def accept_wire(self, wire):
        """Accept serialized JSON-RPC bytes/text; validation precedes all mutation."""
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise LineageError('duplicate JSON member: ' + key)
                result[key] = value
            return result
        def invalid_constant(value):
            raise LineageError('non-JSON numeric constant: ' + value)
        try:
            message = json.loads(wire, object_pairs_hook=unique,
                                 parse_constant=invalid_constant)
        except (ValueError, UnicodeError) as exc:
            raise LineageError(str(exc)) from exc
        return self.accept(message)

    def accept(self, message):
        self._validate(message, 'schema.json')
        if message.get('method') not in ('hooks/intercept', 'hooks/observe'):
            return None  # Other canonical protocol messages carry no event edge.
        event = message['params']['event']
        kind = event['type']
        if kind in OWNED_TYPES:
            self._validate(event, 'task-workspace-event.schema.json')
            if message['method'] == 'hooks/intercept' and not kind.endswith('.before'):
                raise LineageError('actual changes are observe-only')
            self._check_actual(event)
        key = (event['source'], event['id'])
        parent_id = event.get('parentEventId')
        parent = (event['source'], parent_id) if parent_id is not None else None
        # Different subscription views need not be byte-identical. Identity and
        # parentage are invariant; byte-for-byte delivery retry is transport scope.
        if key in self.events:
            old = self.events[key]
            if old['type'] != kind or self.parents[key] != parent:
                raise LineageError('event identity reused with different type or parent')
            if kind.startswith('task.change.') and old['task']['id'] != event['task']['id']:
                raise LineageError('event identity reused for another task')
        if parent is not None:
            if self.require_known_parents and parent not in self.events:
                raise LineageError('producer parent must be a known source-local event')
            seen = {key}
            cursor = parent
            while cursor is not None:
                if cursor in seen:
                    raise LineageError('cyclic parentEventId')
                seen.add(cursor)
                cursor = self.parents.get(cursor)
            if parent in self.events:
                self._check_pair(self.events[parent], event)
        # Check late-arriving parents too: subscriber delivery need not include
        # ancestors first, or include them at all.
        for child, edge in self.parents.items():
            if edge == key:
                self._check_pair(event, self.events[child])
        self.events[key] = copy.deepcopy(event)
        self.parents[key] = parent
        return copy.deepcopy(event)

    @staticmethod
    def _check_pair(parent, child):
        for family, payload, identity in [('task', 'task', 'id'),
                                           ('workspace', 'workspace', 'kind')]:
            if (child['type'] == family + '.change.after'
                    and parent['type'] == family + '.change.before'):
                before, after = parent[payload], child[payload]
                if before[identity] != after[identity]:
                    raise LineageError('after does not match its known proposal')
                if family == 'task' and before['operation'] != after['operation']:
                    raise LineageError('after operation does not match its proposal')
                # Do not compare proposed and applied values: partial application
                # and interception modifications can legitimately differ.

    @staticmethod
    def _check_actual(event):
        kind = event['type']
        if kind == 'task.change.after':
            task = event['task']
            change, prior = task['change'], task.get('prior')
            if task['operation'] == 'update':
                if not change:
                    raise LineageError('actual task update needs changed fields')
                if prior is not None and all(k in prior and prior[k] == v
                                             for k, v in change.items()):
                    raise LineageError('task after reports no actual change')
        if kind == 'workspace.change.after':
            workspace = event['workspace']
            change, prior = workspace['change'], workspace.get('prior')
            if prior is not None and all(k in prior and prior[k] == v
                                         for k, v in change.items()):
                raise LineageError('workspace after reports no actual change')
        if kind == 'file.changed':
            for change in event['changes']:
                if (change['operation'] == 'update' and 'before' in change
                        and 'after' in change and change['before'] == change['after']):
                    raise LineageError('file update reports identical before/after references')
