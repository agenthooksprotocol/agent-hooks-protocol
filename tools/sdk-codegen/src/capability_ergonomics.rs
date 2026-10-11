//! Catalogue-derived typed capability declarations. Raw configuration parsing stays independent.
use crate::{
    ergonomics::object_fields,
    model::{Ir, Property, Shape},
};
use anyhow::{Context, Result, ensure};
use std::collections::{BTreeMap, BTreeSet};

/// A concrete catalogue event's installable delivery modes and known authority.
/// Extension effects remain supported by explicit raw configuration codecs only.
#[derive(Debug, Clone)]
pub(crate) struct EventCapabilities {
    pub event: String,
    pub name: String,
    pub intercept: bool,
    pub observe: bool,
    pub effects: Vec<String>,
    pub modify: BTreeMap<String, Vec<String>>,
    pub flow: Vec<String>,
    pub delivery: Vec<String>,
    pub elicitation: Vec<String>,
}

pub(crate) fn vocabulary(ir: &Ir, shape: &Shape) -> Vec<String> {
    fn visit(ir: &Ir, shape: &Shape, seen: &mut BTreeSet<String>, out: &mut BTreeSet<String>) {
        match shape {
            Shape::Enum { values, .. } => {
                out.extend(values.iter().filter_map(|v| v.as_str().map(str::to_owned)))
            }
            Shape::Literal { value } => {
                if let Some(value) = value.as_str() {
                    out.insert(value.into());
                }
            }
            Shape::Array { items } => visit(ir, items, seen, out),
            Shape::Union { variants, .. } | Shape::Intersection { variants } => {
                for variant in variants {
                    visit(ir, variant, seen, out);
                }
            }
            Shape::Ref { name } if seen.insert(name.clone()) => {
                if let Some(named) = ir.types.iter().find(|n| n.name == *name) {
                    visit(ir, &named.shape, seen, out);
                }
                seen.remove(name);
            }
            _ => {}
        }
    }
    let mut out = BTreeSet::new();
    visit(ir, shape, &mut BTreeSet::new(), &mut out);
    out.into_iter().collect()
}

fn field<'a>(fields: &'a [Property], name: &str) -> Option<&'a Property> {
    fields.iter().find(|field| field.wire_name == name)
}
fn tag_values(
    ir: &Ir,
    shape: &Shape,
    path: &[&str],
    seen: &mut BTreeSet<String>,
    out: &mut BTreeSet<String>,
) {
    if let Shape::Intersection { variants } = shape {
        if variants
            .iter()
            .any(|variant| matches!(variant, Shape::Never))
        {
            return;
        }
        if shape.constrained_reference().is_some() {
            tag_values(ir, &variants[1], path, seen, out);
        } else {
            for variant in variants {
                tag_values(ir, variant, path, seen, out);
            }
        }
        return;
    }
    if let Shape::Ref { name } = shape {
        if seen.insert(name.clone()) {
            if let Some(named) = ir.types.iter().find(|n| n.name == *name) {
                tag_values(ir, &named.shape, path, seen, out);
            }
            seen.remove(name);
        }
        return;
    }
    if let Shape::Union { variants, .. } = shape {
        for variant in variants {
            tag_values(ir, variant, path, seen, out);
        }
        return;
    }
    if path.is_empty() {
        if let Shape::Literal { value } = shape {
            if let Some(value) = value.as_str() {
                out.insert(value.into());
            }
        }
    } else if let Some(fields) = object_fields(ir, shape) {
        if let Some(member) = field(&fields, path[0]) {
            tag_values(ir, &member.shape, &path[1..], seen, out);
        }
    }
}
fn effect_objects(
    ir: &Ir,
    shape: &Shape,
    seen: &mut BTreeSet<String>,
    out: &mut Vec<Vec<Property>>,
) {
    match shape {
        Shape::Ref { name } if seen.insert(name.clone()) => {
            if let Some(named) = ir.types.iter().find(|n| n.name == *name) {
                effect_objects(ir, &named.shape, seen, out);
            }
            seen.remove(name);
        }
        Shape::Union { variants, .. } => {
            for variant in variants {
                effect_objects(ir, variant, seen, out);
            }
        }
        _ => {
            if let Some(fields) = object_fields(ir, shape) {
                out.push(fields);
            }
        }
    }
}
fn member_values(ir: &Ir, fields: &[Property], name: &str) -> Vec<String> {
    field(fields, name)
        .map(|member| vocabulary(ir, &member.shape))
        .unwrap_or_default()
}
fn pascal(event: &str) -> String {
    event
        .split(['.', '_', '-'])
        .map(|word| {
            let mut chars = word.chars();
            chars
                .next()
                .map(|first| first.to_uppercase().to_string() + chars.as_str())
                .unwrap_or_default()
        })
        .collect()
}

/// Permissions come from both capability constraints and contextual effects.
/// The IR does not retain JSON Schema propertyNames, so contextual modify targets
/// supply the equivalent schema-owned target restriction instead of a copied list.
pub(crate) fn catalogue(ir: &Ir) -> Result<Vec<EventCapabilities>> {
    let Some(base) = ir.types.iter().find(|named| named.name == "Capabilities") else {
        return Ok(Vec::new());
    };
    let base_fields = object_fields(ir, &base.shape).context("Capabilities must be an object")?;
    let elicitation = field(&base_fields, "elicitation")
        .and_then(|f| object_fields(ir, &f.shape))
        .unwrap_or_default()
        .into_iter()
        .map(|f| f.wire_name)
        .collect::<Vec<_>>();
    let mut modes = BTreeMap::<String, BTreeSet<&str>>::new();
    for (name, mode) in [
        ("InterceptRequest", "intercept"),
        ("ObserveNotification", "observe"),
    ] {
        if let Some(named) = ir.types.iter().find(|n| n.name == name) {
            let mut events = BTreeSet::new();
            tag_values(
                ir,
                &named.shape,
                &["params", "event", "type"],
                &mut BTreeSet::new(),
                &mut events,
            );
            for event in events {
                modes.entry(event).or_default().insert(mode);
            }
        }
    }
    let mut out = Vec::new();
    for (event, modes) in modes {
        let name = pascal(&event);
        let intercept = modes.contains("intercept");
        let mut entry = EventCapabilities {
            event: event.clone(),
            name: name.clone(),
            intercept,
            observe: modes.contains("observe"),
            effects: Vec::new(),
            modify: BTreeMap::new(),
            flow: Vec::new(),
            delivery: Vec::new(),
            elicitation: if intercept {
                elicitation.clone()
            } else {
                Vec::new()
            },
        };
        if intercept {
            let source = format!("capabilities.schema.json#/$defs/{event}");
            let capability = ir
                .types
                .iter()
                .find(|n| n.source.ends_with(&source))
                .with_context(|| format!("missing event capability constraint {event}"))?;
            let fields = object_fields(ir, &capability.shape)
                .with_context(|| format!("invalid capability object {event}"))?;
            let allowed = member_values(ir, &fields, "effects");
            let effect = ir
                .types
                .iter()
                .find(|n| n.name == format!("{name}Effect"))
                .with_context(|| format!("missing contextual effects {event}"))?;
            let mut objects = Vec::new();
            effect_objects(ir, &effect.shape, &mut BTreeSet::new(), &mut objects);
            let mut families = BTreeSet::new();
            let mut flow = BTreeSet::new();
            let mut delivery = BTreeSet::new();
            for fields in objects {
                let types = member_values(ir, &fields, "type");
                // Open extension discriminants do not grant known-family authority.
                for family in types.into_iter().filter(|family| allowed.contains(family)) {
                    families.insert(family.clone());
                    match family.as_str() {
                        "modify" => {
                            for target in member_values(ir, &fields, "target") {
                                let operations = member_values(ir, &fields, "operation");
                                let old = entry.modify.entry(target).or_default();
                                old.extend(operations);
                                old.sort();
                                old.dedup();
                            }
                        }
                        "flow" => flow.extend(member_values(ir, &fields, "operation")),
                        "inject" => delivery.extend(member_values(ir, &fields, "deliverAt")),
                        _ => {}
                    }
                }
            }
            entry.effects = families.into_iter().collect();
            entry.flow = flow.into_iter().collect();
            entry.delivery = delivery.into_iter().collect();
            ensure!(
                !entry.effects.is_empty(),
                "interception event has no known controls: {event}"
            );
            ensure!(
                !entry.effects.contains(&"modify".into()) || !entry.modify.is_empty(),
                "missing modify targets {event}"
            );
            ensure!(
                !entry.effects.contains(&"flow".into()) || !entry.flow.is_empty(),
                "missing flow operations {event}"
            );
            ensure!(
                !entry.effects.contains(&"inject".into()) || !entry.delivery.is_empty(),
                "missing injection delivery {event}"
            );
        }
        out.push(entry);
    }
    Ok(out)
}

pub(crate) fn typescript(ir: &Ir) -> Result<String> {
    crate::langs::typescript::capabilities::emit(ir)
}
pub(crate) fn rust(ir: &Ir) -> Result<String> {
    crate::langs::rust::capabilities::emit(ir)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn draft() -> Ir {
        crate::compiler::compile(
            &std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../.."),
            "draft",
        )
        .unwrap()
    }
    #[test]
    fn typed_catalogue_covers_each_delivery_mode_and_contextual_target() {
        let entries = catalogue(&draft()).unwrap();
        assert_eq!(entries.len(), 32);
        let tool = entries
            .iter()
            .find(|entry| entry.event == "tool.before")
            .unwrap();
        assert!(tool.intercept && tool.observe);
        assert_eq!(tool.modify.keys().cloned().collect::<Vec<_>>(), ["input"]);
        assert_eq!(tool.modify["input"], ["merge", "replace"]);
        assert_eq!(tool.flow, ["stop"]);
        let compact = entries
            .iter()
            .find(|entry| entry.event == "context.compact.before")
            .unwrap();
        assert_eq!(
            compact.modify.keys().cloned().collect::<Vec<_>>(),
            ["instructions"]
        );
        assert!(!compact.effects.contains(&"allow".into()));
        for entry in entries.iter().filter(|entry| !entry.intercept) {
            assert!(entry.observe);
            assert!(entry.effects.is_empty());
            assert!(entry.modify.is_empty());
        }
    }
    #[test]
    fn contextual_permissions_match_canonical_capability_constraints() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let schema: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(root.join("schema/draft/capabilities.schema.json")).unwrap(),
        )
        .unwrap();
        for entry in catalogue(&draft())
            .unwrap()
            .into_iter()
            .filter(|entry| entry.intercept)
        {
            let properties = &schema["$defs"][&entry.event]["allOf"][1]["properties"];
            let strings = |value: &serde_json::Value| {
                value
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|value| value.as_str().unwrap().to_owned())
                    .collect::<BTreeSet<_>>()
            };
            assert_eq!(
                entry.effects.iter().cloned().collect::<BTreeSet<_>>(),
                strings(&properties["effects"]["items"]["enum"]),
                "effect authority {}",
                entry.event
            );
            if let Some(targets) = properties["modify"]["propertyNames"].get("enum") {
                assert_eq!(
                    entry.modify.keys().cloned().collect::<BTreeSet<_>>(),
                    strings(targets),
                    "modify targets {}",
                    entry.event
                );
            }
            if let Some(operations) =
                properties["flow"]["properties"]["operations"]["items"].get("enum")
            {
                assert_eq!(
                    entry.flow.iter().cloned().collect::<BTreeSet<_>>(),
                    strings(operations),
                    "flow operations {}",
                    entry.event
                );
            }
        }
    }

    #[test]
    fn effect_authority_is_event_specific_not_the_global_vocabulary() {
        let mut ir = draft();
        let named = ir
            .types
            .iter_mut()
            .find(|n| n.name == "ToolBeforeCapabilities")
            .unwrap();
        let Shape::Intersection { variants } = &mut named.shape else {
            panic!("event capability intersection")
        };
        let Shape::Object { properties, .. } = variants.last_mut().unwrap() else {
            panic!("event override")
        };
        field_mut(properties, "effects").shape = Shape::Array {
            items: Box::new(Shape::Enum {
                values: vec![serde_json::json!("deny")],
                open_strings: false,
            }),
        };
        let entries = catalogue(&ir).unwrap();
        let tool = entries
            .iter()
            .find(|entry| entry.event == "tool.before")
            .unwrap();
        assert_eq!(tool.effects, ["deny"]);
        assert!(tool.modify.is_empty());
        assert!(tool.flow.is_empty());
        assert!(tool.delivery.is_empty());
    }
    fn field_mut<'a>(fields: &'a mut [Property], name: &str) -> &'a mut Property {
        fields.iter_mut().find(|f| f.wire_name == name).unwrap()
    }
}
