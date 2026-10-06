use super::*;

pub(super) fn emit(ir: &Ir, context: &mut EmitContext<'_>, out: &mut String) -> Result<()> {
    let events = boundary_inventory(ir);
    if !events.is_empty() {
        out.push_str("\n#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize)]\npub enum EventType {\n");
        for event in events.keys() {
            writeln!(
                out,
                "#[serde(rename = {event:?})] {},",
                type_identifier(event)
            )?;
        }
        out.push_str("}\nimpl EventType { pub fn as_str(self) -> &'static str { match self {\n");
        for event in events.keys() {
            writeln!(out, "Self::{} => {event:?},", type_identifier(event))?;
        }
        out.push_str("} } }\n");
    }
    out.push_str("\n#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]\npub enum DeliveryDiagnosticCode {\n");
    for code in crate::ergonomics::DIAGNOSTIC_CODES {
        writeln!(
            out,
            "#[serde(rename = {code:?})] {},",
            type_identifier(code)
        )?;
    }
    out.push_str("}\n\npub mod ergonomic_inputs {\n#[allow(unused_imports)]\nuse super::*;\n/// Out-of-band source binding, never serialized into wire events.\npub struct ContentSourceBinding<S> { pub path: Vec<String>, pub source: S }\n");
    let mut methods = String::new();
    for named in &ir.types {
        if !named.name.ends_with("Event") {
            continue;
        }
        let Some(properties) = crate::ergonomics::object_fields(ir, &named.shape) else {
            continue;
        };
        let Some(event) = properties
            .iter()
            .find(|p| p.wire_name == "type")
            .and_then(|p| context.fixed_value(&p.shape, &mut BTreeSet::new()))
            .and_then(Value::as_str)
        else {
            continue;
        };
        let event = event.to_owned();
        let inputs = crate::ergonomics::input_fields(ir, &properties, &event)?;
        let name = format!("{}Input", named.name.trim_end_matches("Event"));
        let generic = inputs
            .iter()
            .any(|f| f.property.wire_name == "input" && f.path == ["tool", "input"]);
        let declaration = if generic {
            "<T = serde_json::Value>"
        } else {
            ""
        };
        writeln!(
            out,
            "#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]\npub struct {name}{declaration} {{"
        )?;
        let mut arguments = Vec::new();
        let mut initializers = Vec::new();
        let mut builders = String::new();
        for f in &inputs {
            let p = &f.property;
            let ty = if generic && p.wire_name == "input" {
                "T".into()
            } else {
                context.render_type(
                    &p.shape,
                    &format!("{name}{}", type_identifier(&p.wire_name)),
                )?
            };
            let field = snake_identifier(&p.wire_name);
            if p.required {
                arguments.push(format!("{field}: {ty}"));
                initializers.push(field);
            } else {
                initializers.push(format!("{field}: None"));
                writeln!(
                    builders,
                    "pub fn with_{field}(mut self, value: {ty}) -> Self {{ self.{field} = Some(value); self }}"
                )?;
            }
            writeln!(
                out,
                "#[serde(rename = {:?}{})]\npub {}: {},",
                p.wire_name,
                if p.required {
                    ""
                } else {
                    ", default, skip_serializing_if = \"Option::is_none\""
                },
                snake_identifier(&p.wire_name),
                if p.required {
                    ty
                } else {
                    format!("Option<{ty}>")
                }
            )?;
        }
        writeln!(
            out,
            "}}\nimpl{} {name}{} {{\npub const EVENT_TYPE: &'static str = {event:?};",
            if generic { "<T: serde::Serialize>" } else { "" },
            if generic { "<T>" } else { "" }
        )?;
        writeln!(
            out,
            "pub fn new({}) -> Self {{ Self {{ {} }} }}",
            arguments.join(", "),
            initializers.join(", ")
        )?;
        out.push_str(&builders);
        let method = if event == "tool.before" {
            "tool_before_event".into()
        } else {
            snake_identifier(&event)
        };
        let input_type = format!(
            "$crate::$models::ergonomic_inputs::{name}{}",
            if generic { "<T>" } else { "" }
        );
        let projection = format!(
            "$crate::$models::ergonomic_inputs::{name}{}::to_event_value",
            if generic { "::<T>" } else { "" }
        );
        writeln!(
            methods,
            "pub fn {method}{}(&self, input: {input_type}) -> $crate::hooks::InputBoundary<'_, {input_type}> {{ self.input_for({event:?}, input, {projection}) }}",
            if generic { "<T: serde::Serialize>" } else { "" }
        )?;
        out.push_str("/// Project host facts; the runtime supplies owned fields and validates the complete request.\npub fn to_event_value(&self) -> Result<serde_json::Value, serde_json::Error> {\nlet flat = serde_json::to_value(self)?;\nlet mut event = serde_json::json!({\"type\": Self::EVENT_TYPE});\n");
        for f in &inputs {
            writeln!(
                out,
                "if let Some(value) = flat.get({:?}) {{",
                f.property.wire_name
            )?;
            let mut expr = "event".to_string();
            for path in f.path.iter().take(f.path.len() - 1) {
                expr.push_str(&format!("[{path:?}]"));
                writeln!(
                    out,
                    "if {expr}.is_null() {{ {expr} = serde_json::json!({{}}); }}"
                )?;
            }
            writeln!(
                out,
                "{expr}[{:?}] = value.clone();\n}}",
                f.path.last().unwrap()
            )?;
        }
        out.push_str("Ok(event)\n}\n}\n");
        writeln!(
            out,
            "pub mod {}_sources {{ use super::ContentSourceBinding;",
            snake_identifier(event.as_str())
        )?;
        for slot in crate::ergonomics::content_slots(ir, &inputs) {
            let path = slot
                .path
                .iter()
                .map(|p| {
                    if p == "*" {
                        "index.to_string()".into()
                    } else {
                        format!("{p:?}.to_owned()")
                    }
                })
                .collect::<Vec<_>>()
                .join(", ");
            writeln!(
                out,
                "pub fn {}<S>({}source: S) -> ContentSourceBinding<S> {{ ContentSourceBinding {{ path: vec![{path}], source }} }}",
                snake_identifier(&slot.name),
                if slot.many { "index: usize, " } else { "" }
            )?;
        }
        out.push_str("}\n");
    }
    out.push_str("}\n");
    if !methods.is_empty() {
        writeln!(
            out,
            "/// Named ergonomic boundaries; runtime retains projection failures and validates assembled events.\n#[macro_export]\nmacro_rules! ahp_ergonomic_hook_methods {{\n () => {{ $crate::ahp_ergonomic_hook_methods!(generated); }};\n ($models:ident) => {{\n{methods}\n }};\n}}"
        )?;
    }
    if let Some(request) = ir.types.iter().find(|t| t.name == "InterceptRequest") {
        let properties =
            collect_object_properties(&request.shape, &context.named_shapes, &mut BTreeSet::new())
                .unwrap_or_default();
        if let Some(params) = properties
            .iter()
            .find(|p| p.wire_name == "params")
            .and_then(|p| {
                collect_object_properties(&p.shape, &context.named_shapes, &mut BTreeSet::new())
            })
        {
            if let Some(state) = params.iter().find(|p| p.wire_name == "state") {
                let properties = crate::ergonomics::object_fields(ir, &state.shape).unwrap();
                let Shape::Enum {
                    values: permissions,
                    ..
                } = &properties
                    .iter()
                    .find(|p| p.wire_name == "permission")
                    .unwrap()
                    .shape
                else {
                    anyhow::bail!("canonical state permission must be an enum")
                };
                out.push_str("#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]\n#[serde(rename_all = \"lowercase\")]\npub enum Permission {\n");
                for permission in permissions.iter().filter_map(Value::as_str) {
                    writeln!(out, "{},", type_identifier(permission))?;
                }
                out.push_str("}\n");
                let ty = context.render_type(&state.shape, "ErgonomicInitialState")?;
                out.push_str("pub mod state {\nuse super::*;\n#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]\npub struct Candidate { pub value: serde_json::Value, #[serde(skip_serializing_if = \"Option::is_none\")] pub provenance: Option<BTreeMap<String, serde_json::Value>> }\nimpl Candidate { pub fn new(value: serde_json::Value) -> Self { Self { value, provenance: None } }\npub fn try_new<T: serde::Serialize>(value: T) -> Result<Self, serde_json::Error> { serde_json::to_value(value).map(Self::new) }
pub fn provenance(mut self, provenance: BTreeMap<String, serde_json::Value>) -> Self { self.provenance = Some(provenance); self } }\n");
                let state_fields = &context.ergonomic_fields[&ty];
                let candidate_ty = &state_fields
                    .iter()
                    .find(|(n, _)| n == "candidate")
                    .unwrap()
                    .1;
                let permission_ty = &state_fields
                    .iter()
                    .find(|(n, _)| n == "permission")
                    .unwrap()
                    .1;
                let candidate_obj = &context.ergonomic_arms[candidate_ty]
                    .iter()
                    .find(|(n, _)| n == "Object")
                    .unwrap()
                    .1;
                let provenance_ty = &context.ergonomic_fields[candidate_obj]
                    .iter()
                    .find(|(n, _)| n == "provenance")
                    .unwrap()
                    .1;
                let matches = permissions
                    .iter()
                    .filter_map(Value::as_str)
                    .map(|p| {
                        let variant = type_identifier(p);
                        format!("Permission::{variant} => {permission_ty}::{variant}")
                    })
                    .collect::<Vec<_>>()
                    .join(", ");
                writeln!(
                    out,
                    "pub type InitialState = {ty};\npub fn initial(permission: Permission) -> InitialState {{ {ty}::new((), match permission {{ {matches} }}) }}\n}}\nimpl {ty} {{\npub fn candidate(mut self, candidate: state::Candidate) -> Self {{\nlet mut value = {candidate_obj}::new(candidate.value);\nif let Some(provenance) = candidate.provenance {{ value.provenance = Presence::Present({provenance_ty} {{ additional_properties: provenance }}); }}\nself.candidate = value.into(); self\n}}\n}}"
                )?;
            }
        }
    }
    if let Some(effect) = ir.types.iter().find(|t| t.name == "Effect") {
        if let Shape::Union { variants, .. } = &effect.shape {
            out.push_str("\npub mod effects {\nuse super::*;\n");
            for variant in variants {
                let Some(properties) =
                    collect_object_properties(variant, &context.named_shapes, &mut BTreeSet::new())
                else {
                    continue;
                };
                let Some(tag) = properties
                    .iter()
                    .find(|p| p.wire_name == "type")
                    .and_then(|p| context.fixed_value(&p.shape, &mut BTreeSet::new()))
                    .and_then(Value::as_str)
                else {
                    continue;
                };
                let op = properties
                    .iter()
                    .find(|p| p.wire_name == "operation")
                    .and_then(|p| context.fixed_value(&p.shape, &mut BTreeSet::new()))
                    .and_then(Value::as_str);
                let name = snake_identifier(&format!(
                    "{}{}",
                    tag,
                    op.map(|s| format!("_{s}")).unwrap_or_default()
                ));
                let label = type_identifier(&context.union_label(variant).unwrap());
                let arm = &context.ergonomic_arms["Effect"]
                    .iter()
                    .find(|(n, _)| n == &label)
                    .unwrap()
                    .1;
                let arm = arm
                    .strip_prefix("Box<")
                    .and_then(|s| s.strip_suffix('>'))
                    .unwrap_or(arm);
                let mut args = Vec::new();
                let mut values = Vec::new();
                for p in &properties {
                    if context
                        .fixed_value(&p.shape, &mut BTreeSet::new())
                        .is_none()
                        && p.required
                    {
                        let arg = snake_identifier(&p.wire_name);
                        let ty = &context.ergonomic_fields[arm]
                            .iter()
                            .find(|(n, _)| n == &p.wire_name)
                            .unwrap()
                            .1;
                        args.push(format!("{arg}: {ty}"));
                        values.push(arg);
                    }
                }
                writeln!(
                    out,
                    "pub fn {name}({}) -> Effect {{ {arm}::new({}).into() }}",
                    args.join(", "),
                    values.join(", ")
                )?;
                if tag == "return" {
                    writeln!(
                        out,
                        "pub fn try_return<T: serde::Serialize>(value: T) -> Result<Effect, serde_json::Error> {{ serde_json::to_value(value).map({name}) }}"
                    )?;
                }
            }
            for variant in variants {
                let Some(properties) =
                    collect_object_properties(variant, &context.named_shapes, &mut BTreeSet::new())
                else {
                    continue;
                };
                let Some(Shape::Literal { value: tag }) = properties
                    .iter()
                    .find(|p| p.wire_name == "type")
                    .map(|p| &p.shape)
                else {
                    continue;
                };
                if tag != "modify" {
                    continue;
                }
                let Some(Shape::Enum {
                    values: targets, ..
                }) = properties
                    .iter()
                    .find(|p| p.wire_name == "target")
                    .map(|p| &p.shape)
                else {
                    continue;
                };
                let Some(Shape::Enum {
                    values: operations, ..
                }) = properties
                    .iter()
                    .find(|p| p.wire_name == "operation")
                    .map(|p| &p.shape)
                else {
                    continue;
                };
                for target in targets.iter().filter_map(Value::as_str) {
                    writeln!(out, "pub mod modify_{target} {{ use super::*;")?;
                    for operation in operations.iter().filter_map(Value::as_str) {
                        writeln!(
                            out,
                            "pub fn {operation}(value: JsonValue) -> Effect {{ EffectModify::new(EffectModifyOperation::{}, EffectModifyTarget::{}, value).into() }}",
                            type_identifier(operation),
                            type_identifier(target)
                        )?;
                        writeln!(
                            out,
                            "pub fn try_{operation}<T: serde::Serialize>(value: T) -> Result<Effect, serde_json::Error> {{ serde_json::to_value(value).map({operation}) }}"
                        )?;
                    }
                    out.push_str("}\n");
                }
            }
            out.push_str("}\n");
        }
    }
    Ok(())
}
