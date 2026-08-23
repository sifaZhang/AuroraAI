# PR-IR7 Daily Research Automation

PR-IR7 extends `.github/workflows/dividend-daily-report.yml`; it does not add a second research workflow. The single daily job starts at UTC 12:00 (Beijing 20:00), runs the existing dividend report first, then research discovery/planning/execution, and finally commits eligible research artifacts.

## Preserved dividend behavior

- Scheduled runs send the existing dividend email and do not pass `--date`.
- Manual runs retain `send_email` and `trade_date`. A blank date is omitted; `send_email=false` remains a dividend dry-run.
- Dividend and research are ordered steps in one job, so a failed dividend step prevents research from starting and research reads the just-refreshed local database state.

## Research execution

The daily universe is the union of enabled entry/add/heavy candidates and enabled symbols with an existing `data/research/{symbol}` directory. This retains researched symbols that have moved back to watch without scanning the whole A-share market.

`source_discovery.py` reuses the IR2 CNINFO client and classification. Discovery lists report and relevant-announcement metadata only; it does not download PDFs. Announcement IDs and report period/type/announcement IDs are compared with the latest COMPLETED analysis snapshot. Failed or partial versions never consume a source. A discovery failure is reported as `DISCOVERY_FAILED`; missing current price or yield facts is `MARKET_DATA_MISSING`.

`daily_research_runner.py` is dry-run by default. `--execute` must be explicit. It supports `--symbol`, `--mode auto|full|valuation|decision`, and `--max-llm-calls`. Budget precedence is CLI, `RESEARCH_DAILY_MAX_LLM_CALLS`, then 20. The runner discovers and plans every symbol before any agent runs. If the aggregate estimate exceeds the budget, the run is `BUDGET_BLOCKED` and executes zero agents. Individual failures are isolated and yield a `PARTIAL` batch when other symbols succeed.

An executed run with work or a meaningful failure writes `data/research/runs/YYYY-MM-DD.json`. An all-NO_ACTION run writes only the GitHub step summary, avoiding empty daily Git commits.

## Workflow and Git safety

Manual workflow research defaults to enabled and dry-run. Scheduled research always passes `--execute`. The required secrets are `TUSHARE_TOKEN`, the existing dividend SMTP/email secrets, and `DEEPSEEK_API_KEY`. Optional repository variables select the four per-agent DeepSeek models; each defaults to `deepseek-v4-flash`.

Only `data/research/` is staged. A validator rejects any staged path outside that prefix. No-diff runs skip commit and push. Changes use `research: daily refresh YYYY-MM-DD` with the Beijing date, the standard GitHub Actions bot identity, `GITHUB_TOKEN`, a remote fetch and non-forced rebase/push. Conflicts and branch-protection rejection fail safely.

The workflow has `contents: write`, concurrency group `dividend-research-daily`, `cancel-in-progress: false`, and a 60-minute job timeout. It never prints secrets and adds no research email or Slack notification.
