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
    // Derive identifiers from the capability descriptor, not a copied vocabulary.
    if let Some(named) = ir.types.iter().find(|t| t.name == "Capabilities") {
        fn names(ir: &Ir, shape: &Shape, out: &mut std::collections::BTreeSet<String>) {
            match shape {
                Shape::Enum { values, .. } => {
                    out.extend(values.iter().filter_map(|v| v.as_str().map(str::to_owned)))
                }
                Shape::Literal { value } => {
                    if let Some(value) = value.as_str() {
                        out.insert(value.to_owned());
                    }
                }
                Shape::Array { items } => names(ir, items, out),
                Shape::Ref { name } => {
                    if let Some(named) = ir.types.iter().find(|t| t.name == *name) {
                        names(ir, &named.shape, out);
                    }
                }
                Shape::Union { variants, .. } | Shape::Intersection { variants } => {
                    for variant in variants {
                        names(ir, variant, out);
                    }
                }
                _ => {}
            }
        }
        if let Some(effect) = fields(ir, &named.shape)
            .and_then(|fields| fields.into_iter().find(|p| p.wire_name == "effects"))
        {
            let mut values = std::collections::BTreeSet::new();
            names(ir, &effect.shape, &mut values);
            out.push_str("\n/** Schema-derived family identifiers; custom strings remain supported. */\nexport const effectNames = Object.freeze({\n");
            for value in values {
                writeln!(out, "  {value:?}: {value:?},")?;
            }
            out.push_str("} as const);\nexport type EffectName = OpenString<typeof effectNames[keyof typeof effectNames]>;\n/** Advertised membership only, not authorization or target/operation admission. */\nexport function supports(capabilities: { readonly effects: readonly string[] }, effect: EffectName): boolean { return capabilities.effects.includes(effect); }\n");
        }
    }
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
            if !crate::ergonomics::legacy_source_binding(&slot) {
                continue;
            }
            let indices = if slot.many {
                vec!["index".to_owned()]
            } else {
                Vec::new()
            };
            let mut cursor = 0;
            let path = slot
                .path
                .iter()
                .map(|p| {
                    if p == "*" {
                        {
                            let name = indices[cursor].clone();
                            cursor += 1;
                            name
                        }
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
                indices.iter().map(|i| format!("{i}: number, ")).collect::<String>(),
                indices.iter().map(|i| format!("if (!Number.isSafeInteger({i}) || {i} < 0) throw new RangeError('content index must be a nonnegative integer'); ")).collect::<String>()
            )?;
        }
        out.push_str("},\n");
    }
    out.push_str("} as const;\n");
    emit_host_inputs(ir, out, &inventory)?;
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

/// Host-only overrides are derived exclusively from schema-owned content slots.
fn emit_host_inputs(
    ir: &Ir,
    out: &mut String,
    inventory: &[(String, String, Vec<crate::ergonomics::InputField>)],
) -> Result<()> {
    out.push_str(
        r#"
/** Opaque host-only ownership handle. Wrapping never evaluates or inspects S. */
declare const ownedAttachmentBrand: unique symbol;
export interface OwnedAttachment<S> { readonly [ownedAttachmentBrand]: S }
const ownedAttachmentSources = new WeakMap<object, unknown>();
export function ownedAttachment<S>(source: S): OwnedAttachment<S> {
 const handle = Object.freeze({});
 ownedAttachmentSources.set(handle, source);
 return handle as OwnedAttachment<S>;
}
export type HostTextPart = {
 kind: 'text'; text: string; id?: string; category?: string;
 mediaType?: 'text/plain'; selection?: 'body'; synthesized?: boolean;
};
export type HostAttachmentPart<S> = {
 kind: 'attachment'; mediaType: string; body: OwnedAttachment<S>;
 id?: string; category?: string; selection?: 'body'; synthesized?: boolean;
};
type HostPartFields = 'id' | 'kind' | 'mediaType' | 'selection' | 'category' | 'synthesized';
// Pick explicit keys to keep the host envelope closed even when wire models are open.
type HostWirePart =
 Pick<TextGapPart, HostPartFields | 'gap' | 'size' | 'sha256'> |
 Pick<TextMetadataPart, HostPartFields | 'size' | 'sha256'> |
 Pick<TextOmittedPart, HostPartFields | 'size' | 'sha256'> |
 Pick<AttachmentBodyPart, HostPartFields | 'body'> |
 Pick<AttachmentGapPart, HostPartFields | 'gap' | 'size' | 'sha256'> |
 Pick<AttachmentMetadataPart, HostPartFields | 'size' | 'sha256'> |
 Pick<AttachmentOmittedPart, HostPartFields | 'size' | 'sha256'>;
export type HostContentPart<S> = HostTextPart | HostAttachmentPart<S> | HostWirePart;
export interface HostMessage<S> {
 id?: string; role: 'system' | 'developer' | 'user' | 'assistant' | 'tool';
 parts: HostContentPart<S>[]; synthesized?: boolean;
}
/** Only generated slot paths are replaced; application-owned payload types stay untouched. */
type HostSlot<T, P extends readonly string[], S> =
 P extends readonly [] ? HostContentPart<S> :
 P extends readonly ['parts', '*'] ? HostMessage<S> :
 P extends readonly [infer H extends string, ...infer R extends string[]] ?
 H extends '*' ? T extends (infer U)[] ? HostSlot<U, R, S>[] : T :
 T extends object ? { [K in keyof T]: K extends H ? HostSlot<T[K], R, S> : T[K] } : T : T;
export interface HostEventInputs<S = unknown> {
"#,
    );
    for (event, name, inputs) in inventory {
        writeln!(out, "{event:?}: {{")?;
        let slots = crate::ergonomics::content_slots(ir, inputs);
        for f in inputs {
            let mut ty = format!("{name}[{:?}]", f.property.wire_name);
            for slot in &slots {
                if slot.path.starts_with(&f.path) {
                    let path = &slot.path[f.path.len()..];
                    let tuple = path
                        .iter()
                        .map(|p| format!("{p:?}"))
                        .collect::<Vec<_>>()
                        .join(", ");
                    ty = format!("HostSlot<{ty}, [{tuple}], S>");
                }
            }
            writeln!(
                out,
                "{}{}: {ty};",
                f.property.wire_name,
                if f.property.required { "" } else { "?" }
            )?;
        }
        out.push_str("};\n");
    }
    out.push_str(
        "}\nconst HOST_CONTENT_PATHS: Record<EventType, readonly (readonly string[])[]> = {\n",
    );
    for (event, _, inputs) in inventory {
        let paths = crate::ergonomics::content_slots(ir, inputs)
            .into_iter()
            .map(|s| s.path)
            .collect::<Vec<_>>();
        writeln!(out, "{event:?}: {},", serde_json::to_string(&paths)?)?;
    }
    out.push_str(r#"};
export interface PendingAttachment {
 readonly path: readonly (string | number)[];
 readonly selection: 'body';
}
/** @internal Runtime planning envelope. */
export interface HostInputProjection<S> {
 /** Metadata-only wire facts until the runtime resolves pending attachments. */
 readonly event: Record<string, JsonValue>;
 readonly bindings: ContentSourceBinding<S>[];
 readonly pending: PendingAttachment[];
}
const hostObjectIds = new WeakMap<object, string>();
let hostObjectSequence = 0;
function hostIdentity(value: Record<string, unknown>): {id: string; synthesized?: boolean} {
 if (value.synthesized !== undefined && typeof value.synthesized !== 'boolean') throw new TypeError('invalid synthesized flag');
 if (value.id !== undefined) {
  if (typeof value.id !== 'string' || value.id.length === 0) throw new TypeError('invalid content id');
  return {id: value.id, ...(value.synthesized === undefined ? {} : {synthesized: value.synthesized as boolean})};
 }
 if (value.synthesized === false) throw new TypeError('missing id cannot be explicitly nonsynthesized');
 let id = hostObjectIds.get(value);
 if (id === undefined) { id = `host-content-${++hostObjectSequence}`; hostObjectIds.set(value, id); }
 return {id, synthesized: true};
}
function hostRecord(value: unknown): Record<string, unknown> {
 if (value === null || typeof value !== 'object' || Array.isArray(value) || ownedAttachmentSources.has(value)) throw new TypeError('invalid content placement');
 return value as Record<string, unknown>;
}
/** No I/O. The runtime must resolve bindings and replace metadata at pending paths
 * with body references before delivering a body-selected event. Paths are event-relative,
 * exactly like contentSlots. Opaque native/tool payloads are not traversed or serialized. */
/** @internal Runtime conversion hook; applications pass HostEventInputs to runtime methods. */
export function _projectHostInput<K extends EventType, S = unknown>(type: K, input: HostEventInputs<S>[K]): HostInputProjection<S> {
 const bindings: ContentSourceBinding<S>[] = [];
 const pending: PendingAttachment[] = [];
 const event = toEventInput(type, input as unknown as EventInputs[K]);
 function checkedPart(value: Record<string, unknown>): JsonValue {
  if (value.size !== undefined && (typeof value.size !== 'number' || !Number.isSafeInteger(value.size) || value.size < 0)) throw new TypeError('invalid content size');
  if (value.sha256 !== undefined && (typeof value.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(value.sha256))) throw new TypeError('invalid content hash');
  if (value.gap !== undefined) {
   const gap = hostRecord(value.gap);
   if (typeof gap.reason !== 'string' || gap.reason.length === 0 || (gap.path !== undefined && (typeof gap.path !== 'string' || gap.path.length === 0))) throw new TypeError('invalid content gap');
   if (value.body !== undefined) throw new TypeError('content cannot have both body and gap');
  }
  const parsed = parseContentItem(value);
  if (!parsed.ok) throw new TypeError('invalid canonical content part');
  return value as JsonValue;
 }
 function part(value: unknown, path: (string | number)[]): JsonValue {
  const p = hostRecord(value);
  for (const [field, entry] of Object.entries(p)) {
   if (entry !== null && typeof entry === 'object' && ownedAttachmentSources.has(entry) && !(p.kind === 'attachment' && field === 'body')) throw new TypeError('owned attachment in non-body field');
  }
  const identity = hostIdentity(p);
  if (p.category !== undefined && (typeof p.category !== 'string' || p.category.length === 0)) throw new TypeError('invalid content category');
  const base = {...identity, ...(p.category === undefined ? {} : {category: p.category as string})};
  if (p.kind === 'text' && p.mediaType !== undefined && p.mediaType !== 'text/plain') throw new TypeError('invalid text media type');
  if (p.kind === 'text' && 'text' in p) {
   if (typeof p.text !== 'string' || (p.mediaType !== undefined && p.mediaType !== 'text/plain') || (p.selection !== undefined && p.selection !== 'body') || ['body', 'gap', 'size', 'sha256'].some(key => key in p)) throw new TypeError('invalid inline text');
   return checkedPart({...p, ...base, kind: 'text', mediaType: 'text/plain', selection: 'body', text: p.text});
  }
  if (p.kind === 'attachment') {
   // MIME parameters are host metadata, not part of media classification.
   if (typeof p.mediaType !== 'string' || !/^[!#$%&'*+.^_`|~0-9A-Za-z-]+\/[!#$%&'*+.^_`|~0-9A-Za-z-]+(?:[ \t]*;[ \t]*[!#$%&'*+.^_`|~0-9A-Za-z-]+=(?:[!#$%&'*+.^_`|~0-9A-Za-z-]+|"(?:[^"\\\r\n]|\\[^\r\n])*"))*[ \t]*$/.test(p.mediaType)) throw new TypeError('invalid attachment media type');
   const media = p.mediaType.split(';')[0]!.trim().toLowerCase();
   if (media.startsWith('text/') || /(?:\/json|\+json)$/.test(media)) throw new TypeError('text and JSON attachments are forbidden');
   if (p.body !== null && typeof p.body === 'object' && ownedAttachmentSources.has(p.body)) {
    if (p.selection !== undefined && p.selection !== 'body') throw new TypeError('owned attachment requires body selection');
    if (['text', 'ref', 'gap', 'size', 'sha256'].some(key => key in p)) throw new TypeError('invalid owned attachment placement');
    bindings.push({path: [...path], source: ownedAttachmentSources.get(p.body) as S});
    pending.push({path: [...path], selection: 'body'});
    const {body: _body, ...metadata} = p;
    return checkedPart({...metadata, ...base, kind: 'attachment', mediaType: p.mediaType, selection: 'metadata'});
   }
   if (p.body !== undefined) {
    const body = hostRecord(p.body);
    if (typeof body.ref !== 'string' || body.ref.length === 0 || p.selection !== 'body') throw new TypeError('invalid attachment body');
   }
  } else if (p.kind !== 'text') throw new TypeError('invalid content kind');
  if (!['body', 'metadata', 'omit'].includes(p.selection as string)) throw new TypeError('invalid content selection');
  if (p.selection === 'body' && p.body === undefined && p.gap === undefined) throw new TypeError('body selection requires text, reference, or gap');
  if (p.selection !== 'body' && ('body' in p || 'text' in p || 'gap' in p)) throw new TypeError('metadata or omitted content cannot have a body');
  return checkedPart({...p, ...base});
 }
 function walk(value: unknown, remaining: readonly string[], path: (string | number)[]): unknown {
  if (remaining.length === 0) return part(value, path);
  const [head, ...tail] = remaining;
  if (head === '*') {
   if (!Array.isArray(value)) throw new TypeError('content slot must be an array');
   return Array.from(value, (entry, index) => walk(entry, tail, [...path, index]));
  }
  const record = hostRecord(value);
  if (head === 'parts') {
   if (!['system', 'developer', 'user', 'assistant', 'tool'].includes(record.role as string) || !Array.isArray(record.parts)) throw new TypeError('invalid canonical message');
   return {...record, ...hostIdentity(record), parts: walk(record.parts, tail, [...path, 'parts'])};
  }
  if (head === undefined) throw new TypeError('empty slot path');
  if (record[head] === undefined) return record;
  return {...record, [head]: walk(record[head], tail, [...path, head])};
 }
 let projected: unknown = event;
 for (const path of HOST_CONTENT_PATHS[type]) projected = walk(projected, path, []);
 return {event: projected as Record<string, JsonValue>, bindings, pending};
}
"#);
    Ok(())
}
