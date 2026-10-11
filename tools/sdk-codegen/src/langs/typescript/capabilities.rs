//! Catalogue-driven, event-specific declarative host capabilities.
use crate::model::Ir;
use anyhow::Result;
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::Write;

fn camel(name: &str) -> String {
    let mut chars = name.chars();
    chars
        .next()
        .map(|c| c.to_lowercase().to_string() + chars.as_str())
        .unwrap_or_default()
}
fn title(name: &str) -> String {
    name.split(['_', '-'])
        .map(|part| {
            let mut chars = part.chars();
            chars
                .next()
                .map(|c| c.to_uppercase().to_string() + chars.as_str())
                .unwrap_or_default()
        })
        .collect()
}
fn literals(values: &[String]) -> String {
    if values.is_empty() {
        "never".into()
    } else {
        values
            .iter()
            .map(|v| format!("{v:?}"))
            .collect::<Vec<_>>()
            .join(" | ")
    }
}
fn direct(effect: &str) -> String {
    if effect == "return" {
        "returnResult".into()
    } else {
        camel(&title(effect))
    }
}

pub(crate) fn emit(ir: &Ir) -> Result<String> {
    let events = crate::capability_ergonomics::catalogue(ir)?;
    if events.is_empty() {
        return Ok(String::new());
    }
    let mut fields = BTreeSet::new();
    let mut targets = BTreeSet::new();
    let mut flow = BTreeSet::new();
    let mut delivery = BTreeSet::new();
    let mut elicitation = BTreeSet::new();
    for event in &events {
        for effect in &event.effects {
            if !["modify", "flow", "inject"].contains(&effect.as_str()) {
                fields.insert(direct(effect));
            }
        }
        for target in event.modify.keys() {
            targets.insert(target.clone());
            fields.insert(format!("modify{}", title(target)));
        }
        flow.extend(event.flow.clone());
        delivery.extend(event.delivery.clone());
        elicitation.extend(event.elicitation.clone());
    }
    fields.extend(["flow".into(), "inject".into(), "elicitation".into()]);
    let mut out = String::from(PRELUDE);
    writeln!(
        out,
        "export type CapabilityFlowOperation = {};",
        literals(&flow.iter().cloned().collect::<Vec<_>>())
    )?;
    writeln!(
        out,
        "export type CapabilityDelivery = {};",
        literals(&delivery.iter().cloned().collect::<Vec<_>>())
    )?;
    let stops = flow
        .iter()
        .filter(|s| s.as_str() != "continue")
        .cloned()
        .collect::<Vec<_>>();
    writeln!(
        out,
        "export type CapabilityFlow = {{ readonly operations: CapabilityNonempty<{}>; readonly remainingContinuations?: CapabilityCount; readonly continuationCount?: CapabilityCount; readonly maxContinuations?: CapabilityCount }} | {{ readonly operations: CapabilityNonempty<CapabilityFlowOperation>; readonly remainingContinuations: CapabilityCount; readonly continuationCount: CapabilityCount; readonly maxContinuations?: CapabilityCount }};",
        literals(&stops)
    )?;
    out.push_str("export type CapabilityInjection = { readonly context: { readonly append: true; readonly deliverAt: CapabilityNonempty<CapabilityDelivery> } };\n");
    writeln!(
        out,
        "export type CapabilityElicitation = CapabilityAtLeastOne<{{ {} }}>;",
        elicitation
            .iter()
            .map(|s| format!("readonly {s}?: true"))
            .collect::<Vec<_>>()
            .join("; ")
    )?;
    writeln!(
        out,
        "export interface WireCapabilities {{\n  readonly effects: readonly string[];\n  readonly modify?: {{ {} }};\n  readonly flow?: CapabilityFlow;\n  readonly inject?: CapabilityInjection;\n  readonly elicitation?: {{ {} }};\n}}",
        targets
            .iter()
            .map(|s| format!("readonly {s}?: {{readonly replace:boolean; readonly merge:boolean}}"))
            .collect::<Vec<_>>()
            .join("; "),
        elicitation
            .iter()
            .map(|s| format!("readonly {s}?: Readonly<Record<string, never>>"))
            .collect::<Vec<_>>()
            .join("; ")
    )?;
    out.push_str("export interface WireDeclaration { readonly modes: readonly ['intercept'] | readonly ['observe'] | readonly ['intercept', 'observe']; readonly capabilities?: WireCapabilities }\n");
    for event in &events {
        if !event.intercept {
            continue;
        }
        let mut allowed = BTreeMap::new();
        for effect in &event.effects {
            if !["modify", "flow", "inject"].contains(&effect.as_str()) {
                allowed.insert(direct(effect), "true".to_string());
            }
        }
        for (target, ops) in &event.modify {
            let mut modes = ops.clone();
            if ops.contains(&"replace".into()) && ops.contains(&"merge".into()) {
                modes.push("replace-and-merge".into());
            }
            allowed.insert(format!("modify{}", title(target)), literals(&modes));
        }
        if !event.flow.is_empty() {
            allowed.insert(
                "flow".into(),
                format!(
                    "CapabilityFlow & {{ readonly operations: CapabilityNonempty<{}> }}",
                    literals(&event.flow)
                ),
            );
        }
        if !event.delivery.is_empty() {
            allowed.insert("inject".into(), format!("CapabilityInjection & {{readonly context: {{readonly append:true; readonly deliverAt: CapabilityNonempty<{}>}}}}", literals(&event.delivery)));
        }
        if !event.elicitation.is_empty() {
            allowed.insert(
                "elicitation".into(),
                format!(
                    "CapabilityAtLeastOne<{{ {} }}> & {{ {} }}",
                    event
                        .elicitation
                        .iter()
                        .map(|s| format!("readonly {s}?:true"))
                        .collect::<Vec<_>>()
                        .join("; "),
                    elicitation
                        .iter()
                        .filter(|s| !event.elicitation.contains(s))
                        .map(|s| format!("readonly {s}?:never"))
                        .collect::<Vec<_>>()
                        .join("; ")
                ),
            );
        }
        writeln!(
            out,
            "export type {}Intercept = CapabilityAtLeastOne<{{ {} }}> & {{ {} }};",
            event.name,
            allowed
                .iter()
                .map(|(k, v)| format!("readonly {k}?: {v}"))
                .collect::<Vec<_>>()
                .join("; "),
            fields
                .iter()
                .filter(|k| !allowed.contains_key(*k))
                .map(|k| format!("readonly {k}?: never"))
                .collect::<Vec<_>>()
                .join("; ")
        )?;
        if event.observe {
            writeln!(
                out,
                "export type {}HostCapabilities = {{ readonly intercept: {}Intercept; readonly observe?: true }} | CapabilityObservation;",
                event.name, event.name
            )?;
        } else {
            writeln!(
                out,
                "export type {}HostCapabilities = {{ readonly intercept: {}Intercept; readonly observe?: never }};",
                event.name, event.name
            )?;
        }
    }
    out.push_str("export interface HostCapabilities {\n");
    for event in &events {
        let ty = if event.intercept {
            format!("{}HostCapabilities", event.name)
        } else {
            "CapabilityObservation".into()
        };
        writeln!(out, "  readonly {}?: {ty};", camel(&event.name))?;
    }
    out.push_str("}\n");
    let mut rules = BTreeMap::new();
    for event in &events {
        let controls = event
            .effects
            .iter()
            .filter(|effect| !["modify", "flow", "inject"].contains(&effect.as_str()))
            .map(|effect| direct(effect))
            .collect::<Vec<_>>();
        let mut modify = BTreeMap::new();
        for (target, ops) in &event.modify {
            let mut modes = ops.clone();
            if ops.contains(&"replace".into()) && ops.contains(&"merge".into()) {
                modes.push("replace-and-merge".into());
            }
            modify.insert(format!("modify{}", title(target)), modes);
        }
        rules.insert(
            camel(&event.name),
            serde_json::json!({
                "intercept": event.intercept, "observe": event.observe,
                "controls": controls, "modify": modify, "flow": event.flow,
                "delivery": event.delivery, "elicitation": event.elicitation,
            }),
        );
    }
    writeln!(
        out,
        "const capabilityRules: Readonly<Record<string, CapabilityRule>> = {};",
        serde_json::to_string(&rules)?
    )?;
    out.push_str("export function hostCapabilitiesToWire(host: HostCapabilities): Readonly<Record<string, WireDeclaration>> {\n  host = capabilityOwnedHost(host);\n  const result: Record<string, WireDeclaration> = {};\n");
    for event in &events {
        writeln!(
            out,
            "  {{ const declaration = host.{}; if (declaration !== undefined) {{",
            camel(&event.name)
        )?;
        if event.intercept {
            out.push_str("    if (declaration.intercept !== undefined) {\n      const grants = declaration.intercept;\n      const effects: string[] = [];\n      const capabilities: { -readonly [K in keyof WireCapabilities]: WireCapabilities[K] } = { effects };\n");
            for effect in &event.effects {
                if !["modify", "flow", "inject"].contains(&effect.as_str()) {
                    writeln!(
                        out,
                        "      if (grants.{} === true) effects.push({effect:?});",
                        direct(effect)
                    )?;
                }
            }
            if !event.modify.is_empty() {
                out.push_str("      const modify: { -readonly [K in keyof NonNullable<WireCapabilities['modify']>]: NonNullable<WireCapabilities['modify']>[K] } = {};\n");
                for target in event.modify.keys() {
                    writeln!(
                        out,
                        "      if (grants.modify{} !== undefined) modify.{target} = capabilityModifyToWire(grants.modify{});",
                        title(target),
                        title(target)
                    )?;
                }
                out.push_str("      if (Object.keys(modify).length !== 0) { effects.push('modify'); capabilities.modify = modify; }\n");
            }
            if !event.flow.is_empty() {
                out.push_str("      if (grants.flow !== undefined) { effects.push('flow'); capabilities.flow = { ...grants.flow, operations: [...new Set(grants.flow.operations)] as unknown as CapabilityFlow['operations'] } as CapabilityFlow; }\n");
            }
            if !event.delivery.is_empty() {
                out.push_str("      if (grants.inject !== undefined) { effects.push('inject'); capabilities.inject = {context: {append: true, deliverAt: [...new Set(grants.inject.context.deliverAt)] as unknown as CapabilityNonempty<CapabilityDelivery>}}; }\n");
            }
            if !event.elicitation.is_empty() {
                out.push_str(
                    "      if (grants.elicitation !== undefined) { capabilities.elicitation = {\n",
                );
                for dimension in &event.elicitation {
                    writeln!(
                        out,
                        "        ...(grants.elicitation.{dimension} === true ? {{{dimension}: {{}}}} : {{}}),"
                    )?;
                }
                out.push_str("      }; }\n");
            }
            writeln!(
                out,
                "      result[{:?}] = {{modes: declaration.observe === true ? ['intercept', 'observe'] : ['intercept'], capabilities}};",
                event.event
            )?;
            writeln!(
                out,
                "    }} else {{ result[{:?}] = {{modes: ['observe']}}; }}",
                event.event
            )?;
        } else {
            writeln!(
                out,
                "    result[{:?}] = {{modes: ['observe']}};",
                event.event
            )?;
        }
        out.push_str("  } }\n");
    }
    out.push_str("  return result;\n}\n");
    Ok(out)
}

const PRELUDE: &str = r#"
type CapabilityNonempty<T> = readonly [T, ...T[]];
type CapabilityAtLeastOne<T> = { [K in keyof T]-?: Required<Pick<T, K>> & T }[keyof T];
declare const capabilityCountBrand: unique symbol;
export type CapabilityCount = number & { readonly [capabilityCountBrand]: true };
export function capabilityCount(value: number): CapabilityCount {
  if (!Number.isSafeInteger(value) || value < 0) throw new TypeError('capability count must be a nonnegative safe integer');
  return value as CapabilityCount;
}
function capabilityModifyToWire(mode: string): {replace:boolean;merge:boolean} {
  return {replace: mode === 'replace' || mode === 'replace-and-merge', merge: mode === 'merge' || mode === 'replace-and-merge'};
}

interface CapabilityRule {
  readonly intercept: boolean; readonly observe: boolean;
  readonly controls: readonly string[]; readonly modify: Readonly<Record<string, readonly string[]>>;
  readonly flow: readonly string[]; readonly delivery: readonly string[]; readonly elicitation: readonly string[];
}
function capabilityInvalid(): never { throw new TypeError('invalid or mutated host capability declaration'); }
// Snapshot once, then validate and render only owned data. Readonly types do not protect mutable aliases.
function capabilitySnapshot(value: unknown, active = new Set<object>()): unknown {
  if (value === null || typeof value !== 'object') {
    if (value === undefined || typeof value === 'boolean' || typeof value === 'string' || typeof value === 'number') return value;
    return capabilityInvalid();
  }
  if (active.has(value)) return capabilityInvalid();
  active.add(value);
  let result: unknown;
  if (Array.isArray(value)) {
    result = Array.from(value, item => capabilitySnapshot(item, active));
  } else {
    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null) return capabilityInvalid();
    const owned: Record<string, unknown> = Object.create(null);
    for (const key of Reflect.ownKeys(value)) {
      if (typeof key !== 'string') return capabilityInvalid();
      owned[key] = capabilitySnapshot((value as Record<string, unknown>)[key], active);
    }
    result = owned;
  }
  active.delete(value);
  return result;
}
function capabilityObject(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return capabilityInvalid();
  return value as Record<string, unknown>;
}
function capabilityKeys(value: Record<string, unknown>, allowed: readonly string[]): void {
  for (const key of Object.keys(value)) if (!allowed.includes(key)) capabilityInvalid();
}
function capabilityList(value: unknown, allowed: readonly string[]): readonly string[] {
  if (!Array.isArray(value) || value.length === 0 || value.some(item => typeof item !== 'string' || !allowed.includes(item))) return capabilityInvalid();
  return value as string[];
}
function capabilityOwnedHost(value: unknown): HostCapabilities {
  const host = capabilityObject(capabilitySnapshot(value));
  capabilityKeys(host, Object.keys(capabilityRules));
  for (const [event, selected] of Object.entries(host)) {
    if (selected === undefined) continue;
    const rule = capabilityRules[event]!;
    const declaration = capabilityObject(selected);
    capabilityKeys(declaration, ['observe', 'intercept']);
    if (declaration.observe !== undefined && (!rule.observe || declaration.observe !== true)) capabilityInvalid();
    if (declaration.intercept === undefined) {
      if (declaration.observe !== true) capabilityInvalid();
      continue;
    }
    if (!rule.intercept) capabilityInvalid();
    const grants = capabilityObject(declaration.intercept);
    const allowed = [...rule.controls, ...Object.keys(rule.modify)];
    if (rule.flow.length) allowed.push('flow');
    if (rule.delivery.length) allowed.push('inject');
    if (rule.elicitation.length) allowed.push('elicitation');
    capabilityKeys(grants, allowed);
    let supported = false;
    for (const control of rule.controls) {
      if (grants[control] === undefined) continue;
      if (grants[control] !== true) capabilityInvalid();
      supported = true;
    }
    for (const [target, modes] of Object.entries(rule.modify)) {
      const mode = grants[target];
      if (mode === undefined) continue;
      if (typeof mode !== 'string' || !modes.includes(mode)) capabilityInvalid();
      supported = true;
    }
    if (grants.flow !== undefined) {
      const flow = capabilityObject(grants.flow);
      capabilityKeys(flow, ['operations', 'remainingContinuations', 'continuationCount', 'maxContinuations']);
      const operations = capabilityList(flow.operations, rule.flow);
      for (const field of ['remainingContinuations', 'continuationCount', 'maxContinuations']) {
        const count = flow[field];
        if (count !== undefined && (typeof count !== 'number' || !Number.isSafeInteger(count) || count < 0)) capabilityInvalid();
      }
      if (operations.includes('continue') && (flow.remainingContinuations === undefined || flow.continuationCount === undefined)) capabilityInvalid();
      supported = true;
    }
    if (grants.inject !== undefined) {
      const injection = capabilityObject(grants.inject);
      capabilityKeys(injection, ['context']);
      const context = capabilityObject(injection.context);
      capabilityKeys(context, ['append', 'deliverAt']);
      if (context.append !== true) capabilityInvalid();
      capabilityList(context.deliverAt, rule.delivery);
      supported = true;
    }
    if (grants.elicitation !== undefined) {
      const elicitation = capabilityObject(grants.elicitation);
      capabilityKeys(elicitation, rule.elicitation);
      let facility = false;
      for (const selection of Object.values(elicitation)) {
        if (selection === undefined) continue;
        if (selection !== true) capabilityInvalid();
        facility = true;
      }
      if (!facility) capabilityInvalid();
      supported = true;
    }
    if (!supported) capabilityInvalid();
  }
  return host as HostCapabilities;
}

export type CapabilityObservation = { readonly observe: true; readonly intercept?: never };
"#;
