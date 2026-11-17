# ADR-0003: Defer the official SDK and retain public transport

Status: accepted for Task 2, 2026-11-17.

## Decision

Task 2 explicitly defers the official Polymarket Python SDK. Retain existing
validated public HTTPX/WebSocket acquisition, strict response schemas and the
locked dependency graph. Add no signer, wallet flow, authenticated session,
production order adapter or paper-to-live switch.

The examined SDK requires `websockets>=13,<16`, conflicting with retained
`websockets==17.0.1`. Optional frame dependencies constrain Arrow below the
retained version. Exact CPython 3.14.7 qualification is not established. Those
conflicts do not justify rebuilding the collector for architectural symmetry.
Version/commit evidence is retained in [Research basis](../RESEARCH_BASIS.md).

Paper replay reads recorded references and explicit offline evidence; it never
performs network lookup. A current reference unavailable through an established
authoritative public contract stays unavailable. The implementation does not
invent endpoint schemas or substitute a plausible midpoint/rounding rule.

## Consequences

Existing transport, pins and compatibility fixtures remain intact. Real-market
admission is blocked where source detail is insufficient. A future separately
authorized integration may revisit a reviewed SDK after dependency and runtime
qualification, without implying authorization for live trading.
