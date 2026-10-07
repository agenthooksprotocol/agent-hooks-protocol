//! Immutable convenience declarations. Raw manifests and boundary admission remain unchanged.
use crate::{
    ergonomics::object_fields,
    model::{Ir, Property, Shape},
};
use anyhow::{Context, Result, ensure};
use std::{collections::BTreeSet, fmt::Write};

fn vocabulary(ir: &Ir, shape: &Shape) -> Vec<String> {
    fn visit(ir: &Ir, shape: &Shape, out: &mut BTreeSet<String>, seen: &mut BTreeSet<String>) {
        match shape {
            Shape::Enum { values, .. } => {
                out.extend(values.iter().filter_map(|v| v.as_str().map(str::to_owned)))
            }
            Shape::Literal { value } => {
                if let Some(s) = value.as_str() {
                    out.insert(s.into());
                }
            }
            Shape::Array { items } => visit(ir, items, out, seen),
            Shape::Union { variants, .. } | Shape::Intersection { variants } => {
                for s in variants {
                    visit(ir, s, out, seen);
                }
            }
            Shape::Ref { name } if seen.insert(name.clone()) => {
                if let Some(n) = ir.types.iter().find(|n| n.name == *name) {
                    visit(ir, &n.shape, out, seen);
                }
                seen.remove(name);
            }
            _ => {}
        }
    }
    let mut out = BTreeSet::new();
    visit(ir, shape, &mut out, &mut BTreeSet::new());
    out.into_iter().collect()
}
fn field<'a>(fields: &'a [Property], name: &str) -> Result<&'a Property> {
    fields
        .iter()
        .find(|p| p.wire_name == name)
        .with_context(|| format!("missing capability metadata {name}"))
}
fn children(ir: &Ir, fields: &[Property], name: &str) -> Result<Vec<Property>> {
    object_fields(ir, &field(fields, name)?.shape)
        .with_context(|| format!("capability {name} is not an object"))
}
fn pascal(s: &str) -> String {
    s.split(['_', '-'])
        .map(|s| {
            let mut c = s.chars();
            c.next()
                .map(|h| h.to_uppercase().to_string() + c.as_str())
                .unwrap_or_default()
        })
        .collect()
}
fn snake(s: &str) -> String {
    let mut out = String::new();
    for c in s.chars() {
        if c.is_uppercase() {
            out.push('_');
            out.extend(c.to_lowercase());
        } else {
            out.push(c);
        }
    }
    out
}
struct Metadata {
    effects: Vec<String>,
    targets: Vec<String>,
    operations: Vec<String>,
    flow: Vec<String>,
    delivery: Vec<String>,
    elicitation: Vec<String>,
}
fn metadata(ir: &Ir) -> Result<Option<Metadata>> {
    let Some(root) = ir.types.iter().find(|n| n.name == "Capabilities") else {
        return Ok(None);
    };
    let fields = object_fields(ir, &root.shape).context("Capabilities object")?;
    let effects = vocabulary(ir, &field(&fields, "effects")?.shape);
    let modify = children(ir, &fields, "modify")?;
    let mut operations = None;
    for target in &modify {
        // anyOf true-grant constraints are sibling intersections, not new members.
        let ops = object_fields(ir, &target.shape)
            .or_else(|| {
                if let Shape::Intersection { variants } = &target.shape {
                    variants.iter().find_map(|s| object_fields(ir, s))
                } else {
                    None
                }
            })
            .context("modify operations")?;
        let names = ops.iter().map(|p| p.wire_name.clone()).collect::<Vec<_>>();
        ensure!(
            ops.iter().all(|p| matches!(p.shape, Shape::Boolean)),
            "unsupported modify operation shape"
        );
        if let Some(old) = &operations {
            ensure!(
                old == &names,
                "modify targets have different operation vocabularies"
            );
        }
        operations = Some(names);
    }
    let flow = children(ir, &fields, "flow")?;
    let inject = children(ir, &fields, "inject")?;
    let context = children(ir, &inject, "context")?;
    Ok(Some(Metadata {
        effects,
        targets: modify.iter().map(|p| p.wire_name.clone()).collect(),
        operations: operations.context("empty modify metadata")?,
        flow: vocabulary(ir, &field(&flow, "operations")?.shape),
        delivery: vocabulary(ir, &field(&context, "deliverAt")?.shape),
        elicitation: children(ir, &fields, "elicitation")?
            .iter()
            .map(|p| p.wire_name.clone())
            .collect(),
    }))
}

pub fn typescript(ir: &Ir) -> Result<String> {
    let Some(m) = metadata(ir)? else {
        return Ok(String::new());
    };
    let mut out = String::from(TS_PRELUDE);
    writeln!(
        out,
        "export type CapabilityModifyOperations = {{ {} }};",
        m.operations
            .iter()
            .map(|o| format!("{o}?: boolean"))
            .collect::<Vec<_>>()
            .join("; ")
    )?;
    writeln!(
        out,
        "export type CapabilityFlowOperation = {};",
        m.flow
            .iter()
            .map(|s| format!("{s:?}"))
            .collect::<Vec<_>>()
            .join(" | ")
    )?;
    writeln!(
        out,
        "export type CapabilityDelivery = {};",
        m.delivery
            .iter()
            .map(|s| format!("{s:?}"))
            .collect::<Vec<_>>()
            .join(" | ")
    )?;
    out.push_str(TS_BUILDER);
    for effect in &m.effects {
        if ["modify", "flow", "inject"].contains(&effect.as_str()) {
            continue;
        }
        writeln!(
            out,
            "  {effect}(): CapabilityBuilder {{ return this.add({effect:?}); }}"
        )?;
    }
    for target in &m.targets {
        writeln!(
            out,
            "  modify{}(operations: CapabilityModifyOperations): CapabilityBuilder {{\n    const allowed = {} as readonly string[];\n    if (operations === null || typeof operations !== 'object' || Object.entries(operations).some(([k,v]) => !allowed.includes(k) || typeof v !== 'boolean') || !Object.values(operations).some(v => v === true)) throw new TypeError('nonempty modify operations required');\n    const grant = Object.fromEntries(allowed.map(k => [k, (operations as Record<string, boolean>)[k] === true]));\n    return this.add('modify', {{modify: {{{target}: grant}}}});\n  }}",
            pascal(target),
            serde_json::to_string(&m.operations)?
        )?;
    }
    for mode in &m.elicitation {
        writeln!(
            out,
            "  elicitation{}(): CapabilityBuilder {{ return this.add(undefined, {{elicitation: {{{mode}: {{}}}}}}); }}",
            pascal(mode)
        )?;
    }
    writeln!(
        out,
        "  flow(operations: readonly CapabilityFlowOperation[], counts: {{remainingContinuations?: number; continuationCount?: number; maxContinuations?: number}} = {{}}): CapabilityBuilder {{\n    capabilityNonempty(operations, {});\n    if (Object.entries(counts).some(([k,v]) => !['remainingContinuations','continuationCount','maxContinuations'].includes(k) || !Number.isSafeInteger(v) || v! < 0)) throw new TypeError('invalid continuation count');\n    if (operations.includes('continue') && (counts.remainingContinuations === undefined || counts.continuationCount === undefined)) throw new TypeError('continue requires counts');\n    return this.add('flow', {{flow: {{operations: [...new Set(operations)], ...counts}}}});\n  }}",
        serde_json::to_string(&m.flow)?
    )?;
    writeln!(
        out,
        "  injectContext(deliverAt: readonly CapabilityDelivery[]): CapabilityBuilder {{ capabilityNonempty(deliverAt, {}); return this.add('inject', {{inject: {{context: {{append: true, deliverAt: [...new Set(deliverAt)]}}}}}}); }}",
        serde_json::to_string(&m.delivery)?
    )?;
    out.push_str("}\n/** intercept includes observe; declarations do not prove host execution. */\nexport const capabilities = Object.freeze({intercept: () => CapabilityBuilder.intercept(), observe: (): CapabilityObservation => ({modes: ['observe']})});\n");
    Ok(out)
}
const TS_PRELUDE: &str = r#"
/** Declaration convenience only. Event admission and per-call narrowing are runtime-owned. */
export type CapabilityDeclaration = {modes: ('intercept' | 'observe')[]; capabilities: Capabilities};
export type CapabilityObservation = {modes: ['observe']};
function capabilityNonempty(values: readonly string[], allowed: readonly string[]): void {
  if (!Array.isArray(values) || !values.length || values.some(v => !allowed.includes(v))) throw new TypeError('nonempty valid operations required');
}
function capabilityMerge(a: any, b: any): any {
  const result = {...a};
  for (const [k,v] of Object.entries(b)) {
    if (Array.isArray(v)) result[k] = [...new Set([...(a[k] ?? []), ...v])];
    else if (typeof v === 'object' && v !== null) result[k] = capabilityMerge(a[k] ?? {}, v);
    else result[k] = typeof v === 'boolean' ? a[k] === true || v : v;
  }
  return result;
}
"#;
const TS_BUILDER: &str = r#"
export class CapabilityBuilder {
  readonly #value: Capabilities;
  private constructor(value: Capabilities) { this.#value = value; Object.freeze(this); }
  static intercept(): CapabilityBuilder { return new CapabilityBuilder({effects: []}); }
  private add(effect?: string, grant: object = {}): CapabilityBuilder {
    return new CapabilityBuilder(capabilityMerge(this.#value, {...grant, effects: effect ? [effect] : []}));
  }
  build(): CapabilityDeclaration {
    if (this.#value.effects.length === 0 && !Object.keys((this.#value as any).elicitation ?? {}).length) throw new TypeError('empty interception declaration');
    return {modes: ['intercept', 'observe'], capabilities: JSON.parse(JSON.stringify(this.#value))};
  }
  get modes(): ('intercept' | 'observe')[] { return ['intercept', 'observe']; }
  get capabilities(): Capabilities { return this.build().capabilities; }
  toJSON(): CapabilityDeclaration { return this.build(); }
"#;

pub fn rust(ir: &Ir) -> Result<String> {
    let Some(m) = metadata(ir)? else {
        return Ok(String::new());
    };
    let mut out = String::from(RUST_PRELUDE);
    out.push_str("pub use super::EventType as Event;\n");
    for (name, values) in [("EffectType", &m.effects), ("ModifyTarget", &m.targets)] {
        writeln!(
            out,
            "#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize)] pub enum {name} {{"
        )?;
        for value in values {
            writeln!(out, "#[serde(rename = {value:?})] {},", pascal(value))?;
        }
        writeln!(
            out,
            "}}\nimpl {name} {{ pub fn as_str(self) -> &'static str {{ match self {{"
        )?;
        for value in values {
            writeln!(out, "Self::{} => {value:?},", pascal(value))?;
        }
        out.push_str("} } }\n");
    }

    for (name, values) in [
        ("ModifyOperation", &m.operations),
        ("FlowOperation", &m.flow),
        ("Delivery", &m.delivery),
    ] {
        writeln!(
            out,
            "#[derive(Debug, Clone, Copy, PartialEq, Eq)] pub enum {name} {{ {} }}",
            values
                .iter()
                .map(|s| pascal(s))
                .collect::<Vec<_>>()
                .join(",")
        )?;
        writeln!(
            out,
            "impl {name} {{ fn wire(self) -> &'static str {{ match self {{ {} }} }} }}",
            values
                .iter()
                .map(|s| format!("Self::{} => {s:?}", pascal(s)))
                .collect::<Vec<_>>()
                .join(",")
        )?;
    }
    out.push_str("impl Builder {\n");
    for effect in &m.effects {
        if ["modify", "flow", "inject"].contains(&effect.as_str()) {
            continue;
        }
        let name = if effect == "return" {
            "r#return".to_owned()
        } else {
            snake(effect)
        };
        writeln!(
            out,
            "pub fn {name}(&self) -> Self {{ self.add(Some({effect:?}), serde_json::json!({{}})) }}"
        )?;
    }
    for target in &m.targets {
        writeln!(
            out,
            "pub fn modify_{}(&self, operations: impl IntoIterator<Item=ModifyOperation>) -> Result<Self, Error> {{\nlet mut grant = serde_json::json!({{ {} }}); let mut any = false;\nfor operation in operations {{ grant[operation.wire()] = serde_json::json!(true); any = true; }}\nif !any {{ return Err(Error(\"nonempty modify operations required\".into())); }}\nOk(self.add(Some(\"modify\"), serde_json::json!({{\"modify\": {{{target:?}: grant}}}})))\n}}",
            snake(target),
            m.operations
                .iter()
                .map(|s| format!("{s:?}: false"))
                .collect::<Vec<_>>()
                .join(",")
        )?;
    }
    for mode in &m.elicitation {
        writeln!(
            out,
            "pub fn elicitation_{}(&self) -> Self {{ self.add(None, serde_json::json!({{\"elicitation\": {{{mode:?}: {{}}}}}})) }}",
            snake(mode)
        )?;
    }
    out.push_str(RUST_METHODS);
    out.push_str("}\n");
    Ok(out)
}
const RUST_PRELUDE: &str = r#"
/// Immutable declarations only; boundary admission and per-call narrowing are runtime-owned.
pub mod capability {
use super::Capabilities;
use serde::Serialize;
use serde_json::{Value, json};
#[derive(Debug, Clone, PartialEq, Eq)] pub struct Error(pub String);
impl std::fmt::Display for Error { fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result { f.write_str(&self.0) } }
impl std::error::Error for Error {}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all="lowercase")] pub enum Mode { Intercept, Observe }
#[derive(Debug, Clone, Serialize)]
pub struct Declaration { pub modes: Vec<Mode>, #[serde(skip_serializing_if="Option::is_none")] pub capabilities: Option<Capabilities> }
#[derive(Debug, Clone)] pub struct Builder { value: Value }
/// Deliberately advertises BOTH intercept and observe, unlike raw manifests.
pub fn intercept() -> Builder { Builder { value: json!({"effects": []}) } }
pub fn observe() -> Declaration { Declaration { modes: vec![Mode::Observe], capabilities: None } }
fn merge(a: &mut Value, b: Value) {
    match (a, b) {
        (Value::Object(a), Value::Object(b)) => for (k,v) in b { merge(a.entry(k).or_insert(Value::Null), v); },
        (Value::Array(a), Value::Array(b)) => for v in b { if !a.contains(&v) { a.push(v); } },
        (Value::Bool(a), Value::Bool(b)) => *a |= b,
        (a,b) => *a = b,
    }
}
impl Serialize for Builder {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok,S::Error> {
        self.build().map_err(serde::ser::Error::custom)?.serialize(serializer)
    }
}
"#;
const RUST_METHODS: &str = r#"
fn add(&self, effect: Option<&str>, grant: Value) -> Self {
    let mut next = self.clone(); merge(&mut next.value, grant);
    if let Some(effect) = effect { merge(&mut next.value, json!({"effects": [effect]})); }
    next
}
pub fn build(&self) -> Result<Declaration, Error> {
    let effects = self.value.get("effects").and_then(Value::as_array).map_or(0, Vec::len);
    let modes = self.value.get("elicitation").and_then(Value::as_object).map_or(0, |v| v.len());
    if effects == 0 && modes == 0 { return Err(Error("empty interception declaration".into())); }
    let capabilities = serde_json::from_value(self.value.clone()).map_err(|e| Error(format!("invalid capability declaration: {e}")))?;
    Ok(Declaration { modes: vec![Mode::Intercept, Mode::Observe], capabilities: Some(capabilities) })
}
pub fn flow(&self, operations: impl IntoIterator<Item=FlowOperation>, counts: ContinuationCounts) -> Result<Self, Error> {
    let operations: Vec<_> = operations.into_iter().map(FlowOperation::wire).collect();
    if operations.is_empty() { return Err(Error("nonempty flow operations required".into())); }
    if operations.contains(&"continue") && (counts.remaining_continuations.is_none() || counts.continuation_count.is_none()) { return Err(Error("continue requires counts".into())); }
    let mut flow = json!({"operations": []});
    for operation in operations { merge(&mut flow, json!({"operations": [operation]})); }
    for (key,value) in [("remainingContinuations", counts.remaining_continuations), ("continuationCount", counts.continuation_count), ("maxContinuations", counts.max_continuations)] {
        if let Some(value) = value {
            if value > 9_007_199_254_740_991 { return Err(Error("continuation count exceeds safe integer range".into())); }
            flow[key] = json!(value);
        }
    }
    Ok(self.add(Some("flow"), json!({"flow":flow})))
}
pub fn inject_context(&self, deliver_at: impl IntoIterator<Item=Delivery>) -> Result<Self, Error> {
    let mut values = Vec::new();
    for value in deliver_at { let value = value.wire(); if !values.contains(&value) { values.push(value); } }
    if values.is_empty() { return Err(Error("nonempty delivery operations required".into())); }
    Ok(self.add(Some("inject"), json!({"inject":{"context":{"append":true,"deliverAt":values}}})))
}
}
#[derive(Debug, Clone, Copy, Default)]
pub struct ContinuationCounts { pub remaining_continuations: Option<u64>, pub continuation_count: Option<u64>, pub max_continuations: Option<u64> }
"#;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn effect_helpers_follow_the_ir_vocabulary() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let mut ir = crate::compiler::compile(&root, "draft").unwrap();
        let root = ir
            .types
            .iter_mut()
            .find(|n| n.name == "Capabilities")
            .unwrap();
        let Shape::Object { properties, .. } = &mut root.shape else {
            panic!("capabilities object")
        };
        properties
            .iter_mut()
            .find(|p| p.wire_name == "effects")
            .unwrap()
            .shape = Shape::Array {
            items: Box::new(Shape::Enum {
                values: vec![serde_json::json!("testEffect")],
                open_strings: false,
            }),
        };
        assert!(typescript(&ir).unwrap().contains("testEffect():"));
        assert!(rust(&ir).unwrap().contains("test_effect(&self)"));
        assert!(!typescript(&ir).unwrap().contains("deny():"));
        assert!(!rust(&ir).unwrap().contains("deny(&self)"));
    }

    #[test]
    fn all_schema_modification_targets_have_both_native_helpers() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let ir = crate::compiler::compile(&root, "draft").unwrap();
        let metadata = metadata(&ir).unwrap().unwrap();
        let ts = typescript(&ir).unwrap();
        let rs = rust(&ir).unwrap();
        for target in metadata.targets {
            assert!(ts.contains(&format!("modify{}(", pascal(&target))));
            assert!(rs.contains(&format!("modify_{}(&self", snake(&target))));
        }
    }
}
