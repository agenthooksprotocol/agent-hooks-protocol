//! Semantic APIs share the wire emitter's resolved names and schema graph. No wire
//! declarations are copied into this layer; role metadata only controls ownership.
use super::*;

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
    capability_options(&g, packages.entry("capability".into()).or_default())?;
    capability_composition(&g, packages.entry("capability".into()).or_default())?;
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
            if ty.starts_with('*') {
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
    let mut events = String::from("type Type = string\n");
    let mut methods = String::new();
    for (name, fields) in &g.objects {
        if !name.ends_with("Event") || !fields.iter().any(|f| f.wire_name == "source") {
            continue;
        }
        let short = name.trim_end_matches("Event");
        let generic = short == "ToolBefore";
        input_projection(&g, &mut events, &format!("{short}Input"), fields, generic)?;
        let tag = fields
            .iter()
            .find(|f| f.wire_name == "type")
            .and_then(|f| literal(&g, &f.shape));
        if let Some(serde_json::Value::String(tag)) = tag {
            writeln!(events, "const {short} Type = {tag:?}")?;
            if generic {
                writeln!(
                    methods,
                    "func (c *Hooks) {short}[T any](ctx context.Context, input event.{short}Input[T], opts ...InterceptOption) (*ToolBeforeResult[T], error) {{ return decodeToolBefore[T](c.intercept(ctx, {tag:?}, input, opts...)) }}"
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
    projection(
        &mut tool,
        "Input",
        &g.objects["ExecutionEventTool"],
        true,
        true,
    )?;
    projection_constructor(
        &mut tool,
        "Input",
        &g.objects["ExecutionEventTool"],
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
    let mut files = BTreeMap::new();
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
        if body.contains("content.Source") {
            writeln!(imports, "import \"{MODULE}/content\"")?;
        }
        if body.contains("permission.Permission") {
            writeln!(imports, "import \"{MODULE}/permission\"")?;
        }
        if body.contains("tool.Input") {
            writeln!(imports, "import \"{MODULE}/tool\"")?;
        }
        files.insert(format!("{package}/generated.go"), format!("// Code generated by ahp-codegen. DO NOT EDIT.\npackage {package}\n{imports}\n{body}"));
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
    } else if name.ends_with("Capabilities") || name == "StaticCapabilityManifest" {
        ("capability", name.trim_end_matches("Capabilities").into())
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
fn capability_declarations(shape: &Shape) -> Vec<Property> {
    match shape {
        Shape::Object { properties, .. } => properties.clone(),
        Shape::Intersection { variants } => {
            variants.iter().flat_map(capability_declarations).collect()
        }
        _ => vec![],
    }
}

fn capability_options(g: &Generator<'_>, out: &mut String) -> Result<()> {
    for group in &g.objects["Capabilities"] {
        // Ownership/naming metadata only; leaf names and bodies come from fields.
        let group_name = match group.wire_name.as_str() {
            "modify" => "Modification",
            "elicitation" => "Elicitation",
            _ => continue,
        };
        let leaves = g.objects.get(&group.field_type).ok_or_else(|| {
            anyhow::anyhow!("capability group {} has no object model", group.wire_name)
        })?;
        for leaf in leaves {
            anyhow::ensure!(
                !group.required && !leaf.required,
                "capability grant must be explicitly optional"
            );
            let (helper, args, value) = if group.wire_name == "modify" {
                let mut properties = capability_declarations(&leaf.shape);
                anyhow::ensure!(
                    !properties.is_empty()
                        && properties
                            .iter()
                            .all(|p| p.required && matches!(p.shape, Shape::Boolean)),
                    "unsupported modification grant {}",
                    leaf.wire_name
                );
                // Semantic argument order; membership is entirely schema-derived.
                properties.sort_by_key(|p| (p.wire_name != "replace", p.wire_name.clone()));
                let args = properties
                    .iter()
                    .map(|p| format!("arg{} bool", go_identifier(&p.wire_name)))
                    .collect::<Vec<_>>()
                    .join(", ");
                let mut expression = vec![go_string("{")?];
                for (index, property) in properties.iter().enumerate() {
                    let key = format!(
                        "{}{}:",
                        if index == 0 { "" } else { "," },
                        serde_json::to_string(&property.wire_name)?
                    );
                    expression.push(go_string(&key)?);
                    expression.push(format!(
                        "strconv.FormatBool(arg{})",
                        go_identifier(&property.wire_name)
                    ));
                }
                expression.push(go_string("}")?);
                anyhow::ensure!(
                    leaf.field_type == "json.RawMessage",
                    "unsupported modification model {}",
                    leaf.field_type
                );
                (
                    format!("With{}{group_name}", leaf.field_name),
                    args,
                    format!("json.RawMessage({})", expression.join(" + ")),
                )
            } else {
                let grant_fields = g.objects.get(&leaf.field_type).ok_or_else(|| {
                    anyhow::anyhow!("elicitation grant {} has no object model", leaf.wire_name)
                })?;
                anyhow::ensure!(
                    grant_fields.is_empty(),
                    "elicitation grant {} now requires a typed helper policy",
                    leaf.wire_name
                );
                (
                    format!("With{group_name}{}", leaf.field_name),
                    String::new(),
                    format!("{}{{}}", qualify(&leaf.field_type)),
                )
            };
            let ty = qualify(&leaf.field_type);
            writeln!(
                out,
                "// {helper} grants only the declared nested capability; it never adds effects or modes."
            )?;
            writeln!(
                out,
                "func {helper}({args}) Option {{ return func(v *ahp.Capabilities) {{ v.{}.Present = true; v.{}.Value.{} = ahp.Optional[{ty}]{{Present:true, Value:{value}}} }} }}",
                group.field_name, group.field_name, leaf.field_name
            )?;
        }
    }
    Ok(())
}

// Functional declarations allocate fresh wire models for every composition.
fn capability_composition(g: &Generator<'_>, out: &mut String) -> Result<()> {
    let effects_field = g.objects["Capabilities"]
        .iter()
        .find(|f| f.wire_name == "effects")
        .unwrap();
    let item = effects_field.field_type.strip_prefix("[]").unwrap();
    let arms = &g.unions[item];
    let (custom, _) = arms.iter().find(|(_, ty)| ty == "string").unwrap();
    let (known, known_type) = arms.iter().find(|(_, ty)| ty != "string").unwrap();
    out.push_str(&r#"
type Mode = ahp.StaticCapabilityManifestEventsItemModesItem
const (InterceptMode Mode = "intercept"; ObserveMode Mode = "observe")
type Event struct { Modes []Mode; Capabilities *ahp.Capabilities }
type Grant struct { apply func(*ahp.Capabilities) error }
type ModifyOperation string
// Intercept advertises both modes. It does not authorize host execution.
// The SDK must validate event compatibility and per-call narrowing before delivery.
func Intercept(grants ...Grant) (Event, error) {
    if len(grants) == 0 { return Event{}, fmt.Errorf("intercept requires explicit grants") }
    value := New([]string{})
    for _, grant := range grants {
        if grant.apply == nil { return Event{}, fmt.Errorf("empty capability grant") }
        if err := grant.apply(value); err != nil { return Event{}, err }
    }
    raw, err := json.Marshal(value); if err != nil { return Event{}, err }
    parsed := ahp.ParseCapabilities(raw)
    if !parsed.OK { return Event{}, fmt.Errorf("invalid capability declaration: %v", parsed.Diagnostics) }
    return Event{Modes: []Mode{InterceptMode, ObserveMode}, Capabilities: value}, nil
}
func Observe() Event { return Event{Modes: []Mode{ObserveMode}} }
func addEffect(v *ahp.Capabilities, name string) {
    for _, effect := range v.Effects { if effect.$CUSTOM.Present && effect.$CUSTOM.Value == name { return }; if effect.$KNOWN.Present && string(effect.$KNOWN.Value) == name { return } }
    v.Effects = append(v.Effects, ahp.$ITEM{$KNOWN: ahp.Some(ahp.$KNOWN_TYPE(name))})
}
"#.replace("$CUSTOM", custom).replace("$KNOWN_TYPE", known_type).replace("$KNOWN", known).replace("$ITEM", item));
    let effects = &g.objects["Capabilities"]
        .iter()
        .find(|f| f.wire_name == "effects")
        .unwrap()
        .shape;
    for effect in enum_strings(g, effects) {
        if ["modify", "flow", "inject"].contains(&effect.as_str()) {
            continue;
        }
        writeln!(
            out,
            "func {}() Grant {{ return Grant{{apply: func(v *ahp.Capabilities) error {{ addEffect(v, {effect:?}); return nil }} }} }}",
            go_identifier(&effect)
        )?;
    }
    let operations = g.objects["CapabilitiesModify"]
        .iter()
        .flat_map(|leaf| capability_declarations(&leaf.shape))
        .map(|p| p.wire_name)
        .collect::<BTreeSet<_>>();
    for operation in operations {
        writeln!(
            out,
            "const {} ModifyOperation = {operation:?}",
            go_identifier(&operation)
        )?;
    }
    for leaf in &g.objects["CapabilitiesModify"] {
        let name = &leaf.field_name;
        let flags = capability_declarations(&leaf.shape)
            .iter()
            .map(|p| format!("{:?}:false", p.wire_name))
            .collect::<Vec<_>>()
            .join(",");
        writeln!(
            out,
            r#"
func Modify{name}(operations ...ModifyOperation) Grant {{
    operations = append([]ModifyOperation(nil), operations...)
    return Grant{{apply: func(v *ahp.Capabilities) error {{
        if len(operations) == 0 {{ return fmt.Errorf("modification requires an operation") }}
        flags := map[string]bool{{{flags}}}
        for _, operation := range operations {{
            if _, known := flags[string(operation)]; !known {{ return fmt.Errorf("unknown modify operation %q", operation) }}
            flags[string(operation)] = true
        }}
        if v.Modify.Present && v.Modify.Value.{name}.Present {{
            var previous map[string]bool
            if err := json.Unmarshal(v.Modify.Value.{name}.Value, &previous); err != nil {{ return err }}
            for operation, enabled := range previous {{ flags[operation] = flags[operation] || enabled }}
        }}
        raw, err := json.Marshal(flags); if err != nil {{ return err }}
        v.Modify.Present = true; v.Modify.Value.{name} = ahp.Some(json.RawMessage(raw)); addEffect(v, "modify"); return nil
    }} }}
}}
"#
        )?;
    }

    let flow = g.objects["CapabilitiesFlow"]
        .iter()
        .find(|f| f.wire_name == "operations")
        .unwrap();
    for operation in enum_strings(g, &flow.shape) {
        let name = go_identifier(&operation);
        let (args, counts) = if operation == "continue" {
            (
                "remaining, count int64",
                "v.Flow.Value.RemainingContinuations = ahp.Some(json.Number(strconv.FormatInt(remaining, 10))); v.Flow.Value.ContinuationCount = ahp.Some(json.Number(strconv.FormatInt(count, 10)));",
            )
        } else {
            ("", "")
        };
        let validation = if operation == "continue" {
            format!(
                "if remaining < {min} || count < {min} || remaining > {max} || count > {max} {{ return fmt.Errorf(\"continuation counts must be nonnegative safe integers\") }};",
                min = crate::ergonomics::MIN_CONTINUATION_COUNT,
                max = crate::ergonomics::MAX_CONTINUATION_COUNT
            )
        } else {
            String::new()
        };
        writeln!(
            out,
            "func Flow{name}({args}) Grant {{ return Grant{{apply: func(v *ahp.Capabilities) error {{ {validation} v.Flow.Present = true; {counts} for _, operation := range v.Flow.Value.Operations {{ if string(operation) == {operation:?} {{ return nil }} }}; v.Flow.Value.Operations = append(v.Flow.Value.Operations, ahp.CapabilitiesFlowOperationsItem({operation:?})); addEffect(v, \"flow\"); return nil }} }} }}"
        )?;
    }
    let deliver = g.objects["CapabilitiesInjectContext"]
        .iter()
        .find(|f| f.wire_name == "deliverAt")
        .unwrap();
    writeln!(
        out,
        "type DeliverAt = ahp.CapabilitiesInjectContextDeliverAtItem"
    )?;
    let deliveries = enum_strings(g, &deliver.shape);
    for delivery in &deliveries {
        writeln!(
            out,
            "const {} DeliverAt = {delivery:?}",
            go_identifier(delivery)
        )?;
    }
    let cases = deliveries
        .iter()
        .map(|v| format!("{v:?}"))
        .collect::<Vec<_>>()
        .join(", ");
    writeln!(
        out,
        r#"
func InjectContextAppend(deliveries ...DeliverAt) Grant {{
    deliveries = append([]DeliverAt(nil), deliveries...)
    return Grant{{apply: func(v *ahp.Capabilities) error {{
        if len(deliveries) == 0 {{ return fmt.Errorf("injection requires explicit delivery timing") }}
        for _, delivery := range deliveries {{ switch delivery {{ case {cases}: default: return fmt.Errorf("invalid injection delivery %q", delivery) }} }}
        v.Inject.Present = true; v.Inject.Value.Context.Append = true
        for _, delivery := range deliveries {{ found := false; for _, existing := range v.Inject.Value.Context.DeliverAt {{ if existing == delivery {{ found = true }} }}; if !found {{ v.Inject.Value.Context.DeliverAt = append(v.Inject.Value.Context.DeliverAt, delivery) }} }}
        addEffect(v, "inject"); return nil
    }} }}
}}
"#
    )?;
    // Elicitation is orthogonal to effect grants and must always be explicit.
    for leaf in &g.objects["CapabilitiesElicitation"] {
        writeln!(
            out,
            "func Elicitation{}() Grant {{ return Grant{{apply: func(v *ahp.Capabilities) error {{ WithElicitation{}()(v); return nil }} }} }}",
            leaf.field_name, leaf.field_name
        )?;
    }
    Ok(())
}

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

fn effect_operations(g: &Generator<'_>, out: &mut String) -> Result<()> {
    for (arm, ty) in &g.unions["Effect"] {
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
        if tag == "return" {
            writeln!(
                out,
                "func Return[T any](value T, opts ...ReturnOption) (*ahp.Effect, error) {{ raw, err := json.Marshal(value); if err != nil {{ return nil, err }}; return NewReturn(raw, opts...), nil }}"
            )?;
        }
        if tag == "inject" {
            let deliver = fields.iter().find(|f| f.wire_name == "deliverAt").unwrap();
            writeln!(out, "type DeliverAt = {}", qualify(&deliver.field_type))?;
            for value in enum_strings(g, &deliver.shape) {
                writeln!(out, "const {} DeliverAt = {value:?}", go_identifier(&value))?;
            }
            writeln!(
                out,
                "func InjectContextAppend[T any](deliverAt DeliverAt, value T, opts ...InjectAppendOption) (*ahp.Effect, error) {{ raw, err := json.Marshal(value); if err != nil {{ return nil, err }}; return NewInjectAppend(deliverAt, raw, opts...), nil }}"
            )?;
        }
        if tag != "modify" {
            continue;
        }
        let target = fields.iter().find(|f| f.wire_name == "target").unwrap();
        let operation = fields.iter().find(|f| f.wire_name == "operation").unwrap();
        for target_value in enum_strings(g, &target.shape) {
            for operation_value in enum_strings(g, &operation.shape) {
                let name = format!(
                    "Modify{}{}",
                    go_identifier(&target_value),
                    go_identifier(&operation_value)
                );
                writeln!(
                    out,
                    "func {name}[T any](value T) (*ahp.Effect, error) {{ raw, err := json.Marshal(value); if err != nil {{ return nil, err }}; return &ahp.Effect{{{arm}: ahp.Some(ahp.{ty}{{Type: {tag:?}, Target: {target_value:?}, Operation: {operation_value:?}, Value: raw}})}}, nil }}"
                )?;
            }
        }
    }
    Ok(())
}

fn input_identifier(name: &str) -> String {
    let name = go_identifier(name);
    if let Some(prefix) = name.strip_suffix("Id") {
        format!("{prefix}ID")
    } else {
        name
    }
}

fn input_projection(
    g: &Generator<'_>,
    out: &mut String,
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
    for slot in &slots {
        let suffix = if slot.many { "Sources" } else { "Source" };
        let ty = if slot.many {
            "[]*content.Source"
        } else {
            "*content.Source"
        };
        writeln!(
            out,
            "{}{} {ty} `json:\"-\"`",
            input_identifier(&slot.name),
            suffix
        )?;
    }
    writeln!(
        out,
        "}}\n// AHPContentSources returns owned sources out of band; descriptors stay in canonical JSON.\nfunc (v {name}{use_params}) AHPContentSources() map[string]*content.Source {{ sources := map[string]*content.Source{{}}"
    )?;
    for slot in &slots {
        let field = input_identifier(&slot.name);
        anyhow::ensure!(
            slot.path.iter().filter(|p| p.as_str() == "*").count() <= 1,
            "nested content arrays require an explicit binding policy"
        );
        let path = format!("/{}", slot.path.join("/"));
        if slot.many {
            let (before, after) = path.split_once('*').unwrap();
            writeln!(
                out,
                "for index, source := range v.{field}Sources {{ if source != nil {{ sources[{before:?} + strconv.Itoa(index) + {after:?}] = source }} }}"
            )?;
        } else {
            writeln!(
                out,
                "if v.{field}Source != nil {{ sources[{path:?}] = v.{field}Source }}"
            )?;
        }
    }
    writeln!(
        out,
        "return sources\n}}\nfunc (v {name}{use_params}) MarshalJSON() ([]byte, error) {{ fields := map[string]any{{}}"
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
            f.field_name, f.field_name
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
        Shape::Integer if default.as_i64().is_some() || default.as_u64().is_some() => {
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
            f.field_name, f.field_name
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
        Shape::Integer | Shape::Number => "json.Number".into(),
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
    fn accepted_ergonomics_use_shared_fields_slots_and_codes() {
        let ir = draft();
        let files = emit(&ir).unwrap();
        let events = &files["event/generated.go"];
        assert_eq!(events.matches("AHPContentSources()").count(), 32);
        assert!(events.contains("CallID string"));
        assert!(events.contains("Input T"));
        assert!(events.contains("ItemsSources []*content.Source `json:\"-\"`"));
        assert!(events.contains("InstructionsSource *content.Source `json:\"-\"`"));
        assert!(!events.contains("tool.Input[T]"));
        for code in crate::ergonomics::DIAGNOSTIC_CODES {
            assert!(files["diagnostic/generated.go"].contains(&format!("= {code:?}")));
        }
        assert!(files["state/generated.go"].contains("func Candidate[T any]"));
        assert!(files["effect/generated.go"].contains("func ModifyInputReplace[T any]"));
        assert!(files["effect/generated.go"].contains("func ModifyWorkspaceMerge[T any]"));
        assert!(
            files["capability/generated.go"]
                .contains("func Intercept(grants ...Grant) (Event, error)")
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
            assert!(methods.contains(&format!("{tag:?}")), "missing {tag}");
        }
        assert!(methods.contains("ToolBefore[T any]"));
        assert!(methods.contains("decodeToolBefore[T](c.intercept"));
        assert!(methods.contains("SessionEnd(ctx context.Context, input event.SessionEndInput)"));
        assert!(methods.contains(
            "ContextCompactBefore(ctx context.Context, input event.ContextCompactBeforeInput, opts"
        ));
    }
    #[test]
    fn functional_modify_operations_follow_each_targets_schema_metadata() {
        let ir = draft();
        let mut g = Generator::new(&ir);
        for n in &ir.types {
            g.emit_named(&g.named_name(&n.name), &n.source, &n.shape)
                .unwrap();
        }
        let targets = g.objects.get_mut("CapabilitiesModify").unwrap();
        assert!(targets.len() > 1);
        let first_target = targets[0].field_name.clone();
        let second_target = targets[1].field_name.clone();
        for (index, target) in targets.iter_mut().enumerate() {
            let operation = if index == 0 { "patch" } else { "splice" };
            target.shape = Shape::Object {
                properties: vec![Property {
                    wire_name: operation.into(),
                    required: true,
                    shape: Shape::Boolean,
                    constructor_default: None,
                }],
                forbidden_property_sets: vec![],
                additional: crate::model::AdditionalProperties::Forbidden,
            };
        }
        let mut source = String::new();
        capability_composition(&g, &mut source).unwrap();
        assert_eq!(
            source
                .matches("const Patch ModifyOperation = \"patch\"")
                .count(),
            1
        );
        assert_eq!(
            source
                .matches("const Splice ModifyOperation = \"splice\"")
                .count(),
            1
        );
        assert!(!source.contains("const Replace ModifyOperation"));
        assert!(!source.contains("const Merge ModifyOperation"));
        let first = source
            .split(&format!("func Modify{first_target}("))
            .nth(1)
            .unwrap()
            .split("func ")
            .next()
            .unwrap();
        let second = source
            .split(&format!("func Modify{second_target}("))
            .nth(1)
            .unwrap()
            .split("func ")
            .next()
            .unwrap();
        assert!(first.contains("flags := map[string]bool{\"patch\":false}"));
        assert!(!first.contains("\"splice\""));
        assert!(second.contains("flags := map[string]bool{\"splice\":false}"));
        assert!(!second.contains("\"patch\""));
        assert!(source.contains("known := flags[string(operation)]"));
    }

    #[test]
    fn capability_helpers_follow_schema_targets_without_implicit_grants() {
        let ir = draft();
        let files = emit(&ir).unwrap();
        let source = &files["capability/generated.go"];
        assert!(source.contains("func New(argEffects []string, opts ...Option) *ahp.Capabilities"));
        assert!(source.contains("func WithEffects(values ...string) Option"));
        assert!(source.contains("func WithElicitationForm() Option"));
        assert!(source.contains("func WithElicitationURL() Option"));
        let mut g = Generator::new(&ir);
        for n in &ir.types {
            g.emit_named(&g.named_name(&n.name), &n.source, &n.shape)
                .unwrap();
        }
        for field in &g.objects["CapabilitiesModify"] {
            assert!(source.contains(&format!(
                "func With{}Modification(argReplace bool, argMerge bool) Option",
                field.field_name
            )));
        }
        // A changed schema target creates a corresponding helper, with no list
        // of event boundaries or target names in the implementation.
        let fields = g.objects.get_mut("CapabilitiesModify").unwrap();
        fields[0].wire_name = "custom".into();
        fields[0].field_name = "Custom".into();
        let mut source = String::new();
        capability_options(&g, &mut source).unwrap();
        assert!(source.contains("func WithCustomModification("));
        assert!(!source.contains("v.Effects"));
        assert!(!source.contains("json.Unmarshal"));
        assert!(!source.contains("panic("));
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
        assert!(source.contains("func WithInputMcp(value *ahp.ExecutionEventMcp) InputOption"));
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
                if path == "event/generated.go" && dependency == "content" {
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
