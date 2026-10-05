use super::*;

/// Compile every capability dimension and target into a canonical grant fragment.
pub(super) fn emit(renderer: &Renderer<'_>) -> Result<(String, Vec<String>)> {
    let ir = renderer.ir;
    let shape = &ir
        .types
        .iter()
        .find(|n| n.name == "Capabilities")
        .expect("capabilities root")
        .shape;
    let fields = properties(renderer, shape).expect("capabilities object");
    let effects = fields
        .iter()
        .find(|p| p.wire_name == "effects")
        .expect("effect vocabulary");
    let mut vocabulary = std::collections::BTreeSet::new();
    strings(&effects.shape, &mut vocabulary);
    let mut out = format!(
        "{HEADER}from __future__ import annotations\nfrom typing import Any\nfrom enum import StrEnum\nfrom copy import deepcopy\nimport json\n_UNSET: Any = object()\n\n"
    );
    let mut exports = vec!["Declaration".to_owned(), "Mode".to_owned()];
    let mut modes = std::collections::BTreeSet::new();
    for named in &ir.types {
        if named.name.ends_with("Subscription") {
            if let Some(fields) = properties(renderer, &named.shape) {
                if let Some(Value::String(mode)) = fields
                    .iter()
                    .find(|p| p.wire_name == "mode")
                    .and_then(|p| literal(ir, &p.shape))
                {
                    modes.insert(mode);
                }
            }
        }
    }
    out.push_str("class Mode(StrEnum):\n");
    for mode in modes {
        writeln!(out, "    {} = {mode:?}", snake_case(&mode).to_uppercase())?;
    }
    out.push('\n');
    for effect in &vocabulary {
        let name = pascal_fragment(effect);
        let detail = fields
            .iter()
            .find(|p| p.wire_name == *effect)
            .and_then(|p| properties(renderer, &p.shape));
        if let Some(detail) = detail {
            if detail
                .iter()
                .any(|p| properties(renderer, &p.shape).is_some())
            {
                continue;
            }
            grant(
                &mut out,
                &name,
                Some(effect),
                &[effect.clone()],
                &detail,
                ir,
            )?;
        } else {
            grant(&mut out, &name, Some(effect), &[], &[], ir)?;
        }
        exports.push(name);
    }
    for field in &fields {
        let Some(targets) = properties(renderer, &field.shape) else {
            continue;
        };
        for target in targets {
            let Some(mut detail) = properties(renderer, &target.shape) else {
                continue;
            };
            for p in &mut detail {
                if matches!(p.shape, Shape::Boolean) {
                    p.constructor_default = Some(Value::Bool(false));
                }
            }
            let name = format!(
                "{}{}",
                pascal_fragment(&field.wire_name),
                pascal_fragment(&target.wire_name)
            );
            let effect = vocabulary
                .contains(&field.wire_name)
                .then_some(field.wire_name.as_str());
            grant(
                &mut out,
                &name,
                effect,
                &[field.wire_name.clone(), target.wire_name],
                &detail,
                ir,
            )?;
            exports.push(name);
        }
    }
    out.push_str(r#"
def _merge(left: dict[str, Any], right: dict[str, Any]) -> None:
    for key, value in right.items():
        if key not in left:
            left[key] = deepcopy(value)
        elif isinstance(left[key], dict) and isinstance(value, dict):
            _merge(left[key], value)
        elif isinstance(left[key], list) and isinstance(value, list):
            left[key].extend(deepcopy(item) for item in value if item not in left[key])
        elif isinstance(left[key], bool) and isinstance(value, bool):
            left[key] = left[key] or value
        elif left[key] != value:
            raise ValueError(f"Conflicting capability grant: {key}")

class Declaration(dict[str, Any]):
    """Explicit delivery modes plus typed or canonical grant fragments.

    No mode, effect, form, or URL authority is inferred from another dimension.
    The canonical dictionary form remains accepted by Hooks without this helper.
    """
    def __init__(self, *, modes: list[Mode | str], grants: list[dict[str, Any]], **extra: Any) -> None:
        capabilities: dict[str, Any] = {"effects": []}
        for grant in grants:
            _merge(capabilities, grant)
        super().__init__(extra)
        self["modes"] = list(modes)
        self["capabilities"] = capabilities
"#);
    writeln!(out, "\n__all__ = {}", serde_json::to_string(&exports)?)?;
    Ok((out, exports))
}

fn strings(shape: &Shape, values: &mut std::collections::BTreeSet<String>) {
    match shape {
        Shape::Enum { values: items, .. } => {
            values.extend(items.iter().filter_map(Value::as_str).map(str::to_owned))
        }
        Shape::Union { variants, .. } => {
            for variant in variants {
                strings(variant, values);
            }
        }
        Shape::Array { items } => strings(items, values),
        _ => {}
    }
}

fn grant(
    out: &mut String,
    name: &str,
    effect: Option<&str>,
    path: &[String],
    fields: &[Property],
    ir: &Ir,
) -> Result<()> {
    constructor(out, name, fields, ir)?;
    out.pop();
    out.push_str("        details = dict(self)\n        self.clear()\n");
    let effects = effect.into_iter().collect::<Vec<_>>();
    writeln!(
        out,
        "        self['effects'] = {}",
        serde_json::to_string(&effects)?
    )?;
    if let Some((first, rest)) = path.split_first() {
        let mut value = "details".to_owned();
        for key in rest.iter().rev() {
            value = format!("{{{key:?}: {value}}}");
        }
        writeln!(out, "        self[{first:?}] = {value}")?;
    } else {
        out.push_str("        self.update(details)\n");
    }
    out.push('\n');
    Ok(())
}
