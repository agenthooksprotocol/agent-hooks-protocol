mod capability_ergonomics;
mod compiler;
mod ergonomics;
mod langs;
mod model;

use std::env;
use std::fs;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result, bail};

fn main() {
    if let Err(error) = run() {
        eprintln!("ahp-codegen: {error:#}");
        std::process::exit(1);
    }
}

fn run() -> Result<()> {
    let mut arguments = env::args().skip(1);
    let command = arguments.next().unwrap_or_else(|| "help".into());
    if command == "help" || command == "--help" || command == "-h" {
        print_help();
        return Ok(());
    }
    if command != "generate" && command != "check" {
        bail!("unknown command {command:?}");
    }

    let mut revision = None;
    let mut language = None;
    let mut emit_ir = false;
    let mut output = None;
    let mut repository = PathBuf::from(".");
    while let Some(argument) = arguments.next() {
        match argument.as_str() {
            "--revision" => revision = Some(required_value(&mut arguments, "--revision")?),
            "--language" => language = Some(required_value(&mut arguments, "--language")?),
            "--emit-ir" => emit_ir = true,
            "--output" => output = Some(required_value(&mut arguments, "--output")?),
            "--repository" => {
                repository = PathBuf::from(required_value(&mut arguments, "--repository")?)
            }
            other => bail!("unknown argument {other:?}"),
        }
    }
    let revision = revision.context("--revision is required")?;
    let ir = compiler::compile(&repository, &revision)?;
    if command == "check" {
        println!(
            "SDK generation metadata passed: {} types, {} roots ({revision})",
            ir.types.len(),
            ir.roots.len()
        );
        return Ok(());
    }
    if emit_ir && language.is_some() {
        bail!("--emit-ir and --language are mutually exclusive");
    }
    if language.as_deref() == Some("python-facade") {
        let directory = output.context("--output directory is required for python-facade")?;
        for (relative, contents) in langs::python::facade::emit(&ir)? {
            let path = Path::new(&directory).join(relative);
            write_output(path.to_str(), &contents)?;
        }
        return Ok(());
    }
    if language.as_deref() == Some("go-facade") {
        let directory = output.context("--output directory is required for go-facade")?;
        for (relative, contents) in langs::go::facade::emit(&ir)? {
            let path = Path::new(&directory).join(relative);
            write_output(path.to_str(), &contents)?;
        }
        return Ok(());
    }
    let contents = if emit_ir {
        format!("{}\n", serde_json::to_string_pretty(&ir)?)
    } else {
        match language.as_deref() {
            Some("go") => langs::go::emit(&ir)?,
            Some("python") => langs::python::emit(&ir)?,
            Some("rust") => langs::rust::emit_from_repository(&ir, &repository)?,
            Some("typescript") => langs::typescript::emit(&ir)?,
            Some(other) => {
                bail!("unsupported language {other:?}; available: go, python, rust, typescript")
            }
            None => bail!("--language or --emit-ir is required"),
        }
    };
    write_output(output.as_deref(), &contents)
}

fn required_value(arguments: &mut impl Iterator<Item = String>, option: &str) -> Result<String> {
    arguments
        .next()
        .with_context(|| format!("{option} requires a value"))
}

fn normalize_terminal_newline(contents: &str) -> String {
    format!("{}\n", contents.trim_end_matches(['\r', '\n']))
}

fn write_output(path: Option<&str>, contents: &str) -> Result<()> {
    let contents = normalize_terminal_newline(contents);
    match path {
        None | Some("-") => print!("{contents}"),
        Some(path) => {
            let path = Path::new(path);
            if let Some(parent) = path
                .parent()
                .filter(|parent| !parent.as_os_str().is_empty())
            {
                fs::create_dir_all(parent)
                    .with_context(|| format!("cannot create {}", parent.display()))?;
            }
            fs::write(path, contents)
                .with_context(|| format!("cannot write {}", path.display()))?;
        }
    }
    Ok(())
}

fn print_help() {
    println!(
        "ahp-codegen\n\n\
         Usage:\n  ahp-codegen check --revision <revision> [--repository <path>]\n  \
         ahp-codegen generate --revision <revision> (--language <go|go-facade|python|python-facade|rust|typescript> | --emit-ir) \
         [--output <path>] [--repository <path>]"
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn output_has_exactly_one_terminal_newline() {
        for contents in ["code", "code\n", "code\n\n", "code\r\n\r\n"] {
            assert_eq!(normalize_terminal_newline(contents), "code\n");
        }
        assert_eq!(normalize_terminal_newline(""), "\n");
        assert_eq!(
            normalize_terminal_newline("first\n\nsecond\n\n"),
            "first\n\nsecond\n"
        );
    }

    #[test]
    fn complete_draft_public_surface_has_no_structural_fallback_names() {
        let repository = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let ir = compiler::compile(&repository, "draft").unwrap();
        let mut outputs = vec![
            ("go".to_owned(), langs::go::emit(&ir).unwrap()),
            ("rust".to_owned(), langs::rust::emit(&ir).unwrap()),
            (
                "typescript".to_owned(),
                langs::typescript::emit(&ir).unwrap(),
            ),
        ];
        outputs.extend(
            langs::python::facade::emit(&ir)
                .unwrap()
                .into_iter()
                .map(|(path, source)| (format!("python/{path}"), source)),
        );
        outputs.extend(
            langs::go::facade::emit(&ir)
                .unwrap()
                .into_iter()
                .map(|(path, source)| (format!("go/{path}"), source)),
        );
        for (target, source) in outputs {
            for line in source.lines() {
                // Inspect declared public type names, rather than private parser
                // variables or JSON descriptor text embedded in the runtime.
                let declaration = [
                    "pub struct ",
                    "pub enum ",
                    "pub type ",
                    "type ",
                    "export type ",
                    "export interface ",
                    "class ",
                ]
                .iter()
                .find_map(|prefix| line.strip_prefix(prefix));
                if let Some(declaration) = declaration {
                    let name = declaration
                        .split(|c: char| !c.is_ascii_alphanumeric() && c != '_')
                        .next()
                        .unwrap();
                    assert!(name.len() <= 96, "{target}: unbounded public type {name}");
                    assert!(
                        !name.contains("ObjectOr") && !name.contains("ObjectAnd"),
                        "{target}: structural public type {name}"
                    );
                }
                // Fingerprints and positional variant names are unacceptable in
                // public declarations AND references to their generated helpers.
                for token in line.split(|c: char| !c.is_ascii_alphanumeric() && c != '_') {
                    if let Some((_, suffix)) = token.split_once("Shape") {
                        assert!(
                            !(suffix.len() >= 16
                                && suffix.as_bytes()[..16].iter().all(u8::is_ascii_hexdigit)),
                            "{target}: public structural fingerprint {token}"
                        );
                    }
                    for stem in [
                        "String",
                        "Object",
                        "Array",
                        "Union",
                        "Intersection",
                        "Duplicate",
                    ] {
                        if let Some((_, suffix)) = token.rsplit_once(stem) {
                            assert!(
                                suffix.is_empty() || !suffix.chars().all(|c| c.is_ascii_digit()),
                                "{target}: ordinal public fallback {token}"
                            );
                        }
                    }
                    if let Some((_, suffix)) = token.split_once("Variant") {
                        assert!(
                            !suffix.starts_with(|c: char| c.is_ascii_digit()),
                            "{target}: positional variant {token}"
                        );
                    }
                }
            }
        }
    }

    #[test]
    fn elicitation_validation_retains_all_eight_distinct_canonical_alternatives() {
        let repository = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let ir = compiler::compile(&repository, "draft").unwrap();
        let named = ir
            .types
            .iter()
            .find(|item| item.name == "McpElicitationPrimitiveSchemaDefinition")
            .unwrap();
        let model::Shape::Union { variants, .. } = &named.shape else {
            panic!("elicitation union")
        };
        let names = variants
            .iter()
            .map(|shape| match shape {
                model::Shape::Ref { name } => name.as_str(),
                _ => panic!("elicitation alternatives must retain canonical references"),
            })
            .collect::<std::collections::BTreeSet<_>>();
        assert_eq!(variants.len(), 8);
        assert_eq!(names.len(), 8);
        assert!(names.contains("McpElicitationStringSchema"));
        assert!(names.contains("McpElicitationTitledSingleSelectEnumSchema"));
        assert!(names.contains("McpElicitationUntitledSingleSelectEnumSchema"));
    }

    #[test]
    fn current_profile_compiles() {
        let repository = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let ir = compiler::compile(&repository, "draft").unwrap();
        assert_eq!(ir.schema_revision, "draft");
        assert_eq!(ir.roots.len(), 26);
        assert!(ir.types.iter().any(|item| item.name == "InterceptRequest"));
        // Union selectors must be exact literals, not an open enum that also
        // accepts supplied_result and creates an ambiguous known execution.
        let execution = ir
            .types
            .iter()
            .find(|item| item.name == "ExecutionEventExecution")
            .unwrap();
        let model::Shape::Union { variants, .. } = &execution.shape else {
            panic!("expected execution union");
        };
        assert_eq!(variants.len(), 6);
        for variant in variants.iter().skip(1) {
            let model::Shape::Object { properties, .. } = variant else {
                panic!("expected execution object");
            };
            let reason = properties
                .iter()
                .find(|property| property.wire_name == "reason")
                .unwrap();
            assert!(reason.required);
            assert!(matches!(reason.shape, model::Shape::Literal { .. }));
        }
    }
}
