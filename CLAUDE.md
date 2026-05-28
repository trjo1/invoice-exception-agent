# Agent 1 — P2P Exception Orchestrator

This is the engineering project for TruVs Agent 1 — a Coordination + Routing + Decision-Support agent that spans SAP / Ariba / ServiceNow and handles procure-to-pay exceptions end-to-end.

This is the always-on context for Claude Code sessions on this project. Read this first every session.

---

## What this project is

**The agent.** A multi-archetype agent that watches the P2P process, classifies incoming exceptions (3-way match failures, quantity variances, price variances, missing approvals, duplicate POs, etc.), recommends the right next action with rationale, drafts supplier and internal communications where needed, and orchestrates the resolution across systems with HITL gates on every consequential cross-system action.

**Why TruVs builds this rather than uses an accelerator.** Per the build-vs-accelerate framework (`../../01_foundational_ip/build_vs_accelerate_decision_template.docx`), cross-system orchestration with bespoke business rules defaults to BUILD. The orchestration logic IS the moat — no single accelerator covers SAP + Ariba + ServiceNow cleanly without locking the buyer to MS or Google.

**Where this sits in the TruVs Practice context.** This is Agent 1 of 3 planned build-IP projects. Agents 2 and 3 (Audit & Compliance Findings; Claims Triage & Adjudication) will live as sibling folders when they spin up. The Practice GTM / IP repo lives at `../../` — read-only from this project's perspective. Reference docs but don't pull code from there.

---

## Status

**Phase 9 (2026-05-13).** 9 of 9 logic nodes shipped end-to-end. FastAPI demo console + HITL approval queue + Stage 9 measurement dashboard live. Mock action executor wired (real backend gated on SAP credentials). 80 tests passing. Total LLM spend: $3.74.

**What ships and runs today:**
- **Pipeline (`src/p2p_agent/orchestrator/pipeline.py`)** — plain async function wiring: extract → cross-case context → classify → retrieve → decide → route → draft → enqueue. ~15s per invoice end-to-end. LangGraph wrap is deferred — the nodes are designed as composable async functions so the swap is mechanical.
- **9 logic nodes:** extractor, cross-case context builder, classifier (13 categories), RAG retriever (75 mock policies, bge-large-en embeddings, in-memory cosine), decision-support, HITL router (3-tier, pure rules), drafter (supplier email + internal note), HITL approval queue (SQLite + audit log), mock action executor (16 RecommendedAction → simulated step recipes).
- **Demo console (FastAPI + Jinja).** Run `make hitl-serve` → `/demo` (upload or curated sample → full per-node trace), `/queue` (review with Approve / Reject / Edit-and-approve), `/stage9` (cost / latency / auto-pass-rate / classification mix dashboard), `/demo/runs` (history).
- **Test corpus:** 490 invoices + JSON sidecars. 33.9% error-injection rate; 10 canonical error labels.
- **24 golden cases** (GTC-001 → GTC-024) covering all 13 ExceptionCategory enum values. Includes anti-false-positive anchors (recurring services, emergency PO, strategic-vendor MSA tolerance).
- **80 tests** passing (unit + integration). No regression on any node.
- **All env vars auto-loaded** — `.env` picked up via `src/p2p_agent/__init__.py` import hook; no need to `source` before commands.

**Live classifier accuracy (synthetic corpus, partial eval Phase 8):**
- Classification: ~58.5% on the working portion (down from 66% pre-cross-case-context; recovered from 18% nadir after Phase 5 prompt reorder + Phase 8 payload trim + guardrail). Residual gap is `none → fraud_signal` over-flagging.
- Action vs truth class: 29.3% (was 12.9% pre-Phase-8).
- Decision-support save rate: 17.6%.
- Joint accuracy: 14.6%.

**Decisions signed off:**
- API mode for synthetic corpus generation (DeepSeek V4-Flash via OpenRouter, ~$0.18 for 490 invoices).
- DeepSeek V4-Flash is the per-task default for extraction / classification / drafting / corpus generation. DeepSeek R1 for decision-support reasoning.
- SQLite for HITL queue + pipeline runs DB (swap to Postgres is a `db_url` change). Postgres deferred until multi-user / pilot.
- LangGraph wrap deferred until pilot (durable state isn't a real need with single-user demo + SQLite).
- TJ + Claude Code solo build.

**What's blocked:**
- **Real action executor backend** — gated on SAP S/4HANA Cloud trial credentials.
- **Event ingestion (node 1)** — webhooks + batch polling. Same blocker.
- **Real SAP / Ariba / ServiceNow connectors** — `connectors/` stubs await credentials.

**What's available right now (no blockers):**
- Classifier accuracy iteration #2 (closes residual gap). Needs OpenRouter key top-up; ~$1.50 + 2h.
- More golden cases (24 → 40 target). Best authored against a specific buyer's scenarios.
- Drafter quality pass.
- LangGraph wrap (when pilot needs it).

---

## What's locked (don't relitigate)

| Decision | Status |
|---|---|
| The agent is Coordination + Routing + Decision-Support (with a Drafting sub-component for supplier comms) | LOCKED |
| 5-archetype taxonomy from `../../01_foundational_ip/framework_v2.docx` | LOCKED |
| Reference architecture from `../../01_foundational_ip/reference_architectures/04_coordination_orchestration.docx` | LOCKED — this is the architectural template |
| **API mode for synthetic corpus generation (DeepSeek V4-Flash via OpenRouter)** | LOCKED 2026-05-11 — replaces previous subscription-mode lock. Full 500-invoice corpus runs at ~$0.18. See `docs/subscription_mode_workflow.md` for the now-fallback path. |
| **Open-source models first (DeepSeek V4-Flash / V4-Pro, Kimi K2) for runtime and corpus** | LOCKED — see `docs/model_strategy.md`. V4-Flash is the per-task default for extraction, classification, drafting, corpus generation. V3 kept as fallback. |
| **SAP S/4HANA Cloud trial as primary ERP sandbox** | LOCKED — see `docs/sap_sandbox_setup.md` |
| **Solo build — TJ + Claude Code only, no additional engineer** | LOCKED — sequence all work for solo capacity |
| OpenRouter as the primary model-access route | LOCKED — single API, easy model swap |
| LangGraph for orchestration state machine | LOCKED — per the v2.1 reference architecture |
| Pydantic v2 for all internal data models | LOCKED |
| pytest for the golden-cases regression set | LOCKED |
| The $200K build floor on production deployments (inherited from CLAUDE.md in `../../`) | LOCKED |
| Stage 9 measurement is non-negotiable — instrument from day 1 | LOCKED |

If you find yourself wanting to change one of these, escalate to TJ. Do not silently redesign.

---

## What's open

- Final model selection per task (which exact model for extraction vs routing vs decision-support vs drafting) — first benchmarks land after week 4 of build
- Design partner candidate selection — pending outreach
- Whether to host the agent in a buyer's cloud or run it from a TruVs-controlled inference layer — decided per engagement
- ERP connector library — build all four (SAP / Dynamics / ServiceNow / Ariba) upfront, or stub three and depth-build SAP first? Currently leaning: SAP first to depth, others to working-stub level

---

## Model strategy — the cost discipline

**The thesis.** Open-weight models (DeepSeek V3, DeepSeek R1, Kimi K2, Qwen 2.5 / 3, Llama 3.3) are at 90-95% cost savings vs Anthropic Sonnet / GPT-4o for tasks at agent quality. In test phase we use only open models. In production deployments we swap to closed models per-task when accuracy SLAs require it.

**Why open-first.** Three reasons. (1) Test runs are inherently cost-heavy because the golden set runs every build; closed-model spend would dwarf engineering capacity quickly. (2) The agent's reference architecture should be portable — buyers in regulated industries want on-prem or self-hosted inference, which is easier if the architecture works with open models. (3) DeepSeek V3 alone clears 70%+ of the quality bar across the archetypes we care about for a fraction of the cost.

**Per-task default models (live in `config/models.yaml`):**

| Task | Default model | Why | API route |
|---|---|---|---|
| Extraction (invoice fields, supplier comms) | DeepSeek V4-Flash | Strong on structured extraction, cheap | OpenRouter |
| Classification | DeepSeek V4-Flash | Fast, accurate at multi-class | OpenRouter |
| Decision-support reasoning | DeepSeek R1 | Reasoning with cited rationale | OpenRouter |
| Drafting — supplier comms | DeepSeek V4-Flash | Conversational quality, cheap | OpenRouter |
| Corpus generation | DeepSeek V4-Flash | Bulk synthetic content at $0.000x per invoice | OpenRouter |
| Embedding (RAG) | `bge-large-en-v1.5` (local via sentence-transformers) | Free at runtime, portable on-prem | local |

**API key conventions.** The single env var `MODEL_PROVIDER` controls which provider the model client calls. `OPENROUTER_API_KEY` is the primary credential. Closed-model fallbacks (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) are optional and used only when an explicit per-task override is set.

**Cost tracking.** Every model call goes through `src/p2p_agent/llm/client.py` which logs token counts and computed cost per call to `logs/llm_calls.jsonl`. Stage 9 measurement aggregates this into cost-per-task. A run that exceeds the budget threshold raises an alert.

**See `docs/model_strategy.md` for the full version.**

---

## Folder structure

```
agent1-p2p-orchestrator/
├── CLAUDE.md                  # this file
├── README.md                  # project description
├── status.html                # live build status dashboard (open in browser)
├── docs/
│   ├── PRD.md                 # product requirements
│   ├── technical_design.md    # high-level architecture
│   ├── architecture.md        # Mermaid diagrams for buyer decks
│   ├── CHANGELOG.md           # append-only per-session shipping log
│   ├── model_strategy.md      # open-source-first inference strategy
│   ├── test_corpus_design.md  # data + golden-set strategy
│   ├── authoring_golden_cases.md  # how to write new YAML cases
│   └── sap_sandbox_setup.md   # SAP S/4HANA Cloud trial provisioning
├── src/p2p_agent/             # the agent code
│   ├── __init__.py            # auto-loads .env on import
│   ├── orchestrator/          # pipeline.py (async function wiring 9 nodes)
│   ├── extractors/            # PDF → structured JSON
│   ├── classifiers/           # 13-category classification + confidence guardrail
│   ├── context/               # cross-case lookups (vendor master, PO, history)
│   ├── retrieval/             # RAG over policy library (75 mock entries)
│   ├── decision/              # decision-support reasoning + counterfactuals
│   ├── drafter/               # supplier email + internal note generation
│   ├── hitl/                  # router + SQLite queue + FastAPI demo console
│   │   └── webapp/            # FastAPI routes + Jinja templates for /demo, /queue, /stage9
│   ├── executor/              # mock action executor (16-action recipe map)
│   ├── stage9/                # recorder (jsonl reader) + aggregator (queue + runs reader)
│   ├── connectors/            # SAP / ServiceNow / Ariba — stubs pending creds
│   └── llm/                   # ModelClient + prompts (versioned .md files)
├── tests/
│   ├── golden_cases/          # 24 YAML cases (GTC-001 → GTC-024)
│   ├── unit/                  # 25 unit tests (queue, executor, stage9 recorder + aggregator)
│   ├── integration/           # 36 integration tests (FastAPI TestClient round-trips)
│   └── conftest.py
├── test_corpus/synthetic/     # 490 invoices + JSON sidecars (not in git)
├── scripts/
│   ├── seed_hitl_queue.py     # populate the demo queue from N corpus invoices
│   ├── run_golden_set.py      # golden harness runner
│   ├── eval_pipeline.py       # 100-sample pipeline eval against the corpus
│   └── (corpus generators, ingesters)
├── config/                    # models.yaml, personas.yaml, policy_library.yaml
├── logs/                      # llm_calls.jsonl (cost ledger) + hitl_queue.db (SQLite)
├── pyproject.toml
├── .env.example
└── Makefile                   # hitl-serve, hitl-seed, test-golden, pipeline-eval, ...
```

---

## Working agreement (same tone as the practice repo)

- **Direct, not corporate.** Plain English. No hype.
- **Honest pushback over agreement.** If a design choice is wrong, say so before building.
- **Test discipline.** Every code change runs the golden-cases regression set before merging. If the set is failing, the fix lands before the next feature.
- **Cost discipline.** Watch the `logs/llm_calls.jsonl` aggregate after every test run. If a single test run costs more than $5, redesign — either swap to a cheaper model for that task, cache aggressively, or reduce context size.
- **One artifact per turn.** Don't bundle three features into one commit.

---

## Coding conventions

- Python 3.12+
- `uv` for dependency management (`uv sync`, `uv run`)
- `ruff` for linting and formatting
- `pytest` for tests, with markers `@pytest.mark.golden`, `@pytest.mark.integration`, `@pytest.mark.slow`
- `pydantic v2` for all internal data models — every domain object (PO, Invoice, Exception, RoutingDecision, Recommendation) is a pydantic model
- `structlog` for structured logging — all logs are JSON, never plain strings
- `httpx` for HTTP calls (sync) — connectors use httpx, not requests
- Type hints are required, not optional. CI fails on missing types.
- Imports sorted by `ruff`; no manual reordering
- One module per archetype concern (extractors don't import from classifiers; classifiers don't import from decision)
- All model calls go through `src/p2p_agent/llm/client.py` — never call `openai.chat.completions.create` directly anywhere else

---

## Common commands

```bash
# Set up the environment
uv sync

# Run golden set
make test-golden

# Run unit tests only
make test-unit

# Generate / refresh test corpus
make corpus

# Compute Stage 9 metrics from the latest run
make stage9

# Run a specific golden case end-to-end
uv run python scripts/run_golden_set.py --case GTC-002

# Estimate cost for the full golden set against the currently-configured models
make estimate-cost
```

(The Makefile is the entry point — see `Makefile` for the full list.)

---

## Important files to read first

For a new Claude session on this project, after this CLAUDE.md, read in order:

1. `docs/CHANGELOG.md` — read the LATEST entry to know exactly where we are. This is the source of truth for "what shipped."
2. `docs/architecture.md` — Mermaid diagrams of the 9-node pipeline + decision flow + HITL routing + data persistence. Best at-a-glance view.
3. `docs/PRD.md` — what we are building (product requirements).
4. `docs/technical_design.md` — how it fits together (architecture overview, modules, tech-stack decisions).
5. `docs/model_strategy.md` — open-source-first inference strategy.
6. `docs/test_corpus_design.md` — data + golden-set strategy.
7. `docs/authoring_golden_cases.md` — how to write new YAML cases (24 cases as of Phase 9; target 40).
8. `docs/sap_sandbox_setup.md` — SAP S/4HANA Cloud trial provisioning.
9. `docs/archetypes_and_patterns_cheatsheet.md` — TruVs framework: 5 archetypes, 5 patterns, build-vs-accelerate.
10. `status.html` — open in browser for the live dashboard.
11. `../../01_foundational_ip/reference_architectures/04_coordination_orchestration.docx` — referenced for architectural lineage; do not modify.

---

## Stuff that consistently goes wrong — watch for it

1. **Drift toward "let's just use Sonnet" because it's easier.** Use open models for the test phase even if they're 5-10% less accurate. The cost discipline matters; we can swap closed models in per-task in production.
2. **Mocking too much in tests.** Integration tests against sandboxed ERP environments are non-negotiable. Mock-only test suites give false confidence on the connector layer.
3. **Building the connectors before the orchestrator works on synthetic data.** Wrong order. Synthetic-only flow first (mock connectors), then add real connectors one by one once the orchestrator is solid.
4. **Inlining prompts.** All prompts live in `src/p2p_agent/llm/prompts/` as versioned text files, not inline in Python.
5. **Skipping Stage 9 instrumentation "for now."** Stage 9 telemetry is built first, not last. Cost-per-task, auto-pass rate, HITL accuracy must be tracked from the first test run.

---

## When you finish a session

1. Run `make test-golden` and confirm pass rate didn't regress
2. Update `docs/CHANGELOG.md` (create it if missing) with what shipped
3. If you touched a locked decision, escalate to TJ before merging
