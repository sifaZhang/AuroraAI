# AGENTS.md

# AuroraAI Development Rules

This file defines the mandatory development rules for all AI coding agents working on AuroraAI.

Unless the user explicitly instructs otherwise, these rules always apply.

---

## Project Philosophy

AuroraAI is a long-term personal quantitative research platform.

The goal is to maximize research efficiency, not to build an enterprise software system.

Always prefer:

- Simple
- Stable
- Easy to maintain
- Easy to extend
- Easy to debug

Avoid enterprise-level complexity unless it provides clear value to this project.

Do not implement features only because they are considered "best practice" in large software systems.

---

# 2. Core Principles

## Don't Guess

Never invent data.

Never guess API behavior.

Never guess database contents.

If information is unavailable, report it clearly.

---

## Don't Hide

Never silently ignore errors.

Never silently skip validation.

Never silently change user requirements.

Important issues must always be visible.

---

## Don't Assume

Never change project direction.

Never expand scope without approval.

Never change architecture because it "looks better".

Follow the user's requested scope.

---

# 3. Architecture

Follow this dependency direction:

UI

↓

API

↓

Service

↓

Repository

↓

Database

↓

Data Provider

Business logic must never directly call third-party SDKs.

---

# 4. Data Sources

Use the Provider abstraction layer.

Business code must never directly call:

- Tushare
- AKShare
- GM API
- Other external SDKs

Always access data through Provider interfaces.

Priority:

Tushare

↓

AKShare

↓

Other fallback providers

---

# 5. Database

Current database:

SQLite

Requirements:

- All schema changes use migrations.
- Never modify old migrations.
- Only add new migrations.
- Keep migrations idempotent.
- Preserve existing user data.

---

# 6. Git Rules

Never execute:

- git add
- git commit
- git push
- Create Pull Requests

Unless the user explicitly requests it.

Always leave the repository in a clean reviewable state.

---

# 7. Scope Control

Only implement what the current PR requires.

Do not add unrelated features.

Do not "improve" other modules.

Do not perform large refactors unless requested.

Keep PRs focused.

---

# 8. Code Quality

Requirements:

- Type hints
- Meaningful names
- Small functions
- Clear responsibilities
- No duplicated logic
- No magic numbers
- Minimal comments
- Self-explanatory code

---

# 9. Data Models

Business layer must use domain models.

Do not expose:

- pandas DataFrame
- raw JSON
- third-party response objects

Convert external data into AuroraAI domain models.

---

# 10. Error Handling

Never use:

except:

Always catch specific exceptions.

Errors must contain meaningful messages.

Never hide failures.

---

# 11. Logging

Logs must help debugging.

Never log:

- Tokens
- Passwords
- Secrets
- API Keys

Use structured logs whenever possible.

---

# 12. Configuration

Secrets must come from environment variables.

Never hardcode:

- Tokens
- Passwords
- API Keys

Do not commit secrets.

---

# 13. Testing

Every implementation should include appropriate tests.

When applicable:

- unit tests
- integration tests
- contract tests

Before finishing:

- compile check
- relevant pytest
- git diff --check

Report results honestly.

Never claim tests passed if they were not executed.

---

# 14. Documentation

Each PR should include documentation.

Prefer:

docs/prX_xx_xxx.md

Document:

- purpose
- design
- implementation
- testing
- limitations

---

# 15. Backward Compatibility

Do not break existing APIs.

Do not change existing database behavior unless required.

Preserve compatibility whenever practical.

---

# 16. Performance

Avoid unnecessary work.

Prefer:

- batch operations
- incremental updates
- caching when appropriate

Do not optimize prematurely.

Correctness first.

---

# 17. User Data Safety

Never:

- delete user files
- overwrite user work
- modify production databases

Unless explicitly requested.

---

# 18. Development Strategy

Always prefer:

Step 1

Abstract

↓

Step 2

Implement

↓

Step 3

Optimize

Do not build V3 features inside V1.

Keep the first version simple.

---

# 19. Decision Rules

If multiple implementations are possible:

Prefer:

stable

↓

simple

↓

maintainable

↓

fast

Avoid unnecessary complexity.

---

# 20. Communication

When reporting work:

Clearly separate:

Completed

Not Completed

Known Limitations

Next Suggested Step

Do not exaggerate progress.

---

## 21. Completion Notification

When **ALL** of the following are true:

* Implementation is finished.
* All requested tests have completed.
* `PROJECT_STATUS.md` has been updated to reflect the completed work.
* The final summary has been prepared.
* No further file modifications are required.
* The notification command must be executed only AFTER the final response has been completely generated and no further tool calls or text output will occur. It must be the last executable action of the entire task.

Run the following command as the **final action** of the task:

```powershell
powershell.exe -STA -NoProfile -ExecutionPolicy Bypass -File "F:\Stock\Projects\AuroraAI\scripts\codex_notify.ps1"
```

Rules:

* Update `PROJECT_STATUS.md` **before** writing the final summary.
* Execute the notification command **after** all file modifications are complete.
* Do **not** execute the notification during intermediate progress updates.
* If the notification command fails, do **not** treat the task as failed.
* If the notification fails, clearly state **"Completion notification failed."** at the end of the final summary.

---

# 22. Current Technology Stack

Primary Language

Python

Backend

FastAPI

Database

SQLite

Frontend

HTML / JavaScript

Data Sources

Tushare (Primary)

AKShare (Fallback)

GM API (Realtime)

---

# 23. Current Project Direction

Current priorities:

1. Unified Data Provider
2. Industry Radar
3. First Limit Strategy
4. Market Analysis
5. Valuation System

Do not change this roadmap unless instructed.

---

# 24. General Rule

If uncertain:

Stop.

Explain the uncertainty.

Ask for clarification rather than making assumptions.

Accuracy is always more important than speed.