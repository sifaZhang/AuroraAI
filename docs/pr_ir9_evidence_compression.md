# PR-IR9 Research Evidence Compression

PR-IR9 adds a deterministic, FACT-only compression layer between the evidence pack and the research agents. Original files and `evidence.json` remain authoritative and are never deleted or replaced.

## Artifact and validation

Each full research version may contain `evidence_summary.json`. It records the symbol and analysis version, deterministic method, generation time, compression metrics, nine fixed sections, and an index for every source evidence ID. Each compressed item retains its existing evidence ID, source type, period, source file, priority, source excerpts, and page or snapshot anchor. The `key_numbers` shape is fixed; unavailable values remain `null`.

Validation rejects unknown evidence IDs, missing index entries, mismatched symbol/version/section/counts, invented schema fields, and budget overflow. Compression text is copied from source units; the layer has no inference, assumption, valuation, or recommendation field.

## Deterministic selection

The compressor extracts complete paragraphs or lines, normalizes whitespace, classifies them by keywords, and removes exact duplicates. It ranks current financial reporting, accounting corrections, cash flow, dividends, financial performance, risk and governance evidence ahead of generic business narrative. Recent reports receive a recency boost. Board-assurance boilerplate is excluded, while accounting corrections and dividend/cash-flow evidence remain high priority. Selection drops complete low-priority facts when the configured `RESEARCH_CONTEXT_MAX_CHARS` budget is exhausted; it never slices a sentence by character offset.

The command is explicit and single-version only:

```text
python -m backend.research.evidence_compressor --symbol 000858.SZ --version 2026-08-23_v3
```

It does not scan or backfill historical versions.

## Role-specific context

The Analyst reads all relevant compressed sections. The Skeptic emphasizes accounting, cash flow, risks, balance sheet, dividend and governance evidence, with evidence referenced by Analyst claims selected first. Valuation receives only financial performance, cash-flow and dividend evidence. Budgets are independently configurable with `RESEARCH_ANALYST_CONTEXT_MAX_CHARS`, `RESEARCH_SKEPTIC_CONTEXT_MAX_CHARS`, and `RESEARCH_VALUATION_CONTEXT_MAX_CHARS`; defaults are 30,000, 24,000 and 16,000 characters.

Facts are included as complete source-tagged blocks. The generated Agent metadata records `context_mode` and `context_characters`. A missing or invalid summary falls back to the pre-IR9 raw context path and records `raw_fallback`.

## Orchestration

`FULL_REFRESH` now runs source collection, input-pack creation, compression, Analyst, Skeptic, valuation and Portfolio Manager in order. Compression is local and deterministic, so the estimated LLM count remains four. `VALUATION_REFRESH` and `DECISION_ONLY` copy a prior summary into the new analysis version and record its source version in `run.json`; if an older version has no summary, downstream Agents use raw fallback. `NO_ACTION` creates nothing.

No Portfolio Manager rule, Yield Signal, prompt, database schema, UI, source file, or external provider behavior changes in this PR.
