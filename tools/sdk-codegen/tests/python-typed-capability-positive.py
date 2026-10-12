"""Positive consumer fixture: mypy/Pyright must accept this file."""
from facade_contract.event import EventType
from facade_contract.capability import (
    Delivery, Elicitation, FlowContinue, FlowStop, HostCapabilities, InjectContext, Modify, Observe,
    ToolBeforeCapabilities, ToolBeforeIntercept, TurnFinishBeforeCapabilities,
    TurnFinishBeforeIntercept, UserElicitationRequestCapabilities,
    UserElicitationRequestIntercept, WireDeclaration,
)

host = HostCapabilities(
    session_start=Observe(), session_end=Observe(),
    tool_before=ToolBeforeCapabilities(intercept=ToolBeforeIntercept(
        allow=True, deny=True, return_result=True, modify_input=Modify.REPLACE_AND_MERGE,
        flow=FlowStop(), inject_context=InjectContext(deliver_at=(Delivery.NOW,))), observe=True),
    turn_finish_before=TurnFinishBeforeCapabilities(intercept=TurnFinishBeforeIntercept(
        flow=FlowContinue(remaining_continuations=2, continuation_count=0, include_stop=True))),
)
rendered: dict[EventType, WireDeclaration] = host.to_wire()

observed = HostCapabilities(tool_before=Observe()).to_wire()

facility_only = HostCapabilities(user_elicitation_request=UserElicitationRequestCapabilities(
    intercept=UserElicitationRequestIntercept(elicitation=Elicitation.FORM)))
facility_rendered: dict[EventType, WireDeclaration] = facility_only.to_wire()
