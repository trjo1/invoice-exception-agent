# invoice-exception-agent

A reference implementation of an AI agent that handles procure-to-pay invoice exceptions end-to-end across SAP, Ariba, and ServiceNow — the kind of work where an AP clerk normally spends 10–20 minutes per invoice jumping between systems to figure out what's wrong.

The agent reads the invoice, looks up the relevant PO and goods receipt and vendor history across six master-data sources, classifies the exception into one of 13 categories, retrieves relevant policy snippets via RAG, recommends an action with cited rationale and a counterfactual, routes to the right human tier for sign-off, drafts any supplier or internal communication, and (once approved) writes back to the downstream systems. Full audit trail throughout.

**This is a working build, not a production-deployed product.** The action executor runs in mock mode; the real SAP/Ariba/ServiceNow connectors are stubs gated on credentials. The architecture is production-shaped — open-source models via OpenRouter, structured logging, evaluation harness, HITL queue — but the codebase is a learning artifact and reference, not a live service.

> **Live demo:** [https://web-production-45a73.up.railway.app/demo](https://web-production-45a73.up.railway.app/demo) — try it now (first request takes ~30s while the agent warms up; subsequent runs ~60s end-to-end)
>
> **Documentation:** [https://web-production-45a73.up.railway.app/docs](https://web-production-45a73.up.railway.app/docs) — landing page with the agent overview, detailed technical workflow, and engineering status

---

## What's in the box

**The agent itself.** Ten orchestrated steps, four LLM calls, six master-data lookups, three HITL tiers, sixteen recommended actions. Full async pipeline. Runs end-to-end in ~30-60 seconds per invoice.

**A FastAPI demo console.** Upload any PDF or pick from 10 curated sample invoices (US, Germany, India, Brazil, UK personas covering all 13 exception scenarios). Watch the pipeline stream live via Server-Sent Events. Review the case at `/queue`, browse past runs at `/demo/runs`, check the measurement dashboard at `/stage9`.

**13 exception categories.** Price variance, quantity variance, missing PO, missing goods receipt, missing approval, duplicate invoice, fraud signal, vendor master gap, cross-currency mismatch, tax field mismatch, payment term mismatch, plus "no exception" (auto-pass) and "other" (catch-all).

**24 golden test cases** covering every category, including anti-false-positive anchors (recurring services, emergency PO, strategic-vendor MSA tolerance). 80 unit + integration tests passing.

**Production-shape observability.** Every LLM call logged with cost, latency, token count, and case ID to `logs/llm_calls.jsonl`. A "Stage 9" dashboard aggregates auto-pass rate, classification mix, human-resolution breakdown, cost per case. Designed so the agent measures itself and the quality story is data-driven.

**Cost discipline.** Open-source models first (DeepSeek V4-Flash + R1 via OpenRouter, `bge-large-en-v1.5` for local embeddings). Per-call and daily-total budget caps. Sub-cent per invoice in practice (~$0.005-$0.013 typical). Total LLM spend across the entire build to date: $3.74.

---

## Architecture at a glance

```
Upload PDF
    ↓
[1] Extract       — DeepSeek V4-Flash. PDF → structured InvoiceExtraction.
    ↓
[2] Context       — 6 parallel master-data lookups (vendor / PO / GR /
                     payment status / history / vendor changes). No LLM.
    ↓
[3] Classify      — DeepSeek V4-Flash. Picks 1 of 13 categories with confidence.
    ↓
[4] Retrieve      — RAG over 75 policies (bge-large-en embeddings).
    ↓
[5] Decide        — DeepSeek R1. Picks 1 of 16 actions, with cited rationale +
                     counterfactual.
    ↓
[6] Route         — Rules-based. Maps action → Tier 1 / 2 / 3 + named role.
    ↓
[7] Draft         — DeepSeek V4-Flash (conditional). Supplier email or internal note.
    ↓
[8] Approve       — Human-in-the-loop. SQLite-backed queue + audit log.
    ↓
[9] Execute       — Mock today. SAP / Email / ServiceNow / PagerDuty recipe per action.
```

Tier-1 auto-pass cases (cleanest, highest-confidence) skip steps 7 and 8.

Full architectural walkthrough: `detailed_workflow.html` (real-data trace of one invoice through all 10 steps) and `docs/architecture.md` (Mermaid diagrams of the pipeline, decision flow, HITL routing, and persistence).

---

## Run it locally

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
# Clone
git clone git@github.com:YOUR-USERNAME/invoice-exception-agent.git
cd invoice-exception-agent

# Install dependencies
uv sync

# Set up your OpenRouter API key
cp .env.example .env
# edit .env and add OPENROUTER_API_KEY=sk-or-v1-...

# (Optional) Pre-warm the LLM prompt cache before a live demo
make demo-warmup

# Start the demo console + HITL queue + Stage 9 dashboard
make hitl-serve
# → http://localhost:8080/demo
```

The first invoice through the pipeline will take ~30-90 seconds (bge-large-en model loads on first request). Subsequent invoices are faster (~10-30s typical) thanks to prompt caching and the singleton embedder. Sample invoices are cached by content hash, so re-running the same sample is instant.

Run the test suite:

```bash
make test-unit           # 80 unit + integration tests, no LLM calls
make test-golden         # 24 golden cases against real LLMs (costs ~$0.50 per full run)
```

---

## Documentation

Open these in your browser for a richer walkthrough than this README:

| Doc | What it covers | Audience |
|---|---|---|
| `agent_overview.html` | Plain-English scope walkthrough — what the agent does, the 13 exception types, the data it consults, where humans stay in control | Non-technical readers, business stakeholders |
| `detailed_workflow.html` | Deep technical trace of all 10 steps with real prompts, real data structures, real input/output examples | Engineers, technical interviewers |
| `status.html` | Engineering build status, full pipeline diagram, per-node detail, software stack | Build-status snapshot |
| `docs/architecture.md` | Mermaid diagrams: 10-node pipeline, decision flow, HITL routing, data persistence | Copy/paste-into-deck friendly |
| `docs/PRD.md` | Product requirements | Reference |
| `docs/technical_design.md` | High-level architecture, module boundaries, tech-stack rationale | Reference |
| `docs/model_strategy.md` | Why open-source first, per-task model assignments, cost tracking strategy | Reference |
| `docs/CHANGELOG.md` | Append-only per-session shipping log | History |

---

## Origin

Originally built at TruVs in 2026 while the author (Tribhuvan Joshi) was AI Practice Lead there. It was the reference implementation for the practice's "Multi-System Process Agent" pattern. Now maintained as a personal learning artifact and portfolio piece — the architecture, evaluation discipline, and HITL design are the durable lessons.

Companion `AGENTS.md` in the repo root carries the engineering-session context (coding conventions, locked decisions, common commands) for AI coding tools.

---

## License

MIT. See `LICENSE`.
