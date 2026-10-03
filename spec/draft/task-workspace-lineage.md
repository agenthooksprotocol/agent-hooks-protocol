# Task, workspace, file changes and event lineage

This document defines task changes, workspace changes, and single-parent event lineage in the current draft.
`schema/draft/task-workspace-event.schema.json` contains five standalone event
definitions. Each includes the canonical event envelope, without a reference back
to the catalogue. Before events can be intercepted or observed; after events and
`file.changed` are observations only. Neither boundary requires the other.

## Payloads

* `task.change.before` and `task.change.after`: `task` contains durable `id`,
  `operation` (`create`, `update`, `remove`), and `change`, with optional `prior`
  and `description`. `change` is the proposed change before, and the actually
  applied change after. `prior` contains only known previous fields, not an
  invented inventory. State keys and status values are harness-defined. Removal
  is represented by the operation; an empty change object is valid for removal.
  Updates carry changed fields, not a promise of a universal merge operation.
* `workspace.change.before` and `workspace.change.after`: `workspace` contains
  `kind` (`cwd`, `roots`, `switch`) and `change`; optional `prior` and `reason`.
  `change`/`prior` each contain available `cwd` and/or `workspaceRoots`. A roots
  list may be empty (all roots removed). These fields describe execution context,
  not file edits, plugin discovery, or an automatic worktree lifecycle.
* `file.changed`: nonempty `changes` contains `path`, `operation` (`create`,
  `update`, `remove`) and `agentCaused`. Optional `before`/`after` are canonical
  uploaded-body content references, not inline file contents. Missing references
  do not imply empty files. Content permissions and gaps still apply. No file
  read event or file interception capability is added.

Extension strategy: event envelopes remain open for canonical envelope evolution
and extension metadata. The `task`, `workspace`, workspace-state and file-change
objects have closed protocol keys to detect misspelled payload fields. **Task
`change` and `prior` remain open objects**, including native status values; native
payload and tool arguments retain their existing open definitions. This is not
blanket closure of domain data. Use the envelope `extensions`/`native` mechanisms
for additional protocol metadata, not invented task-state restrictions.

## Lineage and actual changes

Event identity is `(source, id)`; `parentEventId` is an event ID in that same
source, never a call ID. Parentage is single and acyclic. A producer must know
its parent; omit the field otherwise. A subscriber may not have received that
parent and must not demand that it fetch ancestry to interpret the event. Parent
operations can already have completed. Links do not grant permission or imply
success. Timestamps do not establish ancestry.

When both task boundaries exist, after parents to its corresponding before.
That before can parent to the originating tool event. With after-only support,
the after can parent directly to a known originating tool event. Non-tool origins
need no fabricated call or before event. Workspace after similarly links to its
known proposal. Tool invocations retain their independent standard events.

Resolve interception before constructing dependent proposals: a rewritten tool
argument must produce a proposal for the effective task, not the original task.
A denied proposal with no actual change produces no after event. Actual partial
changes remain observable; after values need not equal proposed values. Task
permission does not undo completed side effects or authorize later proposals.
All resolved enclosing invocations still owe honest tool completions where
completion coverage is advertised.

`interop/task_lineage.py` validates full canonical JSON-RPC messages plus these
schemas before recording edges. Its optional `require_known_parents=True` is for
producer histories, not filtered subscribers. It checks cycles (including late
parents), source scope, matching task identity/operation for known proposal
parents, and explicit no-op actual task/workspace updates. It does not invent
missing history or mistake different subscription views for delivery retries.
It cannot prove a disk/task mutation occurred from a notification alone, infer a
hidden proposal, establish native boundary support, or enforce producer emission
suppression merely by observing denial. Those are producer integration duties.

## Deliberate limitations / unresolved shape

The design permits one indivisible task change set but does not choose its wire
member identity/operation shape. This module implements single-task changes only;
no speculative batch API or per-member veto semantics are introduced. The
coordinator must settle that representation before claiming change-set coverage.
No universal task state machine, agent entity, inventory API, legacy profile,
rollback semantics, or blanket workspace path replacement is introduced.
