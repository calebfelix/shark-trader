# Graph Report - shark-trader  (2026-09-16)

## Corpus Check
- 17 files · ~17,805 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 340 nodes · 548 edges · 21 communities (12 shown, 7 thin omitted)
- Extraction: 94% EXTRACTED · 6% INFERRED · 0% AMBIGUOUS · INFERRED: 31 edges (avg confidence: 0.9)
- Token cost: 84,229 input · 0 output

## Community Hubs (Navigation)
- Strategy Base & Long-Only Strategies
- Backtest Engine & Indicator Tests
- Core Trading Engine & Live API Verification
- PSAR Trend Strategy & Strategy Tests
- Exchange Signing & Order Execution
- Leverage-Aware Risk Sizing
- Parabolic SAR Calculator & Walk-Forward Validation
- Fee Gate Tests
- Leveraged Position Sizing Tests
- Project Documentation Overview
- README Usage Guide
- Fee Gate & Dev Dependencies
- Risk Manager Tests
- Performance Tracking
- README Strategy Descriptions
- Parabolic SAR Calculator Tests
- Klines API Contract
- Equity Curve Charting
- HTTP Requests Dependency

## God Nodes (most connected - your core abstractions)
1. `Position` - 35 edges
2. `EntryPlan` - 22 edges
3. `Strategy` - 22 edges
4. `ExchangeClient` - 20 edges
5. `_AlwaysLongOneCandleStrategy` - 14 edges
6. `SmaCrossoverStrategy` - 11 edges
7. `CLAUDE.md — shark-trader (Shark Exchange Algo Trading Bot)` - 11 edges
8. `make_candles()` - 11 edges
9. `TestMeanReversionStrategy` - 11 edges
10. `PSARTrendStrategy` - 10 edges

## Surprising Connections (you probably didn't know these)
- `pytest (dev dependency)` --semantically_similar_to--> `RiskManager`  [INFERRED] [semantically similar]
  requirements-dev.txt → shark/risk.py
- `pytest (dev dependency)` --semantically_similar_to--> `fee_gate_passes()`  [INFERRED] [semantically similar]
  requirements-dev.txt → shark/orders.py
- `marginAsset Always INR` --rationale_for--> `OrderExecutor`  [EXTRACTED]
  CLAUDE.md → shark/orders.py
- `Fee-vs-Edge Gate (MIN_PROFIT_TO_FEE_RATIO)` --references--> `fee_gate_passes()`  [EXTRACTED]
  CLAUDE.md → shark/orders.py
- `psar_trend Strategy` --references--> `_ParabolicSAR`  [EXTRACTED]
  CLAUDE.md → shark/strategies/psar_trend.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Strategies implementing the Strategy interface** — shark_strategies_base_strategy, shark_strategies_mean_reversion, shark_strategies_sma, shark_strategies_scalp, shark_strategies_psar_trend [EXTRACTED 1.00]
- **Order placement verification bug-fix flow** — claude_signature_mismatch_bug, claude_js_number_bug, claude_limit_price_var_allowed, verify_order_endpoint [INFERRED 0.85]
- **Firm strategies evaluated and rejected** — claude_btst_orb_strategy, claude_dma_crossover_strategy, claude_hedged_short_strangle_strategy [EXTRACTED 1.00]

## Communities (21 total, 7 thin omitted)

### Community 0 - "Strategy Base & Long-Only Strategies"
Cohesion: 0.07
Nodes (19): DMA Crossover Strategy (rejected), scalp Strategy, sma Strategy, atr(), bollinger_bands(), Pure indicator functions shared by every strategy and the backtester., rsi(), sma() (+11 more)

### Community 1 - "Backtest Engine & Indicator Tests"
Cohesion: 0.05
Nodes (15): fixture, base_config(), make_candles(), Shared fixtures. No test here ever hits the live Shark Exchange API -…, Minimal config.json-shaped dict, enough for backtest_engine.run_backtest., Builds a minimal oldest-first candle list from a list of closes. high/low are…, _AlwaysLongOneCandleStrategy, Test double: enters LONG on the first candle it sees, exits on the very next… (+7 more)

### Community 2 - "Core Trading Engine & Live API Verification"
Cohesion: 0.07
Nodes (26): JS Number Serialization Bug, Signature Mismatch Bug (JSON re-serialization), One-off diagnostic for the "Cross margin is not enabled" (error 2314) issue on…, ExchangeClient, build_strategy(), cmd_backtest(), cmd_run(), main() (+18 more)

### Community 3 - "PSAR Trend Strategy & Strategy Tests"
Cohesion: 0.10
Nodes (8): Strategy-agnostic live/dry-run poll loop., Position, Strategy interface. Each strategy owns only its signal logic - the engines…, Parabolic SAR trend-following, long+short. Adapted for shark-trader from a…, TestMeanReversionStrategy, TestPSARTrendStrategy, TestScalpStrategy, TestSmaCrossoverStrategy

### Community 4 - "Exchange Signing & Order Execution"
Cohesion: 0.10
Nodes (13): marginAsset Always INR, Response, ExchangeClient, Public endpoint, no auth. Returns the trading constraints for one contract:…, Most recent close for symbol, via a 1-candle klines call., Read-only. position_status is one of OPEN/CLOSED/LIQUIDATED., Sets leverage/margin mode for a symbol - this is an account-wide per-symbol…, The docs' own signing example is JS's JSON.stringify(params) - this backend is… (+5 more)

### Community 5 - "Leverage-Aware Risk Sizing"
Cohesion: 0.14
Nodes (11): Hedged Short Strangle Strategy (rejected), Leverage-Aware Position Sizing, limitPriceVarAllowed Constraint, minNotional Small-Account Conflict, Shark Exchange, get_exchange_info(), calc_leveraged_position_size(), Risk management: position sizing, daily loss halt, consecutive-loss cooldown.… (+3 more)

### Community 6 - "Parabolic SAR Calculator & Walk-Forward Validation"
Cohesion: 0.12
Nodes (9): BTST/ORB-style Breakout Strategy (rejected), mean_reversion Strategy, psar_trend Strategy, walkforward_psar.py, Walk-Forward Validation Methodology, walkforward_xauinr.py, _ParabolicSAR, PSARTrendStrategy (+1 more)

### Community 7 - "Fee Gate Tests"
Cohesion: 0.19
Nodes (3): TestEffectiveFeePct, TestFeeGatePasses, TestRoundTripFeeEstimate

### Community 9 - "Project Documentation Overview"
Cohesion: 0.15
Nodes (12): API contract — confirmed against the live endpoint (2026-09-15), CLAUDE.md — shark-trader (Shark Exchange Algo Trading Bot), Design decisions worth preserving, Long + short support and the scalp strategy (2026-09-15), marginAsset is always "INR", even on non-INR pairs (2026-09-15), Safety/scope notes, Shark Exchange — key facts (verify before relying on these), ⚠️ Still unverified — check before going live (+4 more)

### Community 10 - "README Usage Guide"
Cohesion: 0.17
Nodes (11): 1. Backtest first — validate before running anything live, 2. Run live/dry-run, Adding a new strategy, Before running anything live, config.json, Editing settings, main.py entry point, Setup (+3 more)

### Community 11 - "Fee Gate & Dev Dependencies"
Cohesion: 0.20
Nodes (10): Fee-vs-Edge Gate (MIN_PROFIT_TO_FEE_RATIO), pytest (dev dependency), requirements.txt, effective_fee_pct(), fee_gate_passes(), Fee-aware order placement, shared by every strategy's live engine run. DRY_RUN…, Shark Exchange applies GST on top of the headline fee rate., Returns estimated round-trip fee cost as a % of notional. Fee args should… (+2 more)

### Community 14 - "README Strategy Descriptions"
Cohesion: 0.50
Nodes (4): mean_reversion strategy (README), scalp strategy (README), sma strategy (README), Strategy interface (README, shark/strategies/base.py)

## Knowledge Gaps
- **29 isolated node(s):** `API contract — confirmed against the live endpoint (2026-09-15)`, `Design decisions worth preserving`, `Long + short support and the scalp strategy (2026-09-15)`, `marginAsset is always "INR", even on non-INR pairs (2026-09-15)`, `Safety/scope notes` (+24 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 145 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Position` connect `PSAR Trend Strategy & Strategy Tests` to `Strategy Base & Long-Only Strategies`, `Backtest Engine & Indicator Tests`, `Core Trading Engine & Live API Verification`, `Parabolic SAR Calculator & Walk-Forward Validation`?**
  _High betweenness centrality (0.228) - this node is a cross-community bridge._
- **Why does `ExchangeClient` connect `Exchange Signing & Order Execution` to `Core Trading Engine & Live API Verification`, `Fee Gate & Dev Dependencies`?**
  _High betweenness centrality (0.116) - this node is a cross-community bridge._
- **Why does `Strategy` connect `Strategy Base & Long-Only Strategies` to `Backtest Engine & Indicator Tests`, `Core Trading Engine & Live API Verification`, `PSAR Trend Strategy & Strategy Tests`, `Parabolic SAR Calculator & Walk-Forward Validation`?**
  _High betweenness centrality (0.109) - this node is a cross-community bridge._
- **Are the 9 inferred relationships involving `Position` (e.g. with `MeanReversionStrategy` and `PSARTrendStrategy`) actually correct?**
  _`Position` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `EntryPlan` (e.g. with `MeanReversionStrategy` and `PSARTrendStrategy`) actually correct?**
  _`EntryPlan` has 6 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `Strategy` (e.g. with `run_backtest()` and `run()`) actually correct?**
  _`Strategy` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `_AlwaysLongOneCandleStrategy` (e.g. with `EntryPlan` and `Position`) actually correct?**
  _`_AlwaysLongOneCandleStrategy` has 2 INFERRED edges - model-reasoned connections that need verification._