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
    fn nested_property_collisions_are_stable_across_sibling_reordering() {
        let fields = vec!["a-b", "a_b"]
            .into_iter()
            .map(|wire_name| Property {
                wire_name: wire_name.into(),
                required: true,
                constructor_default: None,
                shape: Shape::Object {
                    properties: vec![],
                    forbidden_property_sets: vec![],
                    additional: crate::model::AdditionalProperties::Allowed,
                },
            })
            .collect::<Vec<_>>();
        let mut before = BTreeMap::new();
        collect_properties("Parent", fields.iter(), &mut before);
        let mut after = BTreeMap::new();
        collect_properties("Parent", fields.iter().rev(), &mut after);
        assert_eq!(before.len(), 2);
        assert_eq!(
            serde_json::to_value(before).unwrap(),
            serde_json::to_value(after).unwrap()
        );
    }

    #[test]
    fn referenced_candidate_imports_its_canonical_constructor() {
        let mut ir = draft();
        let mut shapes = BTreeMap::new();
        for named in &ir.types {
            collect(&named.name, &named.shape, &mut shapes);
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
        collect("CanonicalCandidate", &object, &mut shapes);
        let identifiers = IdentifierMap::new(&ir);
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
    fn colliding_union_tags_and_enum_members_keep_stable_identities() {
        let mut ir = draft();
        let tagged = |tag: &str| Shape::Object {
            properties: vec![Property {
                wire_name: "type".into(),
                required: true,
                constructor_default: None,
                shape: Shape::Literal {
                    value: Value::String(tag.into()),
                },
            }],
            forbidden_property_sets: vec![],
            additional: crate::model::AdditionalProperties::Allowed,
        };
        let mut alternatives = vec![tagged("a-b"), tagged("a_b"), tagged("unknown")];
        let mut collected = BTreeMap::new();
        collect(
            "Collision",
            &Shape::Union {
                variants: alternatives.clone(),
                discriminator: Some("type".into()),
                mode: crate::model::UnionMode::OneOf,
            },
            &mut collected,
        );
        alternatives.reverse();
        let mut reordered = BTreeMap::new();
        collect(
            "Collision",
            &Shape::Union {
                variants: alternatives,
                discriminator: Some("type".into()),
                mode: crate::model::UnionMode::OneOf,
            },
            &mut reordered,
        );
        collected.remove("Collision");
        reordered.remove("Collision");
        assert_eq!(collected.len(), 6); // each object plus its literal field
        assert_eq!(
            serde_json::to_value(collected).unwrap(),
            serde_json::to_value(reordered).unwrap()
        );
        ir.types.push(crate::model::NamedType {
            name: "CollisionEnum".into(),
            source: "test".into(),
            shape: Shape::Enum {
                values: vec![
                    Value::String("a-b".into()),
                    Value::String("a_b".into()),
                    Value::String("unknown".into()),
                ],
                open_strings: true,
            },
        });
        let before = emit(&ir).unwrap();
        if let Shape::Enum { values, .. } = &mut ir.types.last_mut().unwrap().shape {
            values.reverse();
        }
        let after = emit(&ir).unwrap();
        let members = |source: &str| {
            let mut lines = source
                .split("class CollisionEnum(StrEnum):\n")
                .nth(1)
                .unwrap()
                .split("\n\n")
                .next()
                .unwrap()
                .lines()
                .map(str::to_owned)
                .collect::<Vec<_>>();
            lines.sort();
            lines
        };
        assert_eq!(
            members(&before["_models/__init__.py"]),
            members(&after["_models/__init__.py"])
        );
    }

    #[test]
    fn every_named_object_and_boundary_is_emitted_without_runtime_overwrites() {
        let ir = draft();
        let files = emit(&ir).unwrap();
        let ids = IdentifierMap::new(&ir);
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
        assert!(!files["capability.py"]
            .contains("from ._models.capability import ModifyInput as ModifyInput"));
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
        assert!(files["state.py"]
            .contains("def initial(permission: Permission, *, candidate: Candidate | None = None"));
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
        assert!(models
            .contains("def bind_items_source(self, source: OwnedContentSource, *, index: int)"));
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
        assert!(!serde_json::to_string(&fields)
            .unwrap()
            .contains("constructor_default"));
    }
}
