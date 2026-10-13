use super::*;

/// Event-specific immutable declarations, separate from the raw catalogue.
pub(super) fn emit(renderer: &Renderer<'_>) -> Result<(String, Vec<String>)> {
    let events = crate::capability_ergonomics::catalogue(renderer.ir)?;
    let operations = events
        .iter()
        .flat_map(|e| e.modify.values().flatten())
        .cloned()
        .collect::<std::collections::BTreeSet<_>>();
    let delivery = events
        .iter()
        .flat_map(|e| e.delivery.iter())
        .cloned()
        .collect::<std::collections::BTreeSet<_>>();
    let elicitation = events
        .iter()
        .flat_map(|e| e.elicitation.iter())
        .cloned()
        .collect::<std::collections::BTreeSet<_>>();
    let runtime = RUNTIME
        .replace("# MODIFY_MEMBERS", &enum_subsets(&operations)?)
        .replace(
            "# DELIVERY_MEMBERS",
            &delivery
                .iter()
                .map(|v| format!("    {} = {v:?}", snake_case(v).to_uppercase()))
                .collect::<Vec<_>>()
                .join("\n"),
        )
        .replace("# ELICITATION_MEMBERS", &enum_subsets(&elicitation)?);
    let mut out = format!("{HEADER}{runtime}");
    let mut exports = vec![
        "HostCapabilities",
        "WireDeclaration",
        "Modify",
        "Delivery",
        "FlowStop",
        "FlowContinue",
        "InjectContext",
        "Elicitation",
        "EffectName",
    ]
    .into_iter()
    .map(str::to_owned)
    .collect::<Vec<_>>();
    out.push_str("class EffectName(StrEnum):\n");
    let effects = events
        .iter()
        .flat_map(|e| e.effects.iter())
        .collect::<std::collections::BTreeSet<_>>();
    for effect in effects {
        writeln!(
            out,
            "    {} = {effect:?}",
            snake_case(effect).to_uppercase()
        )?;
    }
    out.push('\n');
    for e in &events {
        let capability = format!("{}Capabilities", e.name);
        if !e.intercept {
            // Observation-only events still get an event-specific declaration.
            exports.push(capability.clone());
            writeln!(
                out,
                "@dataclass(frozen=True, kw_only=True, slots=True)\nclass {capability}:\n    observe: bool = False\n\n    def __post_init__(self) -> None:\n        _bool(self.observe, 'observe')\n        if not self.observe:\n            raise ValueError('declaration requires observation')\n\n    def _wire(self) -> WireDeclaration:\n        return {{'modes': ['observe']}}\n"
            )?;
            continue;
        }
        let intercept = format!("{}Intercept", e.name);
        exports.extend([intercept.clone(), capability.clone()]);
        writeln!(
            out,
            "@dataclass(frozen=True, kw_only=True, slots=True)\nclass {intercept}:"
        )?;
        let simple = e
            .effects
            .iter()
            .filter(|f| !matches!(f.as_str(), "modify" | "flow" | "inject"))
            .collect::<Vec<_>>();
        for f in &simple {
            writeln!(out, "    {}: bool = False", control_name(f))?;
        }
        for target in e.modify.keys() {
            writeln!(
                out,
                "    modify_{}: Modify | None = None",
                snake_case(target)
            )?;
        }
        if !e.flow.is_empty() {
            let ty = if e.flow.iter().any(|v| v == "stop") && e.flow.iter().any(|v| v == "continue")
            {
                "FlowStop | FlowContinue"
            } else if e.flow.iter().any(|v| v == "continue") {
                "FlowContinue"
            } else {
                "FlowStop"
            };
            writeln!(out, "    flow: {ty} | None = None")?;
        }
        if !e.delivery.is_empty() {
            out.push_str("    inject_context: InjectContext | None = None\n");
        }
        if !e.elicitation.is_empty() {
            out.push_str("    elicitation: Elicitation | None = None\n");
        }
        out.push_str("\n    def __post_init__(self) -> None:\n        granted = False\n");
        for f in &simple {
            let field = control_name(f);
            writeln!(
                out,
                "        _bool(self.{field}, {field:?})\n        granted = granted or self.{field}"
            )?;
        }
        for (target, operations) in &e.modify {
            let field = format!("modify_{}", snake_case(target));
            writeln!(
                out,
                "        if self.{field} is not None:\n            _exact(self.{field}, (Modify,), {field:?})\n            if not set(self.{field}.value).issubset({operations:?}):\n                raise ValueError('unsupported modification operation')\n            granted = True"
            )?;
        }
        if !e.flow.is_empty() {
            let classes =
                if e.flow.iter().any(|v| v == "stop") && e.flow.iter().any(|v| v == "continue") {
                    "FlowStop, FlowContinue"
                } else if e.flow.iter().any(|v| v == "continue") {
                    "FlowContinue,"
                } else {
                    "FlowStop,"
                };
            writeln!(
                out,
                "        if self.flow is not None:\n            _exact(self.flow, ({classes}), 'flow')\n            if not set(self.flow._wire()['operations']).issubset({:?}):\n                raise ValueError('unsupported flow operation')\n            granted = True",
                e.flow
            )?;
        }
        if !e.delivery.is_empty() {
            writeln!(
                out,
                "        if self.inject_context is not None:\n            _exact(self.inject_context, (InjectContext,), 'inject_context')\n            if not set(v.value for v in self.inject_context.deliver_at).issubset({:?}):\n                raise ValueError('unsupported delivery')\n            granted = True",
                e.delivery
            )?;
        }
        if !e.elicitation.is_empty() {
            writeln!(
                out,
                "        if self.elicitation is not None:\n            _exact(self.elicitation, (Elicitation,), 'elicitation')\n            if not set(self.elicitation.value).issubset({:?}):\n                raise ValueError('unsupported elicitation mode')\n            granted = True",
                e.elicitation
            )?;
        }
        out.push_str("        if not granted:\n            raise ValueError('interception requires a real control grant')\n\n    def _wire(self) -> dict[str, Any]:\n        effects: list[str] = []\n        result: dict[str, Any] = {'effects': effects}\n");
        for f in &simple {
            writeln!(
                out,
                "        if self.{}:\n            effects.append({f:?})",
                control_name(f)
            )?;
        }
        if !e.modify.is_empty() {
            out.push_str("        modify: dict[str, Any] = {}\n");
            for (target, operations) in &e.modify {
                writeln!(
                    out,
                    "        if self.modify_{} is not None:\n            modify[{target:?}] = {{op: op in self.modify_{}.value for op in {operations:?}}}",
                    snake_case(target),
                    snake_case(target)
                )?;
            }
            out.push_str("        if modify:\n            effects.append('modify')\n            result['modify'] = modify\n");
        }
        if !e.flow.is_empty() {
            out.push_str("        if self.flow is not None:\n            effects.append('flow')\n            result['flow'] = self.flow._wire()\n");
        }
        if !e.delivery.is_empty() {
            out.push_str("        if self.inject_context is not None:\n            effects.append('inject')\n            result['inject'] = {'context': {'append': True, 'deliverAt': [v.value for v in self.inject_context.deliver_at]}}\n");
        }
        if !e.elicitation.is_empty() {
            out.push_str("        if self.elicitation is not None:\n            result['elicitation'] = {v: {} for v in self.elicitation.value}\n");
        }
        out.push_str("        effects.sort()\n        return result\n\n");
        let observation_check = if e.observe {
            ""
        } else {
            "        if self.observe:\n            raise ValueError('observation unsupported for this event')\n"
        };
        writeln!(
            out,
            "@dataclass(frozen=True, kw_only=True, slots=True)\nclass {capability}:\n    intercept: {intercept} | None = None\n    observe: bool = False\n\n    def __post_init__(self) -> None:\n        _bool(self.observe, 'observe')\n{observation_check}        if self.intercept is not None:\n            _exact(self.intercept, ({intercept},), 'intercept')\n        elif not self.observe:\n            raise ValueError('declaration requires interception or observation')\n\n    def _wire(self) -> WireDeclaration:\n        result: WireDeclaration = {{'modes': []}}\n        if self.intercept is not None:\n            result['modes'].append('intercept')\n            result['capabilities'] = self.intercept._wire()\n        if self.observe:\n            result['modes'].append('observe')\n        return result\n"
        )?;
    }
    out.push_str("@dataclass(frozen=True, kw_only=True, slots=True)\nclass HostCapabilities:\n");
    for e in &events {
        writeln!(
            out,
            "    {}: {}Capabilities | None = None",
            snake_case(&e.name),
            e.name
        )?;
    }
    out.push_str("\n    def __post_init__(self) -> None:\n");
    for e in &events {
        let field = snake_case(&e.name);
        writeln!(
            out,
            "        if self.{field} is not None:\n            _exact(self.{field}, ({}Capabilities,), {field:?})",
            e.name
        )?;
    }
    out.push_str("\n    def to_wire(self) -> dict[EventType, WireDeclaration]:\n        result: dict[EventType, WireDeclaration] = {}\n");
    for e in &events {
        let field = snake_case(&e.name);
        writeln!(
            out,
            "        if self.{field} is not None:\n            result[{:?}] = self.{field}._wire()",
            e.event
        )?;
    }
    out.push_str("        return result\n");
    Ok((out, exports))
}
fn enum_subsets(values: &std::collections::BTreeSet<String>) -> Result<String> {
    let mut values = values.iter().collect::<Vec<_>>();
    values.sort_by_key(|v| {
        if v.as_str() == "replace" {
            String::new()
        } else {
            (*v).clone()
        }
    });
    let mut out = String::new();
    for mask in 1usize..(1usize << values.len()) {
        let selected = values
            .iter()
            .enumerate()
            .filter(|(i, _)| mask & (1 << i) != 0)
            .map(|(_, v)| *v)
            .collect::<Vec<_>>();
        let name = selected
            .iter()
            .map(|v| snake_case(v).to_uppercase())
            .collect::<Vec<_>>()
            .join("_AND_");
        let tuple = selected
            .iter()
            .map(|v| format!("{v:?},"))
            .collect::<Vec<_>>()
            .join(" ");
        writeln!(out, "    {name} = ({tuple})")?;
    }
    if out.is_empty() {
        out.push_str("    pass\n");
    }
    Ok(out)
}
fn control_name(effect: &str) -> String {
    if effect == "return" {
        "return_result".into()
    } else {
        snake_case(effect)
    }
}

const RUNTIME: &str = r#"from __future__ import annotations
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import Any, Literal, NotRequired, TypedDict
from ._models import EventType

class WireDeclaration(TypedDict):
    modes: list[Literal['intercept', 'observe']]
    capabilities: NotRequired[dict[str, Any]]

def _exact(value: object, types: tuple[type, ...], name: str) -> None:
    if type(value) not in types:
        raise TypeError(f'{name} has an incorrect type')

def _bool(value: bool, name: str) -> None:
    if type(value) is not bool:
        raise TypeError(f'{name} must be bool')

def _count(value: int, name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f'{name} must be a nonnegative integer')

class Modify(Enum):
# MODIFY_MEMBERS

class Delivery(StrEnum):
# DELIVERY_MEMBERS

class Elicitation(Enum):
# ELICITATION_MEMBERS

@dataclass(frozen=True, kw_only=True, slots=True)
class FlowStop:
    max_continuations: int | None = None

    def __post_init__(self) -> None:
        if self.max_continuations is not None:
            _count(self.max_continuations, 'max_continuations')

    def _wire(self) -> dict[str, Any]:
        result: dict[str, Any] = {'operations': ['stop']}
        if self.max_continuations is not None:
            result['maxContinuations'] = self.max_continuations
        return result

@dataclass(frozen=True, kw_only=True, slots=True)
class FlowContinue:
    remaining_continuations: int
    continuation_count: int
    max_continuations: int | None = None
    include_stop: bool = False

    def __post_init__(self) -> None:
        _count(self.remaining_continuations, 'remaining_continuations')
        _count(self.continuation_count, 'continuation_count')
        if self.max_continuations is not None:
            _count(self.max_continuations, 'max_continuations')
        _bool(self.include_stop, 'include_stop')

    def _wire(self) -> dict[str, Any]:
        result: dict[str, Any] = {'operations': ['continue', 'stop'] if self.include_stop else ['continue'], 'remainingContinuations': self.remaining_continuations, 'continuationCount': self.continuation_count}
        if self.max_continuations is not None:
            result['maxContinuations'] = self.max_continuations
        return result

@dataclass(frozen=True, kw_only=True, slots=True)
class InjectContext:
    deliver_at: tuple[Delivery, ...]

    def __post_init__(self) -> None:
        _exact(self.deliver_at, (tuple,), 'deliver_at')
        if not self.deliver_at or len(set(self.deliver_at)) != len(self.deliver_at):
            raise ValueError('delivery must be nonempty and unique')
        for value in self.deliver_at:
            _exact(value, (Delivery,), 'delivery')

"#;
