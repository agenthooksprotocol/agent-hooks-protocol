mod compiler;
mod model;
mod typescript;

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
            "sdk generation profile passed: {} types, {} roots ({revision})",
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
            Some("typescript") => typescript::emit(&ir)?,
            Some(other) => bail!("unsupported language {other:?}; available: typescript"),
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

fn write_output(path: Option<&str>, contents: &str) -> Result<()> {
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
         ahp-codegen generate --revision <revision> (--language typescript | --emit-ir) \
         [--output <path>] [--repository <path>]"
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn current_profile_compiles() {
        let repository = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let ir = compiler::compile(&repository, "0.1.0-draft.1").unwrap();
        assert_eq!(ir.schema_revision, "0.1.0-draft.1");
        assert_eq!(ir.roots.len(), 5);
        assert!(ir.types.iter().any(|item| item.name == "InterceptRequest"));
    }
}
