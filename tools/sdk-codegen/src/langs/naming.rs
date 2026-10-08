//! Language-neutral public names for union alternatives. Names never select or
//! reorder alternatives: callers must keep using the original shapes for codecs.
use std::collections::{BTreeMap, BTreeSet};

use anyhow::{Result, bail};
use serde_json::Value;

use crate::model::Shape;

/// Public projection only: `source_indices` addresses every original validation
/// alternative represented by this arm. Never replace the validation IR with it.
/// API shared by emitters: projected_union(&[Shape]) -> Result<Vec<ProjectedArm>>.
#[derive(Debug)]
pub(super) struct ProjectedArm {
    pub name: String,
    pub source_indices: Vec<usize>,
}

pub(super) fn projected_union(variants: &[Shape]) -> Result<Vec<ProjectedArm>> {
    let names = try_union_names(variants)?;
    projected_union_with_names(variants, &names)
}

/// Context-aware emitters may resolve unique tags for naming, but projection
/// identity must always come from the untouched original alternatives.
pub(super) fn projected_union_with_names(
    variants: &[Shape],
    names: &[String],
) -> Result<Vec<ProjectedArm>> {
    anyhow::ensure!(
        variants.len() == names.len(),
        "one public name is required for each original alternative"
    );
    let mut used_names = BTreeMap::new();
    let mut arms: Vec<ProjectedArm> = Vec::new();
    let mut identities: BTreeMap<String, usize> = BTreeMap::new();
    for (index, (shape, name)) in variants.iter().zip(names).enumerate() {
        let key = identity(shape);
        if let Some(previous) = used_names.insert(name.clone(), key.clone()) {
            anyhow::ensure!(
                previous == key,
                "distinct public alternatives share name {name:?}; provide canonical schema refs or distinct tags"
            );
        }
        if let Some(&arm) = identities.get(&key) {
            arms[arm].source_indices.push(index);
        } else {
            identities.insert(key, arms.len());
            arms.push(ProjectedArm {
                name: name.clone(),
                source_indices: vec![index],
            });
        }
    }
    Ok(arms)
}

/// Return semantic names in source order. Identical branches repeat their name;
/// callers emitting public declarations must use `projected_union` instead.
#[cfg(test)]
pub(super) fn union_names(variants: &[Shape]) -> Vec<String> {
    try_union_names(variants)
        .expect("union naming failed: add canonical schema refs or distinct semantic tags")
}

pub(super) fn try_union_names(variants: &[Shape]) -> Result<Vec<String>> {
    let custom_string = variants
        .iter()
        .any(|s| matches!(s, Shape::Enum { values, .. } if values.iter().all(Value::is_string)));
    let mut allocated = BTreeMap::new();
    let mut names = Vec::new();
    for shape in variants {
        if matches!(
            shape,
            Shape::Literal {
                value: Value::Array(_) | Value::Object(_)
            }
        ) || matches!(shape, Shape::Literal { value: Value::String(value) } if !value.is_empty() && !value.chars().any(|c| c.is_ascii_alphanumeric()))
        {
            bail!(
                "literal has no meaningful public identifier; provide a canonical schema ref or semantic discriminator"
            );
        }
        let name = if custom_string && matches!(shape, Shape::String) {
            "Custom".into()
        } else {
            label(shape)
        };
        let key = identity(shape);
        if matches!(name.as_str(), "Unknown" | "Self" | "Union" | "Intersection")
            || name.len() > 100
        {
            bail!(
                "public union name {name:?} is reserved or too long; provide a canonical schema ref or semantic discriminator"
            );
        }
        if let Some(previous) = allocated.insert(name.clone(), key.clone()) {
            if previous != key {
                bail!(
                    "distinct union alternatives share public name {name:?}; provide canonical schema refs or distinct semantic discriminator/transport values"
                );
            }
        }
        names.push(name);
    }
    Ok(names)
}

/// Stable value-derived labels for enum constants, in source order.
#[cfg(test)]
pub(super) fn literal_names(values: &[Value]) -> Vec<String> {
    try_literal_names(values)
        .expect("enum naming failed: provide distinct semantic values or canonical schema refs")
}

pub(super) fn try_literal_names(values: &[Value]) -> Result<Vec<String>> {
    try_union_names(
        &values
            .iter()
            .cloned()
            .map(|value| Shape::Literal { value })
            .collect::<Vec<_>>(),
    )
}

fn identity(shape: &Shape) -> String {
    // Deliberately conservative: preserve nested alternative/property order,
    // validation constraints and constructor defaults (which Serialize skips).
    // This key is private and never becomes part of a public identifier.
    format!("{shape:?}")
}

/// Discriminator-derived label, including tagged intersection components.
pub(super) fn tagged_label(shape: &Shape) -> Option<String> {
    match shape {
        Shape::Object { properties, .. } => {
            let literals = properties
                .iter()
                .filter(|p| p.required)
                .filter_map(|p| {
                    if let Shape::Literal {
                        value: Value::String(value),
                    } = &p.shape
                    {
                        Some((p.wire_name.as_str(), value.as_str()))
                    } else if let Shape::Enum {
                        values,
                        open_strings: false,
                    } = &p.shape
                    {
                        if values.len() == 1 {
                            values[0].as_str().map(|v| (p.wire_name.as_str(), v))
                        } else {
                            None
                        }
                    } else {
                        None
                    }
                })
                .collect::<BTreeMap<_, _>>();
            if let Some(key) = [
                "type",
                "kind",
                "mode",
                "method",
                "selection",
                "action",
                "status",
                "transport",
            ]
            .into_iter()
            .find(|k| literals.contains_key(k))
            .or_else(|| {
                literals
                    .keys()
                    .copied()
                    .find(|k| !matches!(*k, "jsonrpc" | "protocolVersion"))
            }) {
                let mut result = pascal(literals[key]);
                for (other, value) in &literals {
                    if *other != key && !matches!(*other, "jsonrpc" | "protocolVersion") {
                        result.push_str(&pascal(value));
                    }
                }
                if key == "selection"
                    && properties
                        .iter()
                        .any(|p| p.required && p.wire_name == "gap")
                {
                    result.push_str("Gap");
                }
                return Some(result);
            }
            if properties.iter().any(|p| {
                p.required && p.wire_name == "transport" && matches!(p.shape, Shape::String)
            }) {
                return Some("CustomTransport".into());
            }
            None
        }
        Shape::Intersection { variants } => {
            let labels = variants
                .iter()
                .filter_map(tagged_label)
                .collect::<BTreeSet<_>>();
            if labels.is_empty() {
                None
            } else {
                Some(labels.into_iter().collect::<Vec<_>>().join("And"))
            }
        }
        _ => None,
    }
}

fn label(shape: &Shape) -> String {
    if let Some(label) = tagged_label(shape) {
        return label;
    }
    match shape {
        Shape::Any => "Any".into(),
        Shape::Never => "Never".into(),
        Shape::Null => "Null".into(),
        Shape::Boolean => "Boolean".into(),
        Shape::Integer => "Integer".into(),
        Shape::Number => "Number".into(),
        Shape::String => "String".into(),
        Shape::Ref { name } => pascal(name),
        Shape::Literal { value } => literal(value),
        Shape::Enum { values, .. } if values.iter().all(Value::is_string) => "Known".into(),
        Shape::Enum { .. } => "KnownValues".into(),
        Shape::Array { items } => format!("{}Array", label(items)),
        Shape::Object { properties, .. } => {
            let required = properties.iter().filter(|p| p.required).collect::<Vec<_>>();
            if required.len() == 1 {
                format!("{}Object", pascal(&required[0].wire_name))
            } else {
                "Object".into()
            }
        }
        Shape::Union { .. } => "Union".into(),
        Shape::Intersection { .. } => "Intersection".into(),
    }
}

fn literal(value: &Value) -> String {
    match value {
        Value::String(s) if s.is_empty() => "Empty".into(),
        Value::String(s) => {
            let name = pascal(s);
            if matches!(name.as_str(), "Unknown" | "Self") {
                format!("{name}Value")
            } else {
                name
            }
        }
        Value::Null => "Null".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(n) => pascal(&format!(
            "Value {}",
            n.to_string()
                .replace('-', "Negative ")
                .replace('.', " Point ")
        )),
        // Composite constants have no intrinsic semantic name. Require the
        // schema author to supply a named model rather than exposing its shape.
        Value::Array(_) | Value::Object(_) => "Value".into(),
    }
}

fn pascal(value: &str) -> String {
    let chars = value.chars().collect::<Vec<_>>();
    let mut result = String::new();
    let mut start = true;
    for (i, c) in chars.iter().copied().enumerate() {
        if !c.is_ascii_alphanumeric() {
            start = true;
            continue;
        }
        let boundary = c.is_ascii_uppercase()
            && i > 0
            && (chars[i - 1].is_ascii_lowercase()
                || chars[i - 1].is_ascii_digit()
                || chars.get(i + 1).is_some_and(char::is_ascii_lowercase));
        result.push(if start || boundary {
            c.to_ascii_uppercase()
        } else {
            c.to_ascii_lowercase()
        });
        start = false;
    }
    if result.is_empty() {
        result.push_str("Value");
    }
    if result.as_bytes()[0].is_ascii_digit() {
        result.insert_str(0, "Value");
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{AdditionalProperties, Property};
    fn object(name: &str, shape: Shape) -> Shape {
        Shape::Object {
            properties: vec![Property {
                wire_name: name.into(),
                required: true,
                shape,
                constructor_default: None,
            }],
            forbidden_property_sets: vec![],
            additional: AdditionalProperties::Allowed,
        }
    }
    #[test]
    fn semantic_names() {
        let shapes = vec![
            Shape::Ref {
                name: "JSONRPCRequest".into(),
            },
            object(
                "kind",
                Shape::Literal {
                    value: "tool-call".into(),
                },
            ),
            Shape::Enum {
                values: vec!["ok".into()],
                open_strings: false,
            },
            Shape::String,
            Shape::Integer,
            Shape::Number,
            Shape::Null,
            Shape::Array {
                items: Box::new(Shape::String),
            },
            object("result", Shape::Any),
        ];
        assert_eq!(
            union_names(&shapes),
            [
                "JsonrpcRequest",
                "ToolCall",
                "Known",
                "Custom",
                "Integer",
                "Number",
                "Null",
                "StringArray",
                "ResultObject"
            ]
        );
    }
    #[test]
    fn ambiguous_names_fail_actionably_without_hashes() {
        for shapes in [
            vec![
                Shape::Ref {
                    name: "foo-bar".into(),
                },
                Shape::Ref {
                    name: "foo_bar".into(),
                },
            ],
            vec![
                object("result", Shape::String),
                object("result", Shape::Integer),
            ],
        ] {
            let error = try_union_names(&shapes).unwrap_err().to_string();
            assert!(error.contains("canonical schema refs"));
        }
    }
    #[test]
    fn duplicate_shapes_share_one_public_arm_with_all_source_indices() {
        let shapes = [Shape::String, Shape::Integer, Shape::String];
        assert_eq!(union_names(&shapes), ["String", "Integer", "String"]);
        let projection = projected_union(&shapes).unwrap();
        assert_eq!(projection.len(), 2);
        assert_eq!(projection[0].name, "String");
        assert_eq!(projection[0].source_indices, [0, 2]);
        assert_eq!(projection[1].source_indices, [1]);
    }
    #[test]
    fn literal_names_are_semantic_or_rejected() {
        assert_eq!(
            literal_names(&[
                serde_json::json!(-42),
                serde_json::json!(3.5),
                "unknown".into()
            ]),
            ["ValueNegative42", "Value3Point5", "UnknownValue"]
        );
        assert!(try_literal_names(&["foo-bar".into(), "foo_bar".into()]).is_err());
        assert!(
            try_literal_names(&[serde_json::json!([1, 2]), serde_json::json!([2, 1])]).is_err()
        );
    }
    #[test]
    fn legitimate_value_labels_are_not_unnamed_composites() {
        assert_eq!(try_literal_names(&["value".into()]).unwrap(), ["Value"]);
        assert_eq!(
            try_union_names(&[Shape::Ref {
                name: "value".into()
            }])
            .unwrap(),
            ["Value"]
        );
        assert!(try_literal_names(&[serde_json::json!({"value": 1})]).is_err());
        assert!(try_literal_names(&[serde_json::json!([1])]).is_err());
    }

    #[test]
    fn arbitrary_tags_and_custom_transport_are_semantic() {
        assert_eq!(
            union_names(&[
                object(
                    "flavor",
                    Shape::Literal {
                        value: "vendor-fast".into()
                    }
                ),
                object("transport", Shape::String)
            ]),
            ["VendorFast", "CustomTransport"]
        );
    }

    #[test]
    fn tagged_intersections_prefer_discriminators_to_structural_names() {
        let tag = object(
            "type",
            Shape::Literal {
                value: "bearer".into(),
            },
        );
        let token = Shape::Union {
            mode: crate::model::UnionMode::OneOf,
            discriminator: None,
            variants: vec![
                object("tokenEnv", Shape::String),
                object("tokenRef", Shape::String),
            ],
        };
        let mut components = vec![tag, token];
        let bearer = Shape::Intersection {
            variants: components.clone(),
        };
        assert_eq!(union_names(&[bearer]), ["Bearer"]);
        components.reverse();
        assert_eq!(
            union_names(&[Shape::Intersection {
                variants: components
            }]),
            ["Bearer"]
        );
    }

    #[test]
    fn projection_identity_conservatively_preserves_constructor_field_order() {
        let mut shape = object("result", Shape::String);
        if let Shape::Object { properties, .. } = &mut shape {
            properties.push(Property {
                wire_name: "id".into(),
                required: true,
                shape: Shape::Integer,
                constructor_default: None,
            });
        }
        let mut reversed = shape.clone();
        if let Shape::Object { properties, .. } = &mut reversed {
            properties.reverse();
        }
        assert_ne!(identity(&shape), identity(&reversed));
        assert_eq!(label(&shape), label(&reversed));
    }
}
