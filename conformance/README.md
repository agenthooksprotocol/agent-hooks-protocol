# Conformance profiles

**Status: Working Draft.** These descriptions target snapshot `draft` and do not replace the canonical specification or requirements manifest.

`draft/manifest.json` maps language-neutral profile descriptions to stable requirement IDs. The profiles define externally observable inputs, outputs, and invariants without prescribing an implementation language, library, process model, or test framework.

A complete conformance claim includes the Base Protocol profile, at least one capability profile, at least one transport binding, and the implementation role. Run `python3 tools/check_conformance.py` to verify references and artifact hashes.
