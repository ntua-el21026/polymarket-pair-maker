# ADR-0006: Causal readiness declarations and exact subset composition

Status: accepted for Task 6 implementation, 2026-11-17.

## Problem and evidence

The failed six-hour corpus has not been recovered in the bounded authorized
search. Current official international sources and a selected ordinary binary
reference market do not resolve all midpoint, exact fee settlement, deployment,
asset, minimum-age and effective-interval questions. No real fill calibration or
profitability evaluation is justified. A typed readiness boundary can prepare
future inputs without promoting declarations or synthetic fixtures to acceptance.

The starting `07825c0` complete 1,000-level diagnostic measured 1.003 CPU seconds
in repeated full-bid sums and 1.396 CPU seconds in owner economic identity.
After bid-total reuse, the latter still consumed 1.386 CPU seconds. Those
separately instrumented inclusive measurements justify the two optimizations
below; they are not formal acceptance timings and cannot be added as savings.

## Decisions

Add the network-free calibration readiness batch and report described in
[CALIBRATION_READINESS.md](../CALIBRATION_READINESS.md). Preserve the existing
market-scoped estimator, financial assumptions and real-market admission block.
Validate a declared multi-market cohort with consistent condition/parent/category,
single-target collector-run/completion, protocol and raw-source identities.
Separate quote-as-of rule knowledge from later report availability. Retain missing
markouts, unfilled trials, censoring and unknown exposure. Only declared observed
execution with upstream reviewed-report identities can count toward descriptive
support; those declarations still require independent verification. A readiness
status is never a collector, venue, execution or economic PASS.

Reuse the original ordered exact bid-quantity sum on the same actual immutable
tuple in each independent `DecisionEngine`. Key by retained tuple identity and
verify `is`; audit concrete tuple/BookLevel/Price/Quantity/ExactDecimal/int values,
at most 4,096 levels, 512 coefficient bits and scale 128. Retain two LRU entries
per engine. Changed identity recomputes; unsupported, mutable, malformed or
oversized input clears the cells and uses the original sum. Numeric totals have
no market or scenario state. Every gate still checks fresh scope, references,
health, calibration and risk in its original order. No eager work precedes input
identity or earlier gate failures.

Compose an audited owner's economic-subset bytes from the already proven strict
sequence encodings and freshly normalized other fields. Keep declaration-order
normalization of the complete original projection before selecting fields. Sort
keys only at final encoding, preserve the schema override and exact SHA-256 domain,
and prime the existing full-owner memo under its unchanged 64 KiB aggregate bound.
No new cache or wider registration/resource policy is introduced. Overridden
projections and values whose canonical dispatch precedes dataclass dispatch keep
the original path. Fragment encoding errors return to whole-tree encoding so
global Unicode positions and later JSON-error precedence are preserved.

## Verification and qualification

Independent ordered-sum and original whole-projection oracles compare full
decisions and exact canonical bytes/hashes. Regressions cover replacement,
eviction, mutable/subclass inputs, scope/time/reference changes, Unicode and
malformed projections. Complete replay differentials retain all streams and
economic transitions; only declared source metadata and its derived terminal
and manifest hashes may differ. V1/v2 format contracts remain unchanged.

Task 6 separately requires three complete formal attempts at one frozen source
revision, each depth sustaining at least 120 raw records/s. The original 100
records/s gate remains separate. Inputs, grids, scenarios, three warmups, timed
fourth replay, verification/publication, child deadline and all capacity/memory
limits remain unchanged. Diagnostic pilots cannot qualify either formal claim.
The external Task 6 evidence directory records every final attempt, exact source,
workload and environment hashes, measurements and per-gate verdicts.

No new collector operation, external support message or economic experiment is
authorized by this ADR. Collector Gate 1 remains FAIL; venue rules remain BLOCKED;
execution calibration and empirical profitability remain INSUFFICIENT_EVIDENCE.
