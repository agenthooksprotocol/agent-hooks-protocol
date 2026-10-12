use super::*;

pub(super) fn emit(
    ir: &Ir,
    context: &mut EmitContext<'_>,
    out: &mut String,
) -> Result<BTreeSet<String>> {
    let mut exports = BTreeSet::from(["DeliveryDiagnosticCode".into(), "inputs".into()]);
    let support = String::from(
        r#"
#[allow(unused_imports)]
use super::*;
/// Out-of-band source binding, never serialized into wire events.
pub struct ContentSourceBinding<S> { pub path: Vec<String>, pub source: S }
pub(super) mod sealed { pub trait Sealed {} }
/// Runtime projection contracts; implementations are schema-owned.
pub trait ProjectHostInput<S>: sealed::Sealed {
    fn into_host_event(self) -> Result<(serde_json::Value, Vec<ContentSourceBinding<S>>), serde_json::Error>;
}
pub trait EventInput: sealed::Sealed { fn event_value(&self) -> Result<serde_json::Value, serde_json::Error>; }
pub(crate) fn project_event<I: EventInput>(input: &I) -> Result<serde_json::Value, serde_json::Error> { input.event_value() }
"#,
    );
    // Reuse the schema's open effect vocabulary, including extension strings.
    if let Some(fields) = context.ergonomic_fields.get("Capabilities") {
        if let Some((_, effect_vec)) = fields.iter().find(|(name, _)| name == "effects") {
            let item = effect_vec
                .strip_prefix("Vec<")
                .and_then(|ty| ty.strip_suffix('>'));
            if let Some(known) = item
                .and_then(|item| context.ergonomic_arms.get(item))
                .and_then(|arms| arms.iter().find(|(arm, _)| arm == "Known"))
                .map(|(_, ty)| ty.clone())
            {
                exports.insert("EffectId".into());
                writeln!(
                    out,
                    "/// Schema effect-family identifier, including `Unknown(String)` extensions.\npub type EffectId = {known};"
                )?;
                let item = item.expect("effect array item");
                writeln!(
                    out,
                    "impl {item} {{ pub fn as_str(&self) -> &str {{ match self {{ Self::Known(value) => value.as_str(), Self::Custom(value) => value.as_str() }} }} }}"
                )?;
                for named in &ir.types {
                    let name = context.type_name(&named.name);
                    if !name.ends_with("Capabilities") {
                        continue;
                    }
                    let Some(fields) = context.ergonomic_fields.get(&name) else {
                        continue;
                    };
                    if !fields.iter().any(|(name, _)| name == "effects") {
                        continue;
                    }
                    writeln!(
                        out,
                        "impl {name} {{\n    /// Query advertised effect-family membership only. This is not authorization,\n    /// target/operation admission, or canonical/contextual protocol validation.\n    pub fn supports(&self, effect: EffectId) -> bool {{\n        self.effects.iter().any(|item| item.as_str() == effect.as_str())\n    }}\n}}"
                    )?;
                }
            }
        }
    }
    let events = boundary_inventory(ir);
    if !events.is_empty() {
        exports.insert("EventType".into());
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
    out.push_str("}\n\npub mod inputs {\n#[allow(unused_imports)]\nuse super::*;\n#[allow(unused_imports)]\nuse __support::{ContentSourceBinding, EventInput, ProjectHostInput};\n");

    if ir.types.iter().any(|n| n.name == "CanonicalMessage") {
        out.push_str(r#"
// Identity is assigned once at construction and retained by the owned input.
fn host_identity() -> String {
    static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);
    let sequence = NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let epoch = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_nanos();
    format!("ahp-host-{epoch:x}-{sequence:x}")
}
fn host_error(message: &str) -> serde_json::Error { <serde_json::Error as serde::ser::Error>::custom(message) }
fn validate_host_identity(value: &serde_json::Value) -> Result<(), serde_json::Error> {
    if value.get("id").and_then(serde_json::Value::as_str).is_none_or(|id| id.is_empty()) {
        return Err(host_error("host identity must be nonempty"));
    }
    if value.get("category").is_some_and(|category| category.as_str().is_none_or(|s| s.is_empty())) {
        return Err(host_error("host category must be nonempty"));
    }
    Ok(())
}
fn validate_host_part(value: &serde_json::Value) -> Result<(), serde_json::Error> {
    validate_host_identity(value)?;
    if value["kind"] == "attachment" {
        let media = value["mediaType"].as_str().ok_or_else(|| host_error("attachment mediaType must be a string"))?;
        let lower = media.to_ascii_lowercase();
        let base = lower.split(';').next().unwrap_or("");
        let subtype = base.split_once('/').map(|(_, subtype)| subtype);
        if media.is_empty() || lower.starts_with("text/") || subtype.is_some_and(|s| s == "json" || s.ends_with("+json")) {
            return Err(host_error("attachment mediaType must identify non-text, non-JSON media"));
        }
    }
    let _: ContentItem = serde_json::from_value(value.clone())?;
    Ok(())
}
/// Owned host parts. Projection never serializes or opens S.
pub enum PartInput<S> {
    Wire(ContentItem), Text(TextBodyPart),
    Attachment { metadata: AttachmentBodyPart, body: S },
}
impl<S> PartInput<S> {
    pub fn text(value: TextBodyPart) -> Self { Self::Text(value) }
    pub fn inline_text(text: impl Into<String>) -> Self {
        Self::text(TextBodyPart::new(host_identity(), text).with_synthesized(true))
    }
    pub fn owned(body: S, media_type: impl Into<String>) -> Self {
        Self::synthesized_attachment(host_identity(), media_type, body)
    }
    /// Preserve metadata, replacing only the reference during runtime planning.
    pub fn attachment(metadata: AttachmentBodyPart, body: S) -> Self { Self::Attachment { metadata, body } }
    /// Direct owned attachment construction without a wire reference.
    pub fn owned_attachment(id: impl Into<String>, media_type: impl Into<String>, body: S) -> Self {
        Self::attachment(AttachmentBodyPart::new(ContentReference::new("ahp:host-pending"), id, media_type), body)
    }
    /// Caller supplies a stable identity deliberately when host identity is absent.
    pub fn synthesized_attachment(id: impl Into<String>, media_type: impl Into<String>, body: S) -> Self {
        Self::attachment(AttachmentBodyPart::new(ContentReference::new("ahp:host-pending"), id, media_type).with_synthesized(true), body)
    }
    fn project(self, path: Vec<String>, sources: &mut Vec<ContentSourceBinding<S>>) -> Result<serde_json::Value, serde_json::Error> {
        match self {
            Self::Wire(value) => { let value = serde_json::to_value(value)?; validate_host_part(&value)?; Ok(value) },
            Self::Text(value) => { let value = serde_json::to_value(value)?; validate_host_part(&value)?; Ok(value) },
            Self::Attachment { metadata, body } => {
                let mut value = serde_json::to_value(metadata)?;
                if value["kind"] != "attachment" || value["selection"] != "body" {
                    return Err(host_error("owned attachment requires kind attachment and selection body"));
                }
                value["body"] = serde_json::json!({"ref":"ahp:host-pending"});
                validate_host_part(&value)?;
                sources.push(ContentSourceBinding { path, source: body }); Ok(value)
            }
        }
    }
}
pub struct MessageInput<S> { pub metadata: CanonicalMessage, pub parts: Vec<PartInput<S>> }
impl<S> MessageInput<S> {
    pub fn from_parts(role: CanonicalMessageRole, parts: Vec<PartInput<S>>) -> Self {
        Self::synthesized(host_identity(), role, parts)
    }
    pub fn new(id: impl Into<String>, role: CanonicalMessageRole, parts: Vec<PartInput<S>>) -> Self {
        Self { metadata: CanonicalMessage::new(id, Vec::new(), role), parts }
    }
    pub fn synthesized(id: impl Into<String>, role: CanonicalMessageRole, parts: Vec<PartInput<S>>) -> Self {
        let mut value = Self::new(id, role, parts); value.metadata = value.metadata.with_synthesized(true); value
    }
    fn project(self, path: Vec<String>, sources: &mut Vec<ContentSourceBinding<S>>) -> Result<serde_json::Value, serde_json::Error> {
        let mut value = serde_json::to_value(self.metadata)?;
        let mut part_path = path; part_path.push("parts".into());
        value["parts"] = project_parts(self.parts, part_path, sources)?;
        validate_host_identity(&value)?;
        let _: CanonicalMessage = serde_json::from_value(value.clone())?;
        Ok(value)
    }
}
fn project_parts<S>(parts: Vec<PartInput<S>>, path: Vec<String>, sources: &mut Vec<ContentSourceBinding<S>>) -> Result<serde_json::Value, serde_json::Error> {
    let mut values = Vec::new();
    for (index, part) in parts.into_iter().enumerate() {
        let mut child = path.clone(); child.push(index.to_string()); values.push(part.project(child, sources)?);
    }
    Ok(serde_json::Value::Array(values))
}
#[allow(dead_code)]
enum HostReplacement<S> { Messages(Vec<MessageInput<S>>), Message(MessageInput<S>), Parts(Vec<PartInput<S>>), Part(PartInput<S>) }
/// Envelope around unchanged typed event facts; builders exist only for schema-owned slots.
pub struct HostInput<I, S> { pub input: I, replacements: Vec<(Vec<String>, HostReplacement<S>)> }
impl<I, S> __support::sealed::Sealed for HostInput<I, S> {}
impl<I: EventInput, S> HostInput<I, S> {
    /// Consume schema-owned content slots without serializing or opening sources.
    pub fn into_host_event(self) -> Result<(serde_json::Value, Vec<ContentSourceBinding<S>>), serde_json::Error> {
        ProjectHostInput::into_host_event(self)
    }
}
impl<I: EventInput, S> ProjectHostInput<S> for HostInput<I, S> {
    fn into_host_event(self) -> Result<(serde_json::Value, Vec<ContentSourceBinding<S>>), serde_json::Error> {
        let mut event = self.input.event_value()?; let mut sources = Vec::new();
        for (path, replacement) in self.replacements {
            sources.retain(|binding: &ContentSourceBinding<S>| !binding.path.starts_with(&path));
            let value = match replacement {
                HostReplacement::Message(message) => message.project(path.clone(), &mut sources)?,
                HostReplacement::Messages(messages) => {
                    let mut values = Vec::new();
                    for (index, message) in messages.into_iter().enumerate() {
                        let mut child = path.clone(); child.push(index.to_string()); values.push(message.project(child, &mut sources)?);
                    }
                    serde_json::Value::Array(values)
                },
                HostReplacement::Parts(parts) => project_parts(parts, path.clone(), &mut sources)?,
                HostReplacement::Part(part) => part.project(path.clone(), &mut sources)?,
            };
            let mut target = &mut event;
            for component in path {
                if target.is_array() {
                    let index: usize = component.parse().map_err(|_| <serde_json::Error as serde::ser::Error>::custom("invalid host array index"))?;
                    target = target.get_mut(index).ok_or_else(|| <serde_json::Error as serde::ser::Error>::custom("host array index out of bounds"))?;
                } else {
                    if target.is_null() { *target = serde_json::json!({}); }
                    if !target.is_object() { return Err(host_error("host slot parent must be an object or array")); }
                    target = &mut target[component];
                }
            }
            *target = value;
        }
        Ok((event, sources))
    }
}
"#);
    }
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
            "$crate::$models::inputs::{name}{}",
            if generic { "<T>" } else { "" }
        );
        let projection = format!(
            "$crate::$models::inputs::__support::project_event::<$crate::$models::inputs::{name}{}>",
            if generic { "<T>" } else { "" }
        );
        writeln!(
            methods,
            "pub fn {method}{}(&self, input: {input_type}) -> $crate::hooks::InputBoundary<'_, {input_type}> {{ self.input_for($crate::EventType::{variant}, input, {projection}) }}",
            if generic { "<T: serde::Serialize>" } else { "" },
            variant = type_identifier(&event)
        )?;
        let impl_generics = if generic { "<T: serde::Serialize>" } else { "" };
        let type_generics = if generic { "<T>" } else { "" };
        writeln!(
            out,
            "}}\nimpl{impl_generics} EventInput for {name}{type_generics} {{\nfn event_value(&self) -> Result<serde_json::Value, serde_json::Error> {{\nlet flat = serde_json::to_value(self)?;\nlet mut event = serde_json::json!({{\"type\": Self::EVENT_TYPE}});"
        )?;
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
            "impl{impl_generics} __support::sealed::Sealed for {name}{type_generics} {{}}"
        )?;
        if ir.types.iter().any(|n| n.name == "CanonicalMessage") {
            writeln!(
                out,
                "impl{impl_generics} {name}{type_generics} {{ pub fn with_sources<S>(self) -> HostInput<Self, S> {{ HostInput {{ input: self, replacements: Vec::new() }} }} }}"
            )?;
            writeln!(
                out,
                "impl{} HostInput<{name}{type_generics}, S> {{",
                if generic {
                    "<T: serde::Serialize, S>"
                } else {
                    "<S>"
                }
            )?;
            let mut emitted = BTreeSet::new();
            for slot in crate::ergonomics::content_slots(ir, &inputs) {
                let mut path = slot.path.clone();
                let part_many = path.last().is_some_and(|p| p == "*");
                if part_many {
                    path.pop();
                }
                let message = path.last().is_some_and(|p| p == "parts");
                if message {
                    path.pop();
                }
                let many = if message {
                    path.last().is_some_and(|p| p == "*")
                } else {
                    part_many
                };
                if message && many {
                    path.pop();
                }
                if !emitted.insert(path.clone()) {
                    continue;
                }
                let method = snake_identifier(
                    &path
                        .iter()
                        .filter(|p| *p != "*")
                        .cloned()
                        .collect::<Vec<_>>()
                        .join("_"),
                );
                let mut index = 0;
                let mut args = Vec::new();
                let expressions = path
                    .iter()
                    .map(|p| {
                        if p == "*" {
                            let arg = format!("index_{index}");
                            index += 1;
                            args.push(format!("{arg}: usize"));
                            format!("{arg}.to_string()")
                        } else {
                            format!("{p:?}.to_owned()")
                        }
                    })
                    .collect::<Vec<_>>()
                    .join(", ");
                let (ty, variant) = match (message, many) {
                    (true, true) => ("Vec<MessageInput<S>>", "Messages"),
                    (true, false) => ("MessageInput<S>", "Message"),
                    (false, true) => ("Vec<PartInput<S>>", "Parts"),
                    (false, false) => ("PartInput<S>", "Part"),
                };
                args.push(format!("value: {ty}"));
                writeln!(
                    out,
                    "pub fn with_{method}(mut self, {}) -> Self {{ self.replacements.push((vec![{expressions}], HostReplacement::{variant}(value))); self }}",
                    args.join(", ")
                )?;
            }
            out.push_str("}\n");
        }
    }
    writeln!(
        out,
        "#[doc(hidden)]\npub mod __support {{\n{support}\n}}\n}}"
    )?;
    if !methods.is_empty() {
        writeln!(
            out,
            "/// Named ergonomic boundaries; runtime retains projection failures and validates assembled events.\n#[allow(unused_macros)]\nmacro_rules! ahp_ergonomic_hook_methods {{\n () => {{ $crate::generated::ahp_ergonomic_hook_methods!(generated); }};\n ($models:ident) => {{\n{methods}\n }};\n}}\n#[allow(unused_imports)]\npub(crate) use ahp_ergonomic_hook_methods;"
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
                exports.extend(["Permission".into(), "state".into()]);
                out.push_str("#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]\n#[serde(rename_all = \"lowercase\")]\npub enum Permission {\n");
                for permission in permissions.iter().filter_map(Value::as_str) {
                    writeln!(out, "{},", type_identifier(permission))?;
                }
                out.push_str("}\n");
                let ty = context.render_type(&state.shape, "ErgonomicInitialState")?;
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
                    .find(|(_, ty)| {
                        context
                            .ergonomic_fields
                            .get(ty)
                            .is_some_and(|fields| fields.iter().any(|(name, _)| name == "value"))
                    })
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
                    "pub mod state {{\nuse super::*;\n/// The canonical candidate descriptor, including extension members.\npub type Candidate = {candidate_obj};\nimpl Candidate {{\npub fn try_new<T: serde::Serialize>(value: T) -> Result<Self, serde_json::Error> {{ serde_json::to_value(value).map(Self::new) }}\npub fn provenance(mut self, provenance: BTreeMap<String, serde_json::Value>) -> Self {{ self.provenance = Presence::Present({provenance_ty} {{ additional_properties: provenance }}); self }}\n}}\npub type InitialState = {ty};\npub fn initial(permission: Permission) -> InitialState {{ {ty}::new((), match permission {{ {matches} }}) }}\n}}\nimpl {ty} {{\npub fn candidate(mut self, candidate: state::Candidate) -> Self {{ self.candidate = candidate.into(); self }}\n}}"
                )?;
            }
        }
    }
    if let Some(effect) = ir.types.iter().find(|t| t.name == "Effect") {
        if let Shape::Union { .. } = &effect.shape {
            let variants = crate::ergonomics::effect_variants(ir);
            exports.insert("effects".into());
            out.push_str("\npub mod effects {\nuse super::*;\n");
            for (variant, label) in variants.iter().zip(context.try_union_names(&variants)?) {
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
                let name = snake_identifier(&crate::ergonomics::effect_builder_name(ir, variant));
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
                if tag == "return" && name == "return_" {
                    writeln!(
                        out,
                        "pub fn try_return<T: serde::Serialize>(value: T) -> Result<Effect, serde_json::Error> {{ serde_json::to_value(value).map({name}) }}"
                    )?;
                }
            }
            for (variant, label) in variants.iter().zip(context.try_union_names(&variants)?) {
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
                let Some(target_property) = properties.iter().find(|p| p.wire_name == "target")
                else {
                    continue;
                };
                let Some(operation_property) =
                    properties.iter().find(|p| p.wire_name == "operation")
                else {
                    continue;
                };
                let Some(value_property) = properties.iter().find(|p| p.wire_name == "value")
                else {
                    continue;
                };
                let arm = &context.ergonomic_arms["Effect"]
                    .iter()
                    .find(|(n, _)| n == &label)
                    .unwrap()
                    .1;
                let arm = arm
                    .strip_prefix("Box<")
                    .and_then(|s| s.strip_suffix('>'))
                    .unwrap_or(arm);
                let emitted_fields = &context.ergonomic_fields[arm];
                let value_type = &emitted_fields.iter().find(|(n, _)| n == "value").unwrap().1;
                for target in crate::ergonomics::string_values(ir, &target_property.shape) {
                    let helper =
                        crate::ergonomics::modify_helper_target(&target, &value_property.shape);
                    writeln!(out, "pub mod modify_{helper} {{ use super::*;")?;
                    for operation in crate::ergonomics::string_values(ir, &operation_property.shape)
                    {
                        let mut args = Vec::new();
                        for p in &properties {
                            if !p.required
                                || context
                                    .fixed_value(&p.shape, &mut BTreeSet::new())
                                    .is_some()
                            {
                                continue;
                            }
                            let ty = &emitted_fields
                                .iter()
                                .find(|(n, _)| n == &p.wire_name)
                                .unwrap()
                                .1;
                            args.push(match p.wire_name.as_str() {
                                "target" | "operation" => {
                                    let label = type_identifier(if p.wire_name == "target" {
                                        &target
                                    } else {
                                        &operation
                                    });
                                    if let Some(arms) = context.ergonomic_arms.get(ty) {
                                        let (_, payload) =
                                            arms.iter().find(|(arm, _)| *arm == label).unwrap();
                                        format!(
                                            "{ty}::{label}({})",
                                            context.default_expression(payload)
                                        )
                                    } else {
                                        format!("{ty}::{label}")
                                    }
                                }
                                _ => snake_identifier(&p.wire_name),
                            });
                        }
                        writeln!(
                            out,
                            "pub fn {operation}(value: {value_type}) -> {arm} {{ {arm}::new({}) }}",
                            args.join(", ")
                        )?;
                        writeln!(
                            out,
                            "pub fn try_{operation}<T: serde::Serialize>(value: T) -> Result<{arm}, serde_json::Error> {{ serde_json::to_value(value).and_then(serde_json::from_value).map({operation}) }}"
                        )?;
                    }
                    out.push_str("}\n");
                }
            }
            for named in ir.types.iter().filter(|t| {
                t.name.starts_with("Return")
                    && t.name.ends_with("Effect")
                    && t.name != "ReturnToolEffect"
            }) {
                let arm = type_identifier(&named.name);
                if let Some(emitted_fields) = context.ergonomic_fields.get(&arm) {
                    let Some(properties) = crate::ergonomics::object_fields(ir, &named.shape)
                    else {
                        continue;
                    };
                    let mut args = Vec::new();
                    let mut values = Vec::new();
                    for p in properties.iter().filter(|p| {
                        p.required
                            && context
                                .fixed_value(&p.shape, &mut BTreeSet::new())
                                .is_none()
                    }) {
                        let arg = snake_identifier(&p.wire_name);
                        let ty = &emitted_fields
                            .iter()
                            .find(|(n, _)| n == &p.wire_name)
                            .unwrap()
                            .1;
                        args.push(format!("{arg}: {ty}"));
                        values.push(arg);
                    }
                    let name = snake_identifier(named.name.trim_end_matches("Effect"));
                    writeln!(
                        out,
                        "pub fn {name}({}) -> {arm} {{ {arm}::new({}) }}",
                        args.join(", "),
                        values.join(", ")
                    )?;
                }
            }
            out.push_str("}\n");
        }
    }
    // SDK generic projections copy their members from named canonical models.
    // Payload substitution is the only semantic change; unknown bags remain.
    for (source, public, params) in [
        ("ToolCandidate", "Candidate", "T = JsonValue, P = JsonValue"),
        (
            "McpElicitationElicitResult",
            "ElicitResult",
            "T = FormAnswers",
        ),
    ] {
        let Some(named) = ir.types.iter().find(|t| t.name == source) else {
            continue;
        };
        let Some(properties) = crate::ergonomics::object_fields(ir, &named.shape) else {
            continue;
        };
        let Some(emitted) = context.ergonomic_fields.get(source) else {
            continue;
        };
        writeln!(
            out,
            "\n/// Generic SDK projection derived from `{source}`.\n#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]"
        )?;
        writeln!(
            out,
            "#[serde(bound(deserialize = {:?}))]",
            if public == "Candidate" {
                "T: serde::Deserialize<'de>, P: serde::Deserialize<'de>"
            } else {
                "T: serde::Deserialize<'de>"
            }
        )?;
        exports.insert(public.into());
        writeln!(out, "pub struct {public}<{params}> {{")?;
        for p in &properties {
            let field = snake_identifier(&p.wire_name);
            let substitution = match p.wire_name.as_str() {
                "value" | "content" => Some("T"),
                "provenance" if public == "Candidate" => Some("P"),
                _ => None,
            };
            let ty = if let Some(generic) = substitution {
                if p.required {
                    generic.into()
                } else {
                    format!("Presence<{generic}>")
                }
            } else {
                {
                    let canonical = &emitted.iter().find(|(n, _)| n == &p.wire_name).unwrap().1;
                    if p.required {
                        canonical.clone()
                    } else {
                        format!("Presence<{canonical}>")
                    }
                }
            };
            writeln!(out, "#[serde(rename = {:?})]", p.wire_name)?;
            if !p.required {
                out.push_str("#[serde(default, skip_serializing_if = \"Presence::is_missing\")]\n");
            }
            writeln!(out, "pub {field}: {ty},")?;
        }
        out.push_str(
            "#[serde(flatten)]\npub additional_properties: BTreeMap<String, JsonValue>,\n}\n",
        );
    }
    let responses = crate::ergonomics::contextual_responses(ir);
    if !responses.is_empty() {
        exports.extend(
            [
                "ContextualResponse",
                "response_for_request",
                "stage_response_for_request",
                "responses",
            ]
            .map(str::to_owned),
        );
        out.push_str("\n/// Validated contextual responses; publication remains the runtime's responsibility.\n#[derive(Debug, Clone)]\npub enum ContextualResponse {\n");
        for (_, event, response) in &responses {
            writeln!(out, "{}(Box<{response}>),", event.trim_end_matches("Event"))?;
        }
        out.push_str("}\npub fn response_for_request(event_type: EventType, input: &str) -> ParseResult<ContextualResponse> {\nmatch event_type {\n");
        for (event, event_model, response) in &responses {
            let arm = event_model.trim_end_matches("Event");
            writeln!(
                out,
                "EventType::{} => match parse_root::<{response}>({response:?}, input) {{ ParseResult::Success {{value, raw, diagnostics}} => ParseResult::Success {{value: ContextualResponse::{arm}(Box::new(value)), raw, diagnostics}}, ParseResult::Failure {{raw, diagnostics}} => ParseResult::Failure {{raw, diagnostics}} }},",
                type_identifier(event)
            )?;
        }
        out.push_str("_ => ParseResult::Failure { raw: None, diagnostics: vec![ParseDiagnostic {path:String::new(), code:DiagnosticCode::UnknownVariant, severity:DiagnosticSeverity::Error, message:format!(\"Request event does not support interception: {}\", event_type.as_str())}] },\n}\n}\n/// Run application validation before returning a publishable response. No state is changed.\npub fn stage_response_for_request(event_type: EventType, input: &str, validate: impl FnOnce(&ContextualResponse) -> Result<(), String>) -> ParseResult<ContextualResponse> {\nmatch response_for_request(event_type, input) {\nParseResult::Success {value, raw, mut diagnostics} => match validate(&value) { Ok(()) => ParseResult::Success {value,raw,diagnostics}, Err(message) => { diagnostics.push(ParseDiagnostic {path:String::new(),code:DiagnosticCode::InvalidKnownVariant,severity:DiagnosticSeverity::Error,message}); ParseResult::Failure {raw:Some(raw),diagnostics} } },\nfailure => failure,\n}\n}\n");
        out.push_str("\npub mod responses { use super::*;\n");
        for event in ir.types.iter().filter(|t| t.name.ends_with("Event")) {
            let boundary = event.name.trim_end_matches("Event");
            let response = format!("{boundary}InterceptResponse");
            if ir.types.iter().any(|t| t.name == response) {
                let helper = snake_identifier(&format!("for_{boundary}"));
                writeln!(
                    out,
                    "/// Compile-time correlation only; does not validate the response.\npub fn {helper}(_event: &{}, response: {response}) -> {response} {{ response }}",
                    event.name
                )?;
            }
        }
        out.push_str("}\n");
    }
    Ok(exports)
}
