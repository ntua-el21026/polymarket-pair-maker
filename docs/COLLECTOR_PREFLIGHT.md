# Prospective collector preflight

Task 7A prepared an offline, reviewable next step. Task 7B records owner approval
of the new prospective lineage and conditional approval of exactly one
60-second Gamma 703257 smoke under the reviewed supervision policy. Its mandatory
access-scope preflight is **BLOCKED**; fresh market requests and the smoke remain
**NOT RUN**. No later stage is authorized. The
[current assessment](TASK7B_PREFLIGHT.md) distinguishes approval from satisfied
execution conditions. The first historical six-hour run remains
`GATE 1: FAIL`; its absent complete corpus has not been replayed under corrected
code. Both required corrected historical replays remain **UNSATISFIED**.

The external Task 7A evidence directory contains the exact local paths, hashes,
stage configurations, offline checks and proposed bounded launch recipe. These
machine-specific paths belong in external evidence, rather than committed
configuration. The original Task 3–6 evidence remains read-only.

## Decisions before the first stage

Two owner decisions remain separate; the owner approved both in Task 7B, with
the second conditional on every mandatory preflight gate passing:

1. Approve a prospective research programme despite the unavailable historical
   corpus, with a new lineage and the original failure and unsatisfied historical
   prerequisites retained explicitly. This does not make an old requirement pass.
2. Authorize only the first 60-second explicit-target smoke, including its
   permitted public-reference checks and proposed external supervision policy.
   A smoke authorization does not authorize calibration, stability or a soak.

Read-only collection is limited to permitted public-data access. Do not bypass
geographic or access restrictions, infer eligibility from an endpoint, or infer
permission from the presence of a candidate market. This document makes no
jurisdiction conclusion. The captured `restricted: true` flag requires explicit
qualification of any downstream interpretation; it does not establish that a
particular public read is illegal or that economic admission is satisfied.

The retained sequence is fixed:

| Stage | Duration | Command | Advancement |
| --- | ---: | --- | --- |
| Explicit-target smoke | 60 s | `observe polymarket` | Review and separate authorization |
| Storage/workload calibration | 180 s | `calibrate polymarket` | Passing v2 projection and independent review |
| Stability observation | 2,700 s | `observe polymarket` | Independent pre-soak review and separate authorization |
| Six-hour qualification | 21,600 s | `observe polymarket` | Unchanged evidence and acceptance requirements |

The first smoke is `CONDITIONALLY_APPROVED_PREFLIGHT_BLOCKED`; later stages are
`AWAITING_AUTHORIZATION`. No automatic transition,
restart, target substitution, duration extension or limit increase is allowed.

## Freeze source, environment and storage

Use the exact final committed source, a clean tracked and untracked worktree,
ordinary-GIL CPython 3.14.7, and the repository-local uv 0.12.5 launcher
`./scripts/uv`. Record source SHA, lock and pyproject file hashes, original and
effective TOML file hashes, canonical configuration hash, imported package path,
actual interpreter/uv paths, CPU, logical cores, memory, kernel and filesystem.
Repeat engineering and no-live qualification on that exact source. A previous
test result or a dirty-source hash does not qualify a future run.

The initial Task 7A baseline was
`1528b60a87291d9f80d116221a524b1fb9c4d99d`. The availability projection correction
changes the prospective calibration contract to `ppm-pre-soak-calibration-v2`
and `phase1-pre-soak-calibration-bindings-v2`. Historical v1 reports retain their
omitted availability scope and original bytes; they are not relabeled as v2.

`config/phase1.toml` remains the source of every numerical limit. Prepare a fresh
configuration for each stage by changing **only** these four storage values:

```toml
[storage]
root = "data/task7a/<date>-<stage>-<attempt>"
journal_directory = "data/task7a/<date>-<stage>-<attempt>/raw"
analytical_directory = "data/task7a/<date>-<stage>-<attempt>/analytical"
health_directory = "data/task7a/<date>-<stage>-<attempt>/health"
```

Keep all other tables and storage fields byte-equivalent in value. Compare
parsed configurations and reject changes outside the four path fields.

The CLI discovers the workspace by walking the configuration's parent paths for
`pyproject.toml`; if none is found, it uses the current working directory. Run
from the repository root and verify the inferred workspace explicitly. Storage
paths are repository-relative, must remain inside that workspace after symlink
resolution, and cannot point through a symlink to an external evidence directory.
Use fresh ignored `data/` roots for capture; retain review logs and bindings in
fresh external stage storage, disjoint from the sealed Task 7A evidence. The
calibration report output may also be a fresh external path. Treat sealed
configuration, runtime and review files as read-only inputs to the later stage;
keep its approved authorization and generated outputs outside that evidence root.

The observer creates run-ID-scoped files inside configured roots. It does not
require the whole root to be fresh. Before launch, reject an existing or symlinked
stage root and command-output directory, including an empty previous attempt.
The calibration additionally rejects dirty source and an existing report output.
Rebuild outputs must be fresh and disjoint from raw input. A failed attempt
gets a new attempt name; preserve its original files.

## Exact target and protocol checks

The proposed candidate is one ordinary binary market:

| Identity | Value |
| --- | --- |
| Gamma market ID | `703257` |
| Condition | `0x747dc809fb79e1b05be09c42d6179459a58de2ef3e40f02484a4e1260f741f75` |
| YES token | `107505882767731489358349912513945399560393482969656700824895970500493757150417` |
| NO token | `7305630249804085635496399869905769372294302716159034447326228509068694952392` |

Four Task 6 responses parse successfully through the current production schemas
offline: Gamma detail, the actual collector `/clob-markets/{condition}` route,
and the two `/book?token_id=...` responses. Preserve their original request times,
sizes and SHA-256. This is **compatibility for captured bytes**, not a fresh
target, protocol, legal, economic or operational qualification.

Before an authorized smoke, retain fresh permitted reference responses and
compare the requested/resolved Gamma ID, condition, exact YES/NO role mapping,
book identities, ordinary binary/non-negative-risk semantics, lifecycle,
tick/minimum size, source protocol and asset applicability. The direct captured
Gamma detail omits the parent event. Check the exact-ID relationship route
`GET /markets?id=703257&limit=2`, then only returned parent-event detail if present.
Do not infer an event ID or token role from unrelated array order. A missing,
ambiguous, changed or unsupported identity prevents this attempt.

The actual collector CLOB route is `/clob-markets/{condition}`, using the
abbreviated `c/t/mos/mts/fd` response schema. A legacy `/markets/{condition}`
response cannot substitute for it. The deterministic public subscription contains
exactly the two verified token IDs, `type=market`, `initial_dump=true`, `level=2`
and `custom_feature_enabled=true`. Retain its exact bytes and semantics hash.
Current WebSocket messages, parent-event responses and protocol applicability
remain unavailable until the specifically authorized preflight/stage executes.

The current CLI accepts a Gamma ID, rather than expected condition/token pins.
Bootstrap independently checks its fresh direct/relationship/canonical sources
against each other before subscribing, but does not compare them to a previously
approved external token snapshot. A fresh exact-reference check immediately
before launch reduces that gap; it does not remove the time-of-check gap. Record
the resolved startup identities and stop on a changed approved target. Do not
claim that the current command enforces immutable prior token pins before WS.

`restricted: true`, reward settings without an active native allocation, and
unresolved sponsored allocation do not qualify economic admission. Midpoint
construction, reward age, effective rules, fee units/rounding/asset applicability,
deployment/collateral identities and exact settlement authority remain separately
blocked. Collection alone does not supply fills, queue priority, verified rewards,
independent confirmation or profitability evidence.

## Resource and abort contract

Keep these original values and distinctions:

| Bound | Existing value and failure rule |
| --- | --- |
| Ingress/journal/analytical queues | 1,024 records each; projected threat at HWM >= 922 (90%); loss/overflow is a hard failure |
| Health/availability queues | 64 each; projected threat at HWM >= 58 (90%) |
| Ingress bytes | 64 MiB; overflow fails closed |
| HTTP / WS payload | 8 MiB / 1 MiB; oversize fails required protocol/schema admission |
| Raw segment | 10,000 events or 64 MiB uncompressed; ordinary segment rotation is expected |
| Raw run | 4 GiB uncompressed; projection >= cap fails; runtime cap reached prevents eligibility |
| Journal submission | 5 s timeout; failure is retained and cannot pass |
| Group commit | <=16 events, zero configured batching delay |
| Analytical record/buffer | 8 MiB / 64 MiB; writer failure/rejection/saturation prevents acceptance |
| Health evidence | 10 s cadence plus final; 8,192 records / 64 MiB |
| Availability evidence | 8,192 intervals / 64 MiB; exact chronology and aggregates required |
| Artifact paths | 65,536 per root; projection >= cap fails |
| Free disk | Preserve at least 1 GiB; below this amount blocks capture eligibility |
| Public HTTP | <=4 concurrent; <=3 attempts/read; 10 s request timeout; backoff 0.25–2 s, jitter 0.2/seed 0, Retry-After <=10 s; deadline constrained |
| Local 10 s request budgets | Gamma general 100/markets 30; CLOB general 100/book 100/books 50 |
| Audit/transition | Periodic 30 s; freshness guard 4 s; race quiet 0.25 s/<=4 retries; reported-top <=8 frames/2 s; book age <=5 s and interleg skew <=1 s |
| WS reconnect | Initial 1 s, maximum 15 s, <=8 attempts; preserve typed interval and terminal outcome |

Stop and preserve the attempt on identity/protocol mismatch, required schema or
book/arithmetic/provenance failure, non-contiguous ingest sequence, any data loss,
writer/durability/seal/verification failure, unexpected lifecycle termination,
replay/rebuild mismatch, unreconciled evidence, threatened resource bounds, or
any authenticated/mutation capability. A typed external disconnect or a fully
recovered observed mismatch remains evidence requiring review. It is not
automatically an internal defect; pending, unknown, local-defect or unevidenced
restoration cannot pass. Do not terminate normal bounded recovery merely to hide
its interval or convert cumulative observed mismatches into unresolved failures.

For the proposed 60-second stage only, the external launch recipe adds **proposed
engineering guardrails requiring owner acceptance in the stage authorization**:
16 MiB combined retained stdout/stderr, 2 GiB sampled process-group RSS, a 90 s
outer deadline and a 5 s interrupt-cleanup grace followed by forced kill and a
bounded final drain. The 90 s budget is 60 s observation + 10 s request + 5 s
journal submission + 15 s cleanup allowance. These are not measured arrival
rates or evidence that collector memory is acceptable. RSS sampling may miss
short peaks. Pipe writes are capped before retention; discarded output and
forced termination are incomplete evidence and cannot publish acceptance.
These guardrails do not enlarge or replace any application limit.

The calibration v2 projection uses exact ceiling arithmetic for 21,600 s at
**2x measured workload**. Include raw, analytical, health, availability and any
separately measured retained logs, 128 MiB temporary sealing, raw+analytical
replay/rebuild reserve, **25% additional disk safety**, and **1 GiB free reserve**.
Require strictly sub-cap raw/path/health/availability projections, no >=90% queue
threats, consistent accepted/written/rejected availability counters, complete
writers and healthy/reconciled evidence. The built-in log model remains
`STDIO_STREAM_ONLY_UNMANAGED` with zero managed bytes; captured external logs need
their own measured bounded accounting before later-stage admission. Do not
interpret this zero as zero disk for externally retained stdout/stderr.

Actual arrival rate, growth, CPU, RSS slope, latency and queue pressure remain
unknown. No storage/capacity projection has been executed on new market data.

## Task 7A launch recipe: NOT RUN

The external evidence supplies `configs/smoke-a01.toml`, an unapproved
`smoke-authorization.template.json`, and `run_smoke.py`. The default `--check`
only verifies local bindings/review references; it never invokes the observer.
It does not adjudicate the content or authority of a human decision.

The exact prospective CLI is shown below. It remains **NOT RUN**. The historical
template remains unapproved and unchanged; Task 7B owner approval is recorded
separately, with activation withheld because access and fresh-reference gates
are unsatisfied. A later source-changing documentation commit also invalidates
the sealed recipe's exact-source gate; do not edit sealed bindings or claim
that the old command qualifies a different SHA.
The external bounded launcher owns stdout/stderr; do not replace it with
unbounded shell redirection. `--offline --no-sync` prevents dependency setup;
it does not prevent the authorized application from contacting public services.

```bash
cd /home/peppasmich/projects/polymarket-pair-maker
E=/home/peppasmich/projects/polymarket-pair-maker-task7a-evidence-20261117
CFG="$E/collector/configs/smoke-a01.toml"
AUTHORIZATION=/home/peppasmich/projects/polymarket-pair-maker-smoke-authorization-20261117-a01.json

# Safe offline checklist; the unapproved template must refuse with exit 3.
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ./scripts/uv run --frozen --offline --no-sync --all-groups python "$E/collector/run_smoke.py" --check

# NOT RUN: underlying argv, executed only by the bounded authorized launcher.
./scripts/uv run --frozen --offline --no-sync --all-groups ppm observe polymarket --duration 60 --gamma-market-id 703257 --config "$CFG"

# NOT RUN: only after both owner decisions and all exact-source prerequisites.
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ./scripts/uv run --frozen --offline --no-sync --all-groups python "$E/collector/run_smoke.py" --authorization "$AUTHORIZATION" --execute
```

The proposed command-output root is the fresh sibling
`/home/peppasmich/projects/polymarket-pair-maker-smoke-evidence-20261117-a01`.
Neither that directory nor the approved authorization file exists as evidence of
an authorized run in Task 7A.

The launcher refuses reused roots, dirty source, mismatched source/config/lock/
target hashes, incomplete review references and unaccepted supervision budgets.
It offers no restart, subsequent-stage command or discovery fallback. Retain
partial captures, exit status, stop reason, maximum sampled RSS and all failed
attempts. A process exit of zero does not establish completed data or Gate PASS.

## Post-stage evidence before any advancement

1. Retain pre/post inventories with size/SHA-256 for every raw input; exhaust
   `JournalReader.iter_verified(include_partial=False)`. Verify every segment,
   contiguous sequence, manifest/hash chain and atomic raw completion head.
2. Verify analytical completion references that exact raw completion hash and
   inventory, matching run/source/config identity, no rejected writer records,
   and no partial artifact substituted for complete evidence. Completion heads
   are published after durable data; an interrupted prefix remains interrupted.
3. Validate the original stage root. Run two independent replay processes using
   the same exact source/config/run. Compare deterministic complete processor
   content, terminal/state/eligibility/audit/trigger/divergence/transition chains;
   keep the expected terminal hash distinct from a completion-manifest hash.
4. Rebuild analytics into a fresh disjoint directory with the independently
   recorded replay terminal hash; validate it and compare deterministic semantic
   content with the original through bounded column-selective reads. Repeat
   mismatch-lineage analysis twice with identical canonical output. Current
   book-eligibility reporting must state its migration scope; the removed
   historical taker scan-report command is not a currently executable CLI.
5. Validate current `ppm-health-checkpoint-v1` monotonic chronology and cadence,
   final checkpoint, exact run/config/source/target identity and paired terminal
   cutoff. Historical v1/v2/v3 health schemas retain their separate interpretation.
   Reconcile typed availability interval count, duration, longest interval,
   open-at-shutdown state and complete cause/recovery evidence exactly.
6. Independently inspect every recovery: exact REST divergence cause, separate
   authoritative resnapshot, one new connection's complete fresh snapshot set,
   positive admission ordinals after resolution, drained admitted prefix,
   independent post-processing freshness/book checks and the durable v2 release
   control at its exact paired decision time. Snapshot-only coverage, one binary
   leg or socket readiness cannot close the interval or restore eligibility.
7. Recheck raw inventory unchanged, host/resource/latency evidence and no-live
   capability. Preserve every negative result and unavailable check. Decide the
   stage outcome independently, then request separate authorization to advance.

Future offline commands are `ppm validate-data <stage-root>`, two independent
`ppm replay <raw-root> --run-id <actual-run-id> --config <stage-config>`,
`ppm rebuild-analytical <raw-root> --run-id <actual-run-id> --config <stage-config>
--expect-terminal-hash <independent-processor-hash> --output <fresh-rebuild>` and
`ppm validate-data <fresh-rebuild>`, all through the same isolated launcher and
bounded external capture. Actual run IDs and hashes are unavailable before an
authorized run; placeholders are not evidence. No six-hour Gate verdict follows
from this document, offline tests, a successful smoke, or synthetic replay speed.
