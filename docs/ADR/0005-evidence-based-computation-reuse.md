# ADR-0005: Exact bounded computation reuse

Status: accepted for Task 5 implementation, 2026-11-17.

## Measured problem

Complete 1,004-record, 2,000-canonical-event profiles at 6/100/1,000 book levels
preceded production edits. At 1,000 levels, diagnostic canonical normalization
accounted for 185.50 cumulative seconds and 82.04 self seconds; snapshot economic
identities were recomputed 10,027 times, costing 74.65 cumulative seconds. Strategy
input/context hash callers accounted for 97.61 cumulative seconds. Candidate
construction and evaluation lookup each serialized/hashed 147,147 candidates.
Dataclass field discovery had 35.49 million calls and 35.55 cumulative seconds.
These inclusive profile costs overlap, include instrumentation, and are not
acceptance timings. Artifact appends cost only 0.83 inclusive CPU seconds; removing
verification or audit output would target the wrong boundary.

An apparent sorting hotspot was rejected after caller analysis: hashing performed
inside generator arguments dominated its inclusive time. No change to reward,
midpoint, fee, exact arithmetic or policy is justified by that attribution.

## Decision and identity contract

Reuse pure calculations on precisely identified immutable inputs. Retain all
canonical JSON bytes, SHA-256 domains, strategy input hashes, candidate identities,
reference/time gates, financial values and the Task 4 v2 artifact representation.
Caches cannot supply affirmative eligibility or share mutable scenario state.

| Mechanism | Key and validity | Bound and eviction |
| --- | --- | --- |
| Dataclass field discovery | Fixed source-selected dataclass type; retain Field objects, read each value and its canonical metadata flag on every call | 128 types; eviction rebuilds fields, without caching values |
| Snapshot canonical form | Weak identity of the exact owner, actual recursively immutable canonical fields, and `allow_floats` mode; return independent public normalized trees | At most two forms and 64 KiB aggregate encoded bytes per snapshot; oversized values use the original path; new/replaced snapshots begin empty |
| Snapshot diagnostic/economic digests | That exact immutable snapshot under each existing hash domain | Two digest strings per retained snapshot; replacement resets, collection releases; mutable malformed inputs use uncached behavior |
| Candidate grid | Market/token identity, tick, trading/reward minimum size, configured offsets/multipliers and both best bid/ask prices | At most `maximum_markets` grids per engine (maximum 32); LRU eviction regenerates through the unchanged public generator |
| Candidate evaluation lookup | Complete immutable Candidate plus the unchanged canonical context hash; ordinary hash collisions still require full key equality | Existing `cache_capacity` and LRU policy, default 256 and maximum 4,096; eviction recomputes exact economics |
| Evaluation context digest | Fresh canonical bytes of configuration, fees, calibration and capacity, market identity, and full equality of audited immutable depth | One last context per engine, at most 64 KiB non-book bytes and 16,384 levels; replacement or any changed input recomputes the original hash |
| Native decimal parsing | Complete source lexeme, after the original type and 128-character guards; exact immutable parsed value | 4,096 successful lexemes; invalid values retain current path-specific errors and are never retained |
| Normalized level construction | Complete concrete immutable price/size decimal values, at most 512 coefficient bits and scale 128 each | 4,096 successful nonzero levels; oversized/unknown values use original construction; side validation, duplicate checks, zero filtering and sorting still execute |
| Complete native side normalization | Full equality of a strictly typed immutable NativeBookLevel tuple and the actual BookSide, after successful validation | One last side per BID/ASK, at most 4,096 levels each; changed/bypassed inputs remove the previous cell; failed validation installs no success |
| Pure reward result | Fresh canonical bytes of all non-book reward inputs and current eligibility predicates, plus full ordered calculation-relevant book fields | One last result per existing scenario-local reward cache, at most 64 KiB non-book bytes and 16,384 levels; actual immutable type graph required; old identity-cache bounds/counters remain |
| Owner numeric side validation | Identical retained immutable canonical input tuples, identical exact tick object, and current verified precision | One last successful pair per owner, at most 8,192 input levels; changed identity/reference, invalidation or failed validation recomputes; observation/provenance updates always execute |
| Explicit immutable sequence encoding | Identical strongly retained exact tuple, successful actual-value audit under an explicit element/whitelist policy, and strict original encoding | Eight sequences, at most 4,096 items and 64 KiB each, 256 KiB aggregate encoded bytes; LRU eviction removes proof and encoding together |

Eligibility, health, freshness, reference knowledge/effective times and calibration
checks still execute before computation-cache use. Grid reuse ignores only values
that the pure generator does not read. Full decision input identity still binds
all context/current-quote fields. Evaluation context identity is unchanged and
still binds configuration, fees, calibration, capacity and complete price/size
inputs. No shorter approximate key replaces a persisted identity.

A frozen annotation alone does not prove immutable values. Snapshot memoization
must inspect actual canonical fields, bypass memoization for mutable or unknown
values, and preserve original error behavior. Private cache fields are excluded
from initialization, representation, equality, hashing and canonical serialization.
A `dataclasses.replace` operation gets new cache state. Permissive float encoding
cannot prime a strict-mode result. Public `canonicalize` results cannot alias a
cached mutable mapping. Unsupported JSON/UTF-8 encodings retain their original
normalization/encoding failure boundary.

Memo cells weakly bind their owner and reset if reused for a different object;
unweakrefable owners bypass reuse. Cached projections preserve primitive enum
leaf types through at most 64 paths of depth at most 32, discovered during the
immutable-value audit. Values beyond those bounds use the original path. No
strong owner cycle or shared mutable normalized tree is introduced.

Class schemas are fixed by the evaluated source revision. Runtime mutation of
class field declarations or Python reflection bypassing frozen state is outside
that contract. Mutable instance data is not implicitly memoized by the field
metadata cache. Snapshot caches add storage to retained snapshots; reward caches
may retain hundreds of snapshots, so memory growth is measured rather than
assumed to disappear immediately.

The first optimized complete 1,000-level diagnostic still spent 7.39 CPU seconds
on decision input hashes, 8.49 on evaluation-context hashes, 3.56 on native level
parsing and 2.51 on canonical level construction. Its reward profile retained
36.57 inclusive seconds under instrumentation. These measurements justify a
second reuse pass. Actual snapshot encodings were about 29.3 KiB: increasing the
64 KiB memo bound was rejected. Full snapshots replace level objects; per-level
memo cells were also rejected because they would allocate on every replacement.

Concrete Price/Quantity wrappers with exact immutable decimal values use the
same single-field canonical projection directly. Their live field metadata and
malformed/subclass fallback remain intact. No decimal arithmetic is approximated.

Reward reuse binds instrument/market identities, the exact healthy predicate,
complete ordered depth, full rules/orders, depth completeness, order-age
qualification, rule validity reasons and future/missing-book-time predicates.
Generation, source lineage and trade diagnostics do not enter the reward formula;
they remain in canonical observations and decision input identities. An explicit
actual-value audit excludes mutable values and unaudited numeric subclasses.
All three reward caches and their economic scenario states remain independent.

The second optimized profile measured 5.56 CPU seconds in complete input hashing
and 2.71 in economic identity. Fresh non-book encodings together cost only 0.44
seconds, so another non-book cache was rejected. The serializer instead composes
opted-in dataclass fields from their exact canonical encodings, using cached
snapshot bytes directly. Field normalization keeps declaration order; final keys
retain canonical sorted order. Encoding failures return to the original whole-tree
path to preserve normalization/error priority. Public normalized trees remain
independent. MarketContext is the explicit composition boundary; the hash domain,
all input fields and canonical bytes remain unchanged.

Native numeric caches belong to the serial canonical normalization path. They
retain pure values, never source lineage, reference admission or mutable book
ownership. A new event still receives its original source identity, event ID,
chronology, provenance, current precision validation and health transition.
Complete-side hits reuse a previously proven numeric normalization only after
checking actual immutable types and every input value. Module caches retain at
most two native sides and 4,096 individual levels. Immutable zero/one values are
constructed once per fallback side, and the owner's measured per-level upper
bound comparison reuses the exact constant one.

After those simpler changes, a complete 1,000-level pilot still took 12.53
seconds. Its next diagnostic measured economic identity at 3.23 CPU seconds,
including 0.93 in repeated immutable audits, and numeric owner validation at 0.87
seconds. This justifies the bounded sequence registry: successful owner validation
registers complete immutable BookLevel sides, and identical input tuples can
retain those sides across observations. Snapshot audits and serialization reuse
the same explicit proof and exact encoding. This changes no canonical book value.

Registration checks actual element types, immutable fields, depth/work/scalar
resource bounds and strict original encoding before publication. Primitive enum
leaf paths are excluded to preserve exact normalized leaf types. A caller with a
narrower audit whitelist cannot inherit a broader proof. Strong identity prevents
object-ID reuse; incompatible registration policies are rechecked. Encoding and
audit work occur outside a short registry metadata lock; eviction and byte counts
remain bounded. Unknown, mutable, oversized or evicted inputs use the original
path. Returned normalized trees remain fresh. This derived cache contains no
portfolio, queue, order, risk, reference-admission or inventory state.

## Verification and scope

Tests compare exact optimized/reference structures, bytes and hash identities
across misses, hits, mutations, eviction/reappearance, malformed values, timestamps,
references, markets and scenario isolation. Complete before/after replays compare
every v2 stream byte, eligibility assessment and canonical processing result.
Only source metadata and its consequent terminal/report digest may differ.
Historical v1 and v2 readers retain their documented compatibility boundaries.

The original formal workload, 100 records/s requirement, warmups, 300-second
child deadline, memory constraints and 256 MiB artifact limit remain unchanged.
Diagnostic profiles, variability stress and formal acceptance are separately
reported in the Task 5 engineering evidence. A failed screen stays failed.
Collector, venue-rule, execution-calibration and empirical-profitability gates
remain separate; computation speed cannot qualify them.
