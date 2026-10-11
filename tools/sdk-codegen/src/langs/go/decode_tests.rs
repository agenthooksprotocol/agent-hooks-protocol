use super::*;
use crate::model::{AdditionalProperties, NamedType, PublicRoot, UnionMode};

#[test]
fn exported_decoders_share_structural_validation() {
    let object = |field: &str| Shape::Object {
        properties: vec![
            Property {
                constructor_default: None,
                wire_name: "kind".into(),
                required: true,
                shape: Shape::Literal {
                    value: "same".into(),
                },
            },
            Property {
                constructor_default: None,
                wire_name: field.into(),
                required: true,
                shape: Shape::Integer,
            },
        ],
        forbidden_property_sets: vec![vec!["forbiddenA".into(), "forbiddenB".into()]],
        additional: AdditionalProperties::Forbidden,
    };
    let reference = |name: &str| Shape::Ref { name: name.into() };
    let union = |mode, variants| Shape::Union {
        mode,
        variants,
        discriminator: Some("kind".into()),
    };
    let nullable = |shape| Shape::Union {
        mode: UnionMode::AnyOf,
        variants: vec![Shape::Null, shape],
        discriminator: None,
    };
    let projected = Shape::Intersection {
        variants: vec![
            Shape::Object {
                properties: vec![
                    Property {
                        constructor_default: None,
                        wire_name: "url".into(),
                        required: false,
                        shape: Shape::String,
                    },
                    Property {
                        constructor_default: None,
                        wire_name: "gaps".into(),
                        required: false,
                        shape: Shape::Array {
                            items: Box::new(Shape::String),
                        },
                    },
                ],
                forbidden_property_sets: vec![vec!["evidenceA".into(), "evidenceB".into()]],
                additional: AdditionalProperties::Allowed,
            },
            Shape::Union {
                mode: UnionMode::OneOf,
                discriminator: None,
                variants: ["url", "gaps"]
                    .into_iter()
                    .map(|wire_name| Shape::Object {
                        properties: vec![Property {
                            constructor_default: None,
                            wire_name: wire_name.into(),
                            required: true,
                            shape: Shape::Any,
                        }],
                        forbidden_property_sets: vec![],
                        additional: AdditionalProperties::Allowed,
                    })
                    .collect(),
            },
        ],
    };
    let shapes = vec![
        ("Projected", projected),
        ("Left", object("a")),
        ("Right", object("b")),
        (
            "TaggedOne",
            union(
                UnionMode::OneOf,
                vec![reference("Left"), reference("Right")],
            ),
        ),
        (
            "TaggedAny",
            union(
                UnionMode::AnyOf,
                vec![reference("Left"), reference("Right")],
            ),
        ),
        (
            "Repeated",
            union(UnionMode::OneOf, vec![reference("Left"), reference("Left")]),
        ),
        (
            "Closed",
            Shape::Enum {
                values: vec!["yes".into()],
                open_strings: false,
            },
        ),
        (
            "Open",
            Shape::Enum {
                values: vec!["yes".into()],
                open_strings: true,
            },
        ),
        (
            "Literal",
            Shape::Literal {
                value: "yes".into(),
            },
        ),
        ("Integer", Shape::Integer),
        ("Number", Shape::Number),
        ("Text", Shape::String),
        ("TextAlias", reference("Text")),
        ("UnionAlias", reference("TaggedOne")),
        ("Boolean", Shape::Boolean),
        ("NullOnly", Shape::Null),
        ("Anything", Shape::Any),
        ("Never", Shape::Never),
        (
            "Integers",
            Shape::Array {
                items: Box::new(Shape::Integer),
            },
        ),
        ("MaybeInteger", nullable(Shape::Integer)),
        ("MaybeNever", nullable(Shape::Never)),
        (
            "MaybeScalar",
            nullable(Shape::Intersection {
                variants: vec![Shape::Integer, Shape::Literal { value: 3.into() }],
            }),
        ),
        (
            "MaybeIntegers",
            nullable(Shape::Array {
                items: Box::new(Shape::Integer),
            }),
        ),
        (
            "Both",
            Shape::Intersection {
                variants: vec![reference("Left"), reference("Right")],
            },
        ),
        (
            "ScalarBoth",
            Shape::Intersection {
                variants: vec![Shape::Integer, Shape::Literal { value: 3.into() }],
            },
        ),
    ];
    let ir = Ir {
        schema_revision: "test".into(),
        protocol_version: "test".into(),
        roots: shapes
            .iter()
            .map(|(name, _)| PublicRoot {
                name: (*name).into(),
                schema: format!("{name}.json"),
            })
            .collect(),
        types: shapes
            .into_iter()
            .map(|(name, shape)| NamedType {
                name: name.into(),
                source: format!("{name}.json#"),
                shape,
            })
            .collect(),
    };
    let dir = std::env::temp_dir().join(format!("ahp-go-decode-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("go.mod"), "module decodetest\n\ngo 1.24\n").unwrap();
    std::fs::write(dir.join("generated.go"), emit(&ir).unwrap()).unwrap();
    std::fs::write(dir.join("decode_test.go"), r#"package ahp_test
import (
 "bytes"
 "encoding/json"
 "testing"
 ahp "decodetest"
)
func agreement[T any](t *testing.T, parse func([]byte) ahp.ParseResult[T], accepted, rejected []string) {
 t.Helper()
 for _, group := range []struct { values []string; ok bool }{{accepted,true},{rejected,false}} {
  for _, text := range group.values {
   var value T
   err := json.Unmarshal([]byte(text), &value)
   result := parse([]byte(text))
   if (err == nil) != group.ok || result.OK != group.ok { t.Fatalf("%T %s: direct=%v parse=%+v want=%v",value,text,err,result.Diagnostics,group.ok) }
  }
 }
}
func TestStructuralAgreement(t *testing.T) {
 agreement(t,ahp.ParseProjected,[]string{`{"url":"https://example.test","extension":true}`,`{"gaps":["url"]}`},[]string{`{}`,`null`,`{"url":1}`,`{"url":"x","gaps":["url"]}`,`{"url":"x","evidenceA":true,"evidenceB":true}`})
 projected := ahp.ParseProjected([]byte(`{"url":"https://example.test","extension":true}`))
 if !projected.OK || !projected.Value.URL.Present || projected.Value.URL.Value != "https://example.test" || projected.Value.Gaps.Present { t.Fatalf("typed projection lost fields: %+v", projected) }
 raw, err := json.Marshal(projected.Value)
 if err != nil || !bytes.Contains(raw, []byte(`"extension":true`)) { t.Fatalf("extension lost: %s %v", raw, err) }
 constructed := ahp.Projected{Gaps: ahp.Some([]string{"url"})}
 if constructed.Gaps.Value[0] != "url" { t.Fatal(constructed) }
 agreement(t,ahp.ParseLeft,[]string{`{"kind":"same","a":1,"extension":true}`},[]string{`{}`,`null`,`{"kind":"wrong","a":1}`,`{"kind":"same","a":1.5}`,`{"kind":"same","a":1,"forbiddenA":null,"forbiddenB":false}`})
 agreement(t,ahp.ParseTaggedOne,[]string{`{"kind":"same","a":1}`,`{"kind":"same","b":2}`,`{"kind":"future"}`},[]string{`{"kind":"same"}`,`{"kind":"same","a":1,"b":2}`,`{}`,`null`})
 agreement(t,ahp.ParseTaggedAny,[]string{`{"kind":"same","b":2}`,`{"kind":"same","a":1,"b":2}`},[]string{`{"kind":"same"}`})
 agreement(t,ahp.ParseRepeated,[]string{`{"kind":"future"}`},[]string{`{"kind":"same","a":1}`})
 agreement(t,ahp.ParseClosed,[]string{`"yes"`},[]string{`"future"`,`null`,`3`})
 agreement(t,ahp.ParseOpen,[]string{`"yes"`,`"future"`},[]string{`null`,`3`})
 if result := ahp.ParseOpen([]byte(`"future"`)); len(result.Diagnostics)!=1 || result.Diagnostics[0].Severity!=ahp.SeverityWarning { t.Fatal(result) }
 agreement(t,ahp.ParseLiteral,[]string{`"yes"`},[]string{`"future"`,`null`})
 agreement(t,ahp.ParseInteger,[]string{`1`,`1.0`,`1e0`,`9007199254740992`},[]string{`null`,`"1"`,`1.5`})
 agreement(t,ahp.ParseNumber,[]string{`1.5`,`1e100`,`1e9999`},[]string{`null`,`"1"`})
 agreement(t,ahp.ParseTextAlias,[]string{`""`},[]string{`null`,`3`})
 agreement(t,ahp.ParseUnionAlias,[]string{`{"kind":"future"}`},[]string{`null`,`{}`})
 agreement(t,ahp.ParseText,[]string{`""`},[]string{`null`,`3`})
 agreement(t,ahp.ParseBoolean,[]string{`false`},[]string{`null`,`3`})
 agreement(t,ahp.ParseNullOnly,[]string{`null`},[]string{`false`,`{}`})
 agreement(t,ahp.ParseAnything,[]string{`null`,`{"number":9007199254740993}`},[]string{`{`})
 agreement(t,ahp.ParseNever,nil,[]string{`null`,`false`,`{}`})
 agreement(t,ahp.ParseIntegers,[]string{`[]`,`[1,2]`},[]string{`null`,`[1.5]`,`[null]`})
 agreement(t,ahp.ParseMaybeNever,[]string{`null`},[]string{`1`,`{}`})
 agreement(t,ahp.ParseMaybeScalar,[]string{`null`,`3`},[]string{`4`,`"bad"`,`{}`})
 agreement(t,ahp.ParseMaybeInteger,[]string{`null`,`1`},[]string{`1.5`,`"1"`})
 agreement(t,ahp.ParseMaybeIntegers,[]string{`null`,`[1,2]`},[]string{`[null]`,`[1.5]`})
 agreement(t,ahp.ParseBoth,[]string{`{"kind":"same","a":1,"b":2}`},[]string{`{"kind":"same","a":1}`})
 agreement(t,ahp.ParseScalarBoth,[]string{`3`},[]string{`4`,`null`})
 var selected ahp.TaggedOne
 if err := json.Unmarshal([]byte(`{"kind":"same","b":2}`),&selected); err != nil || !selected.Right.Present { t.Fatalf("later same-tag branch lost: %+v %v",selected,err) }
}
"#).unwrap();
    let result = std::process::Command::new("go")
        .args(["test", "./..."])
        .current_dir(&dir)
        .output()
        .unwrap();
    assert!(
        result.status.success(),
        "{}\n{}",
        String::from_utf8_lossy(&result.stdout),
        String::from_utf8_lossy(&result.stderr)
    );
    std::fs::remove_dir_all(dir).unwrap();
}

#[test]
fn schema_compositions_keep_original_descriptors_and_typed_fields() {
    let repository = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let ir = crate::compiler::compile(&repository, "draft").unwrap();
    let mut generator = Generator::new(&ir);
    for named in &ir.types {
        generator
            .emit_named(
                &generator.named_name(&named.name),
                &named.source,
                &named.shape,
            )
            .unwrap();
    }
    for named in &ir.types {
        if let Some(descriptor) = generator
            .decoder_shapes
            .get(&generator.named_name(&named.name))
        {
            assert_eq!(
                serde_json::to_value(descriptor).unwrap(),
                serde_json::to_value(&named.shape).unwrap(),
                "{} validation descriptor changed",
                named.name
            );
        }
    }
    for name in [
        "ExecutionEventContextCompactBefore",
        "CapabilitiesModifyContent",
        "AuthenticationBearer",
    ] {
        assert!(
            generator.objects.contains_key(name),
            "{name} is not an object model"
        );
    }
    let visible = &generator.objects["ModelVisibleItem"];
    assert!(
        visible
            .iter()
            .any(|field| field.wire_name == "role" && field.required)
    );
    assert!(
        visible
            .iter()
            .any(|field| field.wire_name == "parts" && field.required)
    );
    // Raw storage is reserved for true unconstrained JSON, null/never shapes,
    // explicit unknown fields/variants, and arrays of such values. No composed
    // schema property in the current draft may silently fall back to raw JSON.
    for (name, fields) in &generator.objects {
        for field in fields {
            if field.field_type == "json.RawMessage" {
                assert!(
                    matches!(field.shape, Shape::Any | Shape::Null | Shape::Never),
                    "structured raw field {name}.{}: {:?}",
                    field.wire_name,
                    field.shape
                );
            }
        }
    }
}

#[test]
fn typed_additional_properties_validate_and_hydrate_maps() {
    let dictionary = |shape| Shape::Object {
        properties: vec![],
        forbidden_property_sets: vec![],
        additional: AdditionalProperties::Typed {
            shape: Box::new(shape),
        },
    };
    let nullable_integer = Shape::Union {
        mode: UnionMode::AnyOf,
        variants: vec![Shape::Integer, Shape::Null],
        discriminator: None,
    };
    let mut mixed = dictionary(Shape::Integer);
    if let Shape::Object { properties, .. } = &mut mixed {
        properties.push(Property {
            constructor_default: None,
            wire_name: "label".into(),
            required: true,
            shape: Shape::String,
        });
    }
    let nonnegative = Shape::BoundedInteger {
        minimum: Some(0.into()),
        maximum: None,
    };
    let shapes = [
        (
            "SessionCounters",
            Shape::Object {
                properties: vec![Property {
                    constructor_default: None,
                    wire_name: "inputTokens".into(),
                    required: false,
                    shape: nonnegative.clone(),
                }],
                forbidden_property_sets: vec![],
                additional: AdditionalProperties::Typed {
                    shape: Box::new(nonnegative),
                },
            },
        ),
        ("UnboundedInteger", Shape::Integer),
        (
            "BoundedIntegerValue",
            Shape::BoundedInteger {
                minimum: Some((-2).into()),
                maximum: Some(2.into()),
            },
        ),
        ("Dictionary", dictionary(Shape::String)),
        ("NullableDictionary", dictionary(nullable_integer)),
        (
            "NestedDictionary",
            dictionary(Shape::Ref {
                name: "NullableDictionary".into(),
            }),
        ),
        ("MixedDictionary", mixed),
        (
            "ClosedBag",
            Shape::Object {
                properties: vec![],
                forbidden_property_sets: vec![],
                additional: AdditionalProperties::Forbidden,
            },
        ),
    ];
    let ir = Ir {
        schema_revision: "test".into(),
        protocol_version: "test".into(),
        roots: shapes
            .iter()
            .map(|(name, _)| PublicRoot {
                name: (*name).into(),
                schema: "test.json".into(),
            })
            .collect(),
        types: shapes
            .into_iter()
            .map(|(name, shape)| NamedType {
                name: name.into(),
                source: "test.json#".into(),
                shape,
            })
            .collect(),
    };
    let output = emit(&ir).unwrap();
    assert!(output.contains("type Dictionary map[string]string"));
    assert!(output.contains("type NullableDictionary map[string]Nullable["));
    assert!(output.contains("type NestedDictionary map[string]*NullableDictionary"));
    assert!(output.contains("AdditionalProperties map[string]json.Number"));
    let dir = std::env::temp_dir().join(format!("ahp-go-typed-maps-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("go.mod"), "module typedmaps\n\ngo 1.23\n").unwrap();
    std::fs::write(dir.join("models.go"), output).unwrap();
    std::fs::write(dir.join("models_test.go"), r#"package ahp
import ("encoding/json"; "testing"; "fmt")
func TestTypedMaps(t *testing.T) {
    good := ParseDictionary([]byte(`{"hello":"world"}`))
    if !good.OK || good.Value["hello"] != "world" { t.Fatal(good) }
    bad := ParseDictionary([]byte(`{"a/b":false}`))
    if bad.OK || len(bad.Diagnostics) != 1 || bad.Diagnostics[0].Path != "/a~1b" { t.Fatal(bad) }
    var direct Dictionary
    if json.Unmarshal([]byte(`{"wrong":null}`), &direct) == nil { t.Fatal("direct validation missing") }
    nested := ParseNestedDictionary([]byte(`{"outer":{"zero":0,"nil":null}}`))
    if !nested.OK || nested.Value["outer"] == nil { t.Fatal(nested) }
    values := *nested.Value["outer"]
    if !values["zero"].Valid || values["nil"].Valid { t.Fatal("nullable map hydration", values) }
    encoded, err := EncodeNestedDictionary(nested.Value)
    if err != nil || string(encoded) != `{"outer":{"nil":null,"zero":0}}` { t.Fatal(string(encoded), err) }
    mixed := ParseMixedDictionary([]byte(`{"label":"ok","count":2}`))
    if !mixed.OK || string(mixed.Value.AdditionalProperties["count"]) != "2" { t.Fatal(mixed) }
    if ParseMixedDictionary([]byte(`{"label":"ok","count":"bad"}`)).OK { t.Fatal("additional validation missing") }
    encoded, err = EncodeMixedDictionary(mixed.Value)
    if err != nil || string(encoded) != `{"count":2,"label":"ok"}` { t.Fatal(string(encoded), err) }
    var directMixed MixedDictionary
    if json.Unmarshal([]byte(`{"label":"ok","count":false}`), &directMixed) == nil { t.Fatal("direct mixed validation missing") }
    closed := ParseClosedBag([]byte(`{"future":{"opaque":true}}`))
    if !closed.OK || string(closed.Value.AdditionalProperties["future"]) != `{"opaque":true}` { t.Fatal("closed unknown bag", closed) }
    for _, lexeme := range []string{"1208925819614629174706176", "1267650600228229401496703205376", "340282366920938463463374607431768211456"} {
        for _, key := range []string{"inputTokens", "futureCounter"} {
            text := fmt.Sprintf(`{"%s":%s}`, key, lexeme)
            parsed := ParseSessionCounters([]byte(text))
            if !parsed.OK { t.Fatal(text, parsed.Diagnostics) }
            integer := parsed.Value.AdditionalProperties[key]
            if key == "inputTokens" { integer = parsed.Value.InputTokens.Value }
            if integer.String() != lexeme { t.Fatal("rounded counter", integer, lexeme) }
            encoded, err := EncodeSessionCounters(parsed.Value)
            if err != nil || string(encoded) != text { t.Fatal(text, string(encoded), err) }
            var direct SessionCounters
            if err := json.Unmarshal([]byte(text), &direct); err != nil { t.Fatal(text, err) }
            encoded, err = json.Marshal(direct)
            if err != nil || string(encoded) != text { t.Fatal(text, string(encoded), err) }
            for _, invalid := range []string{"-"+lexeme, lexeme+".5", "null", "true"} {
                text := fmt.Sprintf(`{"%s":%s}`, key, invalid)
                if ParseSessionCounters([]byte(text)).OK { t.Fatal("invalid counter", text) }
                if json.Unmarshal([]byte(text), &direct) == nil { t.Fatal("invalid direct counter", text) }
            }
        }
        for _, sign := range []string{"", "-"} {
            text := sign+lexeme
            parsed := ParseUnboundedInteger([]byte(text))
            if !parsed.OK { t.Fatal(text, parsed.Diagnostics) }
            encoded, err := EncodeUnboundedInteger(parsed.Value)
            if err != nil || string(encoded) != text { t.Fatal(text, string(encoded), err) }
        }
    }
    for _, text := range []string{"-2", "2", "0", "-0.0", "20e-1", "1e10000"} {
        if !ParseUnboundedInteger([]byte(text)).OK { t.Fatal("integral lexeme rejected", text) }
    }
    for _, text := range []string{"1.5", "1e-1", "1208925819614629174706176.5"} {
        if ParseUnboundedInteger([]byte(text)).OK { t.Fatal("fraction accepted", text) }
    }
    for _, text := range []string{"-2", "2", "0", "-0.0", "20e-1"} {
        if !ParseBoundedIntegerValue([]byte(text)).OK { t.Fatal("bounded integer rejected", text) }
        var direct BoundedIntegerValue
        if json.Unmarshal([]byte(text), &direct) != nil { t.Fatal("direct bounded integer rejected", text) }
    }
    for _, text := range []string{"-3", "3", "1e10000", "-1e10000", "2.1"} {
        if ParseBoundedIntegerValue([]byte(text)).OK { t.Fatal("bounds ignored", text) }
        var direct BoundedIntegerValue
        if json.Unmarshal([]byte(text), &direct) == nil { t.Fatal("direct bounds ignored", text) }
    }

}
"#).unwrap();
    let result = std::process::Command::new("go")
        .args(["test", "./..."])
        .current_dir(&dir)
        .output()
        .expect("Go is required for typed-map regression");
    std::fs::remove_dir_all(&dir).unwrap();
    assert!(
        result.status.success(),
        "{}{}",
        String::from_utf8_lossy(&result.stdout),
        String::from_utf8_lossy(&result.stderr)
    );
}
