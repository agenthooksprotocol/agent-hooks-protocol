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
    fn annotation_imports_follow_emitted_signatures() {
        let mut source = format!(
            "{HEADER}from __future__ import annotations\ndef value(x: Literal[\"Never\"]) -> str: ...\n"
        );
        add_annotation_imports(&mut source);
        assert!(source.contains("from typing import Literal\n"));
        assert!(!source.contains("import Never"));
        assert!(!source.contains("from . import _models"));
        let mut source = format!("{HEADER}def value(x: _models.Example) -> Never: ...\n");
        add_annotation_imports(&mut source);
        assert!(source.contains("from typing import Never\n"));
        assert!(source.contains("from . import _models\n"));
        assert!(!source.contains("import Literal"));
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
            assert!(models.contains(&format!(
                "gaps: list[ExecutionEventMcpConnection{transport}GapsItem]"
            )));
            assert!(models.contains(&format!(
                "def gaps(self) -> list[ExecutionEventMcpConnection{transport}GapsItem] | None:"
            )));
        }
        assert!(!models.contains("ConnectionObject"));
        assert!(!models.contains("PrimitiveSchemaDefinitionString"));
        assert!(models.contains("items: list[ModelVisibleItemBody | ModelVisibleItemBodyGap | ModelVisibleItemMetadata | ModelVisibleItemOmit]"));
        let wire = super::super::emit(&ir).unwrap();
        assert!(wire.contains("ModelVisibleItem: TypeAlias = Union["));
        assert!(!wire.contains("ModelVisibleItem: TypeAlias = JsonValue"));
        // Only a genuinely unconstrained named schema remains fully dynamic.
        let raw = wire
            .lines()
            .filter(|line| line.ends_with(": TypeAlias = JsonValue"))
            .collect::<Vec<_>>();
        assert_eq!(raw, vec!["NativeEvent: TypeAlias = JsonValue"]);
    }

    #[test]
    fn real_dynamic_fields_are_unconstrained_not_composition_fallbacks() {
        let ir = draft();
        let names = HashMap::new();
        let renderer = Renderer {
            ir: &ir,
            names: &names,
        };
        let mut shapes = BTreeMap::new();
        for named in &ir.types {
            collect(&ir, &named.name, &named.shape, &mut shapes).unwrap();
        }
        for (name, shape) in shapes {
            let Some(fields) = properties(&renderer, &shape) else {
                continue;
            };
            for field in fields {
                let hint = field_hint(&name, &field.wire_name).unwrap();
                let rendered = annotation(&ir, &field.shape, &hint, "").unwrap();
                let original = match &field.shape {
                    Shape::Ref { name } => {
                        &ir.types.iter().find(|n| n.name == *name).unwrap().shape
                    }
                    shape => shape,
                };
                if rendered == "Any" {
                    assert!(
                        matches!(original, Shape::Any),
                        "opaque structured field {hint}: {original:?}"
                    );
                }
                if rendered == "list[Any]" {
                    assert!(
                        matches!(original, Shape::Array { items } if matches!(items.as_ref(), Shape::Any)),
                        "opaque structured array {hint}: {original:?}"
                    );
                }
            }
        }
    }

    #[test]
    fn capability_event_alias_has_an_explicit_stable_owner() {
        let files = emit(&draft()).unwrap();
        assert!(files["_models/capability.py"]
            .contains("from . import CapabilitiesResponseResultManifestEventsItemEvent as Event"));
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
        collect(&draft(), "Choice", &Shape::String, &mut shapes).unwrap();
        collect(&draft(), "Choice", &Shape::String, &mut shapes).unwrap();
        assert!(collect(&draft(), "Choice", &Shape::Number, &mut shapes)
            .unwrap_err()
            .to_string()
            .contains("public Python model collision"));
        assert!(
            literal_labels(&[Value::String("a-b".into()), Value::String("a_b".into())]).is_err()
        );
    }

    #[test]
    fn referenced_candidate_imports_its_canonical_constructor() {
        let mut ir = draft();
        let mut shapes = BTreeMap::new();
        for named in &ir.types {
            collect(&ir, &named.name, &named.shape, &mut shapes).unwrap();
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
        collect(&draft(), "CanonicalCandidate", &object, &mut shapes).unwrap();
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
        // Public naming is order-independent; untouched validation descriptors
        // intentionally retain original union order (including warning selection).
        for (path, source) in &before {
            let declarations = |source: &str| {
                source
                    .lines()
                    .filter(|line| !line.starts_with("_DESCRIPTORS ="))
                    .collect::<Vec<_>>()
                    .join("\n")
            };
            assert_eq!(declarations(source), declarations(&after[path]), "{path}");
        }
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
        assert!(collect(&draft(), "Collision", &union, &mut shapes).is_err());
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
                    files["_models/__init__.py"].contains(&format!("class {}(", named.name))
                        || files["_models/__init__.py"].contains(&format!("{} = ", named.name)),
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
    fn original_composed_descriptors_and_union_ambiguity_are_cached() {
        use crate::model::{NamedType, PublicRoot, UnionMode};
        let mut ir = draft();
        let original = Shape::Intersection {
            variants: vec![
                Shape::Object {
                    properties: vec![Property {
                        wire_name: "choice".into(),
                        required: true,
                        constructor_default: None,
                        shape: Shape::Union {
                            mode: UnionMode::OneOf,
                            discriminator: None,
                            variants: vec![Shape::Integer, Shape::Number, Shape::String],
                        },
                    }],
                    forbidden_property_sets: vec![],
                    additional: AdditionalProperties::Allowed,
                },
                Shape::Object {
                    properties: vec![],
                    forbidden_property_sets: vec![vec!["left".into(), "right".into()]],
                    additional: AdditionalProperties::Allowed,
                },
            ],
        };
        ir.types.push(NamedType {
            name: "StructuralComposed".into(),
            source: "synthetic".into(),
            shape: original.clone(),
        });
        ir.roots.push(PublicRoot {
            name: "StructuralComposed".into(),
            schema: "synthetic".into(),
        });
        for (name, field) in [("HydrationLeft", "left"), ("HydrationRight", "right")] {
            ir.types.push(NamedType {
                name: name.into(),
                source: "synthetic".into(),
                shape: Shape::Object {
                    properties: vec![Property {
                        wire_name: field.into(),
                        required: true,
                        constructor_default: None,
                        shape: Shape::String,
                    }],
                    forbidden_property_sets: vec![],
                    additional: AdditionalProperties::Allowed,
                },
            });
        }
        ir.types.push(NamedType {
            name: "StructuralHydration".into(),
            source: "synthetic".into(),
            shape: Shape::Object {
                properties: vec![Property {
                    wire_name: "items".into(),
                    required: true,
                    constructor_default: None,
                    shape: Shape::Union {
                        mode: UnionMode::OneOf,
                        discriminator: None,
                        variants: ["HydrationLeft", "HydrationRight"]
                            .iter()
                            .map(|name| Shape::Array {
                                items: Box::new(Shape::Ref {
                                    name: (*name).into(),
                                }),
                            })
                            .collect(),
                    },
                }],
                forbidden_property_sets: vec![],
                additional: AdditionalProperties::Allowed,
            },
        });
        for (name, scalar) in [
            ("HydrationNumbers", Shape::Number),
            ("HydrationIntegers", Shape::Integer),
        ] {
            ir.types.push(NamedType {
                name: name.into(),
                source: "synthetic".into(),
                shape: Shape::Object {
                    properties: vec![Property {
                        wire_name: "items".into(),
                        required: true,
                        constructor_default: None,
                        shape: Shape::Union {
                            mode: UnionMode::OneOf,
                            discriminator: None,
                            variants: vec![
                                Shape::String,
                                Shape::Array {
                                    items: Box::new(Shape::Union {
                                        mode: UnionMode::OneOf,
                                        discriminator: None,
                                        variants: vec![
                                            scalar,
                                            Shape::Ref {
                                                name: "HydrationLeft".into(),
                                            },
                                        ],
                                    }),
                                },
                            ],
                        },
                    }],
                    forbidden_property_sets: vec![],
                    additional: AdditionalProperties::Allowed,
                },
            });
            ir.roots.push(PublicRoot {
                name: name.into(),
                schema: "synthetic".into(),
            });
        }
        let files = emit(&ir).unwrap();
        let source = &files["_models/__init__.py"];
        let encoded = source
            .lines()
            .find_map(|line| line.strip_prefix("_DESCRIPTORS = json.loads("))
            .unwrap()
            .strip_suffix(", parse_float=Decimal)")
            .unwrap();
        let json: String = serde_json::from_str(encoded).unwrap();
        let descriptors: Value = serde_json::from_str(&json).unwrap();
        assert_eq!(
            descriptors["StructuralComposed"],
            serde_json::to_value(original).unwrap()
        );
        assert!(source.contains("class StructuralComposed(_WireModel):"));
        assert!(source.contains("model = cls.__new__(cls)"));
        // Optional runtime-smoke fixture output, never into an SDK checkout.
        if let Ok(directory) = std::env::var("AHP_PYTHON_STRUCTURAL_OUTPUT") {
            let directory = std::path::Path::new(&directory);
            std::fs::create_dir_all(directory).unwrap();
            for (path, source) in files {
                let path = directory.join(path);
                std::fs::create_dir_all(path.parent().unwrap()).unwrap();
                std::fs::write(path, source).unwrap();
            }
            std::fs::write(
                directory.join("generated.py"),
                super::super::emit(&ir).unwrap(),
            )
            .unwrap();
        }
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
        constructor(&mut output, "Synthetic", &fields, &ir, false).unwrap();
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
