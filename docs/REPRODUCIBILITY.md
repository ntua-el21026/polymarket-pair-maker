# Qualified local development environment

The project requires ordinary-GIL CPython **3.14.7** and **uv 0.12.5**. The exact
uv requirement in `pyproject.toml` intentionally rejects other versions before
running project operations. Keep `uv.lock`, `.python-version` and the dependency
constraints unchanged when repairing a tool-selection mismatch.

## Isolate the tool

Run project commands through `./scripts/uv`. It selects only
`.tools/uv/0.12.5/bin/uv`, checks the exact declared version, changes to the
repository root and prepends that binary directory to its child PATH. Nested
pre-commit commands therefore resolve the same tool. Missing, nonexecutable or
wrong-version tools fail with exit 2. It never downloads a tool, updates a global
installation or falls back to whichever `uv` happens to be on PATH. Arguments are
delegated directly, without a shell.

The launcher uses the bootstrap system Python's standard library (Python 3.11 or
later for `tomllib`); the project itself still runs under the required 3.14.7.
The ignored `.tools/` directory is separate from the project `.venv`.

For a fresh local tool installation, use an isolated environment:

```bash
python3 -m venv .tools/uv/0.12.5
.tools/uv/0.12.5/bin/python -m pip install --only-binary=:all: uv==0.12.5
./scripts/uv --version
```

This installs the tool inside the project and leaves global packages/tools
unchanged. Obtain packages only through the approved distribution source and
record their identities. Do not recreate an existing environment without
checking its role and preserving it. An already qualified standalone executable
may instead be copied to the same `bin/uv` location after verifying its SHA-256,
size and version; record the original and copied identities. Do not copy an
unknown binary or turn a version string into a provenance claim.

Task 7A used the latter offline method: the byte-verified Task 6 uv executable was
copied into the ignored local tool directory. The per-user global installation
and its receipt report 0.12.9. Those observations establish separate tool
identities, not who installed the global version or why. Task 6 explicitly used
its own 0.12.5 tool; its successful results never qualified the global PATH.

## Preserve and qualify the runtime

Use `./scripts/uv sync --frozen --all-groups` only for the intended project
environment. If `.venv` points into historical evidence, preserve that target
and prepare a fresh environment before any sync. For an external qualification
environment, set `UV_PROJECT_ENVIRONMENT` explicitly and record it. Pyright's
configured `.venv` must resolve to the same qualified dependency environment.
Record any local symlink change; do not delete the previous target.

Task 7A copied the preserved Task 6 environment into a fresh external evidence
directory, changed only copied executable shebangs, and repointed the ignored
project symlink. Historical environments remained untouched. Set
`PYTHONDONTWRITEBYTECODE=1` and a fresh `PRE_COMMIT_HOME` for evidence runs.

Required local checks, from the repository root:

```bash
./scripts/uv run --frozen --all-groups pytest -q
./scripts/uv run --frozen --all-groups pyright
./scripts/uv run --frozen --all-groups ruff check .
./scripts/uv run --frozen --all-groups ruff check scripts/uv
./scripts/uv run --frozen --all-groups ruff format --check .
./scripts/uv run --frozen --all-groups ruff format --check scripts/uv
./scripts/uv run --frozen --all-groups pre-commit run --all-files
./scripts/uv run --frozen --all-groups python scripts/check_repository.py
./scripts/uv lock --check --offline
./scripts/uv build --out-dir <fresh-external-package-directory>
```

Run the configured runtime dependency export/audit in
[VALIDATION_GATES.md](VALIDATION_GATES.md), then install the new wheel into a
fresh isolated destination with the qualified dependency environment. Check the
installed import path and source bytes, CLI help, offline synthetic demo,
complete artifact verification and typed reconstruction. A source-tree import
alone is not an installed-wheel smoke test.

Record the starting/final source SHA, clean status, actual executable paths,
versions, GIL mode, lock/configuration/source hashes, exact argv, exit codes and
all negative results. Seal the external evidence inventory only after writers
finish; exclude environments/caches explicitly and verify every indexed hash.

These checks exercise local engineering. They do not authorize a collector,
qualify venue rules, establish real fills or demonstrate profitability.
