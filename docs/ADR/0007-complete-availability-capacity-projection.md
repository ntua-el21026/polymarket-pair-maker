# ADR-0007: Complete availability-ledger capacity projection

Status: accepted for the Task 7A corrective phase, 2026-11-17.

## Independently reproduced omission

The pre-soak calibration measured raw, analytical and health storage, but omitted
availability-ledger bytes, interval counts, writer completion and queue pressure.
A synthetic offline reproduction supplied 500,000 bytes, 50 completed intervals
and a queue high-water mark of 58/64 during a 180-second measurement. The v1
measurement was unchanged and the report could PASS. The unchanged six-hour,
two-times-workload projection instead predicts 120,000,000 bytes and 12,000
intervals, exceeding the configured 64 MiB and 8,192 limits; 58/64 also breaches
the existing 90% queue threshold. Runtime limits still enforced their bounds.
The defect was incomplete prospective capacity evidence, not unbounded runtime
writing or a trading-model error.

## Narrow correction

`pipeline/calibration.py` now retains the availability writer's measured bytes,
accepted/written/rejected counts, completion/failure status and queue high-water
mark. Actual limits come from the same `Phase1Config`. Required counters are known
non-negative integers; missing or malformed values fail before publication.
Contradictory counts/bytes, rejected records or an incomplete/ineligible/failing
writer prevent a passing report.

`research/calibration.py` projects availability bytes and written intervals with
the existing exact integer ceiling, 21,600-second horizon and 20,000 basis-point
headroom. Ledger bytes enter disk workload before the unchanged 25% safety margin
and configured disk reserve. Reaching either availability cap fails, as does
reaching the existing 90% queue threshold. No configuration limit, acceptance
threshold, financial formula, execution assumption or strategy parameter changed.

The report and binding contracts become `ppm-pre-soak-calibration-v2` and
`phase1-pre-soak-calibration-bindings-v2`. Historical v1 constants remain named.
There is no legacy report loader to migrate: original v1 JSON and hashes retain
their historical, narrower scope. They are never rewritten, relabeled or accepted
as complete v2 evidence for a future stage. The current typed report rejects a
v1 schema declaration rather than silently interpreting it as v2.

## Verification and remaining limits

The reproduction was captured before correction; a targeted test failed before
the fix and passed afterward. Tests cover exact file/count boundaries, disk
accounting before safety/reserve, queue boundaries, missing/invalid counters,
writer failure, deterministic artifacts and schema identity. Existing test
identities and substantive assertions remain; synthetic fixtures supply explicit
availability facts and the schema assertion now expects v2. An independent
review checked the arithmetic with rational calculations and configuration binding.

This is operational storage calibration, separate from execution/fill calibration
in `research/paper_calibration.py`; that estimator remains unchanged. A projection
is conditional on observed workload and cannot prove future recovery frequency.
Zero measured intervals do not establish that future interruptions are absent.

The stdio contract remains `STDIO_STREAM_ONLY_UNMANAGED`, with zero **managed** log
bytes. An external harness that captures stdout/stderr must bound and account for
those files separately; zero managed bytes is not a zero external-storage claim.
The prospective preflight defines that scope. No observation or calibration was
run in Task 7A, and no v2 field substitutes for qualified real measurements.
