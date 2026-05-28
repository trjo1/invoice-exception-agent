# P2P Exception Orchestrator — Engineering Context

A reference implementation of a Coordination + Routing + Decision-Support agent that handles procure-to-pay exceptions end-to-end across SAP / Ariba / ServiceNow.

Originally built at TruVs in 2026 while the author was AI Practice Lead. Now maintained as a personal learning artifact. This file is the always-on engineering context for AI coding sessions on the repo — read it first.

---

## What this project is

A multi-archetype agent that watches the P2P process, classifies incoming exceptions (3-way match failures, quantity variances, price variances, missing approvals, duplicate POs, etc.), recommends the right next action with rationale, drafts supplier and internal communications where needed, and orchestrates the resolution across systems with human-in-the-loop gates on every consequential cross-system action.

It's a working build, not a deployed product. The action executor runs in mock mode (every downstream call is logged as "would have done X"); the real backend would swap in behind the same interface once SAP credentials are wired.

---

## Status

**Phase 10 (2026-05-14).** 10 of 10 logic nodes shipped end-to-end. FastAPI demo console + HITL approval queue + Stage 9 measurement dashboard live. Mock action executor wired. 80 tests passing. Total LLM spend across the entire build: $3.74.

**What ships and runs today:**
- **Pipeline (`src/p2p_agent/orchestrator/pipeline.py`)** — async function wiring: extract → cross-case context → classify → retrieve → decide → route → draft → enqueue. Median ~30-60s per invoice end-to-end after Phase 10 latency cuts. LangGraph wrap is deferred; nodes are designed as composable async functions so the swap is mechanical.
- **10 logic nodes:** extractor, cross-case context builder, classifier (13 categories), RAG retriever (75 mock policies, bge-large-en embeddings, in-memory cosine), decision-support, HITL router (3-tier, pure rules), drafter (supplier email + internal note), HITL approval queue (SQLite + audit log), mock action executor (16 RecommendedAction → simulated step recipes).
- **Demo console (FastAPI + Jinja + SSE).** Run `make hitl-serve` → `/demo` (upload or curated sample → full per-node trace with live streaming), `/queue` (review with Approve / Reject / Edit-and-approve), `/stage9` (cost / latency / auto-pass-rate / classification mix dashboard), `/demo/runs` (history).
- **Test corpus:** 490 invoices + JSON sidecars (locally generated, not in git). 33.9% error-injection rate; 10 canonical error labels.
- **24 golden cases** (GTC-001 → GTC-024) covering all 13 ExceptionCategory enum values. Includes anti-false-positive anchors (recurring services, emergency PO, strategic-vendor MSA tolerance).
- **80 tests** passing (unit + integration).
- **PDF extraction cache** keyed on content hash — same PDF uploaded twice = 0 LLM calls.
- **Prompt-prefix cache** via OpenRouter / DeepSeek (auto + explicit `cache_control` hint) — 20-30% cost savings on system prompts.
- **All env vars auto-loaded** — `.env` picked up via `src/p2p_agent/__init__.py` import hook.

**Live classifier accuracy (synthetic corpus, partial eval Phase 8):**
- Classification: ~58.5% on the working portion. Eval cycle #2 still pending (needs OpenRouter top-up; small).

**Locked decisions:**
- API mode for synthetic corpus generation (DeepSeek V4-Flash via OpenRouter, ~$0.18 for 490 invoices).
- DeepSeek V4-Flash is the per-task default for extraction / classification / drafting / corpus generation. DeepSeek R1 for decision-support reasoning.
- SQLite for HITL queue + pipeline runs DB (swap to Postgres is a `HITL_DB_URL` change). Postgres deferred until multi-user / pilot.
- LangGraph wrap deferred until pilot (durable state isn't a real need with single-user demo + SQLite).
- Solo build — primary maintainer + an AI pair programmer (Claude Code).

**What's blocked:**
- **Real action executor backend** — gated on SAP S/4HANA Cloud trial credentials.
- **Event ingestion (node 1)** — webhooks + batch polling. Same blocker.
- **Real SAP / Ariba / ServiceNow connectors** — `connectors/` stubs await credentials.

---

## What's locked (don't relitigate)

| Decision | Status |
|---|---|
| Agent archetype: Coordination + Routing + Decision-Support (with a Drafting sub-component) | LOCKED |
| Open-source models first (DeepSeek V4-Flash, R1, Kimi K2) for runtime and corpus | LOCKED — see `docs/model_strategy.md` |
| SAP S/4HANA Cloud trial as the primary ERP sandbox | LOCKED — see `docs/sap_sandbox_setup.md` |
| OpenRouter as the primary model-access route | LOCKED — single API, easy model swap |
| LangGraph for orchestration state machine (deferred until pilot) | LOCKED |
| Pydantic v2 for all internal data models | LOCKED |
| pytest for the golden-cases regression set | LOCKED |
| Stage 9 measurement is non-negotiable — instrument from day 1 | LOCKED |

---

## What's open

- Final model selection per task — first benchmarks tracked in `docs/CHANGELOG.md`
- ERP connector library — build all four (SAP / Dynamics / ServiceNow / Ariba) upfront, or stub three and depth-build SAP first? Currently leaning: SAP first to depth, others to working-stub level

---

## Model strategy — the cost discipline

**The thesis.** Open-weight models (DeepSeek V3, R1, V4-Flash, Kimi K2, Qwen 2.5 / 3, Llama 3.3) are at 90-95% cost savings vs Anthropic Sonnet / GPT-4o for tasks at agent quality. In test phase we use only open models. Closed models swap in per-task when accuracy SLAs require it.

**Why open-first.** Three reasons. (1) Test runs are inherently cost-heavy because the golden set runs every build; closed-model spend would dwarf engineering capacity quickly. (2) The agent's reference architecture is portable — buyers in regulated industries want on-prem or self-hosted inference, which is easier if the architecture works with open models. (3) DeepSeek V3 alone clears 70%+ of the quality bar across the archetypes we care about for a fraction of the cost.

**Per-task default models (live in `config/models.yaml`):**

| Task | Default model | Why | API route |
|---|---|---|---|
| Extraction (invoice fields, supplier comms) | DeepSeek V4-Flash | Strong on structured extraction, cheap | OpenRouter |
| Classification | DeepSeek V4-Flash | Fast, accurate at multi-class | OpenRouter |
| Decision-support reasoning | DeepSeek R1 | Reasoning with cited rationale | OpenRouter |
| Drafting — supplier comms | DeepSeek V4-Flash | Conversational quality, cheap | OpenRouter |
| Corpus generation | DeepSeek V4-Flash | Bulk synthetic content at $0.000x per invoice | OpenRouter |
| Embedding (RAG) | `bge-large-en-v1.5` (local via sentence-transformers) | Free at runtime, portable on-prem | local |

**API key conventions.** The env var `MODEL_PROVIDER` controls which provider the model client calls. `OPENROUTER_API_KEY` is the primary credential. Closed-model fallbacks (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) are optional and used only when an explicit per-task override is set.

**Cost tracking.** Every model call goes through `src/p2p_agent/llm/client.py` which logs token counts and computed cost per call to `logs/llm_calls.jsonl`. Stage 9 measurement aggregates this into cost-per-task. Per-call hard ceilings prevent runaway spending; a daily-total cap (env var `DAILY_BUDGET_CAP_USD`) is also enforced for the hosted demo.

**See `docs/model_strategy.md` for the full version.**

---

## Folder structure

```
invoice-exception-agent/
├── AGENTS.md                  # this file (always-on engineering context)
├── README.md                  # portfolio description + setup guide
├── LICENSE                    # MIT
├── Procfile                   # Railway start command
├── Makefile                   # hitl-serve, hitl-seed, test-*, demo-warmup, ...
├── pyproject.toml             # dependencies + tool config
├── status.html                # build status dashboard (public-facing)
├── agent_overview.html        # plain-English scope walkthrough
├── detailed_workflow.html     # technical step-by-step trace
├── docs_index.html            # landing page for the docs/ routes
├── docs/
│   ├── PRD.md                 # product requirements
│   ├── technical_design.md    # architecture overview
│   ├── architecture.md        # Mermaid diagrams (10-node pipeline + decision flow + HITL routing)
│   ├── CHANGELOG.md           # append-only per-session shipping log
│   ├── model_strategy.md      # open-source-first inference strategy
│   ├── test_corpus_design.md  # data + golden-set strategy
│   ├── authoring_golden_cases.md
│   └── sap_sandbox_setup.md
├── src/p2p_agent/             # the agent code
│   ├── __init__.py            # auto-loads .env
│   ├── orchestrator/          # pipeline.py (async function wiring 10 nodes)
│   ├── extractors/            # PDF → structured JSON
│   ├── classifiers/           # 13-category classification + confidence guardrail
│   ├── context/               # cross-case lookups (vendor master, PO, GR, history, payment, vendor changes)
│   ├── retrieval/             # RAG over policy library (75 mock entries)
│   ├── decision/              # decision-support reasoning + counterfactuals
│   ├── drafter/               # supplier email + internal note generation
│   ├── hitl/                  # router + SQLite queue + FastAPI demo console
│   │   └── webapp/            # FastAPI routes + Jinja templates
│   ├── executor/              # mock action executor (16-action recipe map)
│   ├── stage9/                # cost / latency / outcome aggregation
│   ├── connectors/            # SAP / ServiceNow / Ariba — stubs pending creds
│   └── llm/                   # ModelClient + prompts (versioned .md files)
├── tests/
│   ├── golden_cases/          # 24 YAML cases (GTC-001 → GTC-024)
│   ├── unit/                  # unit tests
│   ├── integration/           # FastAPI TestClient round-trips
│   └── conftest.py
├── test_corpus/synthetic/     # 490 invoices + JSON sidecars (NOT in git)
├── scripts/
│   ├── seed_hitl_queue.py     # populate the demo queue from N corpus invoices
│   ├── run_golden_set.py      # golden harness runner
│   ├── eval_pipeline.py       # 100-sample pipeline eval against the corpus
│   ├── demo_warmup.py         # pre-warm the LLM prompt cache before a live demo
│   └── (corpus generators, ingesters)
├── config/                    # models.yaml, personas.yaml, policy_library.yaml
├── logs/                      # llm_calls.jsonl (cost ledger) + hitl_queue.db (NOT in git)
└── .env.example               # template — copy to .env and fill keys
```

---

## Working agreement

- **Direct, not corporate.** Plain English. No hype.
- **Honest pushback over agreement.** If a design choice is wrong, say so before building.
- **Test discipline.** Every code change runs `make test-unit` (and `make test-golden` when API budget allows) before merging.
- **Cost discipline.** Watch `logs/llm_calls.jsonl` after every test run. If a single run costs more than $5, redesign — swap to a cheaper model for that task, cache aggressively, or reduce context size.
- **One artifact per turn.** Don't bundle three features into one commit.

---

## Coding conventions

- Python 3.12+
- `uv` for dependency management (`uv sync`, `uv run`)
- `ruff` for linting and formatting
- `pytest` for tests, with markers `@pytest.mark.golden`, `@pytest.mark.integration`, `@pytest.mark.slow`
- `pydantic v2` for all internal data models
- `structlog` for structured logging — all logs are JSON, never plain strings
- `httpx` for HTTP calls — connectors use httpx, not requests
- Type hints are required, not optional
- Imports sorted by `ruff`; no manual reordering
- One module per archetype concern (extractors don't import from classifiers; classifiers don't import from decision)
- All model calls go through `src/p2p_agent/llm/client.py` — never call `openai.chat.completions.create` directly anywhere else

---

## Common commands

```bash
# Set up the environment
uv sync

# Run all tests
make test-unit

# Run the golden set against real LLMs (costs ~$0.50 per full run)
make test-golden

# Start the local demo (http://localhost:8080/demo)
make hitl-serve

# Pre-warm the LLM prompt cache before a live demo
make demo-warmup

# Generate / refresh the test corpus
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

For a new AI session on this project, after this AGENTS.md, read in order:

1. `docs/CHANGELOG.md` — read the LATEST entry to know exactly where we are.
2. `docs/architecture.md` — Mermaid diagrams of the 10-node pipeline + decision flow + HITL routing.
3. `docs/PRD.md` — what we are building (product requirements).
4. `docs/technical_design.md` — how it fits together.
5. `docs/model_strategy.md` — open-source-first inference strategy.
6. `docs/test_corpus_design.md` — data + golden-set strategy.
7. `docs/authoring_golden_cases.md` — how to write new YAML cases.
8. `docs/sap_sandbox_setup.md` — SAP S/4HANA Cloud trial provisioning.
9. `status.html` — open in browser for the live dashboard.

---

## Stuff that consistently goes wrong — watch for it

1. **Drift toward "let's just use Sonnet" because it's easier.** Use open models for the test phase even if they're 5-10% less accurate. The cost discipline matters; we can swap closed models in per-task in production.
2. **Mocking too much in tests.** Integration tests against sandboxed ERP environments are non-negotiable when those become available. Mock-only test suites give false confidence on the connector layer.
3. **Building the connectors before the orchestrator works on synthetic data.** Wrong order. Synthetic-only flow first (mock connectors), then add real connectors one by one once the orchestrator is solid.
4. **Inlining prompts.** All prompts live in `src/p2p_agent/llm/prompts/` as versioned text files, not inline in Python.
5. **Skipping Stage 9 instrumentation "for now."** Stage 9 telemetry is built first, not last. Cost-per-task, auto-pass rate, HITL accuracy must be tracked from the first test run.

---

## Portfolio integration (live demo deployment)

This agent ships to **Railway** as a public live demo. It integrates with the personal portfolio
at `https://tj-joshi-portfolio.vercel.app` via path-based routing — visitors hit
`tj-joshi-portfolio.vercel.app/demos/p2p` and land on this agent's `/demo` page.

**Canonical integration plan:** `/Users/ankitadwivedi/Job Search/Prep and Search/portfolio/DEMO_ARCHITECTURE.html`. Read this first when working on deployment, public URL conventions, or demo-behavior contracts. Every cross-component decision is documented there.

**What this agent must expose for the portfolio:**
- A stable public Railway URL (set after first deploy)
- `/healthz` endpoint returning 200 OK (already wired in `server.py`)
- Cold-start under 30s on wake (depends on Railway sleep behavior + bge model load)
- Demo-safe defaults (see contract below)

**Demo behavior contracts (non-negotiable for the live demo):**

- **No auto-submit.** The action executor must stay in `mode="mock"`. Real-mode raises on construction — don't change that.
- **No real customer data.** Pre-seeded knowledge only — the 75 mock policies, the 24 golden cases, the curated sample picker. Visitors cannot inject identifying data.
- **Read-only on real assets.** Nothing the demo does should modify production-shaped state outside the per-run SQLite + jsonl logs.
- **Daily LLM budget cap.** Hard cap via `DAILY_BUDGET_CAP_USD` env var (recommended: $2/day). The `ModelClient` raises `CostCeilingExceeded` before each call when the cap is hit; the FastAPI handler surfaces a friendly "demo budget reached" message.
- **Visitor isolation (known gap).** The HITL queue + run history are shared across visitors. For synthetic data this is acceptable; if it becomes a UX issue, add a banner or per-IP filtering.

**Vercel rewrite vs redirect — open decision:**
- Path-based rewrites (`/demos/p2p/*` → `railway/*`) require FastAPI to know its mount path. Either set `uvicorn --root-path /demos/p2p` OR every template's absolute link (`/queue`, `/stage9`, `/docs/...`) breaks. Multi-page FastAPI behind a rewrite is fiddly.
- Redirects are simpler: visitor clicks "View demo" → browser navigates to the Railway URL directly. URL bar shows Railway. Every link works.
- Default to redirects unless the rewrites approach gets explicitly proven out.

**Workflow when the Railway URL lands:**
1. Deploy to Railway, copy the assigned `*.up.railway.app` URL.
2. Share the URL back to the portfolio session (paste at the file path above).
3. Portfolio session updates its `vercel.json` rewrites + uncomments the "View live demo" button.

---

## When you finish a session

1. Run `make test-unit` and confirm pass rate didn't regress
2. Update `docs/CHANGELOG.md` with what shipped
3. Don't update this AGENTS.md unless something fundamental changed
