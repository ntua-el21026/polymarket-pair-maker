# Historical collector evidence

This directory preserves substantive evidence for the failed first six-hour
Polymarket collector soak and the accepted recovery contracts present before
migration. The current acceptance result remains **`GATE 1: FAIL`**. It records a
completed observation with successful raw preservation and deterministic replay,
alongside book-state invariant failures, a bounded reporting failure, and missing
recovery/availability evidence. Subsequent remediation and local regressions do
not constitute independent long-run acceptance.

The five preserved documents are byte-identical to their originals at source
commit `3ece7fe5ebb2aa94ae4993847d69661620b551c4`. Their original repository
paths, sizes, and SHA-256 hashes are recorded in
[documents.json](documents.json). Verify them before interpreting or relocating
evidence. New explanatory text belongs here, not inside the frozen documents.

| Preserved document | Purpose |
| --- | --- |
| [Failure report](GATE_1_FAILURE_20260822.md) | Run identity, historical outcomes, hard failures, and missing evidence |
| [Remediation specification](GATE_1_REMEDIATION_SPEC.md) | Correction and independent rerun requirements |
| [Soak specification](GATE_1_SOAK_SPEC.md) | Original acceptance criteria and evidence requirements |
| [ADR-0053](0053-bounded-reported-top-transition-state.md) | Bounded reported-top uncertainty, resnapshot, and regression obligations |
| [ADR-0054](0054-audit-divergence-state-and-gate-eligibility.md) | Immutable mismatch history, fail-closed recovery, replacement capture, and release evidence |

Historical paths, commands, names, metrics, architecture references, and relative
links inside these frozen documents retain their original meaning. Resolve
references absent from this directory at the source commit, using
`git show <source-commit>:<original-path>` or the
[historical tree](https://github.com/ntua-el21026/polymarket-pair-maker/tree/3ece7fe5ebb2aa94ae4993847d69661620b551c4).
They are intentional historical exceptions to current naming and link checks.
They do not reintroduce obsolete scope or override the current project,
strategy, architecture, and validation specifications.

The full raw journal, Parquet outputs, and independent artifact-review directory
are external inputs and are not supplied by a clean clone. The historical local
paths in the report identify the original evidence environment; they do not
establish availability in this WSL workspace. The sanitized, byte-exact four-case
fixture corpus remains under
[`tests/golden/fixtures/gate1/`](../../tests/golden/fixtures/gate1/), with decoded
payload sizes/hashes and original run/segment provenance in `corpus-metadata.json`.
Those messages are regression evidence, not complete replayable book histories.

The separate
[`historical_raw_envelope_v1.json`](../../tests/golden/fixtures/historical_raw_envelope_v1.json)
fixture records a 787-byte pre-rename envelope from the source commit with SHA-256
`9ab91e2a73e0ea3a855c9287250bfde20f261a2f980909023b55005bbe7c6f0f`.
Together with fixed deterministic-ID vectors, it tests the persisted wire
contract independently of import naming. It is separate from the six-hour corpus.

The migration neither reruns the six-hour soak nor claims its acceptance passed.
The old scanner's complete terminal results require its historical code and
configuration. Current raw decoding and canonical-identity compatibility are
verified separately; changes to derived output contracts must remain explicit.

Verify this document inventory from the repository root:

```bash
python3 - <<'PY'
import hashlib
import json
from pathlib import Path

inventory = json.loads(Path("docs/historical/documents.json").read_text())
for record in inventory["files"]:
    payload = Path(record["preserved_path"]).read_bytes()
    assert len(payload) == record["bytes"], record["preserved_path"]
    assert hashlib.sha256(payload).hexdigest() == record["sha256"], record["preserved_path"]
print(f"Verified {len(inventory['files'])} historical documents")
PY
```
