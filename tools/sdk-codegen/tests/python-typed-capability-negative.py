"""Negative consumer fixture: every marked line must fail mypy/Pyright."""
from facade_contract.capability import (
    FlowContinue, HostCapabilities, InjectContext, Modify,
    ToolAfterCapabilities, ToolAfterIntercept, ToolBeforeCapabilities, ToolBeforeIntercept,
)

HostCapabilities(tool_before=ToolAfterCapabilities(intercept=ToolAfterIntercept(deny=True)))  # wrong event
ToolBeforeCapabilities(intercept=ToolAfterIntercept(deny=True))  # wrong interceptor
ToolBeforeIntercept(modify_output=Modify.REPLACE)  # wrong target
ToolBeforeIntercept(flow=FlowContinue(remaining_continuations=0, continuation_count=0))  # wrong flow
ToolBeforeIntercept(allow="true")  # wrong boolean
ToolBeforeIntercept(modify_input={"replace": True})  # dict hydration
HostCapabilities(session_end=ToolBeforeCapabilities(observe=True))  # wrong event observation
HostCapabilities(tool_before={"modes": ["observe"]})  # dict hydration
InjectContext(deliver_at=("now",))  # wrong delivery
FlowContinue(remaining_continuations=0)  # incomplete dependent grant
ToolBeforeCapabilities(deny=True)  # removed grant stage
