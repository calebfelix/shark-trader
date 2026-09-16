# Graph Report - shark-trader  (2026-09-16)

## Corpus Check
- 22 files · ~14,378 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 201 nodes · 364 edges · 17 communities (10 shown, 7 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 20 edges (avg confidence: 0.93)
- Token cost: 84,790 input · 0 output

## Community Hubs (Navigation)
- Live API Bug Fixes
- Exchange Client & Risk Sizing
- ExchangeClient API Methods
- CLAUDE.md Project Notes
- README Usage Guide
- Mean-Reversion Design Rationale
- Shared Indicators
- Strategy Interface Contract
- SMA Crossover Strategy
- Leverage-Aware Risk Sizing
- PSAR Trend Strategy
- Mean-Reversion Entry/Exit Logic
- Scalp Strategy Logic
- Parabolic SAR Calculator
- Shark Exchange Fee Promo
- Equity Curve Charting
- Strategy Base Module

## God Nodes (most connected - your core abstractions)
1. `ExchangeClient` - 26 edges
2. `Strategy` - 20 edges
3. `Position` - 18 edges
4. `EntryPlan` - 15 edges
5. `shark-trader` - 12 edges
6. `SmaCrossoverStrategy` - 12 edges
7. `CLAUDE.md — shark-trader (Shark Exchange Algo Trading Bot)` - 11 edges
8. `run()` - 11 edges
9. `OrderExecutor` - 11 edges
10. `PSARTrendStrategy` - 11 edges

## Surprising Connections (you probably didn't know these)
- `mean_reversion strategy (README)` --semantically_similar_to--> `mean_reversion strategy`  [INFERRED] [semantically similar]
  README.md → CLAUDE.md
- `sma strategy (README)` --semantically_similar_to--> `sma strategy`  [INFERRED] [semantically similar]
  README.md → CLAUDE.md
- `shark-trader` --references--> `/v1/order/place-order request schema (doc-verified, live-verified 2026-09-15)`  [EXTRACTED]
  README.md → CLAUDE.md
- `shark-trader` --references--> `shark-trader project`  [EXTRACTED]
  README.md → CLAUDE.md
- `cmd_backtest()` --calls--> `plot_equity_curve()`  [EXTRACTED]
  main.py → shark/backtest_engine.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Strategies implementing the Strategy interface** — claude_mean_reversion, claude_sma, claude_scalp, claude_strategy_interface [EXTRACTED 1.00]
- **Live order-placement verification debugging flow** — claude_signature_byte_mismatch_bug, claude_js_number_formatting_bug, claude_limitpricevarallowed, verify_order_endpoint [EXTRACTED 1.00]
- **Shared execution/risk/reporting engine components used by every strategy** — claude_riskmanager, claude_orderexecutor, claude_performancetracker, shark_backtest_engine, shark_live_engine [EXTRACTED 1.00]

## Communities (17 total, 7 thin omitted)

### Community 0 - "Live API Bug Fixes"
Cohesion: 0.09
Nodes (21): JS whole-number float serialization bug, limitPriceVarAllowed constraint, Signature byte-mismatch bug (json vs requests serialization), One-off diagnostic for the "Cross margin is not enabled" (error 2314) issue on…, build_strategy(), cmd_backtest(), cmd_run(), main() (+13 more)

### Community 1 - "Exchange Client & Risk Sizing"
Cohesion: 0.12
Nodes (26): ExchangeClient (shark/exchange.py), get_klines endTime pagination, Leverage-aware position sizing, marginAsset always INR design decision, minNotional / small-account leverage conflict, OrderExecutor (shark/orders.py), PerformanceTracker (shark/performance.py), RiskManager (shark/risk.py) (+18 more)

### Community 2 - "ExchangeClient API Methods"
Cohesion: 0.14
Nodes (9): Response, ExchangeClient, Public endpoint, no auth. Returns the trading constraints for one contract:…, Most recent close for symbol, via a 1-candle klines call., Read-only. position_status is one of OPEN/CLOSED/LIQUIDATED., Sets leverage/margin mode for a symbol - this is an account-wide per-symbol…, The docs' own signing example is JS's JSON.stringify(params) - this backend is…, requests' default raise_for_status() discards the response body, which is… (+1 more)

### Community 3 - "CLAUDE.md Project Notes"
Cohesion: 0.15
Nodes (12): API contract — confirmed against the live endpoint (2026-09-15), CLAUDE.md — shark-trader (Shark Exchange Algo Trading Bot), Design decisions worth preserving, Long + short support and the scalp strategy (2026-09-15), marginAsset is always "INR", even on non-INR pairs (2026-09-15), Safety/scope notes, Shark Exchange — key facts (verify before relying on these), ⚠️ Still unverified — check before going live (+4 more)

### Community 4 - "README Usage Guide"
Cohesion: 0.15
Nodes (12): /v1/order/place-order request schema (doc-verified, live-verified 2026-09-15), 1. Backtest first — validate before running anything live, 2. Run live/dry-run, Adding a new strategy, Before running anything live, config.json, Editing settings, main.py entry point (+4 more)

### Community 5 - "Mean-Reversion Design Rationale"
Cohesion: 0.23
Nodes (12): ATR-based trend filter, Fee-vs-edge gate (MIN_PROFIT_TO_FEE_RATIO), mean_reversion strategy, Mean-reversion retuning against longer window (2026-09-15), scalp strategy, sma strategy, Strategy interface (shark/strategies/base.py), User's stated goals (consistent returns, fee-awareness) (+4 more)

### Community 6 - "Shared Indicators"
Cohesion: 0.33
Nodes (6): atr(), bollinger_bands(), Pure indicator functions shared by every strategy and the backtester., rsi(), Mean reversion: Bollinger Bands + RSI entries, ATR trend filter, maker orders…, Scalp: fast/tight Bollinger Bands + RSI mean-reversion on 1m candles, long AND…

### Community 7 - "Strategy Interface Contract"
Cohesion: 0.20
Nodes (4): candles is the full history seen so far (oldest first). Returns None if there…, Returns (exit_reason, exit_price, exit_is_maker) or None., Called once per loop iteration after entry/exit decisions are made, so a…, Strategy

### Community 8 - "SMA Crossover Strategy"
Cohesion: 0.28
Nodes (3): sma(), SMA crossover (trend-following): fast/slow SMA cross, market orders only, no…, SmaCrossoverStrategy

### Community 9 - "Leverage-Aware Risk Sizing"
Cohesion: 0.28
Nodes (4): calc_leveraged_position_size(), Returns (qty, info) via calc_leveraged_position_size - see there for what info…, Min-notional/leverage-aware sizing for live trading. account_capital is always…, RiskManager

## Knowledge Gaps
- **24 isolated node(s):** `API contract — confirmed against the live endpoint (2026-09-15)`, `Design decisions worth preserving`, `Long + short support and the scalp strategy (2026-09-15)`, `marginAsset is always "INR", even on non-INR pairs (2026-09-15)`, `Safety/scope notes` (+19 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 89 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `shark-trader project` connect `Exchange Client & Risk Sizing` to `README Usage Guide`, `Mean-Reversion Design Rationale`?**
  _High betweenness centrality (0.246) - this node is a cross-community bridge._
- **Why does `ExchangeClient` connect `ExchangeClient API Methods` to `Live API Bug Fixes`, `Exchange Client & Risk Sizing`?**
  _High betweenness centrality (0.211) - this node is a cross-community bridge._
- **Why does `Strategy` connect `Strategy Interface Contract` to `Exchange Client & Risk Sizing`, `Shared Indicators`, `SMA Crossover Strategy`, `PSAR Trend Strategy`, `Mean-Reversion Entry/Exit Logic`, `Scalp Strategy Logic`, `Strategy Base Module`?**
  _High betweenness centrality (0.180) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `ExchangeClient` (e.g. with `run()` and `OrderExecutor`) actually correct?**
  _`ExchangeClient` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `Strategy` (e.g. with `run_backtest()` and `run()`) actually correct?**
  _`Strategy` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `Position` (e.g. with `MeanReversionStrategy` and `PSARTrendStrategy`) actually correct?**
  _`Position` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `EntryPlan` (e.g. with `MeanReversionStrategy` and `PSARTrendStrategy`) actually correct?**
  _`EntryPlan` has 4 INFERRED edges - model-reasoned connections that need verification._