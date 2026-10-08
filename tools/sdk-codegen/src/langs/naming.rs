//! Language-neutral public names for union alternatives. Names never select or
//! reorder alternatives: callers must keep using the original shapes for codecs.
use std::collections::{BTreeMap, BTreeSet};

use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::model::Shape;

/// Return PascalCase labels in input order, independent of alternative order.
/// Only ambiguous labels receive a structural digest. Identical alternatives
/// necessarily share an identity; their repeated occurrences get local suffixes.
/// `Unknown` is reserved for emitters' forward-compatible fallback arm.
pub(super) fn union_names(variants: &[Shape]) -> Vec<String> {
    let custom_string = variants
        .iter()
        .any(|s| matches!(s, Shape::Enum { values, .. } if values.iter().all(Value::is_string)));
    let bases = variants
        .iter()
        .map(|s| {
            if custom_string && matches!(s, Shape::String) {
                "Custom".into()
            } else {
                label(s)
            }
        })
        .collect::<Vec<String>>();
    let mut counts = BTreeMap::new();
    for base in &bases {
        *counts.entry(base.clone()).or_insert(0usize) += 1;
    }
    let identities = variants.iter().map(identity).collect::<Vec<_>>();
    let mut allocated = BTreeMap::new();
    let mut used = BTreeSet::from(["Unknown".to_owned(), "Self".to_owned()]);
    // Reserve unambiguous semantic names before allocating digest names.
    for base in &bases {
        if counts[base] == 1 && !matches!(base.as_str(), "Unknown" | "Self") {
            used.insert(base.clone());
        }
    }
    let keys = bases
        .iter()
        .cloned()
        .zip(identities.iter().cloned())
        .collect::<BTreeSet<_>>();
    for (base, key) in keys {
        let name = if counts[&base] == 1 && !matches!(base.as_str(), "Unknown" | "Self") {
            base.clone()
        } else {
            let digest = format!("{:x}", Sha256::digest(key.as_bytes()));
            let stem = format!("{base}Shape{}", &digest[..16]);
            let mut candidate = stem.clone();
            let mut suffix = 2;
            while !used.insert(candidate.clone()) {
                candidate = format!("{stem}{suffix}");
                suffix += 1;
            }
            candidate
        };
        allocated.insert((base, key), name);
    }
    let mut occurrences = BTreeMap::new();
    bases
        .into_iter()
        .zip(identities)
        .map(|key| {
            let name = &allocated[&key];
            let count = occurrences.entry(key).or_insert(0usize);
            *count += 1;
            if *count == 1 {
                name.clone()
            } else {
                format!("{name}Duplicate{count}")
            }
        })
        .collect()
}

/// Stable value-derived labels for enum constants, in source order.
pub(super) fn literal_names(values: &[Value]) -> Vec<String> {
    union_names(
        &values
            .iter()
            .cloned()
            .map(|value| Shape::Literal { value })
            .collect::<Vec<_>>(),
    )
}

fn identity(shape: &Shape) -> String {
    fn canonical(value: &mut Value) {
        match value {
            Value::Object(fields) => {
                // Literal JSON arrays are ordered, unlike schema alternatives.
                for (key, value) in fields {
                    if key == "value" {
                        continue;
                    }
                    canonical(value);
                    if matches!(
                        key.as_str(),
                        "variants" | "values" | "properties" | "forbidden_property_sets"
                    ) {
                        if let Value::Array(items) = value {
                            items.sort_by_key(Value::to_string);
                        }
                    }
                }
            }
            Value::Array(items) => {
                for item in items {
                    canonical(item);
                }
            }
            _ => {}
        }
    }
    let mut value = serde_json::to_value(shape).expect("shape serialization");
    canonical(&mut value);
    value.to_string()
}

fn label(shape: &Shape) -> String {
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
            let literals = properties
                .iter()
                .filter(|p| p.required)
                .filter_map(|p| {
                    if let Shape::Literal {
                        value: Value::String(value),
                    } = &p.shape
                    {
                        Some((p.wire_name.as_str(), value.as_str()))
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
            ]
            .into_iter()
            .find(|k| literals.contains_key(k))
            {
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
                return result;
            }
            let required = properties.iter().any(|p| p.required);
            let names = properties
                .iter()
                .filter(|p| !required || p.required)
                .map(|p| pascal(&p.wire_name))
                .collect::<BTreeSet<_>>();
            if names.is_empty() {
                "Object".into()
            } else {
                format!("{}Object", names.into_iter().collect::<String>())
            }
        }
        Shape::Union { variants, .. } | Shape::Intersection { variants } => {
            let names = variants
                .iter()
                .map(label)
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect::<Vec<_>>();
            names.join(if matches!(shape, Shape::Union { .. }) {
                "Or"
            } else {
                "And"
            })
        }
    }
}

fn literal(value: &Value) -> String {
    match value {
        Value::String(s) if s.is_empty() => "Empty".into(),
        Value::String(s) => pascal(s),
        Value::Null => "Null".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Number(n) => pascal(&format!(
            "Value {}",
            n.to_string()
                .replace('-', "Negative ")
                .replace('.', " Point ")
        )),
        Value::Array(_) => "LiteralArray".into(),
        Value::Object(fields) => format!(
            "Literal{}Object",
            fields.keys().map(|key| pascal(key)).collect::<String>()
        ),
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
    fn collisions_are_structural_and_order_independent() {
        let mut shapes = vec![
            Shape::Ref {
                name: "foo-bar".into(),
            },
            Shape::Ref {
                name: "foo_bar".into(),
            },
            object("result", Shape::String),
            object("result", Shape::Integer),
            Shape::Ref {
                name: "Unknown".into(),
            },
            Shape::Null,
        ];
        let names = union_names(&shapes);
        assert_eq!(names.iter().collect::<BTreeSet<_>>().len(), names.len());
        assert!(names[..5].iter().all(|n| n.contains("Shape")));
        assert_eq!(names[5], "Null");
        shapes.reverse();
        assert_eq!(
            union_names(&shapes),
            names.into_iter().rev().collect::<Vec<_>>()
        );
    }
    #[test]
    fn duplicate_shapes_remain_distinct() {
        let names = union_names(&[Shape::String, Shape::String]);
        assert_ne!(names[0], names[1]);
        assert!(!names.iter().any(|n| n.starts_with("Variant")));
    }
    #[test]
    fn literal_names_preserve_value_identity_under_reordering() {
        let mut values = vec![
            serde_json::json!(-42),
            serde_json::json!(3.5),
            serde_json::json!([1, 2]),
            serde_json::json!([2, 1]),
            serde_json::json!({"foo": 1}),
            serde_json::json!({"foo": 2}),
            serde_json::json!("foo-bar"),
            serde_json::json!("foo_bar"),
        ];
        let names = literal_names(&values);
        assert_eq!(names[0], "ValueNegative42");
        assert_eq!(names[1], "Value3Point5");
        assert_eq!(names.iter().collect::<BTreeSet<_>>().len(), names.len());
        values.reverse();
        assert_eq!(
            literal_names(&values),
            names.into_iter().rev().collect::<Vec<_>>()
        );
    }

    #[test]
    fn property_order_does_not_change_identity() {
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
        assert_eq!(identity(&shape), identity(&reversed));
        assert_eq!(label(&shape), label(&reversed));
    }
}
