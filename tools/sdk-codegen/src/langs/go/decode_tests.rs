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
    let shapes = vec![
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
 agreement(t,ahp.ParseLeft,[]string{`{"kind":"same","a":1,"extension":true}`},[]string{`{}`,`null`,`{"kind":"wrong","a":1}`,`{"kind":"same","a":1.5}`,`{"kind":"same","a":1,"forbiddenA":null,"forbiddenB":false}`})
 agreement(t,ahp.ParseTaggedOne,[]string{`{"kind":"same","a":1}`,`{"kind":"same","b":2}`,`{"kind":"future"}`},[]string{`{"kind":"same"}`,`{"kind":"same","a":1,"b":2}`,`{}`,`null`})
 agreement(t,ahp.ParseTaggedAny,[]string{`{"kind":"same","b":2}`,`{"kind":"same","a":1,"b":2}`},[]string{`{"kind":"same"}`})
 agreement(t,ahp.ParseRepeated,[]string{`{"kind":"future"}`},[]string{`{"kind":"same","a":1}`})
 agreement(t,ahp.ParseClosed,[]string{`"yes"`},[]string{`"future"`,`null`,`3`})
 agreement(t,ahp.ParseOpen,[]string{`"yes"`,`"future"`},[]string{`null`,`3`})
 if result := ahp.ParseOpen([]byte(`"future"`)); len(result.Diagnostics)!=1 || result.Diagnostics[0].Severity!=ahp.SeverityWarning { t.Fatal(result) }
 agreement(t,ahp.ParseLiteral,[]string{`"yes"`},[]string{`"future"`,`null`})
 agreement(t,ahp.ParseInteger,[]string{`1`,`1.0`,`1e0`},[]string{`null`,`"1"`,`1.5`,`9007199254740992`})
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
