use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Component, Path, PathBuf};

use anyhow::{Context, Result, anyhow, bail, ensure};
use serde::de::DeserializeOwned;
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::model::{
    AdditionalProperties, Ir, NamedType, Profile, Property, SchemaManifest, Shape, UnionMode,
};

pub fn compile(repository: &Path, revision: &str) -> Result<Ir> {
    let repository = repository
        .canonicalize()
        .with_context(|| format!("cannot open repository {}", repository.display()))?;
    let schema_manifest_path = safe_path(&repository, &format!("schema/{revision}/manifest.json"))?;
    let schema_manifest: SchemaManifest = read_json(&schema_manifest_path)?;
    ensure!(
        schema_manifest.snapshot_version == revision,
        "schema manifest revision mismatch"
    );
    ensure!(
        schema_manifest.dialect == "https://json-schema.org/draft/2020-12/schema",
        "unsupported JSON Schema dialect {}",
        schema_manifest.dialect
    );

    let profile = &schema_manifest.sdk_generation;
    let mut documents = BTreeMap::new();
    for document in &schema_manifest.documents {
        let path = safe_path(&repository, &document.path)?;
        verify_hash(&path, &document.sha256)?;
        let value: Value = read_json(&path)?;
        validate_schema_node(&value, &document.path, "")?;
        documents.insert(document.path.clone(), value);
    }
    let common_document = format!("schema/{revision}/common.schema.json");
    let common_schema = documents
        .get(&common_document)
        .ok_or_else(|| anyhow!("schema manifest lacks {common_document}"))?;
    validate_protocol_version(
        common_schema,
        &schema_manifest.protocol_version,
        &common_document,
    )?;

    let compiler = Compiler {
        profile,
        documents: &documents,
    };
    compiler.validate_metadata()?;

    let mut types = Vec::new();
    let mut names = BTreeSet::new();
    for (source, name) in &profile.stable_names {
        ensure!(
            names.insert(name.clone()),
            "duplicate stable type name {name}"
        );
        let (document, pointer) = split_source(source)?;
        let node = compiler.node(document, pointer)?;
        types.push(NamedType {
            name: name.clone(),
            source: source.clone(),
            shape: compiler.lower(document, pointer, node)?,
        });
    }
    types.sort_by(|a, b| a.name.cmp(&b.name));
    let mut roots = profile
        .stable_names
        .iter()
        .filter_map(|(source, name)| {
            let (schema, pointer) = source.split_once('#')?;
            pointer.is_empty().then(|| crate::model::PublicRoot {
                name: name.clone(),
                schema: schema.to_owned(),
            })
        })
        .collect::<Vec<_>>();
    roots.sort_by(|a, b| a.name.cmp(&b.name));

    Ok(Ir {
        schema_revision: schema_manifest.snapshot_version,
        protocol_version: schema_manifest.protocol_version,
        roots,
        types,
    })
}

struct Compiler<'a> {
    profile: &'a Profile,
    documents: &'a BTreeMap<String, Value>,
}

impl Compiler<'_> {
    fn validate_metadata(&self) -> Result<()> {
        for (document, schema) in self.documents {
            let root_is_named_type = schema.as_object().is_some_and(|object| {
                ["type", "const", "enum", "oneOf", "anyOf", "allOf"]
                    .iter()
                    .any(|keyword| object.contains_key(*keyword))
            });
            if root_is_named_type {
                ensure!(
                    self.profile
                        .stable_names
                        .contains_key(&format!("{document}#")),
                    "schema root {document} needs a stable name in the schema manifest"
                );
            }
            if let Some(definitions) = schema.get("$defs").and_then(Value::as_object) {
                for name in definitions.keys() {
                    let pointer = format!("{document}#/$defs/{}", escape_pointer(name));
                    ensure!(
                        self.profile.stable_names.contains_key(&pointer),
                        "schema definition {pointer} needs a stable name in the schema manifest"
                    );
                }
            }
            self.validate_refs(document, schema)?;
        }
        Ok(())
    }

    fn validate_refs(&self, document: &str, value: &Value) -> Result<()> {
        match value {
            Value::Object(object) => {
                if let Some(reference) = object.get("$ref").and_then(Value::as_str) {
                    let (target_document, pointer) = resolve_reference(document, reference)?;
                    self.node(&target_document, &pointer).with_context(|| {
                        format!("unresolved reference {reference} from {document}")
                    })?;
                }
                for child in object.values() {
                    self.validate_refs(document, child)?;
                }
            }
            Value::Array(values) => {
                for child in values {
                    self.validate_refs(document, child)?;
                }
            }
            _ => {}
        }
        Ok(())
    }

    fn node(&self, document: &str, pointer: &str) -> Result<&Value> {
        let root = self
            .documents
            .get(document)
            .ok_or_else(|| anyhow!("unknown schema document {document}"))?;
        if pointer.is_empty() {
            return Ok(root);
        }
        root.pointer(pointer)
            .ok_or_else(|| anyhow!("unknown schema pointer {document}#{pointer}"))
    }

    fn lower(&self, document: &str, pointer: &str, schema: &Value) -> Result<Shape> {
        if let Some(value) = schema.as_bool() {
            return Ok(if value { Shape::Any } else { Shape::Never });
        }
        let object = schema
            .as_object()
            .ok_or_else(|| anyhow!("schema {document}#{pointer} is not an object or boolean"))?;

        if let Some(reference) = object.get("$ref").and_then(Value::as_str) {
            ensure!(
                object
                    .keys()
                    .all(|key| key == "$ref" || annotation_keyword(key)),
                "$ref with structural siblings is not supported at {document}#{pointer}"
            );
            let (target_document, target_pointer) = resolve_reference(document, reference)?;
            let key = format!("{target_document}#{target_pointer}");
            let name = self.profile.stable_names.get(&key).ok_or_else(|| {
                anyhow!("reference target {key} needs a stable name in the schema manifest")
            })?;
            return Ok(Shape::Ref { name: name.clone() });
        }
        if let Some(value) = object.get("const") {
            ensure_no_structural_siblings(object, &["const"], "const", document, pointer)?;
            return Ok(Shape::Literal {
                value: value.clone(),
            });
        }
        if let Some(values) = object.get("enum").and_then(Value::as_array) {
            ensure_no_structural_siblings(object, &["enum"], "enum", document, pointer)?;
            ensure!(!values.is_empty(), "empty enum at {document}#{pointer}");
            return Ok(Shape::Enum {
                values: values.clone(),
                open_strings: values.iter().all(Value::is_string),
            });
        }
        if let Some(variants) = object.get("oneOf").and_then(Value::as_array) {
            ensure_no_structural_siblings(object, &["oneOf"], "oneOf", document, pointer)?;
            return self.lower_union(document, pointer, variants, UnionMode::OneOf);
        }
        if let Some(variants) = object.get("anyOf").and_then(Value::as_array) {
            ensure_no_structural_siblings(object, &["anyOf"], "anyOf", document, pointer)?;
            return self.lower_union(document, pointer, variants, UnionMode::AnyOf);
        }
        if object.get("type").is_none()
            && !object.contains_key("properties")
            && let Some(variants) = object.get("allOf").and_then(Value::as_array)
        {
            let variants = variants
                .iter()
                .enumerate()
                .map(|(index, value)| {
                    self.lower(document, &child_pointer(pointer, "allOf", index), value)
                })
                .collect::<Result<Vec<_>>>()?;
            return Ok(Shape::Intersection { variants });
        }

        match object.get("type").and_then(Value::as_str) {
            Some("null" | "boolean" | "integer" | "number" | "string") => {
                ensure_no_structural_siblings(object, &["type"], "type", document, pointer)?;
            }
            Some("array") => ensure_no_structural_siblings(
                object,
                &["type", "items"],
                "array type",
                document,
                pointer,
            )?,
            _ => {}
        }

        match object.get("type").and_then(Value::as_str) {
            Some("null") => Ok(Shape::Null),
            Some("boolean") => Ok(Shape::Boolean),
            Some("integer") => Ok(Shape::Integer),
            Some("number") => Ok(Shape::Number),
            Some("string") => Ok(Shape::String),
            Some("array") => {
                let items = object.get("items").unwrap_or(&Value::Bool(true));
                Ok(Shape::Array {
                    items: Box::new(self.lower(document, &format!("{pointer}/items"), items)?),
                })
            }
            Some("object") | None
                if object.contains_key("properties")
                    || object.get("type").and_then(Value::as_str) == Some("object") =>
            {
                if let Some(variants) = object.get("allOf").and_then(Value::as_array) {
                    ensure!(
                        variants.iter().all(conditional_validation_only),
                        "structural allOf siblings are unsupported at {document}#{pointer}"
                    );
                }
                let required: BTreeSet<&str> = object
                    .get("required")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .filter_map(Value::as_str)
                    .collect();
                let mut properties = Vec::new();
                if let Some(entries) = object.get("properties").and_then(Value::as_object) {
                    for (wire_name, value) in entries {
                        properties.push(Property {
                            wire_name: wire_name.clone(),
                            required: required.contains(wire_name.as_str()),
                            shape: self.lower(
                                document,
                                &format!("{pointer}/properties/{}", escape_pointer(wire_name)),
                                value,
                            )?,
                        });
                    }
                }
                properties.sort_by(|a, b| a.wire_name.cmp(&b.wire_name));
                let forbidden_property_sets = forbidden_property_sets(object, document, pointer)?;
                let additional = match object.get("additionalProperties") {
                    None | Some(Value::Bool(true)) => AdditionalProperties::Allowed,
                    Some(Value::Bool(false)) => AdditionalProperties::Forbidden,
                    Some(_) => {
                        bail!("typed additionalProperties is unsupported at {document}#{pointer}")
                    }
                };
                Ok(Shape::Object {
                    properties,
                    forbidden_property_sets,
                    additional,
                })
            }
            None if object.is_empty()
                || object
                    .keys()
                    .all(|key| annotation_or_validation_keyword(key)) =>
            {
                Ok(Shape::Any)
            }
            Some(other) => bail!("unsupported schema type {other} at {document}#{pointer}"),
            None => bail!("cannot lower schema at {document}#{pointer}"),
        }
    }

    fn lower_union(
        &self,
        document: &str,
        pointer: &str,
        values: &[Value],
        mode: UnionMode,
    ) -> Result<Shape> {
        ensure!(!values.is_empty(), "empty union at {document}#{pointer}");
        let discriminator = match mode {
            UnionMode::OneOf => infer_discriminator(self, document, values)?,
            UnionMode::AnyOf => None,
        };
        let keyword = match mode {
            UnionMode::OneOf => "oneOf",
            UnionMode::AnyOf => "anyOf",
        };
        let variants = values
            .iter()
            .enumerate()
            .map(|(index, value)| {
                self.lower(document, &child_pointer(pointer, keyword, index), value)
            })
            .collect::<Result<Vec<_>>>()?;
        Ok(Shape::Union {
            mode,
            variants,
            discriminator,
        })
    }
}

fn conditional_validation_only(schema: &Value) -> bool {
    schema.as_object().is_some_and(|object| {
        object.keys().all(|key| {
            matches!(key.as_str(), "if" | "then" | "else" | "not") || annotation_keyword(key)
        })
    })
}

fn forbidden_property_sets(
    schema: &serde_json::Map<String, Value>,
    document: &str,
    pointer: &str,
) -> Result<Vec<Vec<String>>> {
    let Some(not_schema) = schema.get("not") else {
        return Ok(Vec::new());
    };
    let not_object = not_schema
        .as_object()
        .ok_or_else(|| anyhow!("unsupported not constraint at {document}#{pointer}"))?;
    let candidates = if let Some(any_of) = not_object.get("anyOf").and_then(Value::as_array) {
        any_of.iter().collect::<Vec<_>>()
    } else {
        vec![not_schema]
    };
    let mut sets = Vec::new();
    for candidate in candidates {
        let candidate = candidate
            .as_object()
            .ok_or_else(|| anyhow!("unsupported not constraint at {document}#{pointer}"))?;
        ensure!(
            candidate
                .keys()
                .all(|key| key == "required" || annotation_keyword(key)),
            "unsupported structural not constraint at {document}#{pointer}"
        );
        let mut required = candidate
            .get("required")
            .and_then(Value::as_array)
            .ok_or_else(|| anyhow!("not constraint lacks required at {document}#{pointer}"))?
            .iter()
            .map(|value| {
                value
                    .as_str()
                    .map(str::to_owned)
                    .ok_or_else(|| anyhow!("non-string required value at {document}#{pointer}"))
            })
            .collect::<Result<Vec<_>>>()?;
        ensure!(
            !required.is_empty(),
            "empty required in not constraint at {document}#{pointer}"
        );
        required.sort();
        sets.push(required);
    }
    sets.sort();
    Ok(sets)
}

fn infer_discriminator(
    compiler: &Compiler<'_>,
    document: &str,
    variants: &[Value],
) -> Result<Option<String>> {
    let properties = variants
        .iter()
        .map(|variant| const_string_properties(compiler, document, variant))
        .collect::<Result<Vec<_>>>()?;
    let Some(first) = properties.first() else {
        return Ok(None);
    };
    let mut candidates = first
        .keys()
        .filter(|property| {
            let values = properties
                .iter()
                .filter_map(|variant| variant.get(*property))
                .collect::<BTreeSet<_>>();
            values.len() == properties.len()
        })
        .cloned()
        .collect::<Vec<_>>();
    ensure!(
        candidates.len() <= 1,
        "oneOf has multiple possible discriminators: {}",
        candidates.join(", ")
    );
    Ok(candidates.pop())
}

fn const_string_properties(
    compiler: &Compiler<'_>,
    document: &str,
    schema: &Value,
) -> Result<BTreeMap<String, String>> {
    let mut document = document.to_owned();
    let mut schema = schema;
    if let Some(reference) = schema.get("$ref").and_then(Value::as_str) {
        let (target_document, pointer) = resolve_reference(&document, reference)?;
        document = target_document;
        schema = compiler.node(&document, &pointer)?;
    }
    Ok(schema
        .get("properties")
        .and_then(Value::as_object)
        .into_iter()
        .flatten()
        .filter_map(|(name, property)| {
            property
                .get("const")
                .and_then(Value::as_str)
                .map(|value| (name.clone(), value.to_owned()))
        })
        .collect())
}

fn read_json<T: DeserializeOwned>(path: &Path) -> Result<T> {
    let bytes = fs::read(path).with_context(|| format!("cannot read {}", path.display()))?;
    serde_json::from_slice(&bytes).with_context(|| format!("invalid JSON in {}", path.display()))
}

fn verify_hash(path: &Path, expected: &str) -> Result<()> {
    let bytes = fs::read(path).with_context(|| format!("cannot hash {}", path.display()))?;
    let actual = format!("{:x}", Sha256::digest(bytes));
    ensure!(
        actual == expected,
        "SHA-256 mismatch for {}: expected {expected}, got {actual}",
        path.display()
    );
    Ok(())
}

fn safe_path(repository: &Path, relative: &str) -> Result<PathBuf> {
    let path = Path::new(relative);
    ensure!(
        !path.is_absolute(),
        "absolute path is not allowed: {relative}"
    );
    ensure!(
        path.components()
            .all(|component| matches!(component, Component::Normal(_))),
        "path escapes repository: {relative}"
    );
    let joined = repository.join(path);
    let canonical = joined
        .canonicalize()
        .with_context(|| format!("cannot resolve {relative}"))?;
    ensure!(
        canonical.starts_with(repository),
        "path escapes repository: {relative}"
    );
    Ok(canonical)
}

fn split_source(source: &str) -> Result<(&str, &str)> {
    let (document, fragment) = source
        .split_once('#')
        .ok_or_else(|| anyhow!("stable name source lacks #: {source}"))?;
    ensure!(
        fragment.is_empty() || fragment.starts_with('/'),
        "unsupported fragment in {source}"
    );
    Ok((document, fragment))
}

fn resolve_reference(document: &str, reference: &str) -> Result<(String, String)> {
    ensure!(
        !reference.contains("://"),
        "network reference is forbidden: {reference}"
    );
    let (file, fragment) = reference.split_once('#').unwrap_or((reference, ""));
    ensure!(
        fragment.is_empty() || fragment.starts_with('/'),
        "unsupported reference fragment {reference}"
    );
    let target = if file.is_empty() {
        PathBuf::from(document)
    } else {
        Path::new(document)
            .parent()
            .unwrap_or_else(|| Path::new(""))
            .join(file)
    };
    ensure!(
        target
            .components()
            .all(|component| matches!(component, Component::Normal(_))),
        "reference escapes schema set: {reference}"
    );
    Ok((
        target.to_string_lossy().replace('\\', "/"),
        fragment.to_owned(),
    ))
}

fn validate_schema_node(value: &Value, document: &str, pointer: &str) -> Result<()> {
    if value.is_boolean() {
        return Ok(());
    }
    let object = value
        .as_object()
        .ok_or_else(|| anyhow!("schema node {document}#{pointer} must be object or boolean"))?;
    for key in object.keys() {
        ensure!(
            supported_keyword(key),
            "unsupported JSON Schema keyword {key} at {document}#{pointer}"
        );
    }
    for keyword in ["$defs", "properties"] {
        if let Some(children) = object.get(keyword).and_then(Value::as_object) {
            for (name, child) in children {
                validate_schema_node(
                    child,
                    document,
                    &format!("{pointer}/{keyword}/{}", escape_pointer(name)),
                )?;
            }
        }
    }
    for keyword in ["oneOf", "anyOf", "allOf"] {
        if let Some(children) = object.get(keyword).and_then(Value::as_array) {
            for (index, child) in children.iter().enumerate() {
                validate_schema_node(child, document, &child_pointer(pointer, keyword, index))?;
            }
        }
    }
    for keyword in [
        "items",
        "additionalProperties",
        "contains",
        "not",
        "if",
        "then",
        "else",
        "propertyNames",
    ] {
        if let Some(child) = object.get(keyword) {
            validate_schema_node(child, document, &format!("{pointer}/{keyword}"))?;
        }
    }
    Ok(())
}

fn supported_keyword(key: &str) -> bool {
    key.starts_with("x-")
        || matches!(
            key,
            "$schema"
                | "$id"
                | "$comment"
                | "$defs"
                | "$ref"
                | "title"
                | "description"
                | "deprecated"
                | "examples"
                | "default"
                | "type"
                | "const"
                | "enum"
                | "oneOf"
                | "anyOf"
                | "allOf"
                | "not"
                | "if"
                | "then"
                | "else"
                | "properties"
                | "required"
                | "additionalProperties"
                | "propertyNames"
                | "items"
                | "contains"
                | "minContains"
                | "maxContains"
                | "minItems"
                | "maxItems"
                | "uniqueItems"
                | "minLength"
                | "maxLength"
                | "pattern"
                | "format"
                | "minimum"
                | "maximum"
                | "exclusiveMinimum"
                | "exclusiveMaximum"
                | "multipleOf"
        )
}

fn validate_protocol_version(schema: &Value, expected: &str, document: &str) -> Result<()> {
    let actual = schema
        .pointer("/$defs/protocolVersion/const")
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("{document} lacks a string protocolVersion const"))?;
    ensure!(
        actual == expected,
        "schema protocol version {actual} does not match manifest protocol version {expected}"
    );
    Ok(())
}

fn structural_keyword(key: &str) -> bool {
    matches!(
        key,
        "$ref"
            | "type"
            | "const"
            | "enum"
            | "oneOf"
            | "anyOf"
            | "allOf"
            | "properties"
            | "required"
            | "additionalProperties"
            | "items"
    )
}

fn ensure_no_structural_siblings(
    object: &serde_json::Map<String, Value>,
    handled: &[&str],
    selected: &str,
    document: &str,
    pointer: &str,
) -> Result<()> {
    ensure!(
        object
            .keys()
            .all(|key| !structural_keyword(key) || handled.contains(&key.as_str())),
        "{selected} with structural siblings is not supported at {document}#{pointer}"
    );
    Ok(())
}

fn annotation_keyword(key: &str) -> bool {
    key.starts_with("x-")
        || matches!(
            key,
            "title" | "description" | "$comment" | "deprecated" | "examples" | "default"
        )
}

fn annotation_or_validation_keyword(key: &str) -> bool {
    annotation_keyword(key)
        || matches!(
            key,
            "not"
                | "if"
                | "then"
                | "else"
                | "minLength"
                | "maxLength"
                | "pattern"
                | "format"
                | "minimum"
                | "maximum"
                | "exclusiveMinimum"
                | "exclusiveMaximum"
                | "multipleOf"
                | "minItems"
                | "maxItems"
                | "uniqueItems"
                | "contains"
                | "minContains"
                | "maxContains"
                | "propertyNames"
        )
}

fn child_pointer(parent: &str, keyword: &str, index: usize) -> String {
    format!("{parent}/{keyword}/{index}")
}
fn escape_pointer(value: &str) -> String {
    value.replace('~', "~0").replace('/', "~1")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_network_references() {
        assert!(resolve_reference("schema/a.json", "https://example.com/a.json").is_err());
    }

    #[test]
    fn rejects_parent_references() {
        assert!(resolve_reference("schema/v/a.json", "../a.json").is_err());
    }

    #[test]
    fn rejects_unhandled_structural_siblings() {
        let profile = Profile {
            stable_names: BTreeMap::new(),
        };
        let documents = BTreeMap::new();
        let compiler = Compiler {
            profile: &profile,
            documents: &documents,
        };
        let schema = serde_json::json!({
            "oneOf": [{"type": "string"}, {"type": "number"}],
            "properties": {"value": {"type": "string"}}
        });
        assert!(compiler.lower("schema.json", "", &schema).is_err());
    }

    #[test]
    fn rejects_protocol_version_mismatch() {
        let schema = serde_json::json!({
            "$defs": {"protocolVersion": {"const": "0.2"}}
        });
        assert!(validate_protocol_version(&schema, "0.1", "common.schema.json").is_err());
    }
}
