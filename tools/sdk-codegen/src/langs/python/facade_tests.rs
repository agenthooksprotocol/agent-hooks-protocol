#[cfg(test)]
mod tests {
    use super::*;
    fn draft() -> Ir {
        crate::compiler::compile(
            &std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../.."),
            "draft",
        )
        .unwrap()
    }
    #[test]
    fn real_elicitation_refs_and_connection_transports_are_readable() {
        let ir = draft();
        let source = emit(&ir).unwrap();
        let models = &source["_models/__init__.py"];
        let Shape::Union { variants, .. } = &ir
            .types
            .iter()
            .find(|n| n.name == "McpElicitationPrimitiveSchemaDefinition")
            .unwrap()
            .shape
        else {
            panic!("elicitation union")
        };
        assert_eq!(variants.len(), 8);
        let names = variants
            .iter()
            .map(|v| match v {
                Shape::Ref { name } => name.clone(),
                _ => panic!("canonical reference"),
            })
            .collect::<HashSet<_>>();
        assert_eq!(names.len(), 8);
        for name in names {
            assert_eq!(
                models.matches(&format!("class {name}(")).count(),
                1,
                "{name}"
            );
        }
        for transport in ["Http", "Sse", "Stdio", "CustomTransport"] {
            assert!(models.contains(&format!("class ExecutionEventMcpConnection{transport}(")));
        }
        assert!(!models.contains("ConnectionObject"));
        assert!(!models.contains("PrimitiveSchemaDefinitionString"));
    }

    #[test]
    fn capability_event_alias_has_an_explicit_stable_owner() {
        let files = emit(&draft()).unwrap();
        assert!(files["_models/capability.py"].contains("from . import CapabilitiesResponseResultManifestEventsItemEvent as Event"));
        assert!(files["capability.py"].contains("from ._models.capability import Event as Event"));
    }

    #[test]
    fn aliases_cannot_silently_overwrite_other_models() {
        let mut exports = BTreeMap::new();
        export(&mut exports, "Input".into(), "FirstInput".into()).unwrap();
        export(&mut exports, "Input".into(), "FirstInput".into()).unwrap();
        assert!(export(&mut exports, "Input".into(), "OtherInput".into()).is_err());
        assert_eq!(exports["Input"], "FirstInput");
    }

    #[test]
    fn exact_duplicate_models_share_one_declaration_without_changing_ir() {
        let mut ir = draft();
        let shape = Shape::Object {
            properties: vec![],
            forbidden_property_sets: vec![],
            additional: AdditionalProperties::Allowed,
        };
        ir.types.push(crate::model::NamedType {
            name: "Duplicate".into(),
            source: "test".into(),
            shape: Shape::Union {
                variants: vec![shape.clone(), shape],
                discriminator: None,
                mode: crate::model::UnionMode::OneOf,
            },
        });
        let source = emit(&ir).unwrap();
        assert_eq!(
            source["_models/__init__.py"]
                .matches("class DuplicateObject(")
                .count(),
            1
        );
        let wire = super::super::emit(&ir).unwrap();
        assert!(wire.contains("Duplicate"));
        let Shape::Union { variants, .. } = &ir.types.last().unwrap().shape else {
            panic!("union")
        };
        assert_eq!(variants.len(), 2);
    }

    #[test]
    fn public_model_collisions_fail_instead_of_overwriting() {
        let mut shapes = BTreeMap::new();
        collect("Choice", &Shape::String, &mut shapes).unwrap();
        collect("Choice", &Shape::String, &mut shapes).unwrap();
        assert!(
            collect("Choice", &Shape::Number, &mut shapes)
                .unwrap_err()
                .to_string()
                .contains("public Python model collision")
        );
        assert!(
            literal_labels(&[Value::String("a-b".into()), Value::String("a_b".into())]).is_err()
        );
    }

    #[test]
    fn referenced_candidate_imports_its_canonical_constructor() {
        let mut ir = draft();
        let mut shapes = BTreeMap::new();
        for named in &ir.types {
            collect(&named.name, &named.shape, &mut shapes).unwrap();
        }
        let Shape::Union { variants, .. } = shapes
            .get_mut("InterceptRequestParamsStateCandidate")
            .unwrap()
        else {
            panic!("candidate union")
        };
        let candidate = variants
            .iter_mut()
            .find(|shape| matches!(shape, Shape::Object { .. }))
            .unwrap();
        let object = std::mem::replace(
            candidate,
            Shape::Ref {
                name: "CanonicalCandidate".into(),
            },
        );
        ir.types.push(crate::model::NamedType {
            name: "CanonicalCandidate".into(),
            source: "test".into(),
            shape: object.clone(),
        });
        collect("CanonicalCandidate", &object, &mut shapes).unwrap();
        let identifiers = IdentifierMap::new(&ir).unwrap();
        let renderer = Renderer {
            ir: &ir,
            names: &identifiers.types,
        };
        let mut files = emit(&ir).unwrap();
        ergonomic_modules(&mut files, &renderer, &shapes).unwrap();
        assert!(files["state.py"].contains("import CanonicalCandidate as Candidate"));
        assert!(files["state.py"].contains("import CanonicalCandidateProvenance as Provenance"));
    }

    #[test]
    fn union_models_and_candidate_imports_are_semantic_and_order_independent() {
        let mut ir = draft();
        let before = emit(&ir).unwrap();
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
                Shape::Array { items } => reverse_unions(items),
                _ => {}
            }
        }
        for named in &mut ir.types {
            reverse_unions(&mut named.shape);
        }
        let after = emit(&ir).unwrap();
        assert_eq!(before, after);
        assert!(!before["_models/__init__.py"].contains("Variant2"));
        assert!(before["state.py"].contains("CandidateValueObject as Candidate"));
    }

    #[test]
    fn ambiguous_inline_alternatives_require_canonical_names() {
        let mut shapes = BTreeMap::new();
        let union = Shape::Union {
            variants: vec![
                Shape::Literal {
                    value: Value::String("a-b".into()),
                },
                Shape::Literal {
                    value: Value::String("a_b".into()),
                },
            ],
            discriminator: None,
            mode: crate::model::UnionMode::OneOf,
        };
        assert!(collect("Collision", &union, &mut shapes).is_err());
        let mut ir = draft();
        ir.types.push(crate::model::NamedType {
            name: "CollisionEnum".into(),
            source: "test".into(),
            shape: Shape::Enum {
                values: vec![Value::String("a-b".into()), Value::String("a_b".into())],
                open_strings: true,
            },
        });
        assert!(emit(&ir).is_err());
    }

    #[test]
    fn every_named_object_and_boundary_is_emitted_without_runtime_overwrites() {
        let ir = draft();
        let files = emit(&ir).unwrap();
        let ids = IdentifierMap::new(&ir).unwrap();
        let renderer = Renderer {
            ir: &ir,
            names: &ids.types,
        };
        for named in &ir.types {
            if properties(&renderer, &named.shape).is_some() {
                assert!(
                    files["_models/__init__.py"].contains(&format!("class {}(", named.name)),
                    "{}",
                    named.name
                );
            }
        }
        assert_eq!(files["_boundaries.py"].matches("async def ").count(), 32);
        assert_eq!(
            files["_boundaries.py"]
                .matches("return await cast(\"Hooks\", self).dispatch(")
                .count(),
            32
        );
        for runtime in ["registration.py", "content.py", "__init__.py"] {
            assert!(!files.contains_key(runtime));
        }
        assert!(files["_models/tool.py"].contains("ToolBeforeEventCall as Call"));
        assert!(files["_models/tool.py"].contains("__all__ = ["));
        assert!(files["tool.py"].contains("import Call as Call"));
        assert!(files["tool.py"].contains("import Tool as Tool"));
        assert!(files["capability.py"].contains("from ._grants import ModifyInput as ModifyInput"));
        assert!(
            !files["capability.py"]
                .contains("from ._models.capability import ModifyInput as ModifyInput")
        );
        assert!(files["_boundaries.py"].contains("input: models.ToolBeforeInput | dict[str, Any]"));
        assert_eq!(
            files["_boundaries.py"].matches("-> HookResult:").count(),
            32
        );
        assert!(files["_models/effect.py"].contains("EffectModify as Modify"));
        assert!(files["path.py"].contains("NATIVE = Path.NATIVE"));
    }
    #[test]
    fn ergonomic_surfaces_use_shared_metadata_and_canonical_models() {
        let ir = draft();
        let files = emit(&ir).unwrap();
        let models = &files["_models/__init__.py"];
        assert_eq!(models.matches("    def to_wire(self)").count(), 32);
        assert!(models.contains("call_id: str"));
        assert!(models.contains("target = target.setdefault(\"call\", {})"));
        assert!(
            files["state.py"].contains(
                "def initial(permission: Permission, *, candidate: Candidate | None = None"
            )
        );
        assert!(
            files["candidate.py"].contains("return Candidate(value=value, provenance=provenance)")
        );
        for code in crate::ergonomics::DIAGNOSTIC_CODES {
            assert!(files["diagnostics.py"].contains(&format!("= {code:?}")));
        }
        assert!(files["effect.py"].contains("def replace_input(value: Any) -> Modify:"));
        assert!(files["effect.py"].contains("def merge_input(value: Any) -> Modify:"));
        assert!(files["_grants.py"].contains("def intercept() -> Builder:"));
        assert!(files["_grants.py"].contains("return Builder((Mode.INTERCEPT, Mode.OBSERVE),"));
        assert!(files["_grants.py"].contains("def elicitation_form(self) -> Builder:"));
        assert!(files["capability.py"].contains("from ._grants import intercept as intercept"));
        assert!(files["_boundaries.py"].contains("CONTENT_SOURCE_SLOTS:"));
        assert!(
            models
                .contains("def bind_items_source(self, source: OwnedContentSource, *, index: int)")
        );
        assert!(models.contains("def bind_instructions_source(self, source: OwnedContentSource)"));
    }
    #[test]
    fn constructor_defaults_do_not_change_wire_validation_descriptors() {
        let ir = draft();
        let fields = vec![
            Property {
                wire_name: "requiredName".into(),
                required: true,
                constructor_default: None,
                shape: Shape::String,
            },
            Property {
                wire_name: "enabled".into(),
                required: false,
                constructor_default: Some(Value::Bool(false)),
                shape: Shape::Boolean,
            },
            Property {
                wire_name: "names".into(),
                required: false,
                constructor_default: Some(serde_json::json!([])),
                shape: Shape::Array {
                    items: Box::new(Shape::String),
                },
            },
            Property {
                wire_name: "nullable".into(),
                required: false,
                constructor_default: None,
                shape: Shape::Null,
            },
        ];
        let mut output = String::new();
        constructor(&mut output, "Synthetic", &fields, &ir).unwrap();
        assert!(output.contains("required_name: str,"));
        assert!(output.contains("enabled: bool = _UNSET"));
        assert!(output.contains("json.loads(\"false\")"));
        assert!(output.contains("json.loads(\"[]\")"));
        assert!(output.contains("if nullable is not _UNSET:"));
        assert!(
            !serde_json::to_string(&fields)
                .unwrap()
                .contains("constructor_default")
        );
    }
}
