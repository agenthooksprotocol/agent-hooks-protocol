use super::*;
use crate::model::Property;

fn fields(ir: &Ir, shape: &Shape) -> Option<Vec<Property>> {
    crate::ergonomics::object_fields(ir, shape)
}

pub(super) fn emit(ir: &Ir, out: &mut String) -> Result<()> {
    writeln!(
        out,
        "\nexport type DeliveryDiagnosticCode = {};",
        crate::ergonomics::DIAGNOSTIC_CODES
            .iter()
            .map(|c| format!("{c:?}"))
            .collect::<Vec<_>>()
            .join(" | ")
    )?;
    let mut inventory = Vec::new();
    for named in &ir.types {
        if !named.name.ends_with("Event") {
            continue;
        }
        let Some(properties) = fields(ir, &named.shape) else {
            continue;
        };
        let Some(event) = properties
            .iter()
            .find(|p| p.wire_name == "type")
            .and_then(|p| {
                if let Shape::Literal { value } = &p.shape {
                    value.as_str()
                } else {
                    None
                }
            })
        else {
            continue;
        };
        let inputs = crate::ergonomics::input_fields(ir, &properties, event)?;
        let name = format!("{}Input", named.name.trim_end_matches("Event"));
        let generic = inputs.iter().any(|f| f.path == ["tool", "input"]);
        writeln!(
            out,
            "export type {name}{} = {{",
            if generic { "<T = unknown>" } else { "" }
        )?;
        for f in &inputs {
            writeln!(
                out,
                "  {}{}: {};",
                f.property.wire_name,
                if f.property.required { "" } else { "?" },
                if generic && f.path == ["tool", "input"] {
                    "T".into()
                } else {
                    render(&f.property.shape, 1)?
                }
            )?;
        }
        writeln!(out, "}};")?;
        inventory.push((event.to_string(), name, inputs));
    }
    writeln!(out, "export interface EventInputs {{")?;
    for (event, name, _) in &inventory {
        writeln!(out, "  {event:?}: {name};")?;
    }
    writeln!(
        out,
        "}}\nconst INPUT_PATHS: Record<keyof EventInputs, Record<string, readonly string[]>> = {{"
    )?;
    for (event, _, inputs) in &inventory {
        writeln!(out, "{event:?}: {{")?;
        for f in inputs {
            writeln!(
                out,
                "{:?}: {},",
                f.property.wire_name,
                serde_json::to_string(&f.path)?
            )?;
        }
        writeln!(out, "}},")?;
    }
    out.push_str("};\n/** Project host facts only. The runtime supplies source, IDs, time, and manifest and validates the assembled request. */\nexport function toEventInput<K extends keyof EventInputs>(type: K, input: EventInputs[K]): Record<string, JsonValue> {\n const result: Record<string, JsonValue> = {type};\n for (const [field, path] of Object.entries(INPUT_PATHS[type])) {\n  const value = (input as unknown as Record<string, JsonValue | undefined>)[field];\n  if (value === undefined) continue;\n  let target = result;\n  for (const key of path.slice(0, -1)) {\n   if (target[key] === undefined) target[key] = {};\n   target = target[key] as Record<string, JsonValue>;\n  }\n  const leaf = path[path.length - 1];\n  if (leaf === undefined) throw new TypeError('empty generated input path');\n  target[leaf] = value;\n }\n return result;\n}\n");
    out.push_str("\nexport type EventType = keyof EventInputs;\nexport const events = {\n");
    for (event, name, _) in &inventory {
        let short = name.trim_end_matches("Input");
        let mut chars = short.chars();
        let key = chars.next().unwrap().to_lowercase().to_string() + chars.as_str();
        writeln!(out, "{key}: {event:?},")?;
    }
    out.push_str("} as const;\n");
    out.push_str("\n/** Named out-of-band content bindings. Streams are never inserted into wire models. */\nexport interface ContentSourceBinding<S> { readonly path: readonly (string | number)[]; readonly source: S }\nexport const contentSlots = {\n");
    for (event, _, inputs) in &inventory {
        writeln!(out, "{event:?}: {{")?;
        for slot in crate::ergonomics::content_slots(ir, inputs) {
            let path = slot
                .path
                .iter()
                .map(|p| {
                    if p == "*" {
                        "index".into()
                    } else {
                        format!("{p:?}")
                    }
                })
                .collect::<Vec<_>>()
                .join(", ");
            writeln!(
                out,
                "{}<S>({}source: S): ContentSourceBinding<S> {{ {}return {{path: [{path}], source}}; }},",
                slot.name,
                if slot.many { "index: number, " } else { "" },
                if slot.many {
                    "if (!Number.isSafeInteger(index) || index < 0) throw new RangeError('content index must be a nonnegative integer'); "
                } else {
                    ""
                }
            )?;
        }
        out.push_str("},\n");
    }
    out.push_str("} as const;\n");
    if let Some(request) = ir.types.iter().find(|t| t.name == "InterceptRequest") {
        let request_fields = fields(ir, &request.shape).unwrap_or_default();
        let params = fields(
            ir,
            &request_fields
                .iter()
                .find(|p| p.wire_name == "params")
                .unwrap()
                .shape,
        )
        .unwrap();
        let state_fields = fields(
            ir,
            &params
                .iter()
                .find(|p| p.wire_name == "state")
                .unwrap()
                .shape,
        )
        .unwrap();
        if let Shape::Enum { values, .. } = &state_fields
            .iter()
            .find(|p| p.wire_name == "permission")
            .unwrap()
            .shape
        {
            out.push_str("export enum Permission {\n");
            for value in values.iter().filter_map(|v| v.as_str()) {
                let mut chars = value.chars();
                let name = format!("{}{}", chars.next().unwrap().to_uppercase(), chars.as_str());
                writeln!(out, "{name} = {value:?},")?;
            }
            out.push_str("}\n");
        }
        out.push_str("\nexport type InitialState = NonNullable<InterceptRequest['params']['state']>;\nexport type StateCandidate = NonNullable<InitialState['candidate']>;\nexport const state = {\n candidate(value: JsonValue, provenance?: Record<string, JsonValue>): StateCandidate { return provenance === undefined ? {value} : {value, provenance}; },\n initial(permission: Permission, options: Partial<InitialState> = {}): InitialState { return {...options, candidate: options.candidate ?? null, permission}; }\n} as const;\n");
    }
    if let Some(named) = ir.types.iter().find(|t| t.name == "Effect") {
        if let Shape::Union { variants, .. } = &named.shape {
            out.push_str("\n/** Canonical effect constructors; grants and admission remain runtime checks. */\nexport const effects = {\n");
            for variant in variants {
                let Some(properties) = fields(ir, variant) else {
                    continue;
                };
                let Some(tag) = properties
                    .iter()
                    .find(|p| p.wire_name == "type")
                    .and_then(|p| {
                        if let Shape::Literal { value } = &p.shape {
                            value.as_str()
                        } else {
                            None
                        }
                    })
                else {
                    continue;
                };
                let op = properties
                    .iter()
                    .find(|p| p.wire_name == "operation")
                    .and_then(|p| {
                        if let Shape::Literal { value } = &p.shape {
                            value.as_str()
                        } else {
                            None
                        }
                    });
                let method = format!(
                    "{}{}",
                    tag,
                    op.map(|s| format!("_{}", s)).unwrap_or_default()
                );
                let mut args = Vec::new();
                let mut values = Vec::new();
                for p in &properties {
                    if let Shape::Literal { value } = &p.shape {
                        values.push(format!("{}: {}", p.wire_name, value));
                    } else if p.required {
                        args.push(format!("{}: {}", p.wire_name, render(&p.shape, 1)?));
                        values.push(p.wire_name.clone());
                    }
                }
                writeln!(
                    out,
                    " {method}({}): Effect {{ return {{ {} }}; }},",
                    args.join(", "),
                    values.join(", ")
                )?;
            }
            for variant in variants {
                let Some(properties) = fields(ir, variant) else {
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
                for target in targets.iter().filter_map(|v| v.as_str()) {
                    writeln!(out, " modify_{target}: {{")?;
                    for operation in operations.iter().filter_map(|v| v.as_str()) {
                        writeln!(
                            out,
                            " {operation}(value: JsonValue): Effect {{ return {{type: 'modify', target: {target:?}, operation: {operation:?}, value}}; }},"
                        )?;
                    }
                    out.push_str(" },\n");
                }
            }
            out.push_str("} as const;\n");
        }
    }
    Ok(())
}
