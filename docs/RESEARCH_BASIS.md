# Research basis

Sources were checked on 2026-11-16. Findings below describe their authors'
evidence or models; implementation consequences are our design choices. No
external performance result establishes profitability of this project.

## Academic evidence and theory

| Reference and verified status | Finding and consequence |
| --- | --- |
| Pat Akey, Vincent Grégoire, Nicolas Harvie and Charles Martineau, *Who Wins and Who Loses in Prediction Markets? Evidence from Polymarket* (2026). [CEPR Discussion Paper 21615](https://cepr.org/publications/dp21615), 12 June; [SSRN 6443103](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6443103), revised 18 October. Working paper, not an identified journal publication. | Empirical gains are concentrated and successful accounts often supply liquidity. Modest persistence may reflect selection. Consequence: liquidity provision is a research direction, not a universal edge; evaluate a selectable abstention action and event-level concentration. |
| Hsiang-Chieh (Alex) Yang, *Skilled Liquidity Provision in Prediction Markets: Evidence from 150 Million Trades* (2026). [SSRN 6396698](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6396698); March version record and a later April version are listed. Working paper. | Historical skill classification distinguishes maker and taker performance; maker status alone does not explain profits. Consequence: use chronological information and test adverse-selection/selection hypotheses. Its historical zero-fee sample does not describe today's fee rules. |
| Nikhil Jain, *Optimal Market Making in Prediction Markets: Empirical Distribution Learning, Analytical Policy Characterization, and Reinforcement Learning* (2026). [Harvard bachelor's thesis, repository record dated 2 June](https://dash.harvard.edu/handle/1/42743947). | Studies queue-clearing opportunity against adverse-selection hazards in in-game NBA markets on Kalshi. Analytical thresholds motivate selective quoting and deeper placement under risk. Consequence: begin with calibrated simple statistics; the venue-specific fitted processes and reinforcement learner are outside our implementation scope. |
| Marcos Cardozo and José Ignacio Rivero-Wildemauwe, *The Favorite–Longshot Bias in Prediction Markets: Evidence from Polymarket* (2026). [arXiv:2609.12878v1](https://arxiv.org/abs/2609.12878v1), 11 September; preprint. | Measured tail returns change with contract/event aggregation and category. Consequence: evaluate independent parent-event groups, preserve weighting choices and avoid treating a pooled bias as a universal executable edge. |
| Siyang Liu, *Wisdom of the Crowd or Wisdom of the Insider? Insider Trading on Prediction Markets* (2026). [SSRN 6678718](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6678718), 29 April; working paper. | Reports greater market-maker losses in markets flagged by its insider-activity methodology. The classification is an empirical inference, not legal proof. Consequence: require adverse-selection stress scenarios and catalyst exclusions; do not equate public midpoint with fundamental value. |
| Pat Akey, Vincent Grégoire, Nicolas Harvie and Charles Martineau, *Trades, Quotes, and Price Discovery in Prediction Markets* (2026). [SSRN 7547106](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7547106), 1 October; working paper. | Quote updates contribute price information even in intervals without trades; last-trade accuracy deteriorates with age. Consequence: preserve quote dynamics and freshness, and do not build fair value from stale last trades alone. |
| Philipp D. Dubach, *The Anatomy of a Decentralized Prediction Market: Microstructure Evidence from the Polymarket Order Book* (2026). [arXiv:2604.24366v2](https://arxiv.org/abs/2604.24366v2), 14 May version record; preprint. | Public-feed inferred trade direction disagrees materially with on-chain ground truth. Version 2 removes a residual time-to-close interpretation after controls. Consequence: retain raw events and direction uncertainty; use independently reconciled trades for direction-dependent markouts. Do not carry forward the superseded version-1 inference. |
| Dominik Feil and Max Nendel, *Optimal Market Making in Prediction Markets* (2026). [arXiv:2607.17991v1](https://arxiv.org/abs/2607.17991v1), 20 July; preprint. | A stochastic-control model separates mark-to-market inventory risk and binary settlement risk. Its optimality depends on its specified diffusion, intensities and preferences. Consequence: account for inventory, time to resolution and terminal exposure; no transfer of its model-optimal policy or numerical gains is assumed. |
| Corey Garriott, Vincent van Kervel and Marius Zoican, *Queuing and inventories in limit order markets* (2025). [Journal of Financial Markets 75, 100982](https://doi.org/10.1016/j.finmar.2025.100982); published article. [SSRN working version 3991930](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3991930) is a separate version. | Queue priority interacts with inventory, adverse selection and subsequent displayed depth; theory is accompanied by Canadian futures evidence. Consequence: model queue position and inventory jointly; do not treat large displayed depth as proof of safe fills. Empirical magnitudes do not transfer to Polymarket. |
| Skander Lejmi, *Cooperative Multi-Agent Reinforcement Learning for Market Making on Prediction-Market Order Books* (2026). [arXiv:2610.26789v1](https://arxiv.org/abs/2610.26789v1), 19 October. The record describes an LSE MSc dissertation and an extended version of an accepted ICAIF 2026 workshop paper. | Reconstructed L2 orders and reward shaping complicate attribution; reported held-out configurations do not establish positive average portfolio value and use one training seed each. Consequence: preserve negative results, test fills and markouts independently, and keep reinforcement learning outside scope. The arXiv title is used rather than the differing HTML manuscript heading. |

The SSRN/Harvard/CEPR metadata and abstracts, arXiv version records and available
HTML methods were consulted. Several direct SSRN PDF/page fetches and the journal
publisher returned HTTP 403. This is a source-backed design synthesis, not a
claim of exhaustive full-text replication. Harvard's record identifies the
thesis and repository terms; Crossref confirms the Garriott article's authors,
journal, volume and identifier. No manuscript PDFs were downloaded, redistributed
or assigned invented checksums. A later empirical study must record the exact
accessed version, bytes/hash and rights for any locally retained full text.

## Engineering references

These are inspected reference projects, not accepted dependencies or profitability
evidence. Repository metadata reports MIT licensing for all six; no third-party
source code or datasets were copied. Any future reuse must check the actual
selected files, dependency/data rights and notices at a pinned commit and retain
required attribution. A README claim of working live execution does not validate
its accounting or simulation here.

| Reference and observed commit | Useful lesson and adoption boundary |
| --- | --- |
| [Polymarket/py-sdk](https://github.com/Polymarket/py-sdk/tree/6d854392c4acd2f947a701db9178ba9548118efb), `6d854392c4acd2f947a701db9178ba9548118efb` | Official unified public/reference client is the preferred future integration boundary. Validate decimal models and schemas against retained raw/replay contracts. Its signing/account capabilities are not imported during migration. |
| [outcometick/polymarket-backtest](https://github.com/outcometick/polymarket-backtest/tree/363b57256a7ec8fb7707a6ad8fc14e53dba6caa8), `363b57256a7ec8fb7707a6ad8fc14e53dba6caa8` | Event-by-event depth, explicit settlement evidence, costs and post-fill markouts are useful review targets. Validate maker queues, liquidity consumption and fee versions independently; do not import a historical outcome or headline return. |
| [vfeelv/polybot-rewards](https://github.com/vfeelv/polybot-rewards/tree/0ac7d1c146462b93723887284039c2eecf38af58), `0ac7d1c146462b93723887284039c2eecf38af58` | Depth-aware placement and explicit post-fill exit choices motivate queue and liquidation scenarios. Its symmetric quoting and live/paper switching do not define this project's selective action set or authorization boundary. |
| [warproxxx/poly-maker](https://github.com/warproxxx/poly-maker/tree/4f32103591c9582ccd012bdf10f77d86e5879444), `4f32103591c9582ccd012bdf10f77d86e5879444` | A pure quote function and explicit target-order reconciliation support a modular monolith. Study inventory skew, churn tolerances and stale-data halts; do not copy live signing, broad market support or its unverified status claims. |
| [joyce-xy-xu/polymarket-lp-research](https://github.com/joyce-xy-xu/polymarket-lp-research/tree/c833f31ab269f524f772eddeef0247457e76196c), `c833f31ab269f524f772eddeef0247457e76196c` | Public retractions identify configuration paths that were read but unused, inconsistent midpoint models, look-ahead joins and summing mutually exclusive strategies. Event clustering and multiple-comparison checks weakened a positive exploratory result. Consequence: test intermediate values, freeze selection data and keep executable portfolio accounting separate from parameter sweeps. Official sources govern payout rules. |
| [nkaz001/hftbacktest](https://github.com/nkaz001/hftbacktest/tree/5f3ec40b2afb764e0fea112f941ed85523ef4e88), `5f3ec40b2afb764e0fea112f941ed85523ef4e88`; [fill-model documentation](https://github.com/nkaz001/hftbacktest/blob/5f3ec40b2afb764e0fea112f941ed85523ef4e88/docs/order_fill.rst) | Queue uncertainty, latency and partial-fill models need declared assumptions. Its replay documentation explicitly limits counterfactual market impact. Use those limitations in scenario design; no Rust/Numba runtime or venue adapters are required here. |

## Deferred official SDK integration

[Release 0.20.0](https://github.com/Polymarket/py-sdk/releases/tag/polymarket-client-v0.20.0)
was published on 2026-11-16. Asset-filtered open-order queries now require a market
condition ID. The deprecated `associate_trades` may be absent/empty even when
fills occurred: cumulative `size_matched` or account trades matched by order ID
are the documented alternatives. These are future lifecycle requirements, not
evidence of an implemented account adapter.

[SDK dependency metadata](https://github.com/Polymarket/py-sdk/blob/6d854392c4acd2f947a701db9178ba9548118efb/pyproject.toml)
declares `websockets>=13,<16`, incompatible with this foundation's
`websockets==17.0.1`. Optional frame extras require `pyarrow>=15,<22`, conflicting
with retained `pyarrow==25.0.1`; core SDK use does not require those extras.
Python metadata allows `>=3.11` but advertised classifiers stop at 3.13, so the
project's exact 3.14.7 runtime still needs executed compatibility qualification.
The SDK is deliberately absent from the migration lock.

Prompt 2 must select a reviewed SDK version, resolve its full transitive graph,
decide whether to adapt/test the retained collector on a compatible WebSocket
release or wait for a compatible SDK, and test schemas, decimal conversion,
retry/reconnect, order/fill decoding and replay parity. Do not relax pins silently
or enable optional frame extras without need. Any account-interface specification
must preserve the release's filtering/fill rules; authenticated order placement
remains separately blocked. Public/reference integration is sufficient for the
authorized paper implementation.

## Proposed method and evidence limits

The wallet-independent instantaneous reward-share bound and bounded adaptive
YES/NO candidate evaluation in [STRATEGY_SPEC.md](STRATEGY_SPEC.md) are our proposed
methods. Their algebra does not establish that public observations reconstruct
official eligible scores, that sampled rewards equal payment, or that expected
rewards exceed execution loss. Independent tests must distinguish those claims.

Greece deployment eligibility remains unresolved. The [HGC blacklist page](https://hgc.gov.gr/κοινό-και-παίκτες/blacklist/)
was indexed as revised edition 54, decision 35393/30.10.2026, but direct retrieval
returned HTTP 403 and the current `polymarket.com` entry was not verified. The
[HGC official notice](https://hgc.gov.gr/en/blacklist-forbidden/) describes blocking
and sanctions for unlicensed gambling generally. This access limitation supplies
no eligibility conclusion. A venue geoblock response cannot replace applicable
local-law assessment; live capability requires explicit authorization, verified
eligibility and separate acceptance. Research/observation remain limited to
permitted activity.
