
#[cfg(test)]
mod tests {
    use super::*;
    fn draft() -> Ir {
        crate::compiler::compile(&std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../.."), "draft").unwrap()
    }
    #[test]
    fn every_named_object_and_boundary_is_emitted_without_runtime_overwrites() {
        let ir = draft();
        let files = emit(&ir).unwrap();
        let ids = IdentifierMap::new(&ir);
        let renderer = Renderer { ir: &ir, names: &ids.types };
        for named in &ir.types {
            if properties(&renderer, &named.shape).is_some() {
                assert!(files["_models/__init__.py"].contains(&format!("class {}(", named.name)), "{}", named.name);
            }
        }
        assert_eq!(files["_boundaries.py"].matches("async def ").count(), 32);
        assert_eq!(files["_boundaries.py"].matches("return await cast(\"Hooks\", self).dispatch(").count(), 32);
        for runtime in ["registration.py", "content.py", "__init__.py"] { assert!(!files.contains_key(runtime)); }
        assert!(files["_models/tool.py"].contains("ToolBeforeEventCall as Call"));
        assert!(files["_models/tool.py"].contains("__all__ = ["));
        assert!(files["tool.py"].contains("import Call as Call"));
        assert!(files["tool.py"].contains("import Tool as Tool"));
        assert!(files["capability.py"].contains("from ._grants import ModifyInput as ModifyInput"));
        assert!(!files["capability.py"].contains("from ._models.capability import ModifyInput as ModifyInput"));
        assert!(files["_boundaries.py"].contains("input: models.ToolBeforeInput | dict[str, Any]"));
        assert_eq!(files["_boundaries.py"].matches("-> HookResult:").count(), 32);
        assert!(files["_models/effect.py"].contains("EffectModify as Modify"));
        assert!(files["path.py"].contains("NATIVE = Path.NATIVE"));
    }
    #[test]
    fn constructor_defaults_do_not_change_wire_validation_descriptors() {
        let ir = draft();
        let fields = vec![
            Property { wire_name: "requiredName".into(), required: true, constructor_default: None, shape: Shape::String },
            Property { wire_name: "enabled".into(), required: false, constructor_default: Some(Value::Bool(false)), shape: Shape::Boolean },
            Property { wire_name: "names".into(), required: false, constructor_default: Some(serde_json::json!([])), shape: Shape::Array { items: Box::new(Shape::String) } },
            Property { wire_name: "nullable".into(), required: false, constructor_default: None, shape: Shape::Null },
        ];
        let mut output = String::new();
        constructor(&mut output, "Synthetic", &fields, &ir).unwrap();
        assert!(output.contains("required_name: str,"));
        assert!(output.contains("enabled: bool = _UNSET"));
        assert!(output.contains("json.loads(\"false\")"));
        assert!(output.contains("json.loads(\"[]\")"));
        assert!(output.contains("if nullable is not _UNSET:"));
        assert!(!serde_json::to_string(&fields).unwrap().contains("constructor_default"));
    }
}
