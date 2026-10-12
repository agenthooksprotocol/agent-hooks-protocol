//! Catalogue-derived, event-specific capability construction.
use super::{snake_identifier, type_identifier};
use crate::model::Ir;
use anyhow::Result;
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::Write;
fn title(s: &str) -> String {
    s.split('_')
        .map(|part| {
            let mut c = part.chars();
            c.next()
                .map(|c0| c0.to_uppercase().to_string() + c.as_str())
                .unwrap_or_default()
        })
        .collect()
}
fn sets(out: &mut String, name: &str, values: &[String], modify: bool) -> Result<()> {
    anyhow::ensure!(values.len() <= 8, "capability set too large");
    let mut values = values.to_vec();
    values.sort_by_key(|v| match v.as_str() {
        "replace" | "now" => (0, v.clone()),
        "merge" | "next_turn" => (1, v.clone()),
        _ => (2, v.clone()),
    });
    writeln!(out, "#[derive(Clone, Copy, Debug)] pub enum {name} {{")?;
    for mask in 1..(1usize << values.len()) {
        let names = values
            .iter()
            .enumerate()
            .filter(|(i, _)| mask & (1 << i) != 0)
            .map(|(_, v)| title(v))
            .collect::<Vec<_>>();
        writeln!(out, "{},", names.join("And"))?;
    }
    writeln!(
        out,
        "}} impl {name} {{ fn wire(self) -> {} {{ match self {{",
        if modify {
            "BTreeMap<String, bool>"
        } else {
            "Vec<String>"
        }
    )?;
    for mask in 1..(1usize << values.len()) {
        let selected = values
            .iter()
            .enumerate()
            .filter(|(i, _)| mask & (1 << i) != 0)
            .map(|(_, v)| title(v))
            .collect::<Vec<_>>()
            .join("And");
        let expression = if modify {
            let entries = values
                .iter()
                .enumerate()
                .map(|(i, v)| format!("({v:?}.into(), {})", mask & (1 << i) != 0))
                .collect::<Vec<_>>()
                .join(",");
            format!("BTreeMap::from([{entries}])")
        } else {
            let entries = values
                .iter()
                .enumerate()
                .filter(|(i, _)| mask & (1 << i) != 0)
                .map(|(_, v)| format!("{v:?}.into()"))
                .collect::<Vec<_>>()
                .join(",");
            format!("vec![{entries}]")
        };
        writeln!(out, "Self::{selected} => {expression},")?;
    }
    out.push_str("}}}\n");
    Ok(())
}
pub(crate) fn emit(ir: &Ir) -> Result<String> {
    let events = crate::capability_ergonomics::catalogue(ir)?;
    if events.is_empty() {
        return Ok(String::new());
    }
    let mut out = String::from(
        r#"
pub mod capability {
use super::EventType;
use std::collections::{BTreeMap, BTreeSet};
use serde::Serialize;
#[derive(Clone, Copy, Debug)] pub struct Observe;
#[derive(Clone, Debug, Serialize)] pub struct WireDeclaration {
    modes: Vec<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")] capabilities: Option<WireCapabilities>,
}
#[derive(Clone, Debug, Default, Serialize)] struct WireCapabilities {
    effects: BTreeSet<String>,
    #[serde(skip_serializing_if = "BTreeMap::is_empty")] modify: BTreeMap<String, BTreeMap<String, bool>>,
    #[serde(skip_serializing_if = "Option::is_none")] flow: Option<WireFlow>,
    #[serde(skip_serializing_if = "Option::is_none")] inject: Option<WireInject>,
    #[serde(skip_serializing_if = "BTreeMap::is_empty")] elicitation: BTreeMap<String, BTreeMap<String, bool>>,
}
#[derive(Clone, Debug, Serialize)] struct WireFlow {
    operations: Vec<String>,
    #[serde(rename = "remainingContinuations", skip_serializing_if = "Option::is_none")] remaining: Option<u64>,
    #[serde(rename = "continuationCount", skip_serializing_if = "Option::is_none")] count: Option<u64>,
    #[serde(rename = "maxContinuations", skip_serializing_if = "Option::is_none")] max: Option<u64>,
}
#[derive(Clone, Debug, Serialize)] struct WireInject { context: WireContext }
#[derive(Clone, Debug, Serialize)] struct WireContext { append: bool, #[serde(rename = "deliverAt")] deliver_at: Vec<String> }
#[derive(Clone, Debug, Default)] struct State { observe: bool, grants: WireCapabilities }
impl State {
    fn add_flow(&mut self, mut grant: WireFlow) {
        if let Some(old) = &self.grants.flow {
            for operation in &old.operations {
                if !grant.operations.contains(operation) { grant.operations.push(operation.clone()); }
            }
            grant.remaining = grant.remaining.or(old.remaining);
            grant.count = grant.count.or(old.count);
            grant.max = grant.max.or(old.max);
        }
        grant.operations.sort_by_key(|op| if op == "stop" { 0 } else { 1 });
        self.grants.effects.insert("flow".into());
        self.grants.flow = Some(grant);
    }
    fn add_inject(&mut self, mut delivery: Vec<String>) {
        if let Some(old) = &self.grants.inject {
            for mode in &old.context.deliver_at {
                if !delivery.contains(mode) { delivery.push(mode.clone()); }
            }
        }
        delivery.sort_by_key(|mode| if mode == "now" { 0 } else { 1 });
        self.grants.effects.insert("inject".into());
        self.grants.inject = Some(WireInject { context: WireContext { append: true, deliver_at: delivery } });
    }
    fn wire(&self) -> WireDeclaration {
        let intercept = !self.grants.effects.is_empty() || !self.grants.elicitation.is_empty();
        let mut modes = Vec::new();
        if intercept { modes.push("intercept"); }
        if self.observe { modes.push("observe"); }
        WireDeclaration { modes, capabilities: intercept.then(|| self.grants.clone()) }
    }
}
"#,
    );
    let mut definitions: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut event_methods = Vec::new();
    for e in &events {
        let mut methods = vec![];
        if e.observe {
            methods.push((
                "observe".to_string(),
                String::new(),
                "state.observe = true;".to_string(),
            ));
        }
        if e.intercept {
            for effect in &e.effects {
                if ["modify", "flow", "inject"].contains(&effect.as_str()) {
                    continue;
                }
                let method = if effect == "return" {
                    "return_result".to_string()
                } else {
                    snake_identifier(effect)
                };
                methods.push((
                    method,
                    String::new(),
                    format!("state.grants.effects.insert({effect:?}.into());"),
                ));
            }
            for (target, operations) in &e.modify {
                let ty = if operations
                    .iter()
                    .map(String::as_str)
                    .collect::<BTreeSet<_>>()
                    == BTreeSet::from(["merge", "replace"])
                {
                    "Modify".to_string()
                } else {
                    format!("{}Modify{}", e.name, title(target))
                };
                definitions.insert(ty.clone(), operations.clone());
                methods.push((format!("modify_{}", snake_identifier(target)), format!(", grant: {ty}"), format!("state.grants.effects.insert(\"modify\".into()); for (operation, enabled) in grant.wire() {{ let old = state.grants.modify.entry({target:?}.into()).or_default().entry(operation).or_default(); *old |= enabled; }}")));
            }
            if !e.delivery.is_empty() {
                let ty = if e
                    .delivery
                    .iter()
                    .map(String::as_str)
                    .collect::<BTreeSet<_>>()
                    == BTreeSet::from(["now", "next_turn"])
                {
                    "InjectContext".to_string()
                } else {
                    format!("{}InjectContext", e.name)
                };
                definitions.insert(ty.clone(), e.delivery.clone());
                methods.push((
                    "inject_context".into(),
                    format!(", grant: {ty}"),
                    "state.add_inject(grant.wire());".into(),
                ));
            }
            if !e.flow.is_empty() {
                anyhow::ensure!(
                    e.flow.iter().all(|s| s == "stop" || s == "continue"),
                    "unknown flow operation"
                );
                let ty = if e.flow.len() == 2 {
                    "Flow".to_string()
                } else {
                    format!("{}Flow", e.name)
                };
                if !definitions.contains_key(&ty) {
                    writeln!(out, "#[derive(Clone, Copy, Debug)] pub enum {ty} {{")?;
                    let has_stop = e.flow.iter().any(|s| s == "stop");
                    let has_continue = e.flow.iter().any(|s| s == "continue");
                    if has_stop {
                        out.push_str("Stop,\n");
                    }
                    let variants = if has_continue {
                        if has_stop {
                            vec!["Continue", "StopAndContinue"]
                        } else {
                            vec!["Continue"]
                        }
                    } else {
                        vec![]
                    };
                    for v in &variants {
                        writeln!(
                            out,
                            "{v} {{ remaining_continuations: u64, continuation_count: u64, max_continuations: Option<u64> }},"
                        )?;
                    }
                    writeln!(
                        out,
                        "}} impl {ty} {{ fn wire(self) -> WireFlow {{ match self {{"
                    )?;
                    if has_stop {
                        out.push_str("Self::Stop => WireFlow { operations: vec![\"stop\".into()], remaining: None, count: None, max: None },\n");
                    }
                    for v in variants {
                        let ops = if v == "Continue" {
                            "vec![\"continue\".into()]"
                        } else {
                            "vec![\"stop\".into(), \"continue\".into()]"
                        };
                        writeln!(
                            out,
                            "Self::{v} {{ remaining_continuations, continuation_count, max_continuations }} => WireFlow {{operations: {ops}, remaining: Some(remaining_continuations), count: Some(continuation_count), max: max_continuations}},"
                        )?;
                    }
                    out.push_str("}}}\n");
                    definitions.insert(ty.clone(), Vec::new());
                }
                methods.push((
                    "flow".into(),
                    format!(", grant: {ty}"),
                    "state.add_flow(grant.wire());".into(),
                ));
            }
            for mode in &e.elicitation {
                methods.push((
                    format!("elicitation_{}", snake_identifier(mode)),
                    String::new(),
                    format!("state.grants.elicitation.insert({mode:?}.into(), BTreeMap::new());"),
                ));
            }
        }
        event_methods.push(methods);
    }
    for (name, values) in definitions {
        if !values.is_empty() {
            sets(&mut out, &name, &values, name.contains("Modify"))?;
        }
    }
    out.push_str("#[derive(Clone, Debug, Default)] pub struct HostCapabilities {\n");
    for e in &events {
        writeln!(
            out,
            "pub {}: Option<{}>,",
            snake_identifier(&e.event),
            if e.intercept { &e.name } else { "Observe" }
        )?;
    }
    out.push_str("}\nimpl HostCapabilities { pub fn to_wire(&self) -> BTreeMap<EventType, WireDeclaration> { let mut wire = BTreeMap::new();\n");
    for e in &events {
        let render = if e.intercept {
            "_declaration.0.wire()"
        } else {
            "WireDeclaration { modes: vec![\"observe\"], capabilities: None }"
        };
        writeln!(
            out,
            "if let Some(_declaration) = &self.{} {{ wire.insert(EventType::{}, {render}); }}",
            snake_identifier(&e.event),
            type_identifier(&e.event)
        )?;
    }
    out.push_str("wire } }\n");
    for (e, methods) in events.iter().zip(event_methods) {
        if !e.intercept {
            continue;
        }
        let name = &e.name;
        writeln!(
            out,
            "#[derive(Clone, Debug)] pub struct {name}(State);\n#[derive(Clone, Copy, Debug)] pub struct {name}Initial(());\npub fn {}() -> {name}Initial {{ {name}Initial(()) }}",
            snake_identifier(&e.event)
        )?;
        writeln!(
            out,
            "impl Serialize for {name} {{ fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {{ self.0.wire().serialize(serializer) }} }}"
        )?;
        for initial in [true, false] {
            writeln!(
                out,
                "impl {} {{",
                if initial {
                    format!("{name}Initial")
                } else {
                    name.clone()
                }
            )?;
            for (method, args, action) in &methods {
                writeln!(
                    out,
                    "pub fn {method}(&self{args}) -> {name} {{ let mut state = {}; {action} {name}(state) }}",
                    if initial {
                        "State::default()"
                    } else {
                        "self.0.clone()"
                    }
                )?;
            }
            out.push_str("}\n");
        }
    }
    out.push_str("}\n");
    Ok(out)
}
