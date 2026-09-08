# DevBrief Agent

DevBrief is an evidence-driven Agent Harness for engineering decisions. It turns
versioned, redacted Bug Triage transcripts into reviewable, recoverable task plans
with explicit budgets, tool controls, approvals, checkpoints, trace, and replay.

## Current Status

This repository is initializing Phase 1. The target is a local, no-credential
Harness prototype; it is not a meeting-notes application, coding Agent, production
service, or GitHub automation. Audio/ASR, real LLMs, GitHub APIs, MCP, databases,
web UI, CI, containers, multi-Agent orchestration, code execution, and PR merging
are outside this delivery.

## Planned Phase 1 Slices

1. Versioned, redacted transcript fixture and formal contracts.
2. Single-Agent state machine, execution budget, cancellation, and checkpoint.
3. Redacted trace and side-effect-free fake replay.
4. Canonical fake Tool Registry with read/draft/external levels and Policy Gate.

## Development

Python 3.11+ with a `src` layout is required. Once dependencies are installed, the
quality gate is:

```bash
pytest
ruff format --check
ruff check
pyright
```

Only actual commands and their outcomes are recorded in pull requests. See
`docs/requirements/`, `docs/architecture/`, `docs/standards/`, `CONTRIBUTING.md`,
and `AGENTS.md` for the delivery contract and safety boundaries.
