# PR-IR10 Focused Research Trigger Policy

PR-IR10 limits scheduled deep research to enabled companies whose current Dividend Yield Signal is `add` or `heavy`. An `entry` or `watch` company without completed research is not automatically analyzed. Manual `full` remains available for any enabled symbol.

## Daily universe

The scheduled universe is the union of:

- enabled symbols currently at `add` or `heavy`; and
- a previously researched `add`/`heavy` symbol that is now `entry` or `watch` and has not completed confirmation for that inactive signal.

An exit confirmation runs Valuation and Portfolio Manager once (two estimated LLM calls). Successful completion records the inactive signal in `data/research/{symbol}/trigger_state.json`; the symbol then leaves daily discovery. A later move back to `add` or `heavy` re-enters the active pool. Re-entry uses valuation refresh when the last full research is at most 90 days old, otherwise full refresh.

Inactive, exit-confirmed symbols do not run scheduled stale checks or ordinary financial-report/announcement discovery. Their historical research remains readable. No database migration or bulk state backfill is used.

## Downside price ladder

`PRICE_MOVE` remains the compatible enum name, but its IR10 semantics are downside-only. The calculation is:

```text
drop_ratio = current_price / anchor_price - 1
trigger when drop_ratio <= -threshold
```

`RESEARCH_PRICE_DROP_THRESHOLD` takes precedence over the compatible `RESEARCH_PRICE_MOVE_THRESHOLD`; the default is 0.10. A rise alone never creates a price trigger. Yield Signal changes remain independent triggers even when caused by a price rise.

The anchor is loaded from `trigger_state.json`. For older research without that file, the latest completed research price is used in memory. A successful price-triggered valuation refresh advances the anchor. A successful full refresh, including manual full, resets it to the current research price. Failed Agent or Portfolio runs do not update it. Manual valuation does not reset an existing anchor.

For example, an anchor of 100 triggers at 90; after successful completion the anchor becomes 90, so 85 does not retrigger and 81 does. After success at 81, the next threshold is 72.9.

## Trigger state and audit

`trigger_state.json` is orchestration metadata, separate from Agent conclusions. Its strict schema records:

- current anchor price, date and analysis version;
- previous anchor, update reason and timestamp; and
- optional exit-confirmation signal, completion time and analysis version.

Writes use the existing atomic JSON store. `run.json.market_change` records the anchor, current price, change from anchor, negative trigger threshold, and previous/current Yield Signals.

Active `add`/`heavy` source, research-stale, valuation-stale and Yield Signal triggers retain the existing plan priorities and LLM estimates: no action 0, valuation refresh 2 and full refresh 4. The Agent chain, evidence compression, valuation, Portfolio Manager, Dividend thresholds, workflow schedule and database remain unchanged.
