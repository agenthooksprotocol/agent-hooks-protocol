//! Semantic APIs share the wire emitter's resolved names and schema graph. No wire
//! declarations are copied into this layer; role metadata only controls ownership.
use super::*;
#[path = "capabilities.rs"]
mod capabilities;

const MODULE: &str = "github.com/agenthooksprotocol/go-sdk";

pub fn emit(ir: &Ir) -> Result<BTreeMap<String, String>> {
    let mut g = Generator::new(ir);
    let mut named = ir.types.iter().collect::<Vec<_>>();
    named.sort_by_key(|n| (n.name.as_str(), n.source.as_str()));
    for n in named {
        g.emit_named(&g.named_name(&n.name), &n.source, &n.shape)?;
    }
    let mut packages = BTreeMap::<String, String>::new();
    for (name, fields) in &g.objects {
        if !g.ir.types.iter().any(|n| g.named_name(&n.name) == *name) {
            continue;
        }
        if let Some((package, short)) = role(name) {
            let body = packages.entry(package.into()).or_default();
            constructor(&g, body, name, &short, fields)?;
        }
    }
    for event in ir.types.iter().filter(|t| t.name.ends_with("Event")) {
        let boundary = event.name.trim_end_matches("Event");
        let response = format!("{boundary}InterceptResponse");
        if ir.types.iter().any(|t| t.name == response) {
            writeln!(
                packages.entry("event".into()).or_default(),
                "// ResponseFor{boundary} correlates at compile time only; it does not validate the response.\nfunc ResponseFor{boundary}(_ *ahp.{}, response *ahp.{response}) *ahp.{response} {{ return response }}",
                event.name
            )?;
        }
    }
    packages.insert("capability".into(), capabilities::emit(ir)?);
    if ir.types.iter().any(|named| named.name == "Event") {
        packages
            .entry("event".into())
            .or_default()
            .push_str("type Event = ahp.Event\n");
    }

    state_helpers(&g, &mut packages)?;
    effect_operations(&g, packages.entry("effect".into()).or_default())?;
    let mut diagnostics = String::from("type Code string\n");
    for code in crate::ergonomics::DIAGNOSTIC_CODES {
        writeln!(diagnostics, "const {} Code = {code:?}", go_identifier(code))?;
    }
    packages.insert("diagnostic".into(), diagnostics);
    // Inline effect alternatives get names from their semantic literals rather
    // than unstable VariantN allocation names.
    if let Some(arms) = g.unions.get("Effect") {
        for (_, ty) in arms {
            if ty.starts_with('*') || g.ir.types.iter().any(|n| g.named_name(&n.name) == *ty) {
                continue;
            }
            if let Some(fields) = g.objects.get(ty) {
                let tag = fields
                    .iter()
                    .find(|f| f.wire_name == "type")
                    .and_then(|f| literal(&g, &f.shape));
                if let Some(serde_json::Value::String(tag)) = tag {
                    let operation = fields
                        .iter()
                        .find(|f| f.wire_name == "operation")
                        .and_then(|f| literal(&g, &f.shape));
                    let short = match operation {
                        Some(serde_json::Value::String(op)) => {
                            format!("{}{}", go_identifier(&tag), go_identifier(&op))
                        }
                        _ => go_identifier(&tag),
                    };
                    constructor(
                        &g,
                        packages.entry("effect".into()).or_default(),
                        ty,
                        &short,
                        fields,
                    )?;
                }
            }
        }
    }
    // Some canonical events deliberately retain composite constraints as raw
    // wire models. Host projections still expose their declared fields, using
    // raw JSON for inline composite facts rather than inventing new wire types.
    for n in &ir.types {
        if !n.name.ends_with("Event") || g.objects.contains_key(&g.named_name(&n.name)) {
            continue;
        }
        if let Some(properties) = projected_properties(&g, &n.shape) {
            let fields = properties
                .into_iter()
                .map(|p| RenderedField {
                    field_name: go_identifier(&p.wire_name),
                    field_type: projection_type(&g, &p.shape),
                    wire_name: p.wire_name,
                    required: p.required,
                    constructor_default: p.constructor_default,
                    shape: p.shape,
                })
                .collect();
            g.objects.insert(g.named_name(&n.name), fields);
        }
    }
    let intercepts = enum_strings(
        &g,
        &g.objects["InterceptSubscription"]
            .iter()
            .find(|f| f.wire_name == "events")
            .unwrap()
            .shape,
    );
    let mut events = String::from(
        "// Type identifies an event; convert explicitly at string wire boundaries.\ntype Type string\n",
    );
    if g.unions.contains_key("ContentItem") {
        events.push_str(OWNED_CONTENT_INPUTS);
    }
    let mut methods = String::new();
    let mut sources = String::from(INPUT_SOURCE_EXTRACTOR);
    let mut source_cases = String::new();
    for (name, fields) in &g.objects {
        if !name.ends_with("Event") || !fields.iter().any(|f| f.wire_name == "source") {
            continue;
        }
        let short = name.trim_end_matches("Event");
        let generic = short == "ToolBefore";
        input_projection(
            &g,
            &mut events,
            &mut sources,
            &format!("{short}Input"),
            fields,
            generic,
        )?;
        if !generic {
            writeln!(
                source_cases,
                "case event.{short}Input: extract{short}Input(v, sources)\ncase *event.{short}Input: if v != nil {{ extract{short}Input(*v, sources) }}"
            )?;
        }
        let tag = fields
            .iter()
            .find(|f| f.wire_name == "type")
            .and_then(|f| literal(&g, &f.shape));
        if let Some(serde_json::Value::String(tag)) = tag {
            writeln!(events, "const {short} Type = {tag:?}")?;
            if generic {
                writeln!(
                    methods,
                    "func (c *Hooks) {short}[T any](ctx context.Context, input event.{short}Input[T], opts ...InterceptOption) (*ToolBeforeResult[T], error) {{ return interceptToolBefore[T](ctx, c, input, opts...) }}"
                )?;
            } else if intercepts.contains(&tag) {
                writeln!(
                    methods,
                    "func (c *Hooks) {short}(ctx context.Context, input event.{short}Input, opts ...InterceptOption) (*Result, error) {{ return c.intercept(ctx, {tag:?}, input, opts...) }}"
                )?;
            } else {
                writeln!(
                    methods,
                    "func (c *Hooks) {short}(ctx context.Context, input event.{short}Input) (*Result, error) {{ return c.intercept(ctx, {tag:?}, input) }}"
                )?;
            }
        }
    }
    packages.insert("event".into(), events);
    let mut tool = String::new();
    let mut tool_fields = g.objects["ExecutionEventTool"].clone();
    for field in &mut tool_fields {
        field.field_name = field.field_name.replace("Mcp", "MCP");
    }
    projection(&mut tool, "Input", &tool_fields, true, true)?;
    projection_constructor(
        &mut tool,
        "Input",
        &tool_fields,
        "input",
        &["name", "origin", "input"],
    )?;
    packages.insert("tool".into(), tool);
    let duration_helper = r#"
// Milliseconds converts a positive, whole-millisecond duration without truncation.
// It does not apply a default or round fractions. Protocol validation remains required.
// durationMilliseconds is total: preserve invalid fractional/negative values
// exactly so canonical validation can reject them, never silently round them.
func durationMilliseconds(value time.Duration) json.Number {
    whole, fraction := int64(value / time.Millisecond), int64(value % time.Millisecond)
    if fraction == 0 { return json.Number(strconv.FormatInt(whole, 10)) }
    sign := ""
    if value < 0 { sign = "-"; whole = -whole; fraction = -fraction }
    return json.Number(strings.TrimRight(fmt.Sprintf("%s%d.%06d", sign, whole, fraction), "0"))
}
func Milliseconds(value time.Duration) (json.Number, error) {
    if value <= 0 || value % time.Millisecond != 0 { return "", fmt.Errorf("timeout must be positive whole milliseconds") }
    return json.Number(strconv.FormatInt(int64(value / time.Millisecond), 10)), nil
}
"#;
    writeln!(
        sources,
        "// Extract accepts only generated nominal inputs, never external source-provider interfaces.\nfunc Extract(input any) map[string]*content.Source {{ sources := map[string]*content.Source{{}}; switch v := input.(type) {{\ncase toolBeforeEnvelope: for path, source := range v.sources {{ sources[path] = source }}; return sources\ncase event.ToolBeforeInput[any]: return ExtractToolBefore(v)\ncase *event.ToolBeforeInput[any]: if v != nil {{ return ExtractToolBefore(*v) }}\n{source_cases}\n}}; return sources }}"
    )?;
    packages.insert("internal/inputsources".into(), sources);
    let mut files = BTreeMap::new();
    let responses = crate::ergonomics::contextual_responses(ir);
    let mut correlated = String::from(
        "// Code generated by ahp-codegen. DO NOT EDIT.\npackage ahp\n\n// ContextualResponse is admitted by the response schema for a known request event.\ntype ContextualResponse interface { contextualResponse() }\n",
    );
    for (_, _, response) in &responses {
        writeln!(
            correlated,
            "func (*{response}) contextualResponse() {{}}\nfunc Parse{response}(input []byte) ParseResult[{response}] {{ return parseRoot[{response}]({response:?}, input) }}"
        )?;
    }
    let mut context_methods = String::from(
        "// ResponseForRequest validates a known canonical event context without publishing state.\nfunc ResponseForRequest(eventType Type, input []byte) ahp.ParseResult[ahp.ContextualResponse] {\nswitch eventType {\n",
    );
    for (event, _, response) in &responses {
        writeln!(
            context_methods,
            "case {event:?}: r := ahp.Parse{response}(input); result := ahp.ParseResult[ahp.ContextualResponse]{{OK:r.OK,Raw:r.Raw,Diagnostics:r.Diagnostics}}; if r.OK {{ result.Value = &r.Value }}; return result"
        )?;
    }
    context_methods.push_str("default: return ahp.ParseResult[ahp.ContextualResponse]{Diagnostics:[]ahp.ParseDiagnostic{{Code:ahp.DiagnosticUnknownVariant,Severity:ahp.SeverityError,Message:\"Unsupported request event: \"+string(eventType)}}}\n}\n}\n// StageResponseForRequest runs application validation before a runtime may publish state.\nfunc StageResponseForRequest(eventType Type, input []byte, validate func(ahp.ContextualResponse) error) ahp.ParseResult[ahp.ContextualResponse] { r := ResponseForRequest(eventType,input); if r.OK && validate != nil { if err := validate(r.Value); err != nil { r.OK=false; r.Value=nil; r.Diagnostics=append(r.Diagnostics,ahp.ParseDiagnostic{Code:ahp.DiagnosticInvalidKnownVariant,Severity:ahp.SeverityError,Message:err.Error()}) } }; return r }\n");
    packages
        .get_mut("event")
        .unwrap()
        .push_str(&context_methods);
    files.insert("contextual_responses_generated.go".into(), correlated);
    let mut generic_source = String::from(
        "// Code generated by ahp-codegen. DO NOT EDIT.\npackage ahp\nimport \"encoding/json\"\nimport \"fmt\"\n",
    );
    for (source, public, parameters, arguments) in [
        ("ToolCandidate", "Candidate", "T, P any", "T, P"),
        ("McpElicitationElicitResult", "ElicitResult", "T any", "T"),
    ] {
        let Some(named) = ir.types.iter().find(|t| t.name == source) else {
            continue;
        };
        let Some(properties) = crate::ergonomics::object_fields(ir, &named.shape) else {
            continue;
        };
        let Some(emitted) = g.objects.get(source) else {
            continue;
        };
        writeln!(
            generic_source,
            "// {public} projects {source}; unknown members are retained.\ntype {public}[{parameters}] struct {{"
        )?;
        for p in &properties {
            let field = emitted.iter().find(|f| f.wire_name == p.wire_name).unwrap();
            let substitution = match p.wire_name.as_str() {
                "value" | "content" => Some("T"),
                "provenance" if public == "Candidate" => Some("P"),
                _ => None,
            };
            let ty = if let Some(generic) = substitution {
                if p.required {
                    generic.into()
                } else {
                    format!("Optional[{generic}]")
                }
            } else if p.required {
                field.field_type.clone()
            } else {
                format!("Optional[{}]", field.field_type)
            };
            writeln!(
                generic_source,
                "{} {ty} `json:{:?}`",
                field.field_name, p.wire_name
            )?;
        }
        generic_source
            .push_str("AdditionalProperties map[string]json.RawMessage `json:\"-\"`\n}\n");
        writeln!(
            generic_source,
            "func (value {public}[{arguments}]) MarshalJSON() ([]byte,error) {{ fields := make(map[string]json.RawMessage,len(value.AdditionalProperties)+{}); for k,v := range value.AdditionalProperties {{ fields[k]=v }}",
            properties.len()
        )?;
        for p in &properties {
            let field = &emitted
                .iter()
                .find(|f| f.wire_name == p.wire_name)
                .unwrap()
                .field_name;
            if p.required {
                writeln!(
                    generic_source,
                    "{{ raw,err:=json.Marshal(value.{field}); if err!=nil {{return nil,err}}; fields[{:?}]=raw }}",
                    p.wire_name
                )?;
            } else {
                writeln!(
                    generic_source,
                    "if value.{field}.Present {{ raw,err:=json.Marshal(value.{field}.Value); if err!=nil {{return nil,err}}; fields[{:?}]=raw }} else {{delete(fields,{:?})}}",
                    p.wire_name, p.wire_name
                )?;
            }
        }
        generic_source.push_str("return json.Marshal(fields)\n}\n");
        writeln!(
            generic_source,
            "func (value *{public}[{arguments}]) UnmarshalJSON(data []byte) error {{ var fields map[string]json.RawMessage; if err:=json.Unmarshal(data,&fields); err!=nil {{return err}}; var decoded {public}[{arguments}]"
        )?;
        for p in &properties {
            let field = &emitted
                .iter()
                .find(|f| f.wire_name == p.wire_name)
                .unwrap()
                .field_name;
            if p.required {
                writeln!(
                    generic_source,
                    "if raw,ok:=fields[{:?}]; ok {{if err:=json.Unmarshal(raw,&decoded.{field});err!=nil {{return err}};delete(fields,{:?})}} else {{return fmt.Errorf(\"missing required property: %s\",{:?})}}",
                    p.wire_name, p.wire_name, p.wire_name
                )?;
            } else {
                writeln!(
                    generic_source,
                    "if raw,ok:=fields[{:?}]; ok {{decoded.{field}.Present=true; if err:=json.Unmarshal(raw,&decoded.{field}.Value);err!=nil {{return err}};delete(fields,{:?})}}",
                    p.wire_name, p.wire_name
                )?;
            }
        }
        generic_source
            .push_str("decoded.AdditionalProperties=fields; *value=decoded; return nil\n}\n");
    }
    files.insert("contract_projections_generated.go".into(), generic_source);

    for (package, mut body) in packages {
        if body.contains("time.Duration") {
            body.push_str(duration_helper);
        }
        let mut imports = if body.contains("ahp.") {
            format!("import ahp {MODULE:?}\n")
        } else {
            String::new()
        };
        if body.contains("json.") {
            imports.push_str("import \"encoding/json\"\n");
        }
        if body.contains("strconv.") && !body.contains("time.") {
            imports.push_str("import \"strconv\"\n");
        }
        if body.contains("time.") {
            imports.push_str(
                "import \"time\"\nimport \"fmt\"\nimport \"strconv\"\nimport \"strings\"\n",
            );
        }
        if body.contains("fmt.") && !body.contains("time.") {
            imports.push_str("import \"fmt\"\n");
        }
        if body.contains("strings.") && !body.contains("time.") {
            imports.push_str("import \"strings\"\n");
        }
        if body.contains("mime.") {
            imports.push_str("import \"mime\"\n");
        }
        if body.contains("atomic.") {
            imports.push_str("import \"sync/atomic\"\n");
        }
        if body.contains("content.Source") {
            writeln!(imports, "import \"{MODULE}/content\"")?;
        }
        if body.contains("permission.Permission") {
            writeln!(imports, "import \"{MODULE}/permission\"")?;
        }
        if body.contains("tool.Input") {
            writeln!(imports, "import \"{MODULE}/tool\"")?;
        }
        let package_name = package.rsplit('/').next().unwrap();
        if body.contains("event.") {
            writeln!(imports, "import \"{MODULE}/event\"")?;
        }
        files.insert(format!("{package}/generated.go"), format!("// Code generated by ahp-codegen. DO NOT EDIT.\npackage {package_name}\n{imports}\n{body}"));
    }
    files.insert("client/boundaries_generated.go".into(), format!("// Code generated by ahp-codegen. DO NOT EDIT.\npackage client\nimport \"context\"\nimport \"{MODULE}/event\"\n{methods}"));
    files.insert(
        "facade_generated_test.go".into(),
        include_str!("../../../tests/go-facade_test.go").into(),
    );
    files.insert(
        "client/facade_generated_test.go".into(),
        include_str!("../../../tests/go-client-facade_test.go").into(),
    );
    Ok(files)
}

pub(crate) fn role(name: &str) -> Option<(&'static str, String)> {
    let (package, short) = if name == "Registration" {
        ("registration", "".into())
    } else if name == "Backend" {
        ("registration", "Backend".into())
    } else if name.ends_with("Transport") {
        ("transport", name.trim_end_matches("Transport").into())
    } else if name.ends_with("Subscription") {
        ("subscription", name.trim_end_matches("Subscription").into())
    } else if name.ends_with("Effect") {
        ("effect", name.trim_end_matches("Effect").into())
    } else if name.starts_with("Content") {
        ("content", name.trim_start_matches("Content").into())
    } else {
        return None;
    };
    Some((package, short))
}

fn literal(g: &Generator<'_>, shape: &Shape) -> Option<serde_json::Value> {
    literal_visited(g, shape, &BTreeSet::new())
}

fn literal_visited(
    g: &Generator<'_>,
    shape: &Shape,
    visited: &BTreeSet<String>,
) -> Option<serde_json::Value> {
    match shape {
        Shape::Literal { value } => Some(value.clone()),
        Shape::Ref { name } => {
            let mut visited = visited.clone();
            if !visited.insert(name.clone()) {
                return None;
            }
            g.ir.types
                .iter()
                .find(|n| n.name == *name)
                .and_then(|n| literal_visited(g, &n.shape, &visited))
        }
        _ => None,
    }
}

fn qualify(ty: &str) -> String {
    let mut out = String::new();
    let mut word = String::new();
    for c in ty.chars().chain(std::iter::once(' ')) {
        if c.is_alphanumeric() || c == '_' {
            word.push(c);
            continue;
        }
        if !word.is_empty() {
            if word.chars().next().unwrap().is_uppercase() && !out.ends_with('.') {
                out.push_str("ahp.");
            }
            out.push_str(&word);
            word.clear();
        }
        out.push(c);
    }
    out.trim_end().to_owned()
}

// Open string-union collections have an ergonomic string route while still
// selecting the exact union arm allocated by the wire emitter.
fn string_union_list(g: &Generator<'_>, field: &RenderedField, argument: &str) -> Option<String> {
    let item = field.field_type.strip_prefix("[]")?;
    let (arm, ty) = g.unions.get(item)?.iter().find(|(_, ty)| ty == "string")?;
    Some(format!(
        "func() []ahp.{item} {{ items := make([]ahp.{item}, len({argument})); for i, value := range {argument} {{ items[i] = ahp.{item}{{{arm}: ahp.Optional[{ty}]{{Present:true, Value:value}}}} }}; return items }}()"
    ))
}

// Constraints such as anyOf validate combinations; only object declarations
// define helper arguments. Never turn predicate-branch constants into grants.
pub(super) fn capability_queries(g: &Generator<'_>, out: &mut String) -> Result<()> {
    let Some(fields) = g.objects.get("Capabilities") else {
        return Ok(());
    };
    let effects = fields
        .iter()
        .find(|f| f.wire_name == "effects")
        .expect("capability effects");
    out.push_str("// EffectName identifies an advertised effect family, including extension names.\ntype EffectName string\n\n");
    for name in enum_strings(g, &effects.shape) {
        writeln!(
            out,
            "const EffectName{} EffectName = {name:?}",
            go_identifier(&name)
        )?;
    }
    out.push_str("\n// EffectDeny is the shorthand family identifier for deny.\nconst EffectDeny = EffectNameDeny\n");
    for model in ["Capabilities", "InterceptRequestParamsCapabilities"] {
        let Some(fields) = g.objects.get(model) else {
            continue;
        };
        let effects = fields
            .iter()
            .find(|f| f.wire_name == "effects")
            .expect("capability effects");
        let item = effects.field_type.strip_prefix("[]").expect("effect list");
        let arms = &g.unions[item];
        let (custom, _) = arms
            .iter()
            .find(|(_, ty)| ty == "string")
            .expect("custom effect arm");
        let (known, _) = arms
            .iter()
            .find(|(_, ty)| ty != "string")
            .expect("known effect arm");
        writeln!(
            out,
            "\n// Supports reports advertised effect-family membership only.\n// It does not grant execution permission or check target, operation, mode, or\n// per-call restrictions. Nested grants alone never imply family support.\nfunc (value {model}) Supports(effect EffectName) bool {{\n\tfor _, item := range value.Effects {{\n\t\tif item.{known}.Present {{\n\t\t\tif string(item.{known}.Value) == string(effect) {{\n\t\t\t\treturn true\n\t\t\t}}\n\t\t}} else if item.{custom}.Present && item.{custom}.Value == string(effect) {{\n\t\t\treturn true\n\t\t}}\n\t}}\n\treturn false\n}}"
        )?;
    }
    out.push('\n');
    Ok(())
}

// Functional declarations allocate fresh wire models for every composition.
fn state_helpers(g: &Generator<'_>, packages: &mut BTreeMap<String, String>) -> Result<()> {
    let name = "InterceptRequestParamsState";
    let fields = &g.objects[name];
    let permission = fields.iter().find(|f| f.wire_name == "permission").unwrap();
    let mut body = format!("type Permission = ahp.{}\n", permission.field_type);
    for value in enum_strings(g, &permission.shape) {
        writeln!(
            body,
            "const {} Permission = {value:?}",
            go_identifier(&value)
        )?;
    }
    packages.insert("permission".into(), body);
    let mut body = String::new();
    constructor(g, &mut body, name, "", fields)?;
    let candidate_type = &fields
        .iter()
        .find(|f| f.wire_name == "candidate")
        .unwrap()
        .field_type;
    let object_type = &g.nullables[candidate_type];
    let provenance_type = &g.objects[object_type]
        .iter()
        .find(|f| f.wire_name == "provenance")
        .unwrap()
        .field_type;
    body.push_str(&r#"
// Initial represents a native decision already made for this occurrence, not authorization.
func Initial(value permission.Permission, opts ...Option) *ahp.InterceptRequestParamsState {
    v := &ahp.InterceptRequestParamsState{Permission: value, Candidate: NoCandidate()}
    for _, opt := range opts { if opt != nil { opt(v) } }; return v
}
func NoCandidate() ahp.Nullable[ahp.$OBJECT_TYPE] {
    return ahp.Null[ahp.$OBJECT_TYPE]()
}
// Candidate preserves payload encoding errors instead of silently dropping the value.
func Candidate[T any](value T, provenance ...ahp.$PROVENANCE_TYPE) (ahp.Nullable[ahp.$OBJECT_TYPE], error) {
    if len(provenance) > 1 { return ahp.Nullable[ahp.$OBJECT_TYPE]{}, fmt.Errorf("candidate accepts at most one provenance") }
    raw, err := json.Marshal(value); if err != nil { return ahp.Nullable[ahp.$OBJECT_TYPE]{}, err }
    candidate := ahp.$OBJECT_TYPE{Value: raw}
    if len(provenance) == 1 { candidate.Provenance = ahp.Some(provenance[0]) }
    return ahp.NonNull(candidate), nil
}
func WithCandidate(value ahp.Nullable[ahp.$OBJECT_TYPE]) Option { return func(v *ahp.InterceptRequestParamsState) { v.Candidate = value } }
"#.replace("$PROVENANCE_TYPE", provenance_type).replace("$OBJECT_TYPE", object_type));
    packages.insert("state".into(), body);
    Ok(())
}

fn selector_value(g: &Generator<'_>, field: &RenderedField, value: &str) -> Result<String> {
    let ty = field.field_type.trim_start_matches('*');
    if let Some(arms) = g.unions.get(ty) {
        let label = go_identifier(value);
        let (arm, arm_type) = arms
            .iter()
            .find(|(arm, _)| *arm == label)
            .ok_or_else(|| anyhow::anyhow!("missing selector arm {ty}.{label}"))?;
        Ok(format!(
            "{}{}{{{arm}: ahp.Some({}({value:?}))}}",
            if field.field_type.starts_with('*') {
                "&"
            } else {
                ""
            },
            qualify(ty),
            qualify(arm_type)
        ))
    } else {
        Ok(format!("{}({value:?})", qualify(&field.field_type)))
    }
}

fn effect_operations(g: &Generator<'_>, out: &mut String) -> Result<()> {
    for (arm, ty) in &g.unions["Effect"] {
        let pointer = if ty.starts_with('*') { "&" } else { "" };
        let ty = ty.trim_start_matches('*');
        let Some(fields) = g.objects.get(ty) else {
            continue;
        };
        let Some(tag) = fields
            .iter()
            .find(|f| f.wire_name == "type")
            .and_then(|f| literal(g, &f.shape))
            .and_then(|v| v.as_str().map(str::to_owned))
        else {
            continue;
        };
        if tag == "return" && (ty == "EffectReturn" || ty == "ReturnToolEffect") {
            let short = if ty == "ReturnToolEffect" {
                "ReturnTool"
            } else {
                "Return"
            };
            writeln!(
                out,
                "func Return[T any](value T, opts ...{short}Option) (*ahp.Effect, error) {{ raw, err := json.Marshal(value); if err != nil {{ return nil, err }}; return New{short}(raw, opts...), nil }}"
            )?;
        }
        if tag == "inject" {
            let short = if ty == "InjectEffect" {
                "Inject"
            } else {
                "InjectAppend"
            };
            let deliver = fields.iter().find(|f| f.wire_name == "deliverAt").unwrap();
            writeln!(out, "type DeliverAt = {}", qualify(&deliver.field_type))?;
            for value in enum_strings(g, &deliver.shape) {
                writeln!(out, "const {} DeliverAt = {value:?}", go_identifier(&value))?;
            }
            let value_type = qualify(
                &fields
                    .iter()
                    .find(|f| f.wire_name == "value")
                    .unwrap()
                    .field_type,
            );
            writeln!(
                out,
                "func InjectContextAppend[T any](deliverAt DeliverAt, value T, opts ...{short}Option) (*ahp.Effect, error) {{ raw, err := json.Marshal(value); if err != nil {{ return nil, err }}; var canonical {value_type}; if err := json.Unmarshal(raw, &canonical); err != nil {{ return nil, err }}; return New{short}(deliverAt, canonical, opts...), nil }}"
            )?;
        }
        if tag != "modify" {
            continue;
        }
        let target = fields.iter().find(|f| f.wire_name == "target").unwrap();
        let operation = fields.iter().find(|f| f.wire_name == "operation").unwrap();
        let value = fields.iter().find(|f| f.wire_name == "value").unwrap();
        let value_type = qualify(&value.field_type);
        for target_value in enum_strings(g, &target.shape) {
            let helper = crate::ergonomics::modify_helper_target(&target_value, &value.shape);
            for operation_value in enum_strings(g, &operation.shape) {
                let name = format!(
                    "Modify{}{}",
                    go_identifier(&helper),
                    go_identifier(&operation_value)
                );
                let target_value_expression = selector_value(g, target, &target_value)?;
                let operation_value_expression = selector_value(g, operation, &operation_value)?;
                if target_value == "input" {
                    // Host tool payloads remain generic. Decode into the schema-owned
                    // object value, then validate its complete canonical effect model.
                    writeln!(
                        out,
                        "func {name}[T any](value T) (*ahp.Effect, error) {{ raw,err:=json.Marshal(value); if err!=nil {{return nil,err}}; var canonical {value_type}; if err:=json.Unmarshal(raw,&canonical);err!=nil {{return nil,err}}; encoded,err:=json.Marshal({name}Value(canonical));if err!=nil {{return nil,err}}; var validated ahp.{ty}; if err:=json.Unmarshal(encoded,&validated);err!=nil {{return nil,err}}; return &ahp.Effect{{{arm}:ahp.Some({pointer}validated)}},nil }}"
                    )?;
                } else {
                    writeln!(
                        out,
                        "func {name}(value {value_type}) (*ahp.Effect, error) {{ return &ahp.Effect{{{arm}: ahp.Some({pointer}ahp.{ty}{{Type: {tag:?}, Target: {target_value_expression}, Operation: {operation_value_expression}, Value: value}})}}, nil }}"
                    )?;
                }
                writeln!(
                    out,
                    "func {name}Value(value {value_type}) *ahp.{ty} {{ return &ahp.{ty}{{Type: {tag:?}, Target: {target_value_expression}, Operation: {operation_value_expression}, Value: value}} }}"
                )?;
            }
        }
    }
    Ok(())
}

fn projection_name(wire: &str) -> String {
    match wire {
        "FileChangedEventChangesItem" => "FileChangeInput".into(),
        "ExecutionEventFilechange" => "ToolFileChangeInput".into(),
        "UserMessageInboundEventMessage" => "InboundMessageInput".into(),
        "UserMessageInboundEventMessageMessagesItem" => "InboundMessageItemInput".into(),
        "UserMessageOutboundEventMessage" => "OutboundMessageInput".into(),
        "UserMessageOutboundEventMessageMessagesItem" => "OutboundMessageItemInput".into(),
        _ => format!("{wire}Input"),
    }
}

const INPUT_SOURCE_EXTRACTOR: &str = r#"
// toolBeforeEnvelope is internal: host-defined types cannot provide owned sources.
type toolBeforeEnvelope struct { input any; sources map[string]*content.Source }
func (v toolBeforeEnvelope) MarshalJSON() ([]byte, error) { return json.Marshal(v.input) }
// PrepareToolBefore keeps generic host arguments intact for serialization.
func PrepareToolBefore[T any](v event.ToolBeforeInput[T]) any { return toolBeforeEnvelope{input:v, sources:ExtractToolBefore(v)} }
func extractContentPartInput(v *event.ContentPartInput, path string, sources map[string]*content.Source) {
    if v.Wire == nil && v.Text == nil && v.Attachment != nil && v.Attachment.Body != nil { sources[path] = v.Attachment.Body }
}
"#;

fn input_identifier(name: &str) -> String {
    let name = go_identifier(name).replace("Mcp", "MCP");
    if let Some(prefix) = name.strip_suffix("Id") {
        format!("{prefix}ID")
    } else {
        name
    }
}

// Host projections are generated only along schema-owned ContentItem slots.
// Unrelated raw payloads retain their wire types and are never scanned.
fn host_projection(
    g: &Generator<'_>,
    out: &mut String,
    sources: &mut String,
    ty: &str,
    paths: &[Vec<String>],
) -> Result<String> {
    if let Some(item) = ty.strip_prefix("[]") {
        let paths = paths
            .iter()
            .filter(|p| p.first().map(String::as_str) == Some("*"))
            .map(|p| p[1..].to_vec())
            .collect::<Vec<_>>();
        return Ok(format!(
            "[]{}",
            host_projection(g, out, sources, item, &paths)?
        ));
    }
    let wire = ty.trim_start_matches('*');
    if wire == "ContentItem" {
        return Ok("*ContentPartInput".into());
    }
    let host = projection_name(wire);
    if !out.contains(&format!("type {host} struct {{")) {
        let fields = g
            .objects
            .get(wire)
            .ok_or_else(|| anyhow::anyhow!("unresolved Go host content projection {wire}"))?;
        let mut children = Vec::new();
        for field in fields {
            let tails = paths
                .iter()
                .filter(|p| p.first() == Some(&field.wire_name))
                .map(|p| p[1..].to_vec())
                .collect::<Vec<_>>();
            if tails.is_empty() {
                continue;
            }
            let child = host_projection(g, out, sources, &field.field_type, &tails)?;
            children.push((field, child));
        }
        writeln!(
            out,
            "// {host} preserves wire metadata and projects only schema-owned content children.\ntype {host} struct {{ ahp.{wire}"
        )?;
        for (field, child) in &children {
            let child = if field.required {
                child.clone()
            } else {
                format!("ahp.Optional[{child}]")
            };
            writeln!(out, "{} {child}", field.field_name)?;
        }
        writeln!(out, "}}\nfunc (v *{host}) MarshalJSON() ([]byte, error) {{")?;
        if fields.iter().any(|f| f.wire_name == "role")
            && fields.iter().any(|f| f.wire_name == "parts")
        {
            writeln!(
                out,
                "switch string(v.Role) {{ case \"system\", \"developer\", \"user\", \"assistant\", \"tool\": default: return nil, fmt.Errorf(\"host canonical message requires a canonical role\") }}"
            )?;
        }
        if fields.iter().any(|f| f.wire_name == "id") {
            writeln!(
                out,
                "if err := ensureContentIdentity(&v.ID, &v.Synthesized); err != nil {{ return nil, err }}"
            )?;
        }
        writeln!(
            out,
            "base := v.{wire}; raw, err := json.Marshal(base); if err != nil {{ return nil, err }}; fields := map[string]json.RawMessage{{}}; if err := json.Unmarshal(raw, &fields); err != nil {{ return nil, err }}"
        )?;
        for (field, child) in &children {
            if !field.required {
                writeln!(out, "if v.{}.Present {{", field.field_name)?;
            }
            let access = if field.required { "" } else { ".Value" };
            emit_host_placement(
                g,
                out,
                child,
                &format!("v.{}{access}", field.field_name),
                &field.shape,
                0,
            )?;
            let reference = if child.starts_with('*') || child.starts_with("[]") {
                ""
            } else {
                "&"
            };
            writeln!(
                out,
                "{{ raw, err := json.Marshal({reference}v.{}{access}); if err != nil {{ return nil, err }}; fields[{:?}] = raw }}",
                field.field_name, field.wire_name
            )?;
            if !field.required {
                writeln!(out, "}}")?;
            }
        }
        writeln!(
            out,
            "projected, err := json.Marshal(fields); if err != nil {{ return nil, err }}; var canonical ahp.{wire}; if err := json.Unmarshal(projected, &canonical); err != nil {{ return nil, fmt.Errorf(\"invalid host {host}: %w\", err) }}; return projected, nil\n}}"
        )?;
        writeln!(
            sources,
            "func extract{host}(v *event.{host}, path string, sources map[string]*content.Source) {{"
        )?;
        for (field, child) in &children {
            if !field.required {
                writeln!(sources, "if v.{}.Present {{", field.field_name)?;
            }
            let access = if field.required { "" } else { ".Value" };
            let segment = field.wire_name.replace('~', "~0").replace('/', "~1");
            emit_host_sources(
                sources,
                child,
                &format!("v.{}{access}", field.field_name),
                &format!("path + {:?}", format!("/{segment}")),
                0,
            )?;
            if !field.required {
                writeln!(sources, "}}")?;
            }
        }
        writeln!(sources, "}}")?;
    }
    Ok(format!(
        "{}{host}",
        if ty.starts_with('*') { "*" } else { "" }
    ))
}

// Nullability is a schema fact, not an Optional presence flag.
fn host_allows_null(g: &Generator<'_>, shape: &Shape, seen: &mut BTreeSet<String>) -> bool {
    match shape {
        Shape::Null | Shape::Any => true,
        Shape::Literal { value } => value.is_null(),
        Shape::Union { variants, .. } => variants.iter().any(|s| host_allows_null(g, s, seen)),
        Shape::Intersection { variants } => variants.iter().all(|s| host_allows_null(g, s, seen)),
        Shape::Ref { name } => {
            if !seen.insert(name.clone()) {
                return false;
            }
            let nullable =
                g.ir.types
                    .iter()
                    .find(|n| n.name == *name)
                    .map(|n| host_allows_null(g, &n.shape, seen))
                    .unwrap_or(false);
            seen.remove(name);
            nullable
        }
        _ => false,
    }
}

fn host_array_item<'a>(
    g: &'a Generator<'_>,
    shape: &'a Shape,
    seen: &mut BTreeSet<String>,
) -> Option<&'a Shape> {
    match shape {
        Shape::Array { items } => Some(items),
        Shape::Union { variants, .. } | Shape::Intersection { variants } => {
            variants.iter().find_map(|s| host_array_item(g, s, seen))
        }
        Shape::Ref { name } => {
            if !seen.insert(name.clone()) {
                return None;
            }
            let item =
                g.ir.types
                    .iter()
                    .find(|n| n.name == *name)
                    .and_then(|n| host_array_item(g, &n.shape, seen));
            seen.remove(name);
            item
        }
        _ => None,
    }
}

// Host arrays are canonical data, not sparse parallel source-binding arrays.
fn emit_host_placement(
    g: &Generator<'_>,
    out: &mut String,
    ty: &str,
    value: &str,
    shape: &Shape,
    depth: usize,
) -> Result<()> {
    if (ty.starts_with("[]") || ty.starts_with('*'))
        && !host_allows_null(g, shape, &mut BTreeSet::new())
    {
        writeln!(
            out,
            "if {value} == nil {{ return nil, fmt.Errorf(\"host content placement must not be null\") }}"
        )?;
    }
    if let Some(item_ty) = ty.strip_prefix("[]") {
        if !item_ty.starts_with('*') && !item_ty.starts_with("[]") {
            return Ok(());
        }
        let item_shape = host_array_item(g, shape, &mut BTreeSet::new())
            .ok_or_else(|| anyhow::anyhow!("unresolved host array item {ty}"))?;
        writeln!(out, "for placementIndex{depth} := range {value} {{")?;
        emit_host_placement(
            g,
            out,
            item_ty,
            &format!("{value}[placementIndex{depth}]"),
            item_shape,
            depth + 1,
        )?;
        writeln!(out, "}}")?;
    }
    Ok(())
}

fn emit_host_sources(
    out: &mut String,
    ty: &str,
    value: &str,
    path: &str,
    depth: usize,
) -> Result<()> {
    if let Some(item) = ty.strip_prefix("[]") {
        writeln!(out, "for hostIndex{depth} := range {value} {{")?;
        emit_host_sources(
            out,
            item,
            &format!("{value}[hostIndex{depth}]"),
            &format!("{path} + \"/\" + strconv.Itoa(hostIndex{depth})"),
            depth + 1,
        )?;
        writeln!(out, "}}")?;
    } else {
        if ty.starts_with('*') {
            writeln!(out, "if {value} != nil {{")?;
        }
        let name = ty.trim_start_matches('*');
        let value = if ty.starts_with('*') {
            value.to_owned()
        } else {
            format!("&({value})")
        };
        writeln!(out, "extract{name}({value}, {path}, sources)")?;
        if ty.starts_with('*') {
            writeln!(out, "}}")?;
        }
    }
    Ok(())
}

const OWNED_CONTENT_INPUTS: &str = r#"
// ContentPartInput accepts exactly one wire, inline text, or owned attachment part.
// Wire preserves all advanced gap/metadata/omitted and future alternatives.
type ContentPartInput struct {
    Wire *ahp.ContentItem
    Text *ahp.TextBodyPart
    Attachment *AttachmentBodyInput
}
// AttachmentBodyInput embeds wire metadata, but Body is an owned host handle.
// Descriptor is optional; without one, encoding emits a pending descriptor.
// The runtime replaces pending descriptors using internal/inputsources before validation/send.
// Encoding and extraction never read, close, or upload Body.
type AttachmentBodyInput struct {
    ahp.AttachmentBodyPart
    Body *content.Source
    Descriptor *ahp.ContentReference
}
var synthesizedContentSequence atomic.Uint64
// Missing IDs are assigned once to the host object during encoding or planning.
// Inputs must not be mutated or encoded concurrently; supplied identities are unchanged.
func synthesizeContentID() string { return "host-content-" + strconv.FormatUint(synthesizedContentSequence.Add(1), 10) }
func ensureContentIdentity(id *string, synthesized *ahp.Optional[bool]) error {
    if *id != "" { return nil }
    if synthesized.Present && !synthesized.Value { return fmt.Errorf("missing host content id conflicts with synthesized=false") }
    *id = synthesizeContentID()
    *synthesized = ahp.Some(true)
    return nil
}
func validateContentCategory(category ahp.Optional[string]) error {
    if category.Present && category.Value == "" { return fmt.Errorf("host content category must be nonempty when supplied") }
    return nil
}
// Validate only pure projected JSON, never the local source handle. The arm
// check prevents malformed known alternatives from falling back to Unknown.
func marshalHostContentPart(part any, text bool) ([]byte, error) {
    raw, err := json.Marshal(part)
    if err != nil { return nil, err }
    var canonical ahp.ContentItem
    if err := json.Unmarshal(raw, &canonical); err != nil { return nil, fmt.Errorf("invalid host content part: %w", err) }
    if (text && !canonical.TextBodyPart.Present) || (!text && !canonical.AttachmentBodyPart.Present) { return nil, fmt.Errorf("host content projection must remain its canonical body alternative") }
    return raw, nil
}
func validateAttachmentBody(part ahp.AttachmentBodyPart) error {
    if part.Kind != "" && part.Kind != "attachment" { return fmt.Errorf("attachment host input requires attachment kind") }
    if err := validateContentCategory(part.Category); err != nil { return err }
    if part.Selection != "" && part.Selection != "body" { return fmt.Errorf("attachment host input requires body selection") }
    mediaType, _, err := mime.ParseMediaType(part.MediaType)
    if err != nil || mediaType == "" { return fmt.Errorf("attachment host input requires a valid nonempty mediaType") }
    subtype := strings.SplitN(mediaType, "/", 2)
    if len(subtype) != 2 || strings.HasPrefix(mediaType, "text/") || subtype[1] == "json" || strings.HasSuffix(subtype[1], "+json") { return fmt.Errorf("text and JSON content require inline text parts") }
    return nil
}
func (v ContentPartInput) MarshalJSON() ([]byte, error) {
    count := 0
    if v.Wire != nil { count++ }; if v.Text != nil { count++ }; if v.Attachment != nil { count++ }
    if count != 1 { return nil, fmt.Errorf("ContentPartInput requires exactly one alternative") }
    if v.Wire != nil { return json.Marshal(v.Wire) }
    if v.Text != nil {
        if v.Text.Kind != "" && v.Text.Kind != "text" { return nil, fmt.Errorf("text host input requires text kind") }
        if v.Text.MediaType != "" && v.Text.MediaType != "text/plain" { return nil, fmt.Errorf("text host input requires text/plain mediaType") }
        if err := validateContentCategory(v.Text.Category); err != nil { return nil, err }
        if v.Text.Selection != "" && v.Text.Selection != "body" { return nil, fmt.Errorf("text host input requires body selection") }
        if err := ensureContentIdentity(&v.Text.ID, &v.Text.Synthesized); err != nil { return nil, err }
        part := *v.Text
        if part.Kind == "" { part.Kind = "text" }
        if part.MediaType == "" { part.MediaType = "text/plain" }
        if part.Selection == "" { part.Selection = "body" }
        return marshalHostContentPart(part, true)
    }
    if err := validateAttachmentBody(v.Attachment.AttachmentBodyPart); err != nil { return nil, err }
    if err := ensureContentIdentity(&v.Attachment.ID, &v.Attachment.Synthesized); err != nil { return nil, err }
    part := v.Attachment.AttachmentBodyPart
    if part.Kind == "" { part.Kind = "attachment" }
    if part.Selection == "" { part.Selection = "body" }
    if v.Attachment.Body != nil {
        part.Body = v.Attachment.Descriptor
        if part.Body == nil { part.Body = v.Attachment.AttachmentBodyPart.Body }
        if part.Body == nil { part.Body = &ahp.ContentReference{Ref: "ahp:owned:pending"} }
    } else if v.Attachment.Descriptor != nil { part.Body = v.Attachment.Descriptor }
    return marshalHostContentPart(part, false)
}
"#;

fn input_projection(
    g: &Generator<'_>,
    out: &mut String,
    sources: &mut String,
    name: &str,
    fields: &[RenderedField],
    generic: bool,
) -> Result<()> {
    let properties = fields
        .iter()
        .map(|f| Property {
            wire_name: f.wire_name.clone(),
            required: f.required,
            shape: f.shape.clone(),
            constructor_default: f.constructor_default.clone(),
        })
        .collect::<Vec<_>>();
    let tag = fields
        .iter()
        .find(|f| f.wire_name == "type")
        .and_then(|f| literal(g, &f.shape))
        .and_then(|v| v.as_str().map(str::to_owned))
        .unwrap_or_default();
    let projected = crate::ergonomics::input_fields(g.ir, &properties, &tag)?;
    let slots = crate::ergonomics::content_slots(g.ir, &projected);
    let mut host_fields = BTreeMap::new();
    for field in &projected {
        let paths = slots
            .iter()
            .filter(|s| s.path.starts_with(&field.path))
            .map(|s| s.path[field.path.len()..].to_vec())
            .collect::<Vec<_>>();
        if paths.is_empty() {
            continue;
        }
        let mut current = fields;
        let mut resolved = None;
        for segment in &field.path {
            let f = current.iter().find(|f| f.wire_name == *segment).unwrap();
            resolved = Some(f);
            current = g
                .objects
                .get(f.field_type.trim_start_matches('*'))
                .map(Vec::as_slice)
                .unwrap_or(&[]);
        }
        let ty = host_projection(g, out, sources, &resolved.unwrap().field_type, &paths)?;
        host_fields.insert(field.property.wire_name.clone(), ty);
    }
    let params = if generic { "[T any]" } else { "" };
    let use_params = if generic { "[T]" } else { "" };
    writeln!(out, "type {name}{params} struct {{")?;
    for field in &projected {
        let mut current = fields;
        let mut resolved = None;
        for segment in &field.path {
            let f = current
                .iter()
                .find(|f| f.wire_name == *segment)
                .ok_or_else(|| anyhow::anyhow!("unresolved Go input path {:?}", field.path))?;
            resolved = Some(f);
            current = g
                .objects
                .get(f.field_type.trim_start_matches('*'))
                .map(Vec::as_slice)
                .unwrap_or(&[]);
        }
        let ty = if generic && field.path == ["tool", "input"] {
            "T".into()
        } else {
            qualify(&resolved.unwrap().field_type)
        };
        let ty = if field.property.required {
            ty
        } else {
            format!("ahp.Optional[{ty}]")
        };
        writeln!(out, "{} {ty}", input_identifier(&field.property.wire_name))?;
    }
    for (field, ty) in &host_fields {
        writeln!(
            out,
            "// {}Input overrides the wire field when non-nil; embeds owned sources without reading them.",
            input_identifier(field)
        )?;
        writeln!(out, "{}Input *{ty} `json:\"-\"`", input_identifier(field))?;
    }
    writeln!(out, "}}")?;
    if generic {
        writeln!(
            sources,
            "// ExtractToolBefore preserves typed host payloads while extracting only declared content slots.\nfunc ExtractToolBefore[T any](v event.{name}{use_params}) map[string]*content.Source {{ sources := map[string]*content.Source{{}}"
        )?;
    } else {
        writeln!(
            sources,
            "func extract{name}(v event.{name}, sources map[string]*content.Source) {{"
        )?;
    }
    for field in &projected {
        if let Some(ty) = host_fields.get(&field.property.wire_name) {
            let field_name = input_identifier(&field.property.wire_name);
            let path = format!("/{}", field.path.join("/"));
            writeln!(sources, "if v.{field_name}Input != nil {{")?;
            emit_host_sources(
                sources,
                ty,
                &format!("(*v.{field_name}Input)"),
                &format!("{path:?}"),
                0,
            )?;
            writeln!(sources, "}}")?;
        }
    }
    if generic {
        writeln!(sources, "return sources")?;
    }
    writeln!(sources, "}}")?;
    writeln!(
        out,
        "func (v {name}{use_params}) MarshalJSON() ([]byte, error) {{ fields := map[string]any{{}}"
    )?;
    for field in &projected {
        let name = input_identifier(&field.property.wire_name);
        writeln!(out, "{{")?;
        if !field.property.required {
            writeln!(out, "if v.{name}.Present {{")?;
        }
        let mut parent = "fields".to_owned();
        for (depth, segment) in field.path.iter().enumerate().take(field.path.len() - 1) {
            let variable = format!("nested{depth}");
            writeln!(
                out,
                "{variable}, ok := {parent}[{segment:?}].(map[string]any); if !ok {{ {variable} = map[string]any{{}}; {parent}[{segment:?}] = {variable} }}"
            )?;
            parent = variable;
        }
        let access = if field.property.required {
            ""
        } else {
            ".Value"
        };
        writeln!(
            out,
            "{parent}[{:?}] = v.{name}{access}",
            field.path.last().unwrap()
        )?;
        if !field.property.required {
            writeln!(out, "}}")?;
        }
        if host_fields.contains_key(&field.property.wire_name) {
            writeln!(out, "if v.{name}Input != nil {{")?;
            emit_host_placement(
                g,
                out,
                &host_fields[&field.property.wire_name],
                &format!("(*v.{name}Input)"),
                &field.property.shape,
                0,
            )?;
            let mut parent = "fields".to_owned();
            for (depth, segment) in field.path.iter().enumerate().take(field.path.len() - 1) {
                let variable = format!("hostNested{depth}");
                writeln!(
                    out,
                    "{variable}, ok := {parent}[{segment:?}].(map[string]any); if !ok {{ {variable} = map[string]any{{}}; {parent}[{segment:?}] = {variable} }}"
                )?;
                parent = variable;
            }
            writeln!(
                out,
                "{parent}[{:?}] = v.{name}Input",
                field.path.last().unwrap()
            )?;
            writeln!(out, "}}")?;
        }
        writeln!(out, "}}")?;
    }
    writeln!(out, "return json.Marshal(fields)\n}}")?;
    Ok(())
}

fn constructor(
    g: &Generator<'_>,
    out: &mut String,
    name: &str,
    short: &str,
    fields: &[RenderedField],
) -> Result<()> {
    let option = format!("{short}Option");
    writeln!(out, "type {option} func(*ahp.{name})")?;
    for f in fields
        .iter()
        .filter(|f| !f.required || f.constructor_default.is_some())
    {
        let ty = qualify(&f.field_type);
        let value = if f.required {
            "value".into()
        } else {
            format!("ahp.Optional[{ty}]{{Present: true, Value: value}}")
        };
        writeln!(
            out,
            "func With{short}{}(value {ty}) {option} {{ return func(v *ahp.{name}) {{ v.{} = {value} }} }}",
            f.field_name.replace("Mcp", "MCP"),
            f.field_name
        )?;
    }
    // Required collections with a positional first element also expose an
    // explicit replacement option. This is a collection policy, not a list of
    // backend-specific constructors.
    if name != "Registration" {
        for f in fields.iter().filter(|f| {
            f.required && f.constructor_default.is_none() && f.field_type.starts_with("[]")
        }) {
            let (item, value) = if let Some(value) = string_union_list(g, f, "values") {
                ("string".into(), value)
            } else {
                let item = qualify(f.field_type.trim_start_matches("[]"));
                (item.clone(), format!("append([]{item}{{}}, values...)"))
            };
            writeln!(
                out,
                "func With{short}{}(values ...{item}) {option} {{ return func(v *ahp.{name}) {{ v.{} = {value} }} }}",
                f.field_name, f.field_name
            )?;
        }
    }
    let mut args = Vec::new();
    let mut ordered = fields
        .iter()
        .filter(|f| f.required && f.constructor_default.is_none())
        .collect::<Vec<_>>();
    let order: &[&str] = match name {
        "Backend" => &["id", "transport", "subscriptions"],
        "InterceptSubscription" => &["events", "timeoutMs", "failurePolicy", "content"],
        _ => &[],
    };
    if !order.is_empty() {
        ordered.sort_by_key(|f| {
            order
                .iter()
                .position(|key| *key == f.wire_name)
                .unwrap_or(usize::MAX)
        });
    }
    let mut assignments = Vec::new();
    for f in ordered {
        let value = if let Some(value) = literal(g, &f.shape) {
            let raw = serde_json::to_string(&value)?;
            if f.field_type.starts_with('*') {
                format!(
                    "func() {} {{ v := {}({raw}); return &v }}()",
                    qualify(&f.field_type),
                    qualify(f.field_type.trim_start_matches('*'))
                )
            } else {
                raw
            }
        } else {
            let arg = format!("arg{}", f.field_name);
            match (name, f.wire_name.as_str()) {
                ("Backend", "id") => {
                    args.push(format!("{arg} string"));
                    format!(
                        "func() {} {{ v := {}({arg}); return &v }}()",
                        qualify(&f.field_type),
                        qualify(f.field_type.trim_start_matches('*'))
                    )
                }
                ("Backend", "subscriptions") => {
                    args.push(format!("{arg} ahp.BackendSubscriptionsItem"));
                    format!("[]ahp.BackendSubscriptionsItem{{{arg}}}")
                }
                ("Registration", "hooks") => {
                    args.push(format!("{arg} ...*ahp.Backend"));
                    arg
                }
                _ if string_union_list(g, f, &arg).is_some() => {
                    args.push(format!("{arg} []string"));
                    string_union_list(g, f, &arg).unwrap()
                }
                _ => {
                    args.push(format!("{arg} {}", qualify(&f.field_type)));
                    arg
                }
            }
        };
        assignments.push(format!("{}: {value}", f.field_name));
    }
    for f in fields.iter().filter(|f| f.constructor_default.is_some()) {
        assignments.push(format!("{}: {}", f.field_name, constructor_default(f)?));
    }
    // Constructors for a discriminated alternative directly return its public
    // union, while options still operate on the exact concrete wire payload.
    let union = ["Effect", "BackendTransport", "BackendSubscriptionsItem"]
        .iter()
        .find_map(|u| {
            g.unions.get(*u).and_then(|arms| {
                arms.iter()
                    .find(|(_, t)| t.trim_start_matches('*') == name)
                    .map(|(arm, ty)| (*u, arm, ty))
            })
        });
    if name != "Registration" {
        args.push(format!("opts ...{option}"));
    }
    let (ret, result) = if let Some((u, arm, ty)) = union {
        let value = if ty.starts_with('*') { "v" } else { "*v" };
        let prefix = if u == "Effect" { "&" } else { "" };
        (
            format!("{prefix}ahp.{u}").replace('&', "*"),
            format!(
                "{prefix}ahp.{u}{{{arm}: ahp.Optional[{}]{{Present: true, Value: {value}}}}}",
                qualify(ty)
            ),
        )
    } else if name == "Registration" {
        (format!("ahp.{name}"), "*v".into())
    } else {
        (format!("*ahp.{name}"), "v".into())
    };
    let apply = if name == "Registration" {
        ""
    } else {
        "for _, opt := range opts { if opt != nil { opt(v) } };"
    };
    let has_timeout = fields
        .iter()
        .any(|f| f.required && f.constructor_default.is_none() && f.wire_name == "timeoutMs");
    let constructor_name = if has_timeout {
        format!("{short}Milliseconds")
    } else {
        short.to_owned()
    };
    writeln!(
        out,
        "func New{constructor_name}({}) {ret} {{ v := &ahp.{name}{{{}}}; {apply} return {result} }}",
        args.join(", "),
        assignments.join(", ")
    )?;
    if fields
        .iter()
        .any(|f| f.required && f.constructor_default.is_none() && f.wire_name == "timeoutMs")
    {
        let duration_args = args
            .iter()
            .map(|arg| arg.replace("argTimeoutMs json.Number", "argTimeout time.Duration"))
            .collect::<Vec<_>>();
        let forwarded = args
            .iter()
            .map(|arg| {
                let name = arg.split_whitespace().next().unwrap();
                if name == "opts" {
                    "opts...".to_owned()
                } else {
                    name.to_owned()
                }
            })
            .collect::<Vec<_>>();
        let automatic_forwarded = forwarded
            .iter()
            .map(|a| {
                if a == "argTimeoutMs" {
                    "durationMilliseconds(argTimeout)".into()
                } else {
                    a.clone()
                }
            })
            .collect::<Vec<_>>();
        writeln!(
            out,
            "// New{short} constructs data with an exact duration conversion; canonical validation rejects nonpositive or fractional milliseconds."
        )?;
        writeln!(
            out,
            "func New{short}({}) {ret} {{ return New{constructor_name}({}) }}",
            duration_args.join(", "),
            automatic_forwarded.join(", ")
        )?;
        writeln!(
            out,
            "// New{short}Duration checks and converts a duration before constructing the subscription."
        )?;
        writeln!(
            out,
            "func New{short}Duration({}) ({ret}, error) {{ argTimeoutMs, err := Milliseconds(argTimeout); if err != nil {{ var zero {ret}; return zero, err }}; return New{constructor_name}({}), nil }}",
            duration_args.join(", "),
            forwarded.join(", ")
        )?;
    }
    Ok(())
}

// Defaults are source expressions, never runtime decoding. Deliberately reject
// unsupported shapes at generation time rather than making constructors fallible.
fn constructor_default(field: &RenderedField) -> Result<String> {
    let default = field
        .constructor_default
        .as_ref()
        .ok_or_else(|| anyhow::anyhow!("missing constructor default for {}", field.wire_name))?;
    let unsupported = || {
        anyhow::anyhow!(
            "unsupported or incompatible constructor default for {} ({})",
            field.wire_name,
            field.field_type
        )
    };
    let scalar = match &field.shape {
        Shape::Boolean if default.is_boolean() => default.to_string(),
        Shape::String if default.is_string() => go_string(default.as_str().unwrap())?,
        Shape::Integer | Shape::BoundedInteger { .. }
            if default.as_i64().is_some() || default.as_u64().is_some() =>
        {
            go_string(&default.to_string())?
        }
        Shape::Number if default.is_number() => go_string(&default.to_string())?,
        Shape::Literal { value } if value == default => {
            scalar_default(default).ok_or_else(unsupported)??
        }
        Shape::Enum {
            values,
            open_strings,
        } if values.contains(default) || (*open_strings && default.is_string()) => {
            // Match the wire emitter's underlying scalar representation. Mixed
            // enums use raw JSON, so they are not scalar constructor defaults.
            let homogeneous = (values.iter().all(serde_json::Value::is_string)
                && default.is_string())
                || (values.iter().all(serde_json::Value::is_boolean) && default.is_boolean())
                || (values.iter().all(serde_json::Value::is_number) && default.is_number());
            anyhow::ensure!(
                homogeneous,
                "unsupported mixed-enum constructor default for {}",
                field.wire_name
            );
            scalar_default(default).ok_or_else(unsupported)??
        }
        _ => return Err(unsupported()),
    };
    let ty = qualify(&field.field_type);
    let value = format!("{ty}({scalar})");
    Ok(if field.required {
        value
    } else {
        format!("ahp.Optional[{ty}]{{Present: true, Value: {value}}}")
    })
}

fn scalar_default(value: &serde_json::Value) -> Option<Result<String>> {
    match value {
        serde_json::Value::String(value) => Some(go_string(value)),
        serde_json::Value::Bool(value) => Some(Ok(value.to_string())),
        serde_json::Value::Number(value) => Some(go_string(&value.to_string())),
        _ => None,
    }
}

// Generic payloads retain their relation to the caller's type, while fixed
// metadata options use a private non-generic config so calls infer T naturally.
// Field types, presence, assignments and option bodies all come from the graph.
fn projection_constructor(
    out: &mut String,
    name: &str,
    fields: &[RenderedField],
    payload: &str,
    order: &[&str],
) -> Result<()> {
    anyhow::ensure!(
        !fields
            .iter()
            .any(|f| f.wire_name == payload && f.constructor_default.is_some()),
        "generic payload defaults require an explicit typed constructor policy"
    );
    let config = format!("{}Options", name.to_lowercase());
    writeln!(out, "type {config} struct {{")?;
    for f in fields
        .iter()
        .filter(|f| !f.required || f.constructor_default.is_some())
    {
        let ty = qualify(&f.field_type);
        let ty = if f.required {
            ty
        } else {
            format!("ahp.Optional[{ty}]")
        };
        writeln!(out, "{} {ty}", f.field_name)?;
    }
    writeln!(out, "}}\ntype {name}Option func(*{config})")?;
    for f in fields
        .iter()
        .filter(|f| !f.required || f.constructor_default.is_some())
    {
        let ty = qualify(&f.field_type);
        let value = if f.required {
            "value".into()
        } else {
            format!("ahp.Optional[{ty}]{{Present: true, Value: value}}")
        };
        writeln!(
            out,
            "func With{name}{}(value {ty}) {name}Option {{ return func(v *{config}) {{ v.{} = {value} }} }}",
            f.field_name.replace("Mcp", "MCP"),
            f.field_name
        )?;
    }
    let mut ordered = fields
        .iter()
        .filter(|f| f.required && f.constructor_default.is_none())
        .collect::<Vec<_>>();
    ordered.sort_by_key(|f| {
        order
            .iter()
            .position(|key| *key == f.wire_name)
            .unwrap_or(usize::MAX)
    });
    let mut args = Vec::new();
    let mut assignments = Vec::new();
    for f in ordered {
        let ty = if f.wire_name == payload {
            "T".into()
        } else {
            qualify(&f.field_type)
        };
        args.push(format!("arg{} {ty}", f.field_name));
        assignments.push(format!("{}: arg{}", f.field_name, f.field_name));
    }
    for f in fields
        .iter()
        .filter(|f| !f.required || f.constructor_default.is_some())
    {
        assignments.push(format!("{}: config.{}", f.field_name, f.field_name));
    }
    let defaults = fields
        .iter()
        .filter(|f| f.constructor_default.is_some())
        .map(|f| Ok(format!("{}: {}", f.field_name, constructor_default(f)?)))
        .collect::<Result<Vec<_>>>()?
        .join(", ");
    args.push(format!("opts ...{name}Option"));
    writeln!(
        out,
        "// New{name} preserves the caller's payload type; options set only declared metadata."
    )?;
    writeln!(
        out,
        "func New{name}[T any]({}) {name}[T] {{ config := {config}{{{defaults}}}; for _, opt := range opts {{ if opt != nil {{ opt(&config) }} }}; return {name}[T]{{{}}} }}",
        args.join(", "),
        assignments.join(", ")
    )?;
    Ok(())
}

fn projection(
    out: &mut String,
    name: &str,
    fields: &[RenderedField],
    generic: bool,
    tool: bool,
) -> Result<()> {
    let params = if generic { "[T any]" } else { "" };
    let use_params = if generic { "[T]" } else { "" };
    let fields = fields
        .iter()
        .filter(|f| tool || !["source", "type", "manifest"].contains(&f.wire_name.as_str()))
        .collect::<Vec<_>>();
    writeln!(out, "type {name}{params} struct {{")?;
    for f in &fields {
        let ty = if tool && f.wire_name == "input" {
            "T".into()
        } else if generic && !tool && f.wire_name == "tool" {
            "tool.Input[T]".into()
        } else {
            qualify(&f.field_type)
        };
        let ty = if f.required {
            ty
        } else {
            format!("ahp.Optional[{ty}]")
        };
        writeln!(out, "{} {ty} `json:\"{}\"`", f.field_name, f.wire_name)?;
    }
    writeln!(
        out,
        "}}\nfunc (v {name}{use_params}) MarshalJSON() ([]byte, error) {{ fields := map[string]any{{}}"
    )?;
    for f in fields {
        if f.required {
            if !tool && ["id", "time"].contains(&f.wire_name.as_str()) {
                writeln!(
                    out,
                    "if v.{} != \"\" {{ fields[{:?}] = v.{} }}",
                    f.field_name, f.wire_name, f.field_name
                )?;
            } else {
                writeln!(out, "fields[{:?}] = v.{}", f.wire_name, f.field_name)?;
            }
        } else {
            writeln!(
                out,
                "if v.{}.Present {{ fields[{:?}] = v.{}.Value }}",
                f.field_name, f.wire_name, f.field_name
            )?;
        }
    }
    writeln!(out, "return json.Marshal(fields)\n}}")?;
    Ok(())
}

fn enum_strings(g: &Generator<'_>, shape: &Shape) -> BTreeSet<String> {
    enum_strings_visited(g, shape, &BTreeSet::new())
}

fn enum_strings_visited(
    g: &Generator<'_>,
    shape: &Shape,
    visited: &BTreeSet<String>,
) -> BTreeSet<String> {
    match shape {
        Shape::Literal { value } => value
            .as_str()
            .map(|v| BTreeSet::from([v.to_owned()]))
            .unwrap_or_default(),
        Shape::Enum { values, .. } => values
            .iter()
            .filter_map(|v| v.as_str().map(str::to_owned))
            .collect(),
        Shape::Array { items } => enum_strings_visited(g, items, visited),
        Shape::Union { variants, .. } | Shape::Intersection { variants } => variants
            .iter()
            .flat_map(|s| enum_strings_visited(g, s, visited))
            .collect(),
        Shape::Ref { name } => {
            let mut visited = visited.clone();
            if !visited.insert(name.clone()) {
                return BTreeSet::new();
            }
            g.ir.types
                .iter()
                .find(|n| n.name == *name)
                .map(|n| enum_strings_visited(g, &n.shape, &visited))
                .unwrap_or_default()
        }
        _ => BTreeSet::new(),
    }
}

fn projected_properties(g: &Generator<'_>, shape: &Shape) -> Option<Vec<Property>> {
    projected_properties_visited(g, shape, &BTreeSet::new())
}

fn projected_properties_visited(
    g: &Generator<'_>,
    shape: &Shape,
    visited: &BTreeSet<String>,
) -> Option<Vec<Property>> {
    match shape {
        Shape::Any => Some(vec![]),
        Shape::Object { properties, .. } => Some(properties.clone()),
        Shape::Ref { name } => {
            let mut visited = visited.clone();
            if !visited.insert(name.clone()) {
                return None;
            }
            g.ir.types
                .iter()
                .find(|n| n.name == *name)
                .and_then(|n| projected_properties_visited(g, &n.shape, &visited))
        }
        Shape::Intersection { variants } => {
            let mut fields: Vec<Property> = Vec::new();
            for shape in variants {
                for p in projected_properties_visited(g, shape, visited)? {
                    if let Some(f) = fields.iter_mut().find(|f| f.wire_name == p.wire_name) {
                        f.required |= p.required;
                        if f.constructor_default.is_none() {
                            f.constructor_default = p.constructor_default.clone();
                        }
                    } else {
                        fields.push(p);
                    }
                }
            }
            Some(fields)
        }
        _ => None,
    }
}
fn projection_type(g: &Generator<'_>, shape: &Shape) -> String {
    match shape {
        Shape::Ref { name } => format!("*{}", g.named_name(name)),
        Shape::Array { items } => format!("[]{}", projection_type(g, items)),
        Shape::String | Shape::Enum { .. } | Shape::Literal { .. } => "string".into(),
        Shape::Boolean => "bool".into(),
        Shape::Integer | Shape::BoundedInteger { .. } | Shape::Number => "json.Number".into(),
        _ => "json.RawMessage".into(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn draft() -> Ir {
        crate::compiler::compile(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../..")
                .as_path(),
            "draft",
        )
        .unwrap()
    }
    #[test]
    fn facade_resolves_semantic_union_names_after_reordering() {
        fn reverse_unions(shape: &mut Shape) {
            match shape {
                Shape::Union { variants, .. } => {
                    variants.reverse();
                    for variant in variants {
                        reverse_unions(variant);
                    }
                }
                Shape::Intersection { variants } => {
                    for variant in variants {
                        reverse_unions(variant);
                    }
                }
                Shape::Object { properties, .. } => {
                    for property in properties {
                        reverse_unions(&mut property.shape);
                    }
                }
                Shape::Array { items, .. } => reverse_unions(items),
                _ => {}
            }
        }
        let mut ir = draft();
        let before = emit(&ir).unwrap();
        for named in &mut ir.types {
            reverse_unions(&mut named.shape);
        }
        let after = emit(&ir).unwrap();
        for path in ["state/generated.go", "capability/generated.go"] {
            assert_eq!(before[path], after[path]);
            assert!(!after[path].contains("Variant1"));
            assert!(!after[path].contains("Variant2"));
            assert!(!after[path].contains('$'));
        }
    }

    #[test]
    fn host_placement_preserves_schema_nullability_not_optional_presence() {
        let ir = draft();
        let g = Generator::new(&ir);
        let array = Shape::Array {
            items: Box::new(Shape::Ref {
                name: "ContentItem".into(),
            }),
        };
        let nullable = Shape::Union {
            mode: crate::model::UnionMode::AnyOf,
            variants: vec![Shape::Null, array.clone()],
            discriminator: None,
        };
        let mut output = String::new();
        emit_host_placement(
            &g,
            &mut output,
            "[]*ContentPartInput",
            "values",
            &nullable,
            0,
        )
        .unwrap();
        assert!(!output.contains("if values == nil"));
        assert!(output.contains("if values[placementIndex0] == nil"));
        let mut required = String::new();
        emit_host_placement(
            &g,
            &mut required,
            "[]*ContentPartInput",
            "values",
            &array,
            0,
        )
        .unwrap();
        assert!(required.contains("if values == nil"));
    }

    #[test]
    fn accepted_ergonomics_use_shared_fields_slots_and_codes() {
        let ir = draft();
        let files = emit(&ir).unwrap();
        let events = &files["event/generated.go"];
        assert!(!events.contains("AHPContentSources"));
        assert!(events.contains("type Type string"));
        assert!(!events.contains("type Type = string"));
        assert!(events.contains("const ToolBefore Type = \"tool.before\""));
        let extractor = &files["internal/inputsources/generated.go"];
        assert_eq!(extractor.matches("case event.").count(), 32);
        assert!(extractor.contains("func ExtractToolBefore[T any]"));
        assert!(extractor.contains("case event.FileChangedInput:"));
        assert!(!extractor.contains("interface {"));
        assert!(events.contains("CallID string"));
        assert!(events.contains("Input T"));
        assert!(!events.contains("ItemsSources"));
        assert!(!events.contains("ItemsPartsSources"));
        assert!(events.contains("ItemsInput *[]*ModelVisibleItemInput"));
        assert!(events.contains("MessageInput *InboundMessageInput"));
        for (wire, facade) in [
            ("FileChangedEventChangesItem", "FileChangeInput"),
            ("ExecutionEventFilechange", "ToolFileChangeInput"),
            ("UserMessageInboundEventMessage", "InboundMessageInput"),
            (
                "UserMessageInboundEventMessageMessagesItem",
                "InboundMessageItemInput",
            ),
            ("UserMessageOutboundEventMessage", "OutboundMessageInput"),
            (
                "UserMessageOutboundEventMessageMessagesItem",
                "OutboundMessageItemInput",
            ),
        ] {
            assert!(events.contains(&format!("type {facade} struct {{ ahp.{wire}")));
            assert!(!events.contains(&format!("type {wire}Input struct")));
        }
        assert!(!events.contains("Host *"));
        assert!(events.contains("Body *content.Source"));
        assert!(events.contains("synthesizeContentID()"));
        assert!(!events.contains("InstructionsSource"));
        assert!(!events.contains("tool.Input[T]"));
        for code in crate::ergonomics::DIAGNOSTIC_CODES {
            assert!(files["diagnostic/generated.go"].contains(&format!("= {code:?}")));
        }
        let client_example = &files["client/facade_generated_test.go"];
        assert!(client_example.contains("capability.Host"));
        assert!(client_example.contains("ModifyInput: capability.ReplaceAndMerge()"));
        assert!(!client_example.contains("EventCapabilities"));
        assert!(files["state/generated.go"].contains("func Candidate[T any]"));
        assert!(files["effect/generated.go"].contains("func ModifyInputReplace[T any](value T)"));
        assert!(
            files["effect/generated.go"]
                .contains("func ModifyWorkspaceMerge(value *ahp.WorkspaceChange)")
        );
        assert!(
            files["capability/generated.go"]
                .contains("func (v Host) Wire() map[event.Type]WireDeclaration")
        );
    }

    #[test]
    fn complete_boundary_catalogue_and_modality() {
        let ir = draft();
        let files = emit(&ir).unwrap();
        let methods = &files["client/boundaries_generated.go"];
        // Coverage comes from schema selectors, not a handwritten event list.
        let mut g = Generator::new(&ir);
        for n in &ir.types {
            g.emit_named(&g.named_name(&n.name), &n.source, &n.shape)
                .unwrap();
        }
        let selectors = &g.objects["ObserveSubscription"]
            .iter()
            .find(|f| f.wire_name == "events")
            .unwrap()
            .shape;
        let events = enum_strings(&g, selectors);
        assert_eq!(events.len(), 32);
        assert_eq!(methods.matches("func (c *Hooks)").count(), events.len());
        assert!(!methods.contains("*Client"));
        for tag in events {
            if tag == "tool.before" {
                // The runtime helper owns this selector and atomic contract admission.
                assert!(methods.contains("return interceptToolBefore[T](ctx, c, input, opts...)"));
            } else {
                assert!(methods.contains(&format!("{tag:?}")), "missing {tag}");
            }
        }
        assert!(methods.contains("ToolBefore[T any]"));
        assert!(methods.contains("return interceptToolBefore[T](ctx, c, input, opts...)"));
        assert!(!methods.contains("decodeToolBefore"));
        assert!(!methods.contains("internal/inputsources"));
        assert!(methods.contains("import \"context\""));
        assert!(methods.contains("SessionEnd(ctx context.Context, input event.SessionEndInput)"));
        assert!(methods.contains(
            "ContextCompactBefore(ctx context.Context, input event.ContextCompactBeforeInput, opts"
        ));
    }
    #[test]
    fn typed_capability_catalogue_and_zero_grants() {
        let ir = draft();
        let source = capabilities::emit(&ir).unwrap();
        for event in crate::capability_ergonomics::catalogue(&ir).unwrap() {
            assert!(source.contains(&format!("{} {}Declaration", event.name, event.name)));
            assert!(source.contains(&format!("type {} struct", event.name)));
            assert!(source.contains(&format!("type {}Declaration interface", event.name)));
        }
        assert!(!source.contains("Observation"));
        assert!(!source.contains("type Observe struct"));
        assert!(source.contains("ReturnResult bool"));
        assert!(source.contains("if len(caps.Effects)>0"));
        assert!(source.contains("func ReplaceAndMerge() Modify"));
        for obsolete in [
            "func New(",
            "func WithEffects",
            "func Intercept(",
            "type Event =",
            "type Grant",
            "json.Unmarshal",
            "ParseCapabilities",
        ] {
            assert!(!source.contains(obsolete), "obsolete {obsolete}");
        }
    }

    #[test]
    fn recursive_facade_graph_walks_terminate() {
        let mut ir = draft();
        ir.types.push(crate::model::NamedType {
            name: "Cycle".into(),
            source: "test".into(),
            shape: Shape::Ref {
                name: "Cycle".into(),
            },
        });
        let g = Generator::new(&ir);
        let shape = Shape::Ref {
            name: "Cycle".into(),
        };
        assert!(literal(&g, &shape).is_none());
        assert!(enum_strings(&g, &shape).is_empty());
        assert!(projected_properties(&g, &shape).is_none());
        // Independent siblings must not share a visited set: repeated refs
        // are not cycles and must still contribute their own constraints.
        let strings = Shape::Union {
            mode: crate::model::UnionMode::AnyOf,
            variants: vec![
                shape,
                Shape::Enum {
                    values: vec![serde_json::json!("extension")],
                    open_strings: false,
                },
            ],
            discriminator: None,
        };
        assert!(enum_strings(&g, &strings).contains("extension"));
    }
    #[test]
    fn constructors_use_annotation_defaults_not_positional_arguments() {
        let mut ir = draft();
        // Exercise required-default policy without editing the canonical schema.
        let transport = ir
            .types
            .iter_mut()
            .find(|n| n.name == "StdioTransport")
            .unwrap();
        let Shape::Object { properties, .. } = &mut transport.shape else {
            panic!("object expected")
        };
        let lifecycle = properties
            .iter_mut()
            .find(|p| p.wire_name == "lifecycle")
            .unwrap();
        assert!(lifecycle.required);
        lifecycle.constructor_default = Some(serde_json::json!("persistent"));
        let files = emit(&ir).unwrap();
        let source = &files["transport/generated.go"];
        assert!(source.contains("func NewStdio(argCommand string, opts ...StdioOption)"));
        assert!(
            source
                .contains("func WithStdioLifecycle(value ahp.StdioTransportLifecycle) StdioOption")
        );
        assert!(source.contains("v.Lifecycle = value"));
        assert!(source.contains("Lifecycle: ahp.StdioTransportLifecycle(\"persistent\")"));
        assert!(!source.contains("argLifecycle"));
        // Optional defaults must be present, not silently represented by zero.
        assert!(
            files["subscription/generated.go"]
                .contains("IncludeNative: ahp.Optional[bool]{Present: true, Value: bool(false)}")
        );
    }
    #[test]
    fn default_expressions_are_typed_and_fail_at_generation_time() {
        let field = |shape, field_type: &str, value| RenderedField {
            wire_name: "setting".into(),
            field_name: "Setting".into(),
            field_type: field_type.into(),
            required: true,
            shape,
            constructor_default: Some(value),
        };
        for (shape, ty, value, expected) in [
            (
                Shape::Boolean,
                "bool",
                serde_json::json!(false),
                "bool(false)",
            ),
            (
                Shape::String,
                "string",
                serde_json::json!("hello"),
                "string(\"hello\")",
            ),
            (
                Shape::Integer,
                "json.Number",
                serde_json::json!(42),
                "json.Number(\"42\")",
            ),
            (
                Shape::Number,
                "json.Number",
                serde_json::json!(1.5),
                "json.Number(\"1.5\")",
            ),
        ] {
            assert_eq!(
                constructor_default(&field(shape, ty, value)).unwrap(),
                expected
            );
        }
        for (shape, value) in [
            (Shape::Boolean, serde_json::json!("false")),
            (Shape::Integer, serde_json::json!(1.5)),
            (Shape::Null, serde_json::Value::Null),
            (
                Shape::Array {
                    items: Box::new(Shape::String),
                },
                serde_json::json!(["fresh"]),
            ),
            (
                Shape::Ref {
                    name: "Cycle".into(),
                },
                serde_json::json!({}),
            ),
            (
                Shape::Literal {
                    value: serde_json::json!("fixed"),
                },
                serde_json::json!("wrong"),
            ),
            (
                Shape::Enum {
                    values: vec![serde_json::json!("fixed")],
                    open_strings: false,
                },
                serde_json::json!("wrong"),
            ),
        ] {
            let error = constructor_default(&field(shape, "Unsupported", value)).unwrap_err();
            assert!(error.to_string().contains("setting"));
        }
        let mut ir = draft();
        let subscription = ir
            .types
            .iter_mut()
            .find(|n| n.name == "ObserveSubscription")
            .unwrap();
        let Shape::Object { properties, .. } = &mut subscription.shape else {
            panic!("object expected")
        };
        properties
            .iter_mut()
            .find(|p| p.wire_name == "includeNative")
            .unwrap()
            .constructor_default = Some(serde_json::json!({}));
        assert!(emit(&ir).unwrap_err().to_string().contains("includeNative"));
        for source in emit(&draft()).unwrap().values() {
            assert!(!source.contains("constructorDefault"));
            assert!(!source.contains("panic("));
        }
    }
    #[test]
    fn generic_projection_constructor_is_field_driven() {
        let ir = draft();
        let mut g = Generator::new(&ir);
        for n in &ir.types {
            g.emit_named(&g.named_name(&n.name), &n.source, &n.shape)
                .unwrap();
        }
        let files = emit(&ir).unwrap();
        let source = &files["tool/generated.go"];
        assert!(source.contains("func NewInput[T any](argName string, argOrigin ahp.ExecutionEventToolOrigin, argInput T, opts ...InputOption) Input[T]"));
        assert!(source.contains("func WithInputKind(value string) InputOption"));
        assert!(source.contains("func WithInputMCP(value *ahp.ExecutionEventMcp) InputOption"));
        assert!(!source.contains("InputOption[T"));
        // A renamed optional schema property must flow through the config,
        // setter and constructor assignment without editing helper templates.
        let mut fields = g.objects["ExecutionEventTool"].clone();
        let field = fields.iter_mut().find(|f| f.wire_name == "kind").unwrap();
        field.wire_name = "metadata".into();
        field.field_name = "Metadata".into();
        let mut source = String::new();
        projection_constructor(
            &mut source,
            "Input",
            &fields,
            "input",
            &["name", "origin", "input"],
        )
        .unwrap();
        assert!(source.contains("func WithInputMetadata(value string) InputOption"));
        assert!(source.contains("Metadata: config.Metadata"));
        assert!(!source.contains("WithInputKind"));
    }
    #[test]
    fn facade_is_deterministic_and_dependency_graph_is_acyclic() {
        let ir = draft();
        let files = emit(&ir).unwrap();
        assert_eq!(files, emit(&ir).unwrap());
        assert!(
            files["registration/generated.go"]
                .contains("func New(argHooks ...*ahp.Backend) ahp.Registration")
        );
        assert!(
            files["registration/generated.go"]
                .contains("WithBackendSubscriptions(values ...ahp.BackendSubscriptionsItem)")
        );
        assert!(
            files["subscription/generated.go"]
                .contains("NewIntercept(argEvents []string, argTimeout time.Duration")
        );
        for (path, source) in files {
            if path.starts_with("client/") || path.ends_with("_test.go") {
                continue;
            }
            for dependency in [
                "client",
                "server",
                "registration",
                "transport",
                "subscription",
                "content",
                "capability",
                "effect",
                "event",
            ] {
                // Host inputs refer to the leaf source package, never to client runtime.
                if (path == "event/generated.go" && dependency == "content")
                    || (path == "capability/generated.go" && dependency == "event")
                    || (path == "internal/inputsources/generated.go"
                        && ["event", "content"].contains(&dependency))
                {
                    continue;
                }
                assert!(
                    !source.contains(&format!("{MODULE}/{dependency}")),
                    "cycle-prone dependency: {path} -> {dependency}"
                );
            }
        }
    }
}
