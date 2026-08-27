# Golden fixtures

**Status: Working Draft.** Fixtures under `draft/` carry the literal on-wire `protocolVersion` `draft` and target the matching schema snapshot declared by `spec/draft/requirements.json`. Published fixture snapshots carry their `YYYY-MM-DD` publication version instead.

- `stdio/*.jsonl` contains one JSON-RPC object per UTF-8 line. The multiline invalid case demonstrates a framing failure.
- `http/*.json` contains one JSON-RPC object used as an HTTP JSON request or response body.
- `registration/*.json` covers portable backend registration independently of transport delivery.

`draft/manifest.json` records the expected outcome, schema, requirements, and SHA-256 hash for every golden file. Refresh hashes only for a new unpublished edit with:

```sh
python3 tools/check_conformance.py --update-manifests
```

Then run the checker without the update flag to detect drift.
