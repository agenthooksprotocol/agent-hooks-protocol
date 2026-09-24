mod compiler;
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
    let contents = if emit_ir {
        format!("{}\n", serde_json::to_string_pretty(&ir)?)
    } else {
        match language.as_deref() {
            Some("go") => langs::go::emit(&ir)?,
            Some("python") => langs::python::emit(&ir)?,
            Some("rust") => langs::rust::emit(&ir)?,
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
         ahp-codegen generate --revision <revision> (--language <go|python|rust|typescript> | --emit-ir) \
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
