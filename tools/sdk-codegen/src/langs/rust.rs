use std::collections::{BTreeMap, BTreeSet};

#[path = "rust/ergonomics.rs"]
mod ergonomics;
use std::fmt::Write;

use anyhow::{Context, Result};
use serde_json::Value;

use crate::model::{Ir, Property, Shape};

const RESERVED_TYPE_NAMES: &[&str] = &[
    // Imported, prelude, and generated runtime names used unqualified below.
    "BTreeMap",
    "Box",
    "Default",
    "Deserialize",
    "DeserializeOwned",
    "Deserializer",
    "DiagnosticCode",
    "DiagnosticSeverity",
    "Deref",
    "Integer",
    "Hydration",
    "ValidatedHydration",
    "EffectId",
    "Into",
    "From",
    "JsonNumber",
    "JsonValue",
    "OnceLock",
    "Option",
    "ParseDiagnostic",
    "ParseResult",
    "Presence",
    "Result",
    "SchemaNode",
    "SchemaProperty",
    "Serialize",
    "Serializer",
    "String",
    "UnionMode",
    "Vec",
];

/// Emit a self-contained Rust module for the supplied schema IR.
///
/// The generated decoder intentionally performs the same structural checks as
/// the TypeScript SDK. It does not try to reproduce canonical JSON Schema
/// validation. Unknown object members, open-enum strings, and discriminator
/// variants are retained so that decoding and encoding is lossless.
#[allow(dead_code)]
pub fn emit(ir: &Ir) -> Result<String> {
    emit_with_defaults(ir, BTreeMap::new())
}

/// Schema annotations affect construction only, never decoding.
pub fn emit_from_repository(ir: &Ir, repository: &std::path::Path) -> Result<String> {
    let mut defaults = BTreeMap::new();
    for named in &ir.types {
        let Some((path, pointer)) = named.source.split_once('#') else {
            continue;
        };
        let document: Value =
            serde_json::from_str(&std::fs::read_to_string(repository.join(path))?)?;
        if let Some(properties) = document
            .pointer(pointer)
            .and_then(|node| node.get("properties"))
            .and_then(Value::as_object)
        {
            for (field, schema) in properties {
                if let Some(value) = schema.get("default") {
                    defaults.insert((named.name.clone(), field.clone()), value.clone());
                }
            }
        }
    }
    emit_with_defaults(ir, defaults)
}

/// Follow a property path through the normalized IR, keeping only exact literals.
/// Open strings/enums are deliberately not treated as concrete protocol events.
fn boundary_literals(
    ir: &Ir,
    shape: &Shape,
    path: &[&str],
    visiting: &mut BTreeSet<String>,
    names: &mut BTreeSet<String>,
) {
    if let Shape::Intersection { variants } = shape {
        if variants
            .iter()
            .any(|variant| matches!(variant, Shape::Never))
        {
            return;
        }
        if shape.constrained_reference().is_some() {
            boundary_literals(ir, &variants[1], path, visiting, names);
            return;
        }
    }
    match shape {
        Shape::Ref { name } => {
            if visiting.insert(name.clone()) {
                if let Some(named) = ir.types.iter().find(|named| named.name == *name) {
                    boundary_literals(ir, &named.shape, path, visiting, names);
                }
                visiting.remove(name);
            }
        }
        Shape::Union { variants, .. } | Shape::Intersection { variants } => {
            for variant in variants {
                boundary_literals(ir, variant, path, visiting, names);
            }
        }
        Shape::Object { properties, .. } if !path.is_empty() => {
            for property in properties
                .iter()
                .filter(|property| property.wire_name == path[0])
            {
                boundary_literals(ir, &property.shape, &path[1..], visiting, names);
            }
        }
        Shape::Literal {
            value: Value::String(name),
        } if path.is_empty() => {
            names.insert(name.clone());
        }
        _ => {}
    }
}

fn boundary_inventory(ir: &Ir) -> BTreeMap<String, BTreeSet<&'static str>> {
    let mut boundaries = BTreeMap::<String, BTreeSet<&'static str>>::new();
    for (document, mode) in [
        ("observe-notification.schema.json#", "observe"),
        ("intercept-request.schema.json#", "intercept"),
    ] {
        for named in ir
            .types
            .iter()
            .filter(|named| named.source.rsplit('/').next() == Some(document))
        {
            let mut names = BTreeSet::new();
            boundary_literals(
                ir,
                &named.shape,
                &["params", "event", "type"],
                &mut BTreeSet::new(),
                &mut names,
            );
            for name in names {
                boundaries.entry(name).or_default().insert(mode);
            }
        }
    }
    boundaries
}

fn emit_boundaries(ir: &Ir, output: &mut String) -> Result<()> {
    let boundaries = boundary_inventory(ir);
    // Synthetic/non-event schemas can coexist as modules in one consumer crate.
    // Do not export an empty crate-global macro for each such module.
    if boundaries.is_empty() {
        return Ok(());
    }
    output.push_str("\n/// Canonical concrete event boundaries; arbitrary extension events are excluded.\npub mod boundary {\n");
    output.push_str("#[derive(Debug, Clone, Copy, PartialEq, Eq)]\npub struct BoundaryDescriptor {\n    pub name: &'static str,\n    /// Wire modes whose event unions contain this concrete event.\n    pub modes: &'static [&'static str],\n    /// Event-specific capability constraint; None for observation-only events.\n    pub capability_schema: Option<&'static str>,\n}\n");
    output.push_str("pub const ALL_BOUNDARIES: &[BoundaryDescriptor] = &[\n");
    for (name, modes) in &boundaries {
        let reference = format!("capabilities.schema.json#/$defs/{name}");
        let capability = ir
            .types
            .iter()
            .any(|named| named.source.ends_with(&format!("/{reference}")));
        let capability = if capability {
            format!("Some({reference:?})")
        } else {
            "None".into()
        };
        writeln!(
            output,
            "BoundaryDescriptor {{ name: {name:?}, modes: &{modes:?}, capability_schema: {capability} }},",
            modes = modes.iter().collect::<Vec<_>>()
        )?;
    }
    output.push_str("];\n}\n\n/// Add typed complete-event entrypoints inside the runtime Client implementation.\n/// This macro has no runtime dependency until expanded.\n#[macro_export]\nmacro_rules! ahp_event_boundary_methods {\n    () => {\n");
    let mut methods = BTreeSet::new();
    for name in boundaries.keys() {
        let method = format!("{}_event", snake_identifier(name));
        anyhow::ensure!(
            methods.insert(method.clone()),
            "event boundary method collision: {method}"
        );
        writeln!(
            output,
            "/// Execute the `{name}` boundary with a complete event payload.\npub fn {method}<T: serde::Serialize + serde::de::DeserializeOwned>(&self, event: T) -> $crate::runtime::EventBoundary<'_, T> {{ self.event_for({name:?}, event) }}"
        )?;
    }
    output.push_str("    };\n}\n");
    output.push_str("\n/// Add complete-event entrypoints inside the Hooks facade implementation.\n/// Use the `inventory` arm as an expression to obtain the canonical event names.\n/// This macro has no facade dependency until expanded.\n#[macro_export]\nmacro_rules! ahp_hooks_boundary_methods {\n    (inventory) => {\n        &[");
    for name in boundaries.keys() {
        write!(output, "{name:?},")?;
    }
    output.push_str("] as &'static [&'static str]\n    };\n    () => {\n");
    let mut methods = BTreeSet::new();
    for name in boundaries.keys() {
        // The facade reserves tool_before for its typed ToolCallInput helper.
        let method = if name == "tool.before" {
            "tool_before_event".to_owned()
        } else {
            snake_identifier(name)
        };
        anyhow::ensure!(
            methods.insert(method.clone()),
            "Hooks boundary method collision: {method}"
        );
        writeln!(
            output,
            "/// Execute the `{name}` boundary with a complete event payload.\npub fn {method}<T: serde::Serialize + serde::de::DeserializeOwned>(&self, event: T) -> $crate::hooks::EventBoundary<'_, T> {{ self.event_for({name:?}, event) }}"
        )?;
    }
    output.push_str("    };\n}\n");
    Ok(())
}

fn emit_with_defaults(ir: &Ir, defaults: BTreeMap<(String, String), Value>) -> Result<String> {
    let mut output = String::new();
    writeln!(output, "// Generated by ahp-codegen. DO NOT EDIT.")?;
    writeln!(
        output,
        "// Dependency: serde_json must enable its `arbitrary_precision` feature."
    )?;
    writeln!(
        output,
        "// Generated from AHP schema {}.",
        ir.schema_revision
    )?;
    writeln!(
        output,
        "// The source schema is authoritative if this output conflicts with it.\n"
    )?;
    writeln!(
        output,
        "pub const SCHEMA_REVISION: &str = {:?};",
        ir.schema_revision
    )?;
    writeln!(
        output,
        "pub const PROTOCOL_VERSION: &str = {:?};\n",
        ir.protocol_version
    )?;
    output.push_str(PRELUDE);
    emit_boundaries(ir, &mut output)?;

    let mut context = EmitContext::try_new(ir)?;
    context.defaults = defaults
        .into_iter()
        .map(|((name, field), value)| ((context.type_name(&name), field), value))
        .collect();
    let mut declarations = String::new();
    for named in &ir.types {
        writeln!(declarations, "/// Source: {}", one_line(&named.source))?;
        let name = context.type_name(&named.name);
        declarations.push_str(&context.emit_declaration(&name, &named.shape)?);
    }
    ergonomics::emit(ir, &mut context, &mut declarations)?;
    declarations.push_str(&crate::capability_ergonomics::rust(ir)?);
    output.push_str(&declarations);
    output.push_str(&context.helpers);
    let mut modules: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for named in &ir.types {
        let source = named.source.split('#').next().unwrap_or("");
        let document = source
            .rsplit('/')
            .next()
            .unwrap_or("")
            .trim_end_matches(".schema.json");
        let module = match document {
            "execution-event"
            | "event"
            | "lifecycle-event"
            | "interaction-event"
            | "task-workspace-event"
            | "catalogue-event" => "event",
            "content-item"
            | "content-reference"
            | "content-selection"
            | "content-upload"
            | "content-upload-receipt" => "content",
            "effects" | "effect" | "deny-effect" => "effect",
            "jsonrpc" | "json-rpc" | "json-rpc-message" => "transport",
            other => other,
        };
        modules
            .entry(snake_identifier(module))
            .or_default()
            .insert(context.type_name(&named.name));
        if named.name.contains("Subscription") {
            modules
                .entry("subscription".into())
                .or_default()
                .insert(context.type_name(&named.name));
        }
        if named.name.contains("Transport") {
            modules
                .entry("transport".into())
                .or_default()
                .insert(context.type_name(&named.name));
        }
        if named.name.starts_with("Capabilities")
            || named.name.starts_with("Initialize")
            || named.name.ends_with("Request")
            || named.name.ends_with("Response")
            || named.name.ends_with("Notification")
        {
            modules
                .entry("client".into())
                .or_default()
                .insert(context.type_name(&named.name));
        }
    }
    for names in modules.values_mut() {
        loop {
            let previous = names.len();
            for name in names.clone() {
                if let Some(helpers) = context.helper_names.get(&name) {
                    names.extend(helpers.iter().cloned());
                }
            }
            if names.len() == previous {
                break;
            }
        }
    }
    for (module, names) in modules {
        writeln!(
            output,
            "/// Models grouped by protocol domain.\npub mod {module} {{\n    pub use super::{{{}}};\n}}\n",
            names.into_iter().collect::<Vec<_>>().join(", ")
        )?;
    }

    let schemas = ir
        .types
        .iter()
        .map(|named| (named.name.as_str(), &named.shape))
        .collect::<BTreeMap<_, _>>();
    let schemas_json = serde_json::to_string(&schemas)?;
    writeln!(output, "const SCHEMAS_JSON: &str = {:?};\n", schemas_json)?;
    output.push_str(RUNTIME);

    let function_names = root_function_names(ir);
    for root in &ir.roots {
        let rust_name = context.type_name(&root.name);
        let function_name = &function_names[&root.name];
        writeln!(
            output,
            "/// Parse and structurally check a `{rust_name}` JSON document.\npub fn parse_{function_name}(input: &str) -> ParseResult<{rust_name}> {{\n    parse_root({:?}, input)\n}}",
            root.name
        )?;
        writeln!(
            output,
            "/// Structurally check an already-decoded `{rust_name}` JSON value.\npub fn parse_{function_name}_value(input: JsonValue) -> ParseResult<{rust_name}> {{\n    parse_root_value({:?}, input)\n}}",
            root.name
        )?;
        writeln!(
            output,
            "/// Encode a `{rust_name}` without applying canonical normalization.\npub fn encode_{function_name}(value: &{rust_name}) -> Result<String, serde_json::Error> {{\n    serde_json::to_string(value)\n}}\n"
        )?;
    }
    Ok(output)
}

/// Validate the original descriptor, never the ergonomic public projection.
fn validated_declaration(name: &str, shape: &Shape, declaration: String) -> Result<String> {
    // Primitive intersections and `never` cannot be aliases: aliases cannot
    // own Deserialize, and their primitive projection omits source predicates.
    let declaration = if matches!(shape, Shape::Intersection { .. } | Shape::Never)
        && declaration.starts_with("pub type ")
    {
        let target = declaration
            .split_once(" = ")
            .expect("alias target")
            .1
            .trim()
            .trim_end_matches(';');
        format!(
            "#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]\n#[serde(transparent)]\npub struct {name}(pub {target});\n\nimpl {name} {{ pub fn new(value: impl Into<{target}>) -> Self {{ Self(value.into()) }} }}\n"
        )
    } else {
        declaration
    };
    let descriptor = serde_json::to_string(shape)?;
    let check = format!("let _validated = validate_decode::<D::Error>(&value, {descriptor:?})?;");
    if declaration.contains("impl<'de> Deserialize<'de>") {
        return Ok(declaration.replacen(
            "let value = JsonValue::deserialize(deserializer)?;",
            &format!("let value = JsonValue::deserialize(deserializer)?;\n        {check}"),
            1,
        ));
    }
    if !declaration.starts_with("#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]") {
        return Ok(declaration);
    }
    let start = declaration
        .find("pub struct ")
        .or_else(|| declaration.find("pub enum "))
        .expect("derived model declaration");
    let tail = &declaration[start..];
    let end = if tail.starts_with(&format!("pub struct {name}(")) {
        start + tail.find(";\n").expect("tuple declaration end") + 2
    } else {
        start + tail.find("\n}\n").expect("model declaration end") + 3
    };
    let remote = declaration[..end]
        .replacen(
            "#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]",
            &format!("// Mirror the public representation exactly; do not rename arms or remove recursive indirection.\n#[allow(clippy::vec_box, clippy::enum_variant_names)]\n#[derive(Deserialize)]\n#[serde(remote = {name:?})]"),
            1,
        )
        .replacen(&format!("pub struct {name}"), "struct Hydration", 1)
        .replacen(&format!("pub enum {name}"), "enum Hydration", 1);
    let mut output = declaration.replacen("Serialize, Deserialize", "Serialize", 1);
    writeln!(
        output,
        "impl<'de> Deserialize<'de> for {name} {{\n    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {{\n        let value = JsonValue::deserialize(deserializer)?;\n        {check}\n        {remote}\n        Hydration::deserialize(value).map_err(<D::Error as serde::de::Error>::custom)\n    }}\n}}"
    )?;
    Ok(output)
}

struct EmitContext<'a> {
    named_shapes: BTreeMap<&'a str, &'a Shape>,
    type_names: BTreeMap<String, String>,
    used_type_names: BTreeSet<String>,
    helpers: String,
    helper_names: BTreeMap<String, BTreeSet<String>>,
    ergonomic_fields: BTreeMap<String, Vec<(String, String)>>,
    ergonomic_arms: BTreeMap<String, Vec<(String, String)>>,
    defaults: BTreeMap<(String, String), Value>,
}

impl<'a> EmitContext<'a> {
    #[cfg(test)]
    fn new(ir: &'a Ir) -> Self {
        Self::try_new(ir).unwrap()
    }

    fn try_new(ir: &'a Ir) -> Result<Self> {
        let mut used_type_names = RESERVED_TYPE_NAMES
            .iter()
            .map(|name| (*name).to_owned())
            .collect::<BTreeSet<_>>();
        let mut type_names = BTreeMap::new();
        let mut originals = ir
            .types
            .iter()
            .map(|named| named.name.as_str())
            .collect::<Vec<_>>();
        originals.sort_unstable();
        for original in originals {
            type_names.insert(
                original.to_owned(),
                unique_type_identifier(original, &mut used_type_names)?,
            );
        }
        Ok(Self {
            named_shapes: ir
                .types
                .iter()
                .map(|named| (named.name.as_str(), &named.shape))
                .collect(),
            type_names,
            used_type_names,
            helpers: String::new(),
            helper_names: BTreeMap::new(),
            ergonomic_fields: BTreeMap::new(),
            ergonomic_arms: BTreeMap::new(),
            defaults: BTreeMap::new(),
        })
    }

    fn type_name(&self, original: &str) -> String {
        self.type_names
            .get(original)
            .cloned()
            .unwrap_or_else(|| type_identifier(original))
    }

    fn emit_declaration(&mut self, name: &str, shape: &Shape) -> Result<String> {
        let declaration = self.emit_unvalidated_declaration(name, shape)?;
        validated_declaration(name, shape, declaration)
    }

    fn emit_unvalidated_declaration(&mut self, name: &str, shape: &Shape) -> Result<String> {
        if matches!(shape, Shape::Intersection { .. }) {
            if let Some(value) = self.fixed_value(shape, &mut BTreeSet::new()).cloned() {
                return self.emit_literal(name, &value);
            }
        }
        match shape {
            Shape::Object { properties, .. } => self.emit_struct(name, properties),
            Shape::Intersection { .. } => {
                let mut visiting = BTreeSet::new();
                if let Some(properties) =
                    collect_object_properties(shape, &self.named_shapes, &mut visiting)
                {
                    self.emit_struct(name, &properties)
                } else if let Some(projected) = project_intersection(shape, &self.named_shapes) {
                    self.emit_unvalidated_declaration(name, &projected)
                } else {
                    Ok(format!(
                        "#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]\n#[serde(transparent)]\npub struct {name}(pub JsonValue);\n\nimpl {name} {{ pub fn new(value: impl Into<JsonValue>) -> Self {{ Self(value.into()) }} }}\n\n"
                    ))
                }
            }
            Shape::Union {
                variants,
                discriminator,
                ..
            } => self.emit_union(name, variants, discriminator.as_deref()),
            Shape::Literal { value } => self.emit_literal(name, value),
            Shape::Enum {
                values,
                open_strings,
            } => self.emit_enum(name, values, *open_strings),
            Shape::Array { items } => {
                let item = self.render_type(items, &format!("{name} item"))?;
                Ok(format!(
                    "#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]\n#[serde(transparent)]\npub struct {name}(pub Vec<{item}>);\n\nimpl {name} {{ pub fn new(value: impl Into<Vec<{item}>>) -> Self {{ Self(value.into()) }} }}\n\nimpl From<Vec<{item}>> for {name} {{ fn from(value: Vec<{item}>) -> Self {{ Self::new(value) }} }}\n\nimpl Deref for {name} {{ type Target = [{item}]; fn deref(&self) -> &Self::Target {{ &self.0 }} }}\n\n"
                ))
            }
            Shape::Ref { name: target } => {
                let target = self.type_name(target);
                let default = if self.is_literal(shape, &mut BTreeSet::new()) {
                    format!(
                        "impl Default for {name} {{ fn default() -> Self {{ Self(Box::default()) }} }}\n"
                    )
                } else {
                    String::new()
                };
                Ok(format!(
                    "#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]\n#[serde(transparent)]\npub struct {name}(pub Box<{target}>);\n\nimpl {name} {{ pub fn new(value: impl Into<Box<{target}>>) -> Self {{ Self(value.into()) }} }}\n\nimpl From<{target}> for {name} {{ fn from(value: {target}) -> Self {{ Self::new(value) }} }}\n{default}\n"
                ))
            }
            shape => Ok(format!(
                "pub type {name} = {};\n\n",
                self.render_type(shape, name)?
            )),
        }
    }

    fn emit_struct(&mut self, name: &str, properties: &[Property]) -> Result<String> {
        let mut output = String::new();
        writeln!(
            output,
            "#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]\npub struct {name} {{"
        )?;
        let mut used = BTreeSet::new();
        let mut fields = Vec::new();
        for property in properties {
            let field = unique_field_identifier(&property.wire_name, &mut used);
            let ty =
                self.render_type(&property.shape, &format!("{name} {}", property.wire_name))?;
            fields.push((field.clone(), ty.clone(), property));
            writeln!(output, "    #[serde(rename = {:?})]", property.wire_name)?;
            if property.required {
                writeln!(output, "    pub {field}: {ty},")?;
            } else {
                writeln!(
                    output,
                    "    #[serde(default, skip_serializing_if = \"Presence::is_missing\")]\n    pub {field}: Presence<{ty}>,"
                )?;
            }
        }
        self.ergonomic_fields.insert(
            name.to_owned(),
            fields
                .iter()
                .map(|(_, ty, p)| (p.wire_name.clone(), ty.clone()))
                .collect(),
        );
        let extra = unique_field_identifier("additional_properties", &mut used);
        writeln!(
            output,
            "    /// Members not known to this schema revision.\n    #[serde(flatten)]\n    pub {extra}: BTreeMap<String, JsonValue>,\n}}\n"
        )?;
        let mut arguments = Vec::new();
        let mut initializers = Vec::new();
        let mut builders = String::new();
        for (field, ty, property) in &fields {
            let default = self
                .defaults
                .get(&(name.to_owned(), property.wire_name.clone()));
            let literal = self.is_literal(&property.shape, &mut BTreeSet::new());
            let value = if let Some(default) = default {
                let json = serde_json::to_string(default)?;
                format!(
                    "serde_json::from_str::<{ty}>({json:?}).expect(\"schema default matches generated type\")"
                )
            } else if literal {
                "Default::default()".to_owned()
            } else if property.required {
                arguments.push(format!("{field}: impl Into<{ty}>"));
                format!("{field}.into()")
            } else {
                "Presence::Missing".to_owned()
            };
            let value = if !property.required && (default.is_some() || literal) {
                format!("Presence::Present({value})")
            } else {
                value
            };
            initializers.push(format!("            {field}: {value},"));
            if !literal {
                let assignment = if property.required {
                    "value.into()".to_owned()
                } else {
                    "Presence::Present(value.into())".to_owned()
                };
                writeln!(
                    builders,
                    "    pub fn with_{field}(mut self, value: impl Into<{ty}>) -> Self {{\n        self.{field} = {assignment};\n        self\n    }}"
                )?;
            }
        }
        // Required schema members deliberately remain explicit constructor arguments.
        let constructor_lint = if arguments.len() > 7 {
            "    #[allow(clippy::too_many_arguments)]\n"
        } else {
            ""
        };
        writeln!(
            output,
            "impl {name} {{\n    /// Construct a model; schema literals and defaults are supplied automatically.\n{constructor_lint}    pub fn new({}) -> Self {{\n        Self {{\n{}\n            {extra}: BTreeMap::new(),\n        }}\n    }}\n{builders}}}\n",
            arguments.join(", "),
            initializers.join("\n")
        )?;
        if arguments.is_empty() {
            writeln!(
                output,
                "impl Default for {name} {{\n    fn default() -> Self {{ Self::new() }}\n}}\n"
            )?;
        }
        Ok(output)
    }

    fn is_literal(&self, shape: &Shape, visiting: &mut BTreeSet<String>) -> bool {
        self.fixed_value(shape, visiting).is_some()
    }

    fn fixed_value<'s>(
        &'s self,
        shape: &'s Shape,
        visiting: &mut BTreeSet<String>,
    ) -> Option<&'s Value> {
        match shape {
            Shape::Literal { value } => Some(value),
            Shape::Intersection { variants } => variants
                .iter()
                .find_map(|shape| self.fixed_value(shape, visiting)),
            Shape::Ref { name } if visiting.insert(name.clone()) => {
                let value = self
                    .named_shapes
                    .get(name.as_str())
                    .and_then(|shape| self.fixed_value(shape, visiting));
                visiting.remove(name);
                value
            }
            _ => None,
        }
    }

    // Rust aliases are the same type for coherence, even when schema names differ.
    fn type_identity(&self, ty: &str) -> String {
        if let Some(inner) = ty.strip_prefix("Box<").and_then(|ty| ty.strip_suffix('>')) {
            return format!("Box<{}>", self.type_identity(inner));
        }
        for (original, rust_name) in &self.type_names {
            if rust_name != ty {
                continue;
            }
            return match self.named_shapes.get(original.as_str()) {
                Some(Shape::Any | Shape::Never) => "JsonValue".into(),
                Some(Shape::Null) => "()".into(),
                Some(Shape::Boolean) => "bool".into(),
                Some(Shape::Integer) => "Integer".into(),
                Some(Shape::Number) => "JsonNumber".into(),
                Some(Shape::String) => "String".into(),
                _ => ty.to_owned(),
            };
        }
        ty.to_owned()
    }

    fn render_type(&mut self, shape: &Shape, hint: &str) -> Result<String> {
        if let Some(reference) = shape.constrained_reference() {
            return self.render_type(reference, hint);
        }
        Ok(match shape {
            Shape::Any | Shape::Never => "JsonValue".into(),
            Shape::Null => "()".into(),
            Shape::Boolean => "bool".into(),
            Shape::Integer => "Integer".into(),
            Shape::Number => "JsonNumber".into(),
            Shape::String => "String".into(),
            Shape::Array { items } => {
                format!("Vec<{}>", self.render_type(items, &format!("{hint} item"))?)
            }
            Shape::Ref { name } => format!("Box<{}>", self.type_name(name)),
            Shape::Literal { .. }
            | Shape::Enum { .. }
            | Shape::Object { .. }
            | Shape::Union { .. }
            | Shape::Intersection { .. } => self.ensure_helper(hint, shape)?,
        })
    }

    fn ensure_helper(&mut self, hint: &str, shape: &Shape) -> Result<String> {
        let name = unique_type_identifier(hint, &mut self.used_type_names)?;
        let declaration = self.emit_declaration(&name, shape)?;
        writeln!(self.helpers, "/// Inline schema model.")?;
        self.helpers.push_str(&declaration);
        self.helper_names
            .entry(hint.split(' ').next().unwrap_or(hint).to_owned())
            .or_default()
            .insert(name.clone());
        Ok(name)
    }

    fn emit_literal(&self, name: &str, value: &Value) -> Result<String> {
        let encoded = serde_json::to_string(value)?;
        Ok(format!(
            "#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]\npub struct {name};\n\nimpl {name} {{ pub fn new() -> Self {{ Self }} }}\n\nimpl Serialize for {name} {{\n    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {{\n        let value: JsonValue = serde_json::from_str({encoded:?}).expect(\"generated literal is valid JSON\");\n        value.serialize(serializer)\n    }}\n}}\n\nimpl<'de> Deserialize<'de> for {name} {{\n    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {{\n        let value = JsonValue::deserialize(deserializer)?;\n        let expected: JsonValue = serde_json::from_str({encoded:?}).expect(\"generated literal is valid JSON\");\n        if same_json(&value, &expected) {{ Ok(Self) }} else {{ Err(<D::Error as serde::de::Error>::custom(format!(\"expected {{expected}}\"))) }}\n    }}\n}}\n\n"
        ))
    }

    fn emit_enum(&self, name: &str, values: &[Value], open_strings: bool) -> Result<String> {
        let variants = super::naming::try_literal_names(values)?
            .into_iter()
            .zip(values)
            .collect::<Vec<_>>();
        let mut output = String::new();
        writeln!(
            output,
            "#[derive(Debug, Clone, PartialEq, Eq)]\npub enum {name} {{"
        )?;
        for (variant, _) in &variants {
            writeln!(output, "    {variant},")?;
        }
        if open_strings {
            writeln!(output, "    Unknown(String),")?;
        }
        writeln!(output, "}}\n")?;
        if values.iter().all(Value::is_string) {
            writeln!(
                output,
                "impl {name} {{ pub fn as_str(&self) -> &str {{ match self {{"
            )?;
            for (variant, value) in &variants {
                writeln!(output, "Self::{variant} => {:?},", value.as_str().unwrap())?;
            }
            if open_strings {
                writeln!(output, "Self::Unknown(value) => value.as_str(),")?;
            }
            writeln!(output, "}} }} }}")?;
        }
        writeln!(
            output,
            "impl Serialize for {name} {{\n    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {{\n        match self {{"
        )?;
        for (variant, value) in &variants {
            let encoded = serde_json::to_string(value)?;
            writeln!(
                output,
                "            Self::{variant} => {{ let value: JsonValue = serde_json::from_str({encoded:?}).expect(\"generated enum value is valid JSON\"); value.serialize(serializer) }},"
            )?;
        }
        if open_strings {
            writeln!(
                output,
                "            Self::Unknown(value) => value.serialize(serializer),"
            )?;
        }
        writeln!(output, "        }}\n    }}\n}}\n")?;
        writeln!(
            output,
            "impl<'de> Deserialize<'de> for {name} {{\n    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {{\n        let value = JsonValue::deserialize(deserializer)?;"
        )?;
        for (variant, expected) in &variants {
            let encoded = serde_json::to_string(expected)?;
            writeln!(
                output,
                "        if same_json(&value, &serde_json::from_str({encoded:?}).expect(\"generated enum value is valid JSON\")) {{ return Ok(Self::{variant}); }}"
            )?;
        }
        if open_strings {
            writeln!(
                output,
                "        if let Some(value) = value.as_str() {{ return Ok(Self::Unknown(value.to_owned())); }}"
            )?;
        }
        writeln!(
            output,
            "        Err(<D::Error as serde::de::Error>::custom(format!(\"unknown value for {name}: {{value}}\")))\n    }}\n}}\n"
        )?;
        Ok(output)
    }

    /// Resolve tagged references only for naming. Payload types and codec shapes
    /// remain the original alternatives, including their reference boundaries.
    #[cfg(test)]
    fn union_names(&self, variants: &[Shape]) -> Vec<String> {
        self.try_union_names(variants).unwrap()
    }

    fn try_union_names(&self, variants: &[Shape]) -> Result<Vec<String>> {
        fn resolve(
            context: &EmitContext<'_>,
            shape: &Shape,
            visiting: &mut BTreeSet<String>,
        ) -> Shape {
            match shape {
                Shape::Ref { name } if visiting.insert(name.clone()) => {
                    let resolved = context
                        .named_shapes
                        .get(name.as_str())
                        .map(|shape| resolve(context, shape, visiting))
                        .unwrap_or_else(|| shape.clone());
                    visiting.remove(name);
                    resolved
                }
                Shape::Intersection { variants } => Shape::Intersection {
                    variants: variants
                        .iter()
                        .map(|shape| resolve(context, shape, visiting))
                        .collect(),
                },
                Shape::Object {
                    properties,
                    forbidden_property_sets,
                    additional,
                } => Shape::Object {
                    properties: properties
                        .iter()
                        .map(|property| {
                            let mut property = property.clone();
                            if let Some(value) =
                                context.fixed_value(&property.shape, &mut BTreeSet::new())
                            {
                                property.shape = Shape::Literal {
                                    value: value.clone(),
                                };
                            }
                            property
                        })
                        .collect(),
                    forbidden_property_sets: forbidden_property_sets.clone(),
                    additional: additional.clone(),
                },
                _ => shape.clone(),
            }
        }
        let resolved = variants
            .iter()
            .map(|shape| resolve(self, shape, &mut BTreeSet::new()))
            .collect::<Vec<_>>();
        let mut counts = BTreeMap::new();
        for shape in &resolved {
            if let Some(tag) = super::naming::tagged_label(shape) {
                *counts.entry(tag).or_insert(0usize) += 1;
            }
        }
        let naming_shapes = variants
            .iter()
            .zip(resolved)
            .map(
                |(original, resolved)| match super::naming::tagged_label(&resolved) {
                    Some(tag) if counts[&tag] == 1 => resolved,
                    _ => original.clone(),
                },
            )
            .collect::<Vec<_>>();
        super::naming::try_union_names(&naming_shapes)
    }

    fn emit_union(
        &mut self,
        name: &str,
        variants: &[Shape],
        discriminator: Option<&str>,
    ) -> Result<String> {
        let names = self
            .try_union_names(variants)
            .with_context(|| format!("naming union {name}"))?;
        let projection = super::naming::projected_union_with_names(variants, &names)
            .with_context(|| format!("projecting union {name}"))?;
        let mut rendered = Vec::new();
        for arm in projection {
            let index = arm.source_indices[0];
            let shape = &variants[index];
            let variant = arm.name;
            if matches!(shape, Shape::Never) {
                continue;
            }
            let discriminator_value = discriminator.and_then(|property| {
                shape_discriminator_value(shape, property, &self.named_shapes, &mut BTreeSet::new())
                    .map(str::to_owned)
            });
            let ty = self.render_type(shape, &format!("{name} {variant}"))?;
            rendered.push((variant, ty, discriminator_value));
        }
        let mut output = String::new();
        if discriminator.is_some() {
            writeln!(
                output,
                "#[derive(Debug, Clone, PartialEq, Serialize)]\n#[serde(untagged)]\npub enum {name} {{"
            )?;
        } else {
            writeln!(
                output,
                "#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]\n#[serde(untagged)]\npub enum {name} {{"
            )?;
        }
        self.ergonomic_arms.insert(
            name.to_owned(),
            rendered
                .iter()
                .map(|(variant, ty, _)| (variant.clone(), ty.clone()))
                .collect(),
        );
        for (variant, ty, _) in &rendered {
            writeln!(output, "    {variant}({ty}),")?;
        }
        if let Some(discriminator) = discriminator {
            writeln!(
                output,
                "    /// Raw value for a forward-compatible discriminator variant.\n    Unknown(JsonValue),\n}}\n"
            )?;
            writeln!(
                output,
                "impl<'de> Deserialize<'de> for {name} {{\n    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {{\n        let value = JsonValue::deserialize(deserializer)?;\n        let actual = value.as_object()\n            .and_then(|object| object.get({discriminator:?}))\n            .and_then(JsonValue::as_str)\n            .map(str::to_owned)\n            .ok_or_else(|| <D::Error as serde::de::Error>::custom(\"expected string discriminator {discriminator}\"))?;\n        match actual.as_str() {{"
            )?;
            for (variant, ty, value) in &rendered {
                if let Some(value) = value {
                    writeln!(
                        output,
                        "            {value:?} => serde_json::from_value::<{ty}>(value)\n                .map(Self::{variant})\n                .map_err(<D::Error as serde::de::Error>::custom),"
                    )?;
                }
            }
            writeln!(
                output,
                "            _ => Ok(Self::Unknown(value)),\n        }}\n    }}\n}}\n"
            )?;
        } else {
            writeln!(output, "}}\n")?;
        }
        let mut conversions = Vec::new();
        for (variant, ty, _) in &rendered {
            conversions.push((ty.clone(), variant, "value"));
            if let Some(inner) = ty.strip_prefix("Box<").and_then(|ty| ty.strip_suffix('>')) {
                conversions.push((inner.to_owned(), variant, "Box::new(value)"));
            }
        }
        for (ty, variant, value) in &conversions {
            let identity = self.type_identity(ty);
            if identity != name
                && conversions
                    .iter()
                    .filter(|(other, _, _)| self.type_identity(other) == identity)
                    .count()
                    == 1
            {
                writeln!(
                    output,
                    "impl From<{ty}> for {name} {{\n    fn from(value: {ty}) -> Self {{ Self::{variant}({value}) }}\n}}\n"
                )?;
            }
        }
        Ok(output)
    }
}

fn root_function_names(ir: &Ir) -> BTreeMap<String, String> {
    let mut roots = ir
        .roots
        .iter()
        .map(|root| root.name.as_str())
        .collect::<Vec<_>>();
    roots.sort_unstable();
    let mut used = BTreeSet::from(["parse_root".to_owned(), "parse_root_value".to_owned()]);
    let mut result = BTreeMap::new();
    for root in roots {
        let base = snake_identifier(root);
        let mut candidate = base.clone();
        let mut suffix = 2;
        loop {
            let functions = [
                format!("parse_{candidate}"),
                format!("parse_{candidate}_value"),
                format!("encode_{candidate}"),
            ];
            if functions.iter().all(|name| !used.contains(name)) {
                used.extend(functions);
                result.insert(root.to_owned(), candidate);
                break;
            }
            candidate = format!("{base}_{suffix}");
            suffix += 1;
        }
    }
    result
}

/// Select a lossless public representation, never a replacement validator.
/// A value satisfying allOf satisfies each conjunct. Arrays and scalar values
/// can therefore use a typed conjunct while retaining all constraints in IR.
/// Object unions instead distribute allOf so fields from every sibling remain
/// typed in each branch (not hidden in an extension map).
fn project_intersection(shape: &Shape, named: &BTreeMap<&str, &Shape>) -> Option<Shape> {
    fn expand(
        shape: &Shape,
        named: &BTreeMap<&str, &Shape>,
        visiting: &mut BTreeSet<String>,
        out: &mut Vec<Shape>,
    ) {
        match shape {
            Shape::Ref { name } if visiting.insert(name.clone()) => {
                if let Some(target) = named.get(name.as_str()) {
                    expand(target, named, visiting, out);
                } else {
                    out.push(shape.clone());
                }
                visiting.remove(name);
            }
            Shape::Intersection { variants } => {
                for variant in variants {
                    expand(variant, named, visiting, out);
                }
            }
            Shape::Any => {}
            other => out.push(other.clone()),
        }
    }
    let mut conjuncts = Vec::new();
    expand(shape, named, &mut BTreeSet::new(), &mut conjuncts);
    if let Some(typed) = conjuncts.iter().find(|shape| {
        matches!(
            shape,
            Shape::Array { .. }
                | Shape::String
                | Shape::Boolean
                | Shape::Integer
                | Shape::Number
                | Shape::Enum { .. }
                | Shape::Literal { .. }
        )
    }) {
        return Some(typed.clone());
    }
    for (index, conjunct) in conjuncts.iter().enumerate() {
        if let Shape::Union {
            variants,
            mode,
            discriminator,
        } = conjunct
        {
            let variants = variants
                .iter()
                .map(|variant| {
                    let mut siblings = conjuncts.clone();
                    siblings[index] = variant.clone();
                    Shape::Intersection { variants: siblings }
                })
                .collect();
            return Some(Shape::Union {
                variants,
                mode: mode.clone(),
                discriminator: discriminator.clone(),
            });
        }
    }
    None
}

fn collect_object_properties(
    shape: &Shape,
    named_shapes: &BTreeMap<&str, &Shape>,
    visiting: &mut BTreeSet<String>,
) -> Option<Vec<Property>> {
    match shape {
        Shape::Object { properties, .. } => Some(properties.clone()),
        Shape::Intersection { variants } => {
            let mut merged: Vec<Property> = Vec::new();
            let mut object_constrained = false;
            for variant in variants {
                // An unconstrained allOf member does not erase typed siblings.
                if matches!(variant, Shape::Any) {
                    continue;
                }
                object_constrained = true;
                for property in collect_object_properties(variant, named_shapes, visiting)? {
                    if let Some(existing) = merged
                        .iter_mut()
                        .find(|existing| existing.wire_name == property.wire_name)
                    {
                        existing.required |= property.required;
                        existing.shape = intersect_shapes(
                            std::mem::replace(&mut existing.shape, Shape::Any),
                            property.shape,
                        );
                    } else {
                        merged.push(property);
                    }
                }
            }
            object_constrained.then_some(merged)
        }
        Shape::Union { variants, .. } => {
            // Projection only: predicate branches describe presence alternatives,
            // not a new value type. Keep conditional fields optional unless every
            // branch requires them. The ORIGINAL schema graph below remains the
            // authority for anyOf/oneOf counts and forbidden combinations.
            let branches = variants
                .iter()
                .map(|variant| collect_object_properties(variant, named_shapes, visiting))
                .collect::<Option<Vec<_>>>()?;
            if branches.is_empty() {
                return None;
            }
            // A property omitted by an open predicate branch is unconstrained
            // in that branch. Its overall projection is Any, which lets typed
            // allOf siblings supply its actual representation (e.g. booleans).
            for property in branches.iter().flatten() {
                if !matches!(property.shape, Shape::Any)
                    && !variants.iter().any(|variant| matches!(variant,
                        Shape::Object { properties, additional: crate::model::AdditionalProperties::Allowed, .. }
                        if !properties.iter().any(|candidate| candidate.wire_name == property.wire_name)))
                {
                    return None;
                }
            }
            let mut merged = BTreeMap::<String, Property>::new();
            for property in branches.iter().flatten() {
                merged.entry(property.wire_name.clone()).or_insert_with(|| {
                    let mut projected = property.clone();
                    projected.shape = Shape::Any;
                    projected.required = branches.iter().all(|branch| {
                        branch.iter().any(|candidate| {
                            candidate.wire_name == property.wire_name && candidate.required
                        })
                    });
                    projected
                });
            }
            Some(merged.into_values().collect())
        }
        Shape::Ref { name } => {
            if !visiting.insert(name.clone()) {
                return None;
            }
            let result = named_shapes
                .get(name.as_str())
                .and_then(|shape| collect_object_properties(shape, named_shapes, visiting));
            visiting.remove(name);
            result
        }
        _ => None,
    }
}

fn intersect_shapes(left: Shape, right: Shape) -> Shape {
    match (left, right) {
        (Shape::Any, shape) | (shape, Shape::Any) => shape,
        (Shape::Never, _) | (_, Shape::Never) => Shape::Never,
        (left, right) => {
            let mut variants = Vec::new();
            for shape in [left, right] {
                match shape {
                    Shape::Intersection { variants: nested } => variants.extend(nested),
                    shape => variants.push(shape),
                }
            }
            variants.sort_by_cached_key(|shape| {
                serde_json::to_string(shape).expect("shape serializes")
            });
            variants.dedup_by(|left, right| {
                serde_json::to_string(left).expect("shape serializes")
                    == serde_json::to_string(right).expect("shape serializes")
            });
            if variants.len() == 1 {
                variants.pop().expect("one intersection shape")
            } else {
                Shape::Intersection { variants }
            }
        }
    }
}

fn shape_discriminator_value<'a>(
    shape: &'a Shape,
    property: &str,
    named_shapes: &BTreeMap<&str, &'a Shape>,
    visiting: &mut BTreeSet<String>,
) -> Option<&'a str> {
    match shape {
        Shape::Ref { name } => {
            if !visiting.insert(name.clone()) {
                return None;
            }
            let result = named_shapes.get(name.as_str()).and_then(|shape| {
                shape_discriminator_value(shape, property, named_shapes, visiting)
            });
            visiting.remove(name);
            result
        }
        Shape::Intersection { variants } => variants
            .iter()
            .find_map(|shape| shape_discriminator_value(shape, property, named_shapes, visiting)),
        Shape::Object { properties, .. } => properties.iter().find_map(|candidate| {
            if candidate.wire_name == property {
                match &candidate.shape {
                    Shape::Literal { value } => value.as_str(),
                    _ => None,
                }
            } else {
                None
            }
        }),
        _ => None,
    }
}

fn type_identifier(value: &str) -> String {
    let words = identifier_words(value);
    let mut result = words
        .iter()
        .map(|word| {
            let mut chars = word.chars();
            match chars.next() {
                Some(first) => first.to_uppercase().chain(chars).collect::<String>(),
                None => String::new(),
            }
        })
        .collect::<String>();
    if result.is_empty() {
        result.push_str("GeneratedType");
    }
    if result.as_bytes()[0].is_ascii_digit() {
        result.insert(0, '_');
    }
    if is_keyword(&result) {
        result.push('_');
    }
    result
}

fn snake_identifier(value: &str) -> String {
    let mut result = identifier_words(value).join("_");
    if result.is_empty() {
        result.push_str("field");
    }
    if result.as_bytes()[0].is_ascii_digit() {
        result.insert(0, '_');
    }
    if is_keyword(&result) {
        result.push('_');
    }
    result
}

fn unique_field_identifier(value: &str, used: &mut BTreeSet<String>) -> String {
    let base = snake_identifier(value);
    let mut candidate = base.clone();
    let mut suffix = 2;
    while !used.insert(candidate.clone()) {
        candidate = format!("{base}_{suffix}");
        suffix += 1;
    }
    candidate
}

fn unique_type_identifier(value: &str, used: &mut BTreeSet<String>) -> Result<String> {
    anyhow::ensure!(
        !identifier_words(value).is_empty(),
        "public model {value:?} has no semantic identifier; provide a canonical schema name"
    );
    let name = type_identifier(value);
    anyhow::ensure!(
        name.len() <= 96,
        "public model name {name:?} is too long; provide a concise canonical schema name"
    );
    anyhow::ensure!(
        used.insert(name.clone()),
        "public Rust model name {name:?} from {value:?} is reserved or already used; provide a distinct canonical schema name instead of an ordinal alias"
    );
    Ok(name)
}

fn identifier_words(value: &str) -> Vec<String> {
    let chars = value.chars().collect::<Vec<_>>();
    let mut words = Vec::new();
    let mut current = String::new();
    for (index, character) in chars.iter().copied().enumerate() {
        if !character.is_ascii_alphanumeric() {
            if !current.is_empty() {
                words.push(current.to_ascii_lowercase());
                current.clear();
            }
            continue;
        }
        let previous = index
            .checked_sub(1)
            .and_then(|item| chars.get(item))
            .copied();
        let next = chars.get(index + 1).copied();
        let boundary = character.is_ascii_uppercase()
            && !current.is_empty()
            && (previous.is_some_and(|item| item.is_ascii_lowercase() || item.is_ascii_digit())
                || next.is_some_and(|item| item.is_ascii_lowercase()));
        if boundary {
            words.push(current.to_ascii_lowercase());
            current.clear();
        }
        current.push(character);
    }
    if !current.is_empty() {
        words.push(current.to_ascii_lowercase());
    }
    words
}

fn is_keyword(value: &str) -> bool {
    matches!(
        value,
        "Self"
            | "abstract"
            | "as"
            | "async"
            | "await"
            | "become"
            | "box"
            | "break"
            | "const"
            | "continue"
            | "crate"
            | "do"
            | "dyn"
            | "else"
            | "enum"
            | "extern"
            | "false"
            | "final"
            | "fn"
            | "for"
            | "gen"
            | "if"
            | "impl"
            | "in"
            | "let"
            | "loop"
            | "macro"
            | "match"
            | "mod"
            | "move"
            | "mut"
            | "override"
            | "priv"
            | "pub"
            | "ref"
            | "return"
            | "self"
            | "static"
            | "struct"
            | "super"
            | "trait"
            | "true"
            | "try"
            | "type"
            | "typeof"
            | "union"
            | "unsafe"
            | "unsized"
            | "use"
            | "virtual"
            | "where"
            | "while"
            | "yield"
    )
}

fn one_line(value: &str) -> String {
    value.replace(['\r', '\n'], " ")
}

const PRELUDE: &str = r#"use std::collections::BTreeMap;
use std::ops::Deref;
use std::sync::OnceLock;

use serde::de::DeserializeOwned;
use serde::{Deserialize, Deserializer, Serialize, Serializer};
pub use serde_json::{Number as JsonNumber, Value as JsonValue};

/// A JSON integer that is exactly usable by every generated SDK. The original
/// JSON number representation is retained, including an integral `1.0`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Integer(JsonNumber);

impl Integer {
    pub fn new(value: JsonNumber) -> Option<Self> {
        is_safe_integer_number(&value).then_some(Self(value))
    }

    pub fn from_i64(value: i64) -> Option<Self> {
        Self::new(JsonNumber::from(value))
    }

    pub fn as_number(&self) -> &JsonNumber {
        &self.0
    }

    pub fn into_number(self) -> JsonNumber {
        self.0
    }
}

impl Serialize for Integer {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        self.0.serialize(serializer)
    }
}

impl<'de> Deserialize<'de> for Integer {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let value = JsonNumber::deserialize(deserializer)?;
        Self::new(value).ok_or_else(|| {
            <D::Error as serde::de::Error>::custom("expected safely representable integer")
        })
    }
}

/// Presence of an optional object member. Unlike `Option`, this distinguishes
/// an absent member from a present member whose schema accepts JSON null.
#[derive(Debug, Clone, PartialEq, Default)]
pub enum Presence<T> {
    #[default]
    Missing,
    Present(T),
}

impl<T> Presence<T> {
    pub fn is_missing(&self) -> bool {
        matches!(self, Self::Missing)
    }

    pub fn as_ref(&self) -> Presence<&T> {
        match self {
            Self::Missing => Presence::Missing,
            Self::Present(value) => Presence::Present(value),
        }
    }
}

impl<T> Deref for Presence<T> {
    type Target = T;

    fn deref(&self) -> &Self::Target {
        match self {
            Self::Present(value) => value,
            Self::Missing => panic!("attempted to dereference a missing optional member"),
        }
    }
}

impl<T: Serialize> Serialize for Presence<T> {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        match self {
            Self::Present(value) => value.serialize(serializer),
            Self::Missing => serializer.serialize_unit(),
        }
    }
}

impl<'de, T: Deserialize<'de>> Deserialize<'de> for Presence<T> {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        T::deserialize(deserializer).map(Self::Present)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DiagnosticCode {
    InvalidType,
    MissingRequired,
    LiteralMismatch,
    UnknownEnum,
    UnknownVariant,
    InvalidKnownVariant,
    NoUnionMatch,
    AmbiguousUnion,
    InvalidJson,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DiagnosticSeverity {
    Error,
    Warning,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ParseDiagnostic {
    pub path: String,
    pub code: DiagnosticCode,
    pub severity: DiagnosticSeverity,
    pub message: String,
}

#[derive(Debug, Clone, PartialEq)]
pub enum ParseResult<T> {
    Success {
        value: T,
        raw: JsonValue,
        diagnostics: Vec<ParseDiagnostic>,
    },
    Failure {
        raw: Option<JsonValue>,
        diagnostics: Vec<ParseDiagnostic>,
    },
}

impl<T> ParseResult<T> {
    pub fn is_ok(&self) -> bool {
        matches!(self, Self::Success { .. })
    }

    pub fn value(&self) -> Option<&T> {
        match self {
            Self::Success { value, .. } => Some(value),
            Self::Failure { .. } => None,
        }
    }

    pub fn raw(&self) -> Option<&JsonValue> {
        match self {
            Self::Success { raw, .. } => Some(raw),
            Self::Failure { raw, .. } => raw.as_ref(),
        }
    }

    pub fn diagnostics(&self) -> &[ParseDiagnostic] {
        match self {
            Self::Success { diagnostics, .. } | Self::Failure { diagnostics, .. } => diagnostics,
        }
    }

    pub fn into_value(self) -> Option<T> {
        match self {
            Self::Success { value, .. } => Some(value),
            Self::Failure { .. } => None,
        }
    }
}

"#;

const RUNTIME: &str = r#"#[derive(Debug, Deserialize)]
#[serde(tag = "kind", rename_all = "camelCase")]
enum SchemaNode {
    Any,
    Never,
    Null,
    Boolean,
    Integer,
    Number,
    String,
    Literal { value: JsonValue },
    Enum { values: Vec<JsonValue>, open_strings: bool },
    Array { items: Box<SchemaNode> },
    Object {
        properties: Vec<SchemaProperty>,
        forbidden_property_sets: Vec<Vec<String>>,
        #[serde(rename = "additional")]
        _additional: JsonValue,
    },
    Union {
        mode: UnionMode,
        variants: Vec<SchemaNode>,
        discriminator: Option<String>,
    },
    Intersection { variants: Vec<SchemaNode> },
    Ref { name: String },
}

#[derive(Debug, Deserialize)]
struct SchemaProperty {
    wire_name: String,
    required: bool,
    shape: SchemaNode,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
enum UnionMode {
    AnyOf,
    OneOf,
}

fn schemas() -> &'static BTreeMap<String, SchemaNode> {
    static SCHEMAS: OnceLock<BTreeMap<String, SchemaNode>> = OnceLock::new();
    SCHEMAS.get_or_init(|| {
        serde_json::from_str(SCHEMAS_JSON)
            .expect("ahp-codegen emitted an invalid embedded schema table")
    })
}

// Private, synchronous, panic-safe hydration scope. Generated models have no
// consumer callbacks: their fields contain only generated models and JSON values.
thread_local! {
    static VALIDATED_HYDRATION: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
}
struct ValidatedHydration;
impl ValidatedHydration {
    fn enter() -> Self {
        VALIDATED_HYDRATION.with(|depth| depth.set(depth.get() + 1));
        Self
    }
}
impl Drop for ValidatedHydration {
    fn drop(&mut self) {
        VALIDATED_HYDRATION.with(|depth| depth.set(depth.get() - 1));
    }
}
fn validate_decode<E: serde::de::Error>(value: &JsonValue, descriptor: &'static str) -> Result<ValidatedHydration, E> {
    if !VALIDATED_HYDRATION.with(|depth| depth.get() > 0) {
        static DESCRIPTORS: OnceLock<std::sync::Mutex<BTreeMap<&'static str, std::sync::Arc<SchemaNode>>>> = OnceLock::new();
        let schema = {
            let mut cache = DESCRIPTORS.get_or_init(Default::default).lock().expect("descriptor cache lock");
            cache.entry(descriptor).or_insert_with(|| std::sync::Arc::new(serde_json::from_str(descriptor).expect("generated descriptor"))).clone()
        };
        let mut diagnostics = Vec::new();
        check_node(&schema, value, "", &mut diagnostics);
        if has_errors(&diagnostics) {
            return Err(E::custom(format!("structural decode failed: {diagnostics:?}")));
        }
    }
    Ok(ValidatedHydration::enter())
}

fn parse_root<T: DeserializeOwned>(name: &str, input: &str) -> ParseResult<T> {
    match serde_json::from_str(input) {
        Ok(raw) => parse_root_value(name, raw),
        Err(error) => ParseResult::Failure {
            raw: None,
            diagnostics: vec![ParseDiagnostic {
                path: String::new(),
                code: DiagnosticCode::InvalidJson,
                severity: DiagnosticSeverity::Error,
                message: error.to_string(),
            }],
        },
    }
}

fn parse_root_value<T: DeserializeOwned>(name: &str, raw: JsonValue) -> ParseResult<T> {
    let mut diagnostics = Vec::new();
    let schema = schemas()
        .get(name)
        .unwrap_or_else(|| panic!("missing generated schema {name}"));
    check_node(schema, &raw, "", &mut diagnostics);
    if has_errors(&diagnostics) {
        return ParseResult::Failure {
            raw: Some(raw),
            diagnostics,
        };
    }
    let _validated = ValidatedHydration::enter();
    match serde_json::from_value(raw.clone()) {
        Ok(value) => ParseResult::Success {
            value,
            raw,
            diagnostics,
        },
        Err(error) => {
            push_error(
                &mut diagnostics,
                "",
                DiagnosticCode::InvalidType,
                format!("Structurally valid value could not be decoded: {error}"),
            );
            ParseResult::Failure {
                raw: Some(raw),
                diagnostics,
            }
        }
    }
}

fn check_node(
    schema: &SchemaNode,
    value: &JsonValue,
    path: &str,
    diagnostics: &mut Vec<ParseDiagnostic>,
) {
    match schema {
        SchemaNode::Any => {}
        SchemaNode::Never => push_error(
            diagnostics,
            path,
            DiagnosticCode::InvalidType,
            "Value is forbidden",
        ),
        SchemaNode::Null => {
            if !value.is_null() {
                push_error(diagnostics, path, DiagnosticCode::InvalidType, "Expected null");
            }
        }
        SchemaNode::Boolean => {
            if !value.is_boolean() {
                push_error(diagnostics, path, DiagnosticCode::InvalidType, "Expected boolean");
            }
        }
        SchemaNode::Integer => {
            if !value.as_number().is_some_and(is_safe_integer_number) {
                push_error(
                    diagnostics,
                    path,
                    DiagnosticCode::InvalidType,
                    "Expected safely representable integer",
                );
            }
        }
        SchemaNode::Number => {
            if !value.is_number() {
                push_error(diagnostics, path, DiagnosticCode::InvalidType, "Expected finite number");
            }
        }
        SchemaNode::String => {
            if !value.is_string() {
                push_error(diagnostics, path, DiagnosticCode::InvalidType, "Expected string");
            }
        }
        SchemaNode::Literal { value: expected } => {
            if !same_json(value, expected) {
                push_error(
                    diagnostics,
                    path,
                    DiagnosticCode::LiteralMismatch,
                    format!("Expected {expected}"),
                );
            }
        }
        SchemaNode::Enum {
            values,
            open_strings,
        } => {
            if !values.iter().any(|expected| same_json(value, expected)) {
                if *open_strings && value.is_string() {
                    diagnostics.push(ParseDiagnostic {
                        path: path.into(),
                        code: DiagnosticCode::UnknownEnum,
                        severity: DiagnosticSeverity::Warning,
                        message: format!("Unknown enum value {value} was preserved"),
                    });
                } else {
                    push_error(
                        diagnostics,
                        path,
                        DiagnosticCode::InvalidType,
                        "Value does not match the enum's scalar type",
                    );
                }
            }
        }
        SchemaNode::Array { items } => match value.as_array() {
            Some(values) => {
                for (index, item) in values.iter().enumerate() {
                    check_node(items, item, &format!("{path}/{index}"), diagnostics);
                }
            }
            None => push_error(diagnostics, path, DiagnosticCode::InvalidType, "Expected array"),
        },
        SchemaNode::Object {
            properties,
            forbidden_property_sets,
            ..
        } => match value.as_object() {
            Some(object) => {
                for forbidden in forbidden_property_sets {
                    if forbidden.iter().all(|key| object.contains_key(key)) {
                        push_error(
                            diagnostics,
                            path,
                            DiagnosticCode::InvalidType,
                            format!("Forbidden property combination: {}", forbidden.join(", ")),
                        );
                    }
                }
                for property in properties {
                    let property_path = join_path(path, &property.wire_name);
                    match object.get(&property.wire_name) {
                        Some(child) => check_node(
                            &property.shape,
                            child,
                            &property_path,
                            diagnostics,
                        ),
                        None if property.required => push_error(
                            diagnostics,
                            &property_path,
                            DiagnosticCode::MissingRequired,
                            "Required property is absent",
                        ),
                        None => {}
                    }
                }
            }
            None => push_error(diagnostics, path, DiagnosticCode::InvalidType, "Expected object"),
        },
        SchemaNode::Intersection { variants } => {
            for variant in variants {
                check_node(variant, value, path, diagnostics);
            }
        }
        SchemaNode::Ref { name } => check_node(schema_named(name), value, path, diagnostics),
        SchemaNode::Union {
            mode,
            variants,
            discriminator,
        } => check_union(
            *mode,
            variants,
            discriminator.as_deref(),
            value,
            path,
            diagnostics,
        ),
    }
}

fn check_union(
    mode: UnionMode,
    variants: &[SchemaNode],
    discriminator: Option<&str>,
    value: &JsonValue,
    path: &str,
    diagnostics: &mut Vec<ParseDiagnostic>,
) {
    if let Some(discriminator) = discriminator {
        let Some(actual) = value
            .as_object()
            .and_then(|object| object.get(discriminator))
            .and_then(JsonValue::as_str)
        else {
            push_error(
                diagnostics,
                &join_path(path, discriminator),
                DiagnosticCode::InvalidType,
                "Expected string discriminator",
            );
            return;
        };
        let branch = variants.iter().find(|variant| {
            discriminator_value(variant, discriminator).is_some_and(|known| known == actual)
        });
        let Some(branch) = branch else {
            diagnostics.push(ParseDiagnostic {
                path: path.into(),
                code: DiagnosticCode::UnknownVariant,
                severity: DiagnosticSeverity::Warning,
                message: format!("Unknown {discriminator} variant {actual:?} was preserved"),
            });
            return;
        };
        let mut branch_diagnostics = Vec::new();
        check_node(branch, value, path, &mut branch_diagnostics);
        let malformed = has_errors(&branch_diagnostics);
        diagnostics.append(&mut branch_diagnostics);
        if malformed {
            push_error(
                diagnostics,
                path,
                DiagnosticCode::InvalidKnownVariant,
                format!("Known {discriminator} variant is malformed"),
            );
        }
        return;
    }

    let attempts = variants
        .iter()
        .map(|variant| {
            let mut attempt = Vec::new();
            check_node(variant, value, path, &mut attempt);
            attempt
        })
        .collect::<Vec<_>>();
    let matches = attempts
        .iter()
        .filter(|attempt| !has_errors(attempt))
        .collect::<Vec<_>>();
    if matches.is_empty() {
        push_error(
            diagnostics,
            path,
            DiagnosticCode::NoUnionMatch,
            "Value matches no union branch",
        );
    } else if mode == UnionMode::OneOf && matches.len() > 1 {
        push_error(
            diagnostics,
            path,
            DiagnosticCode::AmbiguousUnion,
            "Value matches more than one union branch",
        );
    } else {
        diagnostics.extend(matches[0].iter().cloned());
    }
}

fn discriminator_value<'a>(schema: &'a SchemaNode, property: &str) -> Option<&'a str> {
    match schema {
        SchemaNode::Ref { name } => discriminator_value(schema_named(name), property),
        SchemaNode::Intersection { variants } => variants
            .iter()
            .find_map(|variant| discriminator_value(variant, property)),
        SchemaNode::Object { properties, .. } => properties.iter().find_map(|candidate| {
            if candidate.wire_name == property {
                match &candidate.shape {
                    SchemaNode::Literal { value } => value.as_str(),
                    _ => None,
                }
            } else {
                None
            }
        }),
        _ => None,
    }
}

fn is_safe_integer_number(number: &JsonNumber) -> bool {
    const MAX_SAFE_INTEGER: &str = "9007199254740991";
    let Some((_, digits, scale)) = normalized_number(number) else {
        return false;
    };
    if digits == "0" {
        return true;
    }
    let Ok(scale) = usize::try_from(scale) else {
        return false;
    };
    let Some(length) = digits.len().checked_add(scale) else {
        return false;
    };
    length < MAX_SAFE_INTEGER.len()
        || (length == MAX_SAFE_INTEGER.len()
            && format!("{digits}{}", "0".repeat(scale)).as_str() <= MAX_SAFE_INTEGER)
}

fn same_json(left: &JsonValue, right: &JsonValue) -> bool {
    match (left, right) {
        (JsonValue::Number(left), JsonValue::Number(right)) => same_number(left, right),
        (JsonValue::Array(left), JsonValue::Array(right)) => {
            left.len() == right.len()
                && left.iter().zip(right).all(|(left, right)| same_json(left, right))
        }
        (JsonValue::Object(left), JsonValue::Object(right)) => {
            left.len() == right.len()
                && left.iter().all(|(key, value)| {
                    right.get(key).is_some_and(|right| same_json(value, right))
                })
        }
        _ => left == right,
    }
}

fn same_number(left: &JsonNumber, right: &JsonNumber) -> bool {
    left == right || normalized_number(left) == normalized_number(right)
}

fn normalized_number(number: &JsonNumber) -> Option<(bool, String, i64)> {
    let text = number.to_string();
    let (negative, unsigned) = text
        .strip_prefix('-')
        .map_or((false, text.as_str()), |unsigned| (true, unsigned));
    let (mantissa, exponent) =
        if let Some((mantissa, exponent)) = unsigned.split_once('e').or_else(|| unsigned.split_once('E')) {
            (mantissa, exponent.parse::<i64>().ok()?)
        } else {
            (unsigned, 0)
        };
    let (integer, fraction) = mantissa.split_once('.').unwrap_or((mantissa, ""));
    let mut digits = format!("{integer}{fraction}");
    let mut scale = exponent.checked_sub(i64::try_from(fraction.len()).ok()?)?;
    let first_nonzero = digits.find(|character| character != '0');
    let Some(first_nonzero) = first_nonzero else {
        return Some((false, "0".to_owned(), 0));
    };
    digits.drain(..first_nonzero);
    while digits.ends_with('0') {
        digits.pop();
        scale = scale.checked_add(1)?;
    }
    Some((negative, digits, scale))
}

fn schema_named(name: &str) -> &'static SchemaNode {
    schemas()
        .get(name)
        .unwrap_or_else(|| panic!("missing generated schema {name}"))
}

fn has_errors(diagnostics: &[ParseDiagnostic]) -> bool {
    diagnostics
        .iter()
        .any(|item| item.severity == DiagnosticSeverity::Error)
}

fn join_path(path: &str, key: &str) -> String {
    format!(
        "{path}/{}",
        key.replace('~', "~0").replace('/', "~1")
    )
}

fn push_error(
    diagnostics: &mut Vec<ParseDiagnostic>,
    path: &str,
    code: DiagnosticCode,
    message: impl Into<String>,
) {
    diagnostics.push(ParseDiagnostic {
        path: path.into(),
        code,
        severity: DiagnosticSeverity::Error,
        message: message.into(),
    });
}

"#;

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{AdditionalProperties, NamedType, PublicRoot, UnionMode};

    #[test]
    fn tagged_reference_arms_preserve_existing_semantic_names_and_payloads() {
        let object = |tag: &str| Shape::Object {
            properties: vec![Property {
                wire_name: "type".into(),
                required: true,
                shape: Shape::Literal { value: tag.into() },
                constructor_default: None,
            }],
            forbidden_property_sets: vec![],
            additional: AdditionalProperties::Allowed,
        };
        let ir = Ir {
            schema_revision: "test".into(),
            protocol_version: "test".into(),
            roots: vec![],
            types: vec![
                NamedType {
                    name: "HttpTransport".into(),
                    source: "test".into(),
                    shape: object("http"),
                },
                NamedType {
                    name: "DenyEffect".into(),
                    source: "test".into(),
                    shape: object("deny"),
                },
                NamedType {
                    name: "TurnStartEvent".into(),
                    source: "test".into(),
                    shape: object("turn.start"),
                },
                NamedType {
                    name: "AuthenticationBearer".into(),
                    source: "test".into(),
                    shape: object("bearer"),
                },
            ],
        };
        let mut variants = vec![
            Shape::Ref {
                name: "HttpTransport".into(),
            },
            Shape::Ref {
                name: "DenyEffect".into(),
            },
            Shape::Ref {
                name: "TurnStartEvent".into(),
            },
            Shape::Intersection {
                variants: vec![
                    Shape::Ref {
                        name: "AuthenticationBearer".into(),
                    },
                    Shape::Any,
                ],
            },
        ];
        let mut context = EmitContext::new(&ir);
        assert_eq!(
            context.union_names(&variants),
            ["Http", "Deny", "TurnStart", "Bearer"]
        );
        let output = context.emit_union("Test", &variants, None).unwrap();
        assert!(output.contains("Http(Box<HttpTransport>)"));
        assert!(output.contains("Deny(Box<DenyEffect>)"));
        assert!(output.contains("TurnStart(Box<TurnStartEvent>)"));
        variants.reverse();
        assert_eq!(
            context.union_names(&variants),
            ["Bearer", "TurnStart", "Deny", "Http"]
        );
    }

    #[test]
    fn public_union_and_enum_names_survive_reordering_and_collisions() {
        let ir = Ir {
            schema_revision: "test".into(),
            protocol_version: "test".into(),
            roots: vec![],
            types: vec![],
        };
        let mut shapes = vec![
            Shape::Literal {
                value: "foo-bar".into(),
            },
            Shape::Literal {
                value: "other".into(),
            },
            Shape::Enum {
                values: vec!["ready".into()],
                open_strings: false,
            },
            Shape::String,
            Shape::Null,
            Shape::Object {
                properties: vec![Property {
                    wire_name: "result".into(),
                    required: true,
                    shape: Shape::Integer,
                    constructor_default: None,
                }],
                forbidden_property_sets: vec![],
                additional: AdditionalProperties::Allowed,
            },
        ];
        let mut context = EmitContext::new(&ir);
        let first = context.emit_union("Payload", &shapes, None).unwrap();
        let arms = context.ergonomic_arms["Payload"]
            .iter()
            .cloned()
            .collect::<BTreeMap<_, _>>();
        assert!(first.contains("Custom(String)"));
        assert!(first.contains("Known(PayloadKnown)"));
        assert!(first.contains("ResultObject(PayloadResultObject)"));
        assert!(!first.contains("Variant1"));
        shapes.reverse();
        let mut context = EmitContext::new(&ir);
        context.emit_union("Payload", &shapes, None).unwrap();
        assert_eq!(
            arms,
            context.ergonomic_arms["Payload"].iter().cloned().collect()
        );
        let mut values = vec!["ready".into(), "waiting".into(), "unknown".into()];
        let first = context.emit_enum("Choice", &values, false).unwrap();
        values.reverse();
        let second = context.emit_enum("Choice", &values, false).unwrap();
        for name in super::super::naming::literal_names(&values) {
            assert!(first.contains(&format!("    {name},")));
            assert!(second.contains(&format!("    {name},")));
        }
    }

    #[test]
    fn real_schema_public_declarations_are_bounded_semantic_and_order_independent() {
        let repository = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let ir = crate::compiler::compile(&repository, "draft").unwrap();
        let before = serde_json::to_value(&ir).unwrap();
        let source = emit(&ir).unwrap();
        assert_eq!(before, serde_json::to_value(&ir).unwrap());
        let mut declarations = BTreeSet::new();
        for line in source.lines() {
            let Some(rest) = line
                .strip_prefix("pub enum ")
                .or_else(|| line.strip_prefix("pub struct "))
                .or_else(|| line.strip_prefix("pub type "))
            else {
                continue;
            };
            let name = rest
                .split(|c: char| !c.is_ascii_alphanumeric() && c != '_')
                .next()
                .unwrap();
            assert!(name.len() <= 80, "oversized declaration {name}");
            assert!(
                !name.contains("Shape")
                    && !name.contains("Duplicate")
                    && !name.contains("ObjectOr")
                    && !name.contains("ObjectAnd"),
                "structural declaration {name}"
            );
            assert!(
                !name.ends_with(|c: char| c.is_ascii_digit()),
                "ordinal declaration {name}"
            );
            assert!(declarations.insert(name), "duplicate declaration {name}");
        }
        let mut in_enum = false;
        let mut payloads = BTreeSet::new();
        for line in source.lines() {
            if line.starts_with("pub enum ") {
                in_enum = true;
                payloads.clear();
                continue;
            }
            if in_enum && line == "}" {
                in_enum = false;
            }
            let line = line.trim();
            if !in_enum || line.starts_with("#") || line.starts_with("/") || line.is_empty() {
                continue;
            }
            let name = line
                .split(|c: char| !c.is_ascii_alphanumeric() && c != '_')
                .next()
                .unwrap();
            assert!(
                name.len() <= 80 && !name.contains("Shape") && !name.contains("Duplicate"),
                "bad arm {name}"
            );
            assert!(
                !name.starts_with("Variant") && !name.ends_with(|c: char| c.is_ascii_digit()),
                "ordinal arm {name}"
            );
            if let Some((_, payload)) = line.split_once('(') {
                assert!(
                    payloads.insert(payload.to_owned()),
                    "duplicate modeled payload {line}"
                );
            }
        }
        assert!(source.contains("    UnknownValue,"));
        assert!(declarations.len() > 1000);
        let primitive = ir
            .types
            .iter()
            .find(|t| t.name == "McpElicitationPrimitiveSchemaDefinition")
            .unwrap();
        let Shape::Union { variants, .. } = &primitive.shape else {
            panic!("expected elicitation union")
        };
        assert_eq!(variants.len(), 8);
        let context = EmitContext::new(&ir);
        let names = context.union_names(variants);
        assert_eq!(names.iter().collect::<BTreeSet<_>>().len(), 8);
        assert!(names.contains(&"McpElicitationStringSchema".into()));
        assert!(names.contains(&"McpElicitationNumberSchema".into()));
        for (name, shape) in names.iter().zip(variants) {
            let Shape::Ref { name: canonical } = shape else {
                panic!("expected canonical ref")
            };
            assert!(source.contains(&format!("    {name}(Box<{canonical}>),")));
        }
        let reversed = variants.iter().cloned().rev().collect::<Vec<_>>();
        assert_eq!(
            context.union_names(&reversed),
            names.into_iter().rev().collect::<Vec<_>>()
        );
        let connection = source
            .split("pub enum ExecutionEventMcpConnection {")
            .nth(1)
            .unwrap()
            .split("\n}")
            .next()
            .unwrap();
        for transport in ["Http", "Sse", "Stdio", "CustomTransport"] {
            assert!(connection.contains(&format!(
                "    {transport}(ExecutionEventMcpConnection{transport}),"
            )));
        }
    }

    #[test]
    fn exact_duplicate_public_arms_do_not_change_anyof_or_oneof_descriptors() {
        for mode in [UnionMode::AnyOf, UnionMode::OneOf] {
            let shape = Shape::Union {
                mode,
                variants: vec![Shape::String, Shape::String],
                discriminator: None,
            };
            let original = serde_json::to_value(&shape).unwrap();
            let ir = Ir {
                schema_revision: "test".into(),
                protocol_version: "test".into(),
                roots: vec![],
                types: vec![NamedType {
                    name: "DuplicateInput".into(),
                    source: "test.json#".into(),
                    shape,
                }],
            };
            let output = emit(&ir).unwrap();
            let declaration = output
                .split("pub enum DuplicateInput {")
                .nth(1)
                .unwrap()
                .split("\n}")
                .next()
                .unwrap();
            assert_eq!(declaration.matches("String(String)").count(), 1);
            assert_eq!(serde_json::to_value(&ir.types[0].shape).unwrap(), original);
            // The generated runtime still checks the original branch cardinality,
            // so a duplicate oneOf matches twice even though its public arm is shared.
            assert!(output.contains("matches.len() > 1"));
            assert!(output.contains("Value matches more than one union branch"));
            let encoded = output
                .lines()
                .find_map(|line| line.strip_prefix("const SCHEMAS_JSON: &str = "))
                .unwrap()
                .trim_end_matches(';');
            let json: String = serde_json::from_str(encoded).unwrap();
            let descriptors: Value = serde_json::from_str(&json).unwrap();
            assert_eq!(descriptors["DuplicateInput"], original);
        }
    }

    #[test]
    fn boundary_inventory_matches_canonical_schema_and_capabilities() {
        let repository = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let ir = crate::compiler::compile(&repository, "draft").unwrap();
        let response: Value = serde_json::from_str(
            &std::fs::read_to_string(
                repository.join("schema/draft/capabilities-response.schema.json"),
            )
            .unwrap(),
        )
        .unwrap();
        let events = response.pointer("/allOf/1/properties/result/properties/manifest/properties/events/items/properties/event/enum").unwrap();
        let expected: BTreeSet<_> = events
            .as_array()
            .unwrap()
            .iter()
            .map(|name| name.as_str().unwrap().to_owned())
            .collect();
        let inventory = boundary_inventory(&ir);
        assert!(!inventory.is_empty());
        assert_eq!(inventory.keys().cloned().collect::<BTreeSet<_>>(), expected);
        let capabilities: Value = serde_json::from_str(
            &std::fs::read_to_string(repository.join("schema/draft/capabilities.schema.json"))
                .unwrap(),
        )
        .unwrap();
        let intercept: BTreeSet<_> = inventory
            .iter()
            .filter(|(_, modes)| modes.contains("intercept"))
            .map(|(name, _)| name.clone())
            .collect();
        assert_eq!(
            intercept,
            capabilities["$defs"]
                .as_object()
                .unwrap()
                .keys()
                .cloned()
                .collect()
        );
        let mut output = String::new();
        emit_boundaries(&ir, &mut output).unwrap();
        assert_eq!(output.matches("pub fn ").count(), 2 * expected.len());
        assert_eq!(
            output.matches("BoundaryDescriptor { name:").count(),
            expected.len()
        );
        for name in expected {
            assert!(inventory[&name].contains("observe"));
            let method = format!("{}_event", snake_identifier(&name));
            assert!(output.contains(&format!("pub fn {method}<")));
            assert!(output.contains(&format!("self.event_for({name:?}, event)")));
            if intercept.contains(&name) {
                assert!(
                    output.contains(&format!("Some(\"capabilities.schema.json#/$defs/{name}\")"))
                );
            } else {
                assert!(output.contains(&format!(
                    "name: {name:?}, modes: &[\"observe\"], capability_schema: None"
                )));
            }
        }
    }

    #[test]
    fn boundary_macro_is_inert_until_expanded_and_every_method_executes() {
        let repository = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let ir = crate::compiler::compile(&repository, "draft").unwrap();
        let mut generated = String::new();
        emit_boundaries(&ir, &mut generated).unwrap();
        let directory = std::env::temp_dir().join(format!(
            "ahp-boundary-macro-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&directory).unwrap();
        // No runtime or serde module exists in the unexpanded compilation.
        let inert = format!("mod generated {{ {generated} }} fn main() {{}}");
        let mut expanded = format!(
            r#"
mod generated {{ {generated} }}
mod serde {{ pub trait Serialize {{}} impl Serialize for u8 {{}} pub mod de {{ pub trait DeserializeOwned {{}} impl DeserializeOwned for u8 {{}} }} }}
mod runtime {{ pub struct EventBoundary<'a, T> {{ pub name: &'a str, pub event: T }} }}
mod hooks {{ pub struct EventBoundary<'a, T> {{ pub name: &'a str, pub event: T }} }}
struct Hooks;
impl Hooks {{
    ahp_hooks_boundary_methods!();
    fn tool_before(&self) {{}}
    fn event_for<T>(&self, name: &'static str, event: T) -> hooks::EventBoundary<'_, T> {{ hooks::EventBoundary {{ name, event }} }}
}}
const NAMED_BOUNDARIES: &[&str] = ahp_hooks_boundary_methods!(inventory);
struct Client;
impl Client {{
    ahp_event_boundary_methods!();
    fn event_for<T>(&self, name: &'static str, event: T) -> runtime::EventBoundary<'_, T> {{ runtime::EventBoundary {{ name, event }} }}
}}
fn main() {{ let client = Client; let hooks = Hooks; hooks.tool_before();
"#
        );
        for name in boundary_inventory(&ir).keys() {
            let method = format!("{}_event", snake_identifier(name));
            writeln!(expanded, "let result = client.{method}(7_u8); assert_eq!(result.name, {name:?}); assert_eq!(result.event, 7);").unwrap();
        }
        let names = boundary_inventory(&ir).into_keys().collect::<Vec<_>>();
        writeln!(expanded, "assert_eq!(NAMED_BOUNDARIES, &{names:?});").unwrap();
        for name in &names {
            let method = if name == "tool.before" {
                "tool_before_event".to_owned()
            } else {
                name.replace('.', "_")
            };
            writeln!(expanded, "let result = hooks.{method}(7_u8); assert_eq!(result.name, {name:?}); assert_eq!(result.event, 7);").unwrap();
        }
        expanded.push_str("}");
        for (name, source) in [("inert", inert), ("expanded", expanded)] {
            let path = directory.join(format!("{name}.rs"));
            let binary = directory.join(name);
            std::fs::write(&path, source).unwrap();
            let result = std::process::Command::new("rustc")
                .args(["--edition=2024", "-Awarnings"])
                .arg(&path)
                .arg("-o")
                .arg(&binary)
                .output()
                .unwrap();
            assert!(
                result.status.success(),
                "{}",
                String::from_utf8_lossy(&result.stderr)
            );
            assert!(
                std::process::Command::new(binary)
                    .status()
                    .unwrap()
                    .success()
            );
        }
        std::fs::remove_dir_all(directory).unwrap();
    }

    fn sample_ir() -> Ir {
        Ir {
            schema_revision: "test-revision".into(),
            protocol_version: "1.2.3".into(),
            roots: vec![PublicRoot {
                name: "JsonRpcMessage".into(),
                schema: "message.json".into(),
            }],
            types: vec![NamedType {
                name: "JsonRpcMessage".into(),
                source: "message.json#".into(),
                shape: Shape::Object {
                    properties: vec![Property {
                        constructor_default: None,
                        wire_name: "type".into(),
                        required: false,
                        shape: Shape::Union {
                            mode: UnionMode::OneOf,
                            variants: vec![Shape::String, Shape::Integer],
                            discriminator: None,
                        },
                    }],
                    forbidden_property_sets: Vec::new(),
                    additional: AdditionalProperties::Forbidden,
                },
            }],
        }
    }

    #[test]
    fn non_event_schemas_do_not_export_a_boundary_macro() {
        let output = emit(&sample_ir()).unwrap();
        assert!(!output.contains("macro_rules! ahp_event_boundary_methods"));
        assert!(!output.contains("macro_rules! ahp_hooks_boundary_methods"));
    }

    #[test]
    fn constructors_and_conversion_coherence() {
        let ir = sample_ir();
        let mut context = EmitContext::new(&ir);
        let object = context
            .emit_struct(
                "Example",
                &[
                    Property {
                        constructor_default: None,
                        wire_name: "id".into(),
                        required: true,
                        shape: Shape::String,
                    },
                    Property {
                        constructor_default: None,
                        wire_name: "kind".into(),
                        required: true,
                        shape: Shape::Intersection {
                            variants: vec![
                                Shape::String,
                                Shape::Literal {
                                    value: Value::String("fixed".into()),
                                },
                            ],
                        },
                    },
                    Property {
                        constructor_default: None,
                        wire_name: "optional".into(),
                        required: false,
                        shape: Shape::Boolean,
                    },
                ],
            )
            .unwrap();
        assert!(object.contains("pub fn new(id: impl Into<String>)"));
        assert!(object.contains("kind: Default::default()"));
        assert!(object.contains("optional: Presence::Missing"));
        assert!(object.contains("pub fn with_optional"));
        assert!(!object.contains("pub fn with_kind"));

        let union = context
            .emit_union("Ambiguous", &[Shape::String, Shape::String], None)
            .unwrap();
        assert!(union.contains("impl From<String>"));
        let union = context
            .emit_union("Unique", &[Shape::String, Shape::Boolean], None)
            .unwrap();
        assert!(union.contains("impl From<String> for Unique"));
        assert!(union.contains("impl From<bool> for Unique"));
    }

    #[test]
    fn constructor_argument_lint_is_scoped_to_large_constructors() {
        let ir = sample_ir();
        let mut context = EmitContext::new(&ir);
        let properties = (0..8)
            .map(|index| Property {
                constructor_default: None,
                wire_name: format!("field{index}"),
                required: true,
                shape: Shape::String,
            })
            .collect::<Vec<_>>();
        let large = context.emit_struct("Large", &properties).unwrap();
        assert!(large.contains("#[allow(clippy::too_many_arguments)]\n    pub fn new("));
        assert_eq!(
            large
                .matches("#[allow(clippy::too_many_arguments)]")
                .count(),
            1
        );
        let small = context.emit_struct("Small", &properties[..7]).unwrap();
        assert!(!small.contains("#[allow(clippy::too_many_arguments)]"));
    }

    #[test]
    fn aliased_union_payloads_do_not_get_conflicting_from_impls() {
        let mut ir = sample_ir();
        ir.types.push(NamedType {
            name: "Text".into(),
            source: "text.json#".into(),
            shape: Shape::String,
        });
        ir.types.push(NamedType {
            name: "OtherText".into(),
            source: "other.json#".into(),
            shape: Shape::String,
        });
        let mut context = EmitContext::new(&ir);
        let union = context
            .emit_union(
                "Aliased",
                &[
                    Shape::Ref {
                        name: "Text".into(),
                    },
                    Shape::Ref {
                        name: "OtherText".into(),
                    },
                    Shape::String,
                ],
                None,
            )
            .unwrap();
        assert!(!union.contains("impl From<"));
    }

    #[test]
    fn output_is_deterministic_typed_and_open() {
        let ir = sample_ir();
        let first = emit(&ir).unwrap();
        let second = emit(&ir).unwrap();
        assert_eq!(first, second);
        assert!(first.contains("pub struct JsonRpcMessage"));
        assert!(first.contains("pub type_: Presence<JsonRpcMessageType>"));
        assert!(first.contains("pub enum JsonRpcMessageType"));
        let union = first
            .split("pub enum JsonRpcMessageType")
            .nth(1)
            .unwrap()
            .split('}')
            .next()
            .unwrap();
        assert!(!union.contains("Unknown(JsonValue)"));
        assert!(first.contains("Integer(Integer)"));
        assert!(first.contains("#[serde(flatten)]"));
        assert!(first.contains("pub fn parse_json_rpc_message("));
        assert!(first.contains("pub fn encode_json_rpc_message("));
        assert!(first.contains("UnknownVariant"));
    }

    #[test]
    fn recursive_refs_are_boxed_and_numeric_shapes_are_lossless() {
        let ir = Ir {
            schema_revision: "test".into(),
            protocol_version: "1".into(),
            roots: vec![],
            types: vec![
                NamedType {
                    name: "Node".into(),
                    source: "node.json#".into(),
                    shape: Shape::Object {
                        properties: vec![Property {
                            constructor_default: None,
                            wire_name: "next".into(),
                            required: false,
                            shape: Shape::Ref {
                                name: "Node".into(),
                            },
                        }],
                        forbidden_property_sets: vec![],
                        additional: AdditionalProperties::Forbidden,
                    },
                },
                NamedType {
                    name: "ExactNumber".into(),
                    source: "number.json#".into(),
                    shape: Shape::Number,
                },
                NamedType {
                    name: "LargeLiteral".into(),
                    source: "literal.json#".into(),
                    shape: Shape::Literal {
                        value: serde_json::json!(u64::MAX),
                    },
                },
            ],
        };
        let source = emit(&ir).unwrap();
        assert!(source.contains("pub next: Presence<Box<Node>>"));
        assert!(source.contains("pub type ExactNumber = JsonNumber"));
        assert!(source.contains("pub struct LargeLiteral;"));
    }

    #[test]
    fn draft_compositions_are_typed_without_rewriting_validator_descriptors() {
        let repository = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let ir = crate::compiler::compile(&repository, "draft").unwrap();
        let source = emit(&ir).unwrap();
        assert!(
            !source
                .lines()
                .any(|line| line.starts_with("pub struct ") && line.ends_with("(pub JsonValue);")),
            "schema-expressible compositions must not become opaque JSON wrappers"
        );
        assert!(source.contains("pub enum ModelVisibleItem {"));
        assert!(source.contains("pub struct ExecutionEventMcpConnectionHttp {"));
        assert!(source.contains("pub struct CapabilitiesModifyContent {"));
        assert!(source.contains("pub type NativeEvent = JsonValue;"));
        let descriptor = source
            .lines()
            .find_map(|line| line.strip_prefix("const SCHEMAS_JSON: &str = "))
            .unwrap();
        let encoded: String = serde_json::from_str(descriptor.trim_end_matches(';')).unwrap();
        let actual: Value = serde_json::from_str(&encoded).unwrap();
        let expected = ir
            .types
            .iter()
            .map(|named| (named.name.as_str(), &named.shape))
            .collect::<BTreeMap<_, _>>();
        assert_eq!(actual, serde_json::to_value(expected).unwrap());
    }

    #[test]
    fn intersection_properties_merge_commutatively_with_any_as_identity() {
        fn object(shape: Shape, required: bool) -> Shape {
            Shape::Object {
                properties: vec![Property {
                    constructor_default: None,
                    wire_name: "effects".into(),
                    required,
                    shape,
                }],
                forbidden_property_sets: vec![],
                additional: AdditionalProperties::Allowed,
            }
        }
        let named = BTreeMap::new();
        let typed = Shape::Array {
            items: Box::new(Shape::String),
        };
        let forward = collect_object_properties(
            &Shape::Intersection {
                variants: vec![object(Shape::Any, false), object(typed.clone(), true)],
            },
            &named,
            &mut BTreeSet::new(),
        )
        .unwrap();
        let reverse = collect_object_properties(
            &Shape::Intersection {
                variants: vec![object(typed, true), object(Shape::Any, false)],
            },
            &named,
            &mut BTreeSet::new(),
        )
        .unwrap();
        assert_eq!(
            serde_json::to_value(&forward).unwrap(),
            serde_json::to_value(&reverse).unwrap()
        );
        assert!(forward[0].required);
        assert!(matches!(forward[0].shape, Shape::Array { .. }));
    }

    #[test]
    fn discriminated_unions_alone_emit_guarded_unknown_variants() {
        let branch = |name: &str| Shape::Object {
            properties: vec![Property {
                constructor_default: None,
                wire_name: "type".into(),
                required: true,
                shape: Shape::Literal {
                    value: Value::String(name.into()),
                },
            }],
            forbidden_property_sets: vec![],
            additional: AdditionalProperties::Allowed,
        };
        let ir = Ir {
            schema_revision: "test".into(),
            protocol_version: "1".into(),
            roots: vec![],
            types: vec![NamedType {
                name: "Transport".into(),
                source: "transport.json#".into(),
                shape: Shape::Union {
                    mode: UnionMode::OneOf,
                    variants: vec![branch("http"), branch("stdio")],
                    discriminator: Some("type".into()),
                },
            }],
        };
        let source = emit(&ir).unwrap();
        assert!(source.contains("pub enum Transport"));
        assert!(source.contains("Unknown(JsonValue)"));
        assert!(source.contains("match actual.as_str()"));
        assert!(source.contains("\"http\" => serde_json::from_value"));
    }

    #[test]
    fn normalized_public_symbols_are_globally_collision_safe() {
        let names = [
            "FooBar",
            "foo-bar",
            "JsonValue",
            "Root",
            "root",
            "BTreeMap",
            "Deserialize",
            "String",
            "Vec",
            "Box",
            "Result",
        ];
        let ir = Ir {
            schema_revision: "test".into(),
            protocol_version: "1".into(),
            roots: vec![
                PublicRoot {
                    name: "Root".into(),
                    schema: "root.json".into(),
                },
                PublicRoot {
                    name: "root".into(),
                    schema: "root-lower.json".into(),
                },
            ],
            types: names
                .into_iter()
                .map(|name| NamedType {
                    name: name.into(),
                    source: format!("{name}.json#"),
                    shape: Shape::String,
                })
                .collect(),
        };
        let error = emit(&ir).unwrap_err().to_string();
        assert!(error.contains("distinct canonical schema name"));
    }

    #[test]
    fn identifiers_are_valid_and_collision_safe() {
        let mut used = BTreeSet::new();
        assert_eq!(snake_identifier("JSONRPCMessage"), "jsonrpc_message");
        assert_eq!(snake_identifier("request-id"), "request_id");
        assert_eq!(unique_field_identifier("type", &mut used), "type_");
        assert_eq!(unique_field_identifier("type", &mut used), "type__2");
        assert_eq!(type_identifier("3d-event"), "_3dEvent");
        let mut types = RESERVED_TYPE_NAMES
            .iter()
            .map(|name| (*name).to_owned())
            .collect::<BTreeSet<_>>();
        assert!(unique_type_identifier("json-value", &mut types).is_err());
        assert_eq!(
            unique_type_identifier("foo-bar", &mut types).unwrap(),
            "FooBar"
        );
        assert!(unique_type_identifier("foo_bar", &mut types).is_err());
    }
}
