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
    let mut builders = String::new();
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
        if let Some(ref detail) = detail {
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
        builder_method(&mut builders, &name, detail.as_deref().unwrap_or(&[]), ir)?;
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
            builder_method(&mut builders, &name, &detail, ir)?;
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
    out.push_str(
        r#"
class Builder:
    """Immutable declaration; intercept deliberately supports observation too.

    Grants describe host support, not evidence that the host enacted effects.
    Raw Declaration remains available without convenience inference.
    """
    __slots__ = ("_modes", "_capabilities")

    def __init__(self, modes: tuple[Mode, ...], capabilities: dict[str, Any]) -> None:
        object.__setattr__(self, "_modes", modes)
        object.__setattr__(self, "_capabilities", json.dumps(capabilities))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("Capability builders are immutable")

    def to_wire(self) -> dict[str, Any]:
        capabilities = json.loads(self._capabilities)
        if Mode.INTERCEPT in self._modes and capabilities == {"effects": []}:
            raise ValueError("Intercept declarations require an explicit grant")
        return {"modes": list(self._modes), "capabilities": capabilities}

    def _add(self, grant: dict[str, Any]) -> Builder:
        if grant.get("effects") and Mode.INTERCEPT not in self._modes:
            raise ValueError("Observation-only declarations cannot grant effects")
        capabilities = json.loads(self._capabilities)
        _merge(capabilities, grant)
        return Builder(self._modes, capabilities)

"#,
    );
    out.push_str(&builders);
    out.push_str(
        r#"
def intercept() -> Builder:
    """Advertise both intercept and observe delivery, with explicit grants."""
    return Builder((Mode.INTERCEPT, Mode.OBSERVE), {"effects": []})

def observe() -> Builder:
    """Advertise observation only; no effect authority is inferred."""
    return Builder((Mode.OBSERVE,), {"effects": []})
"#,
    );
    exports.extend(["Builder", "intercept", "observe"].map(str::to_owned));
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

fn builder_method(out: &mut String, name: &str, fields: &[Property], ir: &Ir) -> Result<()> {
    let method = allocate_identifier(&snake_case(name), "grant", &mut reserved_identifiers());
    write!(out, "    def {method}(self")?;
    if !fields.is_empty() {
        out.push_str(", *");
    }
    for field in fields {
        let optional = !field.required
            || field.constructor_default.is_some()
            || literal(ir, &field.shape).is_some();
        write!(
            out,
            ", {}: {}{}",
            snake_case(&field.wire_name),
            annotation(ir, &field.shape),
            if optional { " = _UNSET" } else { "" }
        )?;
    }
    out.push_str(") -> Builder:\n        arguments: dict[str, Any] = {}\n");
    let mut bools = Vec::new();
    for field in fields {
        let param = snake_case(&field.wire_name);
        writeln!(
            out,
            "        if {param} is not _UNSET:\n            arguments[{param:?}] = {param}"
        )?;
        if matches!(field.shape, Shape::Boolean) {
            bools.push(param.clone());
            writeln!(
                out,
                "            if not isinstance({param}, bool):\n                raise ValueError({:?})",
                format!("{param} must be boolean")
            )?;
        }
        let mut vocabulary = std::collections::BTreeSet::new();
        strings(&field.shape, &mut vocabulary);
        if !vocabulary.is_empty() && matches!(field.shape, Shape::Array { .. }) {
            writeln!(
                out,
                "            if not {param} or any(value not in {} for value in {param}):\n                raise ValueError({:?})",
                serde_json::to_string(&vocabulary)?,
                format!("{param} requires known, nonempty values")
            )?;
        }
        if let Some(Value::Bool(expected)) = literal(ir, &field.shape) {
            writeln!(
                out,
                "            if {param} is not {}:\n                raise ValueError({:?})",
                if expected { "True" } else { "False" },
                format!("{param} must be {expected}")
            )?;
        }
    }
    if !bools.is_empty() {
        writeln!(
            out,
            "        if not ({}):\n            raise ValueError(\"Select at least one operation\")",
            bools
                .iter()
                .map(|param| format!("{param} is True"))
                .collect::<Vec<_>>()
                .join(" or ")
        )?;
    }
    writeln!(out, "        return self._add({name}(**arguments))\n")?;
    Ok(())
}
