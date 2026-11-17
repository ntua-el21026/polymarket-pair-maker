"""Single-owner orchestration of independent PAPER scenario portfolios."""

from __future__ import annotations

from collections import Counter
from contextlib import suppress
from dataclasses import replace

from polymarket_pair_maker.core.exact import ExactDecimal, Money, Price, Quantity, RoundingMode
from polymarket_pair_maker.core.ids import InstrumentId, MarketId
from polymarket_pair_maker.core.serialization import canonical_sha256
from polymarket_pair_maker.core.time import UtcTimestamp
from polymarket_pair_maker.economics.fees import canonical_fee_reasons, execution_fee
from polymarket_pair_maker.economics.rewards import RewardOrder, RewardScoreCache
from polymarket_pair_maker.paper.artifacts import PaperArtifacts
from polymarket_pair_maker.paper.config import PaperConfig
from polymarket_pair_maker.paper.evidence import MarketEvidence, PaperEvidence
from polymarket_pair_maker.paper.execution import (
    ObservedPressure,
    PaperSimulator,
    PressureKind,
    QueueScenario,
)
from polymarket_pair_maker.paper.ledger import LiquidationQuote, PaperLedger
from polymarket_pair_maker.paper.normalization import NormalizedArtifacts
from polymarket_pair_maker.paper.orders import (
    Outcome,
    PaperOrderId,
    PaperOrderIntent,
    PaperOrderState,
)
from polymarket_pair_maker.pipeline.market_view import CanonicalMarketView
from polymarket_pair_maker.reference import POLYMARKET_PUSD_ASSET_ID
from polymarket_pair_maker.reference.models import LiquidityRole
from polymarket_pair_maker.risk.limits import RiskController
from polymarket_pair_maker.state.order_books import ImmutableOrderBookSnapshot, InvalidationReason
from polymarket_pair_maker.strategy.engine import (
    ActiveQuote,
    DecisionEngine,
    MarketContext,
    StrategyAction,
    StrategyDecision,
)

ZERO = ExactDecimal.zero()


class ScenarioSession:
    def __init__(
        self,
        config: PaperConfig,
        scenario: QueueScenario,
        evidence: PaperEvidence | None,
        artifacts: PaperArtifacts,
        *,
        source_run_id: str,
    ) -> None:
        self.config = config
        self.scenario = scenario
        self.evidence = evidence
        self.artifacts = artifacts
        self.source_run_id = source_run_id
        self.ledger = PaperLedger(
            config.starting_capital,
            POLYMARKET_PUSD_ASSET_ID,
            maximum_records=config.maximum_ledger_records,
        )
        self.risk = RiskController(config.risk)
        self.simulator = PaperSimulator(
            self.ledger, self.risk, replace(config.execution, scenario=scenario)
        )
        self.engine = DecisionEngine(config.strategy)
        self.reward_cache = RewardScoreCache(capacity=config.strategy.cache_capacity)
        self.active: ActiveQuote | None = None
        self._exit_requested = False
        self.decisions: Counter[str] = Counter()
        self.abstentions: Counter[str] = Counter()
        self.fail_closed: Counter[str] = Counter()
        self._event_index = 0
        self._last_ledger_hash: str | None = None
        self._market_evidence = (
            {} if evidence is None else {m.market_id: m for m in evidence.markets}
        )
        self._calibrations = {
            key: item.calibration() for key, item in self._market_evidence.items()
        }
        pressure_lists: dict[int, list[ObservedPressure]] = {}
        if evidence is not None:
            for item in evidence.pressures:
                pressure_lists.setdefault(item.ingest_seq, []).append(item.pressure)
        self._pressure_by_seq = {seq: tuple(items) for seq, items in pressure_lists.items()}
        self._pressure_boundaries_consumed: set[int] = set()
        self._state_chain = canonical_sha256(("ppm-paper-state-chain-v2", scenario))
        self._record_index = 0
        self._normalized = NormalizedArtifacts(scenario, self._record)

    def _record(self, name: str, seq: int, value: object) -> None:
        record = {
            "scenario": self.scenario,
            "ingest_seq": seq,
            "record_index": self._record_index,
            "value": value,
        }
        self.artifacts.append(name, record)
        self._state_chain = canonical_sha256((self._state_chain, name, record))
        self._record_index += 1

    def _context(self, view: CanonicalMarketView, now: UtcTimestamp) -> MarketContext | None:
        proof = view.proof
        by_id = {book.instrument_id: book for book in view.books}
        yes_id, no_id = proof.yes_instrument_id, proof.no_instrument_id
        if yes_id is None or no_id is None or yes_id not in by_id or no_id not in by_id:
            return None
        market_id = view.bundle.market.market_id
        evidence = self._market_evidence.get(market_id)
        risk_verified = (
            evidence is not None
            and not evidence.risk_evidence.valid_at(now)
            and now.nanoseconds_since_epoch
            - evidence.risk_evidence.times.observed_at.nanoseconds_since_epoch
            <= self.config.strategy.maximum_reference_age_ns
        )
        qualified = self.evidence is not None and self.evidence.collector_qualification in {
            "PASS",
            "SYNTHETIC_ONLY",
        }
        # Existing reservations are counted only for evaluating retention; actual
        # submission is separately atomic and cannot spend this capacity twice.
        existing = self.ledger.reserved_cash if self.active is not None else ZERO
        capacity = min(
            self.ledger.available_cash + existing,
            self.config.risk.maximum_market_commitment.value
            - self.ledger.market_committed(market_id)
            + existing,
            self.config.risk.maximum_total_commitment.value
            - self.ledger.total_committed
            + existing,
        )
        held_markets = {position.market_id for position in self.ledger.positions()}
        held_markets.update(
            reservation.intent.market_id for reservation in self.ledger.reservations()
        )
        if self.risk.halted or not qualified or held_markets - {market_id}:
            capacity = ZERO
        return MarketContext(
            bundle=view.bundle,
            assessment=view.assessment,
            yes_book=by_id[yes_id],
            no_book=by_id[no_id],
            as_of=now,
            fee_rules=None if evidence is None else evidence.fee_rules,
            reward_rules=None if evidence is None else evidence.reward_rules,
            calibration=self._calibrations.get(market_id),
            risk_capacity=Money(max(ZERO, capacity)),
            event_risk_verified=bool(risk_verified and evidence and evidence.event_risk_verified),
            catalyst_excluded=False if evidence is None else evidence.catalyst_excluded,
            settlement_rules_verified=bool(
                risk_verified and evidence and evidence.settlement_rules_verified
            ),
        )

    def _decision(
        self,
        seq: int,
        now: UtcTimestamp,
        decision: StrategyDecision,
        books: dict[InstrumentId, ImmutableOrderBookSnapshot],
        views: dict[MarketId, CanonicalMarketView],
    ) -> None:
        self.decisions[decision.action.value] += 1
        if decision.action is StrategyAction.NO_QUOTE:
            self.abstentions.update(decision.reasons)
        self._normalized.decision(seq, decision)
        if not self.simulator.connected:
            return
        if decision.cancel_immediately:
            self.simulator.safety_invalidate(now, reason=";".join(decision.reasons)[:500])
        candidate = decision.candidate
        outstanding = self.simulator.active_orders()
        if candidate is None:
            self._exit_requested = True
            if outstanding:
                self.simulator.safety_invalidate(now, reason="NO_QUOTE")
            return
        if decision.retained_existing:
            return
        if outstanding:
            # Cancellation acknowledgement, including partial-fill accounting,
            # must precede replacement. Next relevant event re-evaluates anew.
            self.simulator.safety_invalidate(now, reason="REPLACEMENT")
            return
        view = views[candidate.market_id]
        parent = view.bundle.market.gamma_event_id
        if parent is None:
            self.fail_closed["PARENT_EVENT_UNKNOWN"] += 1
            self._record("risk.jsonl", seq, {"blocked": "PARENT_EVENT_UNKNOWN"})
            return
        intents = tuple(
            PaperOrderIntent(
                order_id=PaperOrderId(
                    canonical_sha256(
                        (
                            "ppm-paper-order-v1",
                            self.source_run_id,
                            self.scenario,
                            self.config.config_hash,
                            seq,
                            candidate.identity,
                            leg.outcome,
                        )
                    )
                ),
                market_id=candidate.market_id,
                instrument_id=leg.instrument_id,
                parent_event_id=parent,
                outcome=Outcome(leg.outcome),
                price=leg.price,
                quantity=leg.quantity,
                created_at=now,
                expires_at=UtcTimestamp(
                    now.nanoseconds_since_epoch + self.config.risk.maximum_quote_age_ns
                ),
            )
            for leg in candidate.legs
        )
        risk_decision = self.risk.assess(self.ledger, intents)
        self._record("risk.jsonl", seq, risk_decision)
        if not risk_decision.allowed:
            self.fail_closed.update(risk_decision.reasons)
            return
        self.simulator.submit(intents, books, now)
        self.active = ActiveQuote(candidate, now)
        self._exit_requested = False

    def process(
        self,
        *,
        seq: int,
        now: UtcTimestamp,
        views: dict[MarketId, CanonicalMarketView],
        dirty: bool,
        source_valid: bool,
    ) -> None:
        books = {book.instrument_id: book for view in views.values() for book in view.books}
        pressures = self._pressure_by_seq.get(seq, ())
        if pressures:
            # Delayed public pressure cannot prove which counterfactual fills
            # preceded an already simulated acknowledgement. Preserve capacity
            # and report unavailable reconciliation instead of discarding it.
            for pressure in pressures:
                self.simulator.validate_pressure(pressure)
                if pressure.kind in {PressureKind.PRICE_TOUCH, PressureKind.DEPTH_DECREASE}:
                    continue
                for order in self.simulator.active_orders():
                    if (
                        order.intent.instrument_id != pressure.instrument_id
                        or order.intent.price != pressure.price
                        or pressure.occurred_at >= order.intent.expires_at
                    ):
                        continue
                    evidence = self._market_evidence.get(order.intent.market_id)
                    view = views.get(order.intent.market_id)
                    if (
                        evidence is None
                        or evidence.fee_rules is None
                        or view is None
                        or canonical_fee_reasons(
                            view.bundle,
                            evidence.fee_rules,
                            now,
                            self.config.strategy.maximum_reference_age_ns,
                        )
                    ):
                        self.risk.halt("EXECUTION_FEE_REFERENCE_UNAVAILABLE")
                        if self.simulator.connected:
                            self.simulator.safety_invalidate(
                                now, reason="EXECUTION_FEE_REFERENCE_UNAVAILABLE"
                            )
                        raise ValueError(
                            "fill accounting fee reference unavailable; reservations retained"
                        )
            self._pressure_boundaries_consumed.add(seq)
        if not source_valid:
            self.risk.halt("CANONICAL_INPUT_INTEGRITY_FAILURE")
            self.fail_closed["CANONICAL_INPUT_INTEGRITY_FAILURE"] += 1
        disconnected = any(
            book.invalidation_reason
            in {
                InvalidationReason.DISCONNECT,
                InvalidationReason.BACKPRESSURE_DATA_LOSS,
            }
            for book in books.values()
        )
        if disconnected and self.simulator.connected:
            self.simulator.disconnect(now, reason="RECORDED_CANONICAL_TRANSPORT_LOSS")
            self.risk.halt("RECORDED_TRANSPORT_LOSS_REQUIRES_ORDER_RECONCILIATION")
            self.fail_closed["RECORDED_TRANSPORT_LOSS_REQUIRES_ORDER_RECONCILIATION"] += 1
        safety = self._clock_safety(views, now)
        if safety and self.simulator.connected:
            self.fail_closed.update(safety)
            self.simulator.safety_invalidate(now, reason=";".join(safety)[:500])
        self.simulator.advance(now, books)
        for pressure in pressures:
            if pressure.available_at != now:
                raise ValueError("pressure availability must equal its recorded ingest boundary")
            self._record("pressure.jsonl", seq, self.simulator.process_pressure(pressure))
        # Current fills and accepted depth can breach the loss limit at this
        # boundary. Latch the stop before evaluating or reserving another quote.
        if (
            not self.risk.observe_wealth(
                self.ledger, self.valuation(views, now)["executable_wealth"]
            )
            and self.simulator.connected
        ):
            self.simulator.safety_invalidate(now, reason=self.risk.halted_reason or "RISK_HALT")
        if not self.simulator.active_orders():
            self.active = None
        if dirty or not views:
            contexts = tuple(
                context
                for key in sorted(views, key=lambda item: item.value)
                if (context := self._context(views[key], now)) is not None
            )
            ranking = self.engine.evaluate_markets(contexts, current=self.active)
            selected = ranking.selected
            if selected is None:
                reasons = tuple(
                    dict.fromkeys(
                        reason
                        for _market, decision in ranking.decisions
                        for reason in decision.reasons
                    )
                ) or ("NO_VALIDATED_ELIGIBLE_MARKET",)
                if self.evidence is None or self.evidence.collector_qualification not in {
                    "PASS",
                    "SYNTHETIC_ONLY",
                }:
                    reasons += ("COLLECTOR_QUALIFICATION_UNAVAILABLE_OR_FAILED",)
                selected = StrategyDecision(
                    StrategyAction.NO_QUOTE,
                    None,
                    reasons,
                    (),
                    bool(self.active),
                    False,
                    canonical_sha256((seq, now, tuple(views))),
                )
            self._record(
                "selection.jsonl",
                seq,
                self._normalized.ranking(
                    seq, ranking, fallback=selected if ranking.selected is None else None
                ),
            )
            self._decision(seq, now, selected, books, views)
            for context in contexts:
                if context.reward_rules is not None:
                    own = tuple(
                        RewardOrder(
                            order.intent.order_id.value,
                            order.intent.outcome.value,
                            "BUY",
                            order.intent.price,
                            order.remaining,
                            order.acknowledged_at
                            if order.state
                            in {PaperOrderState.RESTING, PaperOrderState.PARTIALLY_FILLED}
                            else None,
                        )
                        for order in self.simulator.active_orders()
                        if order.intent.market_id == context.bundle.market.market_id
                        and order.remaining.value > ZERO
                    )
                    estimate = self.reward_cache.estimate(
                        yes_book=context.yes_book,
                        no_book=context.no_book,
                        own_orders=own,
                        rules=context.reward_rules,
                        decision_at=now,
                        depth_complete=False,
                    )
                    self._record(
                        "incentives.jsonl",
                        seq,
                        {
                            "classification": "ESTIMATED_INCENTIVES",
                            "snapshot": estimate,
                            "daily_reward_estimate": None,
                            "daily_rebate_estimate": None,
                            "conservative_income": Money(ZERO),
                            "verified_payments": None,
                            "reasons": (
                                "RANDOM_SAMPLING_AND_WALLET_ATTRIBUTION_UNAVAILABLE",
                                "DAILY_PAYOUT_UNVERIFIED",
                            ),
                        },
                    )
        if self._exit_requested or self.risk.halted:
            self._try_exit(seq, now, views)
        valuation = self.valuation(views, now)
        if (
            not self.risk.observe_wealth(self.ledger, valuation["executable_wealth"])
            and self.simulator.connected
        ):
            self.simulator.safety_invalidate(now, reason=self.risk.halted_reason or "RISK_HALT")
        events = self.simulator.events_since(self._event_index)
        for event in events:
            self._record("orders.jsonl", seq, event)
        self._event_index += len(events)
        snapshot = self.ledger.snapshot()
        ledger_hash = canonical_sha256(snapshot)
        if ledger_hash != self._last_ledger_hash:
            self._record("ledger.jsonl", seq, snapshot)
            self._last_ledger_hash = ledger_hash
        self.ledger.check_invariants()

    def _try_exit(
        self, seq: int, now: UtcTimestamp, views: dict[MarketId, CanonicalMarketView]
    ) -> None:
        """Reduce inventory using this accepted depth after orders reconcile.

        Each sweep is an internal taker assumption at a recorded boundary.
        Missing/invalid depth or fees leave positions explicitly unresolved.
        """
        if self.simulator.active_orders() or not self.simulator.connected:
            return
        books = {book.instrument_id: book for view in views.values() for book in view.books}
        for position in self.ledger.positions():
            book = books.get(position.instrument_id)
            evidence = self._market_evidence.get(position.market_id)
            if (
                book is None
                or evidence is None
                or not book.is_fresh(now, max_age_ns=self.config.risk.maximum_market_data_age_ns)
            ):
                self._record(
                    "liquidations.jsonl",
                    seq,
                    {
                        "instrument_id": position.instrument_id,
                        "status": "UNRESOLVED",
                        "reason": "FRESH_EXIT_DEPTH_OR_REFERENCE_UNAVAILABLE",
                    },
                )
                continue
            try:
                fee_rules = evidence.fee_rules
                if fee_rules is None:
                    raise ValueError("liquidation fee reference unavailable")
                fee_reasons = canonical_fee_reasons(
                    views[position.market_id].bundle,
                    fee_rules,
                    now,
                    self.config.strategy.maximum_reference_age_ns,
                )
                if fee_reasons:
                    raise ValueError(";".join(fee_reasons))
                quote = self.ledger.quote_liquidation(
                    book,
                    position.quantity,
                    now=now,
                    maximum_book_age_ns=self.config.risk.maximum_market_data_age_ns,
                    fee_evaluator=lambda price, size, e=evidence: self._fee(e, now, price, size),
                    slippage_fraction=self.config.exit_slippage_fraction,
                    impact_fraction=self.config.exit_impact_fraction,
                )
                event_id = canonical_sha256(("ppm-paper-exit-v1", self.scenario, seq, quote))
                self.ledger.apply_liquidation(event_id, quote, book)
            except ValueError as error:
                self._record(
                    "liquidations.jsonl",
                    seq,
                    {
                        "instrument_id": position.instrument_id,
                        "status": "UNRESOLVED",
                        "reason": str(error),
                    },
                )
                continue
            self._record(
                "liquidations.jsonl",
                seq,
                {
                    "classification": "SIMULATED_EXECUTION",
                    "event_id": event_id,
                    "quote": quote,
                    "status": "PARTIAL_EXIT" if quote.unfilled.value else "EXITED",
                },
            )

    def _clock_safety(
        self,
        views: dict[MarketId, CanonicalMarketView],
        now: UtcTimestamp,
    ) -> tuple[str, ...]:
        """Cheap clock-only invalidation even when an event changes no book."""
        if not self.simulator.active_orders():
            return ()
        reasons: list[str] = []
        for market_id in {order.intent.market_id for order in self.simulator.active_orders()}:
            view = views.get(market_id)
            evidence = self._market_evidence.get(market_id)
            if view is None or evidence is None:
                reasons.append("ACTIVE_MARKET_EVIDENCE_UNAVAILABLE")
                continue
            reasons.extend(evidence.risk_evidence.valid_at(now))
            for reference in (
                view.bundle.market,
                view.bundle.precision,
                view.bundle.cost_rule_set,
                *view.bundle.instruments,
            ):
                times = reference.times
                if (
                    times.observed_at > now
                    or times.effective_from is None
                    or times.effective_from > now
                    or (times.effective_to is not None and now >= times.effective_to)
                    or (times.superseded_at is not None and now >= times.superseded_at)
                ):
                    reasons.append("CANONICAL_REFERENCE_NOT_CURRENT")
                if (
                    now.nanoseconds_since_epoch - times.observed_at.nanoseconds_since_epoch
                    > self.config.strategy.maximum_reference_age_ns
                ):
                    reasons.append("STALE_CANONICAL_REFERENCE")
            economic_references = [evidence.risk_evidence]
            economic_references.extend(
                rules.evidence
                for rules in (evidence.fee_rules, evidence.reward_rules)
                if rules is not None
            )
            if any(
                now.nanoseconds_since_epoch - reference.times.observed_at.nanoseconds_since_epoch
                > self.config.strategy.maximum_reference_age_ns
                for reference in economic_references
            ):
                reasons.append("STALE_ECONOMIC_REFERENCE")
            if evidence.fee_rules is None:
                reasons.append("ACTIVE_FEE_REFERENCE_UNAVAILABLE")
            else:
                reasons.extend(
                    canonical_fee_reasons(
                        view.bundle,
                        evidence.fee_rules,
                        now,
                        self.config.strategy.maximum_reference_age_ns,
                    )
                )
            if evidence.reward_rules is None:
                reasons.append("ACTIVE_REWARD_REFERENCE_UNAVAILABLE")
            else:
                reasons.extend(evidence.reward_rules.eligibility_reasons(now))
            calibration = self._calibrations.get(market_id)
            if calibration is None:
                reasons.append("ACTIVE_CALIBRATION_UNAVAILABLE")
            else:
                reasons.extend(calibration.eligibility_reasons(at=now, market_id=market_id))
            resolution = view.bundle.market.end_time
            if (
                resolution is None
                or resolution.nanoseconds_since_epoch - now.nanoseconds_since_epoch
                < self.config.risk.minimum_time_to_resolution_ns
            ):
                reasons.append("RESOLUTION_TIME_RISK")
            if view.audit_ineligible:
                reasons.append("CANONICAL_AUDIT_INELIGIBLE")
            if any(
                not book.is_fresh(now, max_age_ns=self.config.risk.maximum_market_data_age_ns)
                for book in view.books
            ):
                reasons.append("STALE_OR_UNHEALTHY_BOOK")
        return tuple(sorted(set(reasons)))

    def _fee(
        self, evidence: MarketEvidence, now: UtcTimestamp, price: Price, size: Quantity
    ) -> Money:
        rules = evidence.fee_rules
        if rules is None:
            raise ValueError("liquidation fee reference unavailable")
        estimate = execution_fee(
            price, size, LiquidityRole.TAKER, rules, now, self.ledger.collateral_asset_id
        )
        if estimate.amount is None:
            raise ValueError("liquidation fee reference unavailable: " + ";".join(estimate.reasons))
        return estimate.amount

    def valuation(
        self, views: dict[MarketId, CanonicalMarketView], now: UtcTimestamp
    ) -> dict[str, Money]:
        books = {book.instrument_id: book for view in views.values() for book in view.books}
        quotes: dict[InstrumentId, LiquidationQuote] = {}
        marks: dict[InstrumentId, Price] = {}
        for position in self.ledger.positions():
            book = books.get(position.instrument_id)
            evidence = self._market_evidence.get(position.market_id)
            if book is None or not book.is_fresh(
                now, max_age_ns=self.config.risk.maximum_market_data_age_ns
            ):
                continue
            if book.best_bid is not None and book.best_ask is not None:
                marks[position.instrument_id] = Price(
                    (book.best_bid.price.value + book.best_ask.price.value) / ExactDecimal(2)
                )
            if evidence is not None and evidence.fee_rules is not None:
                with suppress(ValueError):
                    if canonical_fee_reasons(
                        views[position.market_id].bundle,
                        evidence.fee_rules,
                        now,
                        self.config.strategy.maximum_reference_age_ns,
                    ):
                        continue
                    quotes[position.instrument_id] = self.ledger.quote_liquidation(
                        book,
                        position.quantity,
                        now=now,
                        maximum_book_age_ns=self.config.risk.maximum_market_data_age_ns,
                        fee_evaluator=lambda price, size, e=evidence: self._fee(
                            e, now, price, size
                        ),
                        slippage_fraction=self.config.exit_slippage_fraction,
                        impact_fraction=self.config.exit_impact_fraction,
                    )
        return {
            "executable_wealth": self.ledger.executable_valuation(quotes).net_wealth,
            "mark_to_market_wealth": self.ledger.mark_to_market(marks).net_wealth,
        }

    def report(
        self, views: dict[MarketId, CanonicalMarketView], now: UtcTimestamp
    ) -> dict[str, object]:
        if self._pressure_boundaries_consumed != self._pressure_by_seq.keys():
            raise ValueError("unconsumed pressure evidence outside the source ingest boundaries")
        snapshot = self.ledger.snapshot()
        valuations = self.valuation(views, now)
        total = sum(self.decisions.values())
        return {
            "scenario": self.scenario,
            "decisions": dict(sorted(self.decisions.items())),
            "no_quote_rate": ExactDecimal(self.decisions["NO_QUOTE"]).divide(
                ExactDecimal(max(1, total)), scale=18, rounding=RoundingMode.HALF_EVEN
            ),
            "abstention_reasons": dict(sorted(self.abstentions.items())),
            "fail_closed_events": dict(sorted(self.fail_closed.items())),
            "kill_switch": self.risk.halted_reason,
            "orders": self.simulator.orders(),
            "order_event_count": len(self.simulator.events()),
            "simulated_fill_count": sum(
                event.kind.value == "FILL" for event in self.simulator.events()
            ),
            "ledger": snapshot,
            "valuation": valuations,
            "net_liquidation_pnl": Money(
                valuations["executable_wealth"].value - snapshot.starting_cash.value
            ),
            "unresolved_inventory": snapshot.positions,
            "outstanding_orders": tuple(
                order for order in self.simulator.orders() if not order.terminal
            ),
            "state_chain_sha256": self._state_chain,
            "incentive_scenarios": {
                "optimistic": None,
                "baseline": None,
                "conservative": Money(ZERO),
            },
            "verified_payments": None,
        }
