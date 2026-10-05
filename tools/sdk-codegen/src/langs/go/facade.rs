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
    let mut events = String::new();
    let mut methods = String::new();
    for (name, fields) in &g.objects {
        if !name.ends_with("Event") || !fields.iter().any(|f| f.wire_name == "source") {
            continue;
        }
        let short = name.trim_end_matches("Event");
        let generic = short == "ToolBefore";
        projection(
            &mut events,
            &format!("{short}Input"),
            fields,
            generic,
            false,
        )?;
        let tag = fields
            .iter()
            .find(|f| f.wire_name == "type")
            .and_then(|f| literal(&g, &f.shape));
        if let Some(serde_json::Value::String(tag)) = tag {
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
        let mut imports = format!("import ahp {MODULE:?}\n");
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
        if body.contains("tool.") {
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

fn role(name: &str) -> Option<(&'static str, String)> {
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
                    format!("&{arg}")
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
                assert!(
                    !source.contains(&format!("{MODULE}/{dependency}")),
                    "cycle-prone dependency: {path} -> {dependency}"
                );
            }
        }
    }
}
