//! Public subscription constructors retain wire models while accepting native TS options.
use super::*;

fn event_names(ir: &Ir, shape: &Shape, names: &mut std::collections::BTreeSet<String>) {
    match shape {
        Shape::Enum { values, .. } => {
            names.extend(values.iter().filter_map(|v| v.as_str().map(str::to_owned)))
        }
        Shape::Array { items } => event_names(ir, items, names),
        Shape::Union { variants, .. } | Shape::Intersection { variants } => {
            for variant in variants {
                event_names(ir, variant, names);
            }
        }
        Shape::Ref { name } => {
            if let Some(named) = ir.types.iter().find(|named| named.name == *name) {
                event_names(ir, &named.shape, names);
            }
        }
        _ => {}
    }
}

pub(super) fn emit(ir: &Ir, out: &mut String) -> Result<()> {
    if ![
        "InterceptSubscription",
        "ObserveSubscription",
        "ContentSelection",
    ]
    .iter()
    .all(|name| ir.types.iter().any(|named| named.name == *name))
    {
        return Ok(());
    }
    out.push_str(r#"
declare const durationBrand: unique symbol;
/** Positive integral milliseconds, created with duration.milliseconds. */
export type Duration = number & { readonly [durationBrand]: true };
export const duration = Object.freeze({
  milliseconds(value: number): Duration {
    if (!Number.isSafeInteger(value) || value < 1) throw new RangeError('duration must be positive safe integer milliseconds');
    return value as Duration;
  },
});
export type FailurePolicy = 'fail-open' | 'fail-closed';
export type Subscription = InterceptSubscription | ObserveSubscription;
/** Standard selectors use events; wildcard patterns are checked by the schema. */
export type SubscriptionEvent = InterceptEvent | ObserveEvent;
export const content = Object.freeze({
  metadata(): ContentSelection { return {default: 'metadata'}; },
  body(): ContentSelection { return {default: 'body'}; },
  omit(): ContentSelection { return {default: 'omit'}; },
});
"#);
    // Omit on an open wire object loses named fields through its string index
    // signature. Project the schema fields instead, preserving required options.
    for (model, options, selector) in [
        (
            "InterceptSubscription",
            "InterceptOptions",
            "InterceptEvent",
        ),
        ("ObserveSubscription", "ObserveOptions", "ObserveEvent"),
    ] {
        let named = ir.types.iter().find(|named| named.name == model).unwrap();
        let fields = crate::ergonomics::object_fields(ir, &named.shape)
            .ok_or_else(|| anyhow::anyhow!("{model} must be an object"))?;
        let mut names = std::collections::BTreeSet::new();
        if let Some(events) = fields.iter().find(|field| field.wire_name == "events") {
            event_names(ir, &events.shape, &mut names);
        }
        writeln!(
            out,
            "const {model}Events = new Set<string>({});",
            serde_json::to_string(&names)?
        )?;
        let selectors = names
            .iter()
            .map(|name| format!("{name:?}"))
            .collect::<Vec<_>>();
        writeln!(
            out,
            "export type {selector} = {} | '*' | `${{string}}.*`;",
            if selectors.is_empty() {
                "never".to_owned()
            } else {
                selectors.join(" | ")
            }
        )?;
        writeln!(out, "export type {options} = {{")?;
        for field in fields {
            let (name, ty) = match field.wire_name.as_str() {
                "mode" => continue,
                "events" => ("events", format!("readonly {selector}[]")),
                "timeoutMs" => ("timeout", "Duration".to_owned()),
                "failurePolicy" => ("failurePolicy", "FailurePolicy".to_owned()),
                name => (name, render(&field.shape, 1)?),
            };
            writeln!(
                out,
                "  {name}{}: {ty};",
                if field.required { "" } else { "?" }
            )?;
        }
        out.push_str("};\n");
    }
    out.push_str(r#"
function checkSubscriptionEvents(values: readonly SubscriptionEvent[], allowed: ReadonlySet<string>): void {
  if (values.length === 0 || new Set(values).size !== values.length) throw new TypeError('events must be nonempty and unique');
  for (const value of values) {
    if (typeof value !== 'string' || (!allowed.has(value) && !/^(?:\*|[a-z][a-z0-9]*\.\*)$/.test(value))) throw new TypeError('invalid subscription event: ' + value);
  }
}
function checkedSubscription<T extends Subscription>(name: string, value: unknown): T {
  const result = parseRoot(name, value);
  if (!result.ok) throw new TypeError(result.diagnostics.map(d => `${d.path}: ${d.message}`).join('; '));
  return result.value as T;
}
/** Construct validated wire subscriptions without spelling protocol fields. */
export const subscription = Object.freeze({
  intercept(options: InterceptOptions): InterceptSubscription {
    const {timeout, ...rest} = options;
    checkSubscriptionEvents(options.events, InterceptSubscriptionEvents);
    if (options.failurePolicy !== 'fail-open' && options.failurePolicy !== 'fail-closed') throw new TypeError('invalid failure policy');
    return checkedSubscription<InterceptSubscription>('InterceptSubscription', {...rest, events: [...options.events], mode: 'intercept', timeoutMs: duration.milliseconds(timeout)});
  },
  observe(options: ObserveOptions): ObserveSubscription {
    checkSubscriptionEvents(options.events, ObserveSubscriptionEvents);
    return checkedSubscription<ObserveSubscription>('ObserveSubscription', {...options, events: [...options.events], mode: 'observe'});
  },
});
"#);
    Ok(())
}
