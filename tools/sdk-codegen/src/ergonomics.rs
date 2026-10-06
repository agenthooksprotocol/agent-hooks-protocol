//! Shared SDK-only semantic metadata. These projections never change wire schemas.
use crate::model::{Ir, Property, Shape};
use anyhow::{Result, bail};
use std::collections::{BTreeMap, BTreeSet};

/// Delivery causes, not stages or protocol error codes. Preserve existing Go codes.
pub const DIAGNOSTIC_CODES: &[&str] = &[
    "protocol_rejection",
    "remote_rpc",
    "transport",
    "cancelled",
    "deadline_exceeded",
    "preparation",
    "capacity",
];

#[derive(Debug, Clone)]
pub struct InputField {
    pub property: Property,
    pub path: Vec<String>,
}

/// Resolve object members, including allOf siblings, without following cycles.
pub fn object_fields(ir: &Ir, shape: &Shape) -> Option<Vec<Property>> {
    fn visit(ir: &Ir, shape: &Shape, seen: &mut BTreeSet<String>) -> Option<Vec<Property>> {
        match shape {
            Shape::Object { properties, .. } => Some(properties.clone()),
            Shape::Ref { name } => {
                if !seen.insert(name.clone()) {
                    return None;
                }
                let result = ir
                    .types
                    .iter()
                    .find(|n| n.name == *name)
                    .and_then(|n| visit(ir, &n.shape, seen));
                seen.remove(name);
                result
            }
            Shape::Intersection { variants } => {
                let mut fields = BTreeMap::<String, Property>::new();
                let mut has_object = false;
                for variant in variants {
                    // anyOf/oneOf siblings constrain an existing object; their
                    // branch-only members are not unconditional host fields.
                    if matches!(variant, Shape::Any | Shape::Union { .. }) {
                        continue;
                    }
                    let properties = visit(ir, variant, seen)?;
                    has_object = true;
                    for p in properties {
                        if let Some(old) = fields.get_mut(&p.wire_name) {
                            old.required |= p.required;
                            if !matches!(p.shape, Shape::Any) {
                                old.shape = p.shape;
                            }
                        } else {
                            fields.insert(p.wire_name.clone(), p);
                        }
                    }
                }
                has_object.then(|| fields.into_values().collect())
            }
            _ => None,
        }
    }
    visit(ir, shape, &mut BTreeSet::new())
}

/// Every retained host fact maps to exactly one canonical path. Required nested
/// call/tool wrappers are flattened; optional wrappers retain their presence.
pub fn input_fields(ir: &Ir, fields: &[Property], event: &str) -> Result<Vec<InputField>> {
    let mut result = Vec::new();
    let mut names = BTreeSet::new();
    for p in fields {
        if ["type", "source", "protocolVersion"].contains(&p.wire_name.as_str())
            || (event == "session.start" && p.wire_name == "manifest")
        {
            continue;
        }
        let nested =
            if p.required && ["call", "tool"].contains(&p.wire_name.as_str()) {
                Some(object_fields(ir, &p.shape).ok_or_else(|| {
                    anyhow::anyhow!("unmapped {} wrapper in {event}", p.wire_name)
                })?)
            } else {
                None
            };
        if let Some(children) = nested {
            for mut child in children {
                let path = vec![p.wire_name.clone(), child.wire_name.clone()];
                if !(p.wire_name == "tool"
                    && ["name", "input", "origin"].contains(&child.wire_name.as_str()))
                {
                    let mut chars = child.wire_name.chars();
                    child.wire_name = format!(
                        "{}{}{}",
                        p.wire_name,
                        chars
                            .next()
                            .map(|c| c.to_uppercase().to_string())
                            .unwrap_or_default(),
                        chars.as_str()
                    );
                }
                if !names.insert(child.wire_name.clone()) {
                    bail!("colliding input field {} in {event}", child.wire_name);
                }
                result.push(InputField {
                    property: child,
                    path,
                });
            }
        } else {
            let mut property = p.clone();
            if ["id", "time"].contains(&property.wire_name.as_str()) {
                property.required = false;
            }
            if !names.insert(property.wire_name.clone()) {
                bail!("colliding input field {} in {event}", property.wire_name);
            }
            result.push(InputField {
                property,
                path: vec![p.wire_name.clone()],
            });
        }
    }
    Ok(result)
}

/// A schema-declared content item location. `*` is an array index, not a raw
/// JSON pointer accepted from applications. Source bindings remain out of band.
#[derive(Debug, Clone)]
pub struct ContentSlot {
    pub name: String,
    pub path: Vec<String>,
    pub many: bool,
}

pub fn content_slots(ir: &Ir, fields: &[InputField]) -> Vec<ContentSlot> {
    fn walk(
        ir: &Ir,
        shape: &Shape,
        path: &[String],
        seen: &mut BTreeSet<String>,
        slots: &mut BTreeSet<Vec<String>>,
    ) {
        match shape {
            Shape::Ref { name } if name == "ContentItem" => {
                slots.insert(path.to_vec());
            }
            Shape::Ref { name } => {
                if !seen.insert(name.clone()) {
                    return;
                }
                if let Some(n) = ir.types.iter().find(|n| n.name == *name) {
                    walk(ir, &n.shape, path, seen, slots);
                }
                seen.remove(name);
            }
            Shape::Object { properties, .. } => {
                for p in properties {
                    let mut child = path.to_vec();
                    child.push(p.wire_name.clone());
                    walk(ir, &p.shape, &child, seen, slots);
                }
            }
            Shape::Union { variants, .. } | Shape::Intersection { variants } => {
                for variant in variants {
                    walk(ir, variant, path, seen, slots);
                }
            }
            Shape::Array { items } => {
                let mut child = path.to_vec();
                child.push("*".into());
                walk(ir, items, &child, seen, slots);
            }
            _ => {}
        }
    }
    let mut result = Vec::new();
    for field in fields {
        let mut paths = BTreeSet::new();
        walk(
            ir,
            &field.property.shape,
            &field.path,
            &mut BTreeSet::new(),
            &mut paths,
        );
        for path in paths {
            let mut name = field.property.wire_name.clone();
            for part in path
                .iter()
                .skip(field.path.len())
                .filter(|p| p.as_str() != "*")
            {
                let mut chars = part.chars();
                name.push_str(
                    &chars
                        .next()
                        .map(|c| c.to_uppercase().to_string())
                        .unwrap_or_default(),
                );
                name.push_str(chars.as_str());
            }
            result.push(ContentSlot {
                name,
                many: path.iter().any(|p| p == "*"),
                path,
            });
        }
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn boolean_grant_predicates_do_not_replace_declared_operation_types() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let ir = crate::compiler::compile(&root, "draft").unwrap();
        let root = ir.types.iter().find(|n| n.name == "Capabilities").unwrap();
        let fields = object_fields(&ir, &root.shape).unwrap();
        let modify = fields.iter().find(|p| p.wire_name == "modify").unwrap();
        for target in object_fields(&ir, &modify.shape).unwrap() {
            let operations = object_fields(&ir, &target.shape).unwrap();
            assert_eq!(operations.len(), 2);
            assert!(
                operations
                    .iter()
                    .all(|p| p.required && matches!(p.shape, Shape::Boolean))
            );
        }
    }

    #[test]
    fn named_content_slots_follow_schema_references_without_application_data() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let ir = crate::compiler::compile(&root, "draft").unwrap();
        for (name, event, expected) in [
            (
                "ContextCompactBeforeEvent",
                "context.compact.before",
                vec![
                    ("items", vec!["items", "*"]),
                    ("instructions", vec!["instructions"]),
                ],
            ),
            (
                "ToolAfterEvent",
                "tool.after",
                vec![
                    ("fileChangesAfter", vec!["fileChanges", "*", "after"]),
                    ("fileChangesBefore", vec!["fileChanges", "*", "before"]),
                    ("items", vec!["items", "*"]),
                ],
            ),
        ] {
            let fields = object_fields(
                &ir,
                &ir.types.iter().find(|n| n.name == name).unwrap().shape,
            )
            .unwrap();
            let inputs = input_fields(&ir, &fields, event).unwrap();
            let slots = content_slots(&ir, &inputs);
            assert_eq!(slots.len(), expected.len());
            for (name, path) in expected {
                let slot = slots.iter().find(|s| s.name == name).unwrap();
                assert_eq!(slot.path, path);
                assert_eq!(slot.many, path.contains(&"*"));
            }
        }
    }

    #[test]
    fn optional_wrappers_remain_nested_and_empty_ones_are_not_invented() {
        let ir = Ir {
            schema_revision: "test".into(),
            protocol_version: "test".into(),
            roots: vec![],
            types: vec![],
        };
        let fields = vec![Property {
            wire_name: "tool".into(),
            required: false,
            shape: Shape::Any,
            constructor_default: None,
        }];
        let projected = input_fields(&ir, &fields, "custom.event").unwrap();
        assert_eq!(projected[0].path, ["tool"]);
        assert!(!projected[0].property.required);
    }

    #[test]
    fn all_event_inputs_have_unique_complete_paths() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let ir = crate::compiler::compile(&root, "draft").unwrap();
        let mut count = 0;
        for named in &ir.types {
            if !named.name.ends_with("Event") {
                continue;
            }
            let Some(fields) = object_fields(&ir, &named.shape) else {
                continue;
            };
            if !fields.iter().any(|p| p.wire_name == "source") {
                continue;
            }
            let Some(event) = fields
                .iter()
                .find(|p| p.wire_name == "type")
                .and_then(|p| match &p.shape {
                    Shape::Literal { value } => value.as_str(),
                    _ => None,
                })
            else {
                continue;
            };
            count += 1;
            let projected = input_fields(&ir, &fields, event).unwrap();
            let paths = projected
                .iter()
                .map(|p| p.path.clone())
                .collect::<BTreeSet<_>>();
            assert_eq!(
                paths.len(),
                projected.len(),
                "duplicate mapping for {event}"
            );
            for p in &fields {
                if ["source", "type", "protocolVersion"].contains(&p.wire_name.as_str())
                    || (event == "session.start" && p.wire_name == "manifest")
                {
                    continue;
                }
                if p.required && ["call", "tool"].contains(&p.wire_name.as_str()) {
                    for child in object_fields(&ir, &p.shape).unwrap() {
                        assert!(paths.contains(&vec![p.wire_name.clone(), child.wire_name]));
                    }
                } else {
                    assert!(paths.contains(&vec![p.wire_name.clone()]));
                }
            }
        }
        assert_eq!(count, 32);
    }

    #[test]
    fn canonical_tool_projection_preserves_required_facts() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let ir = crate::compiler::compile(&root, "draft").unwrap();
        let shape = &ir
            .types
            .iter()
            .find(|n| n.name == "ToolBeforeEvent")
            .unwrap()
            .shape;
        let fields = object_fields(&ir, shape).unwrap();
        let projected = input_fields(&ir, &fields, "tool.before").unwrap();
        for name in ["callId", "name", "input", "path", "origin"] {
            assert!(
                projected
                    .iter()
                    .any(|p| p.property.wire_name == name && p.property.required),
                "missing required {name}"
            );
        }
        for name in ["id", "time"] {
            assert!(
                projected
                    .iter()
                    .any(|p| p.property.wire_name == name && !p.property.required)
            );
        }
        assert!(!projected.iter().any(|p| p.property.wire_name == "source"));
        let mut collision = fields;
        collision.push(Property {
            wire_name: "callId".into(),
            required: false,
            shape: Shape::String,
            constructor_default: None,
        });
        assert!(input_fields(&ir, &collision, "tool.before").is_err());
    }
}
