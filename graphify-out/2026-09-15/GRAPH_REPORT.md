# Graph Report - shark-trader  (2026-09-15)

## Corpus Check
- 17 files · ~6,035 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 2 file(s) not represented in the graph (top: .example 1, (none) 1)

## Summary
- 122 nodes · 224 edges · 8 communities (7 shown, 1 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 12 edges (avg confidence: 0.95)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Position
- live_engine.py
- main.py
- backtest_engine.py
- CLAUDE.md — shark-trader (Shark Exchange Algo Trading Bot)
- Strategy
- shark-trader
- RiskManager

## God Nodes (most connected - your core abstractions)
1. `Strategy` - 16 edges
2. `ExchangeClient` - 14 edges
3. `Position` - 12 edges
4. `SmaCrossoverStrategy` - 12 edges
5. `run()` - 11 edges
6. `RiskManager` - 10 edges
7. `EntryPlan` - 10 edges
8. `MeanReversionStrategy` - 10 edges
9. `OrderExecutor` - 9 edges
10. `PerformanceTracker` - 9 edges

## Surprising Connections (you probably didn't know these)
- `cmd_backtest()` --calls--> `plot_equity_curve()`  [EXTRACTED]
  main.py → shark/backtest_engine.py
- `cmd_backtest()` --calls--> `ExchangeClient`  [EXTRACTED]
  main.py → shark/exchange.py
- `cmd_run()` --calls--> `PerformanceTracker`  [EXTRACTED]
  main.py → shark/performance.py
- `cmd_run()` --calls--> `RiskManager`  [EXTRACTED]
  main.py → shark/risk.py
- `build_strategy()` --calls--> `strategy_params()`  [EXTRACTED]
  main.py → shark/config.py

## Import Cycles
- None detected.

## Communities (8 total, 1 thin omitted)

### Community 0 - "Position"
Cohesion: 0.13
Nodes (12): atr(), bollinger_bands(), Pure indicator functions shared by every strategy and the backtester., rsi(), sma(), EntryPlan, Position, Strategy interface. Each strategy owns only its signal logic - the engines… (+4 more)

### Community 1 - "live_engine.py"
Cohesion: 0.13
Nodes (15): cmd_run(), ExchangeClient, Shark Exchange API client - klines (public) and HMAC-signed order endpoints.…, Public endpoint, no auth. Returns candles oldest-first as dicts with…, _fee_gate_passes(), Strategy-agnostic live/dry-run poll loop., run(), effective_fee_pct() (+7 more)

### Community 2 - "main.py"
Cohesion: 0.18
Nodes (8): build_strategy(), main(), Shark Exchange trading bot - single entry point for every strategy. Usage:…, load_config(), Loads config.json and layers in API credentials from the environment. API keys…, strategy_params(), PerformanceTracker, Session performance tracking - same report shape for dry-run or live, for every…

### Community 3 - "backtest_engine.py"
Cohesion: 0.23
Nodes (11): cmd_backtest(), _fee_gate_passes(), max_drawdown(), plot_equity_curve(), print_report(), Strategy-agnostic backtest engine. Simulates limit orders always filling at the…, Generalizes the original per-strategy fee-gate check: is the planned take-…, run_backtest() (+3 more)

### Community 4 - "CLAUDE.md — shark-trader (Shark Exchange Algo Trading Bot)"
Cohesion: 0.18
Nodes (10): API contract — confirmed against the live endpoint (2026-09-15), CLAUDE.md — shark-trader (Shark Exchange Algo Trading Bot), Design decisions worth preserving, Safety/scope notes, Shark Exchange — key facts (verify before relying on these), ⚠️ Still unverified — check before going live, Timeframe tuning (2026-09-15), User's goals (established across the conversation that built this) (+2 more)

### Community 5 - "Strategy"
Cohesion: 0.18
Nodes (4): candles is the full history seen so far (oldest first). Returns None if there…, Returns (exit_reason, exit_price, exit_is_maker) or None., Called once per loop iteration after entry/exit decisions are made, so a…, Strategy

### Community 6 - "shark-trader"
Cohesion: 0.22
Nodes (8): 1. Backtest first — validate before running anything live, 2. Run live/dry-run, Adding a new strategy, Before running anything live, Editing settings, Setup, shark-trader, The two strategies

## Knowledge Gaps
- **15 isolated node(s):** `Why this shape`, `User's goals (established across the conversation that built this)`, `Shark Exchange — key facts (verify before relying on these)`, `API contract — confirmed against the live endpoint (2026-09-15)`, `⚠️ Still unverified — check before going live` (+10 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 50 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Strategy` connect `Strategy` to `Position`, `live_engine.py`, `backtest_engine.py`?**
  _High betweenness centrality (0.192) - this node is a cross-community bridge._
- **Why does `Position` connect `Position` to `live_engine.py`, `backtest_engine.py`, `Strategy`?**
  _High betweenness centrality (0.111) - this node is a cross-community bridge._
- **Why does `ExchangeClient` connect `live_engine.py` to `main.py`, `backtest_engine.py`?**
  _High betweenness centrality (0.090) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `Strategy` (e.g. with `run_backtest()` and `run()`) actually correct?**
  _`Strategy` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `ExchangeClient` (e.g. with `run()` and `OrderExecutor`) actually correct?**
  _`ExchangeClient` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `Position` (e.g. with `MeanReversionStrategy` and `SmaCrossoverStrategy`) actually correct?**
  _`Position` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `SmaCrossoverStrategy` (e.g. with `EntryPlan` and `Position`) actually correct?**
  _`SmaCrossoverStrategy` has 2 INFERRED edges - model-reasoned connections that need verification._