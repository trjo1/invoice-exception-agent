# Technical Design — P2P Exception Orchestrator

**Status:** v1 — refined against the build through Phase 9. 9 of 9 logic nodes shipped; action executor in mock mode pending SAP credentials. See `CHANGELOG.md` for the chronological view; `architecture.md` for buyer-facing diagrams.
**Date:** Originally 2026-05-10; revised 2026-05-13
**Owner:** Tribhuvan Joshi
**Companion:** `PRD.md`, `architecture.md`, `model_strategy.md`, `CHANGELOG.md`

---

## 1. System overview

```
                  ┌─────────────────────────────────────────────────────┐
                  │                  BUYER ENVIRONMENT                  │
                  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌────────┐  │
                  │  │   SAP   │  │  Ariba  │  │ServiceN.│  │ Email  │  │
                  │  └────┬────┘  └────┬────┘  └────┬────┘  └───┬────┘  │
                  └───────┼────────────┼────────────┼───────────┼───────┘
                          │            │            │           │
                  ┌───────▼────────────▼────────────▼───────────▼───────┐
                  │              EVENT INGESTION LAYER                   │
                  │   (webhooks + polling fallback; normalizes to        │
                  │    common Exception event with stable case ID)       │
                  └───────────────────────┬──────────────────────────────┘
                                          │
                  ┌───────────────────────▼──────────────────────────────┐
                  │          WORKFLOW STATE STORE (Postgres)             │
                  │   (durable per-case state; LangGraph checkpoints)    │
                  └───────────────────────┬──────────────────────────────┘
                                          │
   ┌──────────────────────────────────────▼──────────────────────────────┐
   │                    LANGGRAPH ORCHESTRATOR                            │
   │                                                                      │
   │   ┌──────────┐    ┌──────────┐    ┌──────────────┐   ┌──────────┐  │
   │   │EXTRACTOR │ ─► │CLASSIFIER│ ─► │  DECISION    │ ─►│ DRAFTER  │  │
   │   │ (docs +  │    │  (12     │    │   SUPPORT    │   │ (comms,  │  │
   │   │ emails)  │    │  cats)   │    │  (RAG+rank+  │   │ internal │  │
   │   │          │    │          │    │   counterfac)│   │  notes)  │  │
   │   └─────┬────┘    └────┬─────┘    └──────┬───────┘   └────┬─────┘  │
   │         │              │                  │                │        │
   │         └──────────────┴──────────┬───────┴────────────────┘        │
   │                                   │                                  │
   │              ┌────────────────────▼──────────────────────┐           │
   │              │      HITL ROUTER (Tier 1 / 2 / 3 logic)   │           │
   │              └────────────────────┬──────────────────────┘           │
   │                                   │                                  │
   │              ┌────────────────────▼──────────────────────┐           │
   │              │     ACTION EXECUTOR (writes back to       │           │
   │              │     SAP / Ariba / ServiceNow via          │           │
   │              │     connectors after HITL approval)       │           │
   │              └────────────────────┬──────────────────────┘           │
   └───────────────────────────────────┼──────────────────────────────────┘
                                       │
                  ┌────────────────────▼──────────────────────┐
                  │      STAGE 9 INSTRUMENTATION              │
                  │   (per-step metrics, cost, audit trail)   │
                  └────────────────────┬──────────────────────┘
                                       │
                  ┌────────────────────▼──────────────────────┐
                  │       LLM CLIENT (model swap, cost log)   │
                  │  OpenRouter → DeepSeek V3 / R1, Kimi K2   │
                  └───────────────────────────────────────────┘
```

The agent is a LangGraph state machine. Each node is a single archetype concern (extract / classify / decide / draft / route to HITL / execute). State is persisted in Postgres after every node so the agent survives restarts and human pauses.

---

## 2. Tech stack (locked)

| Layer | Choice | Rationale |
|---|---|---|
| Language | Python 3.12+ | Familiar tooling; LangGraph and pydantic v2 are Python-native. |
| Dependency manager | `uv` | Fast, modern, reproducible builds. |
| Orchestration engine | **Today: plain async function** (`orchestrator/pipeline.py`). LangGraph wrap deferred until pilot — nodes are designed as composable async functions so the swap is mechanical. | LangGraph is the locked production target; durable state isn't a real need with single-user demo + SQLite. |
| State persistence | **Today: SQLite** (`logs/hitl_queue.db`) for HITL queue + pipeline-runs + audit log. SQLAlchemy ORM means swap to Postgres is a `db_url` change. | Pgvector / Postgres swap is reserved for pilot scale; today's volume fits SQLite + in-memory numpy cosine. |
| Demo console | FastAPI + Jinja2 + Chart.js (CDN). `/demo` upload flow, `/queue` approval queue, `/stage9` measurement dashboard, `/demo/runs` history. | One web app, no SPA overhead. `make hitl-serve` to boot. |
| Data models | Pydantic v2 | Type safety, JSON serialization, validation. |
| HTTP client | httpx | Sync first; async if performance requires. |
| LLM access | OpenRouter (OpenAI-compatible API) | Single contract; per-task model swap via config. |
| Logging | structlog | JSON logs, structured, queryable. |
| Tests | pytest + pytest-asyncio | Standard. Markers for golden / integration / slow. |
| Linting + formatting | ruff | Single tool replaces black + isort + flake8. |
| Type checking | pyright | Stricter than mypy; faster. |
| Inference fallback | vLLM (when self-hosting) | Production-grade; only when buyer requires. |
| Containerization | Docker (multi-stage) | Standard. |

---

## 3. Module layout

```
src/p2p_agent/
├── __init__.py
├── models/                    # pydantic data models
│   ├── exception_event.py
│   ├── po.py
│   ├── invoice.py
│   ├── classification.py
│   ├── recommendation.py
│   └── draft.py
├── llm/                       # model client + prompts + cost log
│   ├── client.py              # ModelClient — the only place model APIs are called
│   ├── prompts/               # versioned prompts as .txt / .jinja files
│   ├── cost_calculator.py
│   └── providers.py           # provider config registry
├── orchestrator/              # LangGraph nodes + graph definition
│   ├── graph.py               # builds the StateGraph
│   ├── state.py               # AgentState (pydantic)
│   ├── nodes/
│   │   ├── ingest.py
│   │   ├── extract.py
│   │   ├── classify.py
│   │   ├── retrieve.py        # RAG retrieval
│   │   ├── decide.py
│   │   ├── draft.py
│   │   ├── hitl_route.py
│   │   └── execute.py
│   └── checkpointer.py        # Postgres checkpointer
├── extractors/                # document reading
│   ├── invoice.py
│   ├── email.py
│   └── attachments.py
├── classifiers/
│   ├── exception_classifier.py
│   └── confidence.py
├── decision/
│   ├── recommender.py
│   ├── counterfactual.py
│   └── retrieval.py           # RAG over policy + master data
├── connectors/                # one module per external system
│   ├── base.py                # abstract connector interface
│   ├── sap.py
│   ├── ariba.py
│   ├── servicenow.py
│   ├── dynamics.py
│   ├── email_ms.py
│   └── email_google.py
├── hitl/
│   ├── queue.py               # the HITL queue itself
│   ├── tier_router.py
│   └── approval.py
├── stage9/                    # measurement instrumentation
│   ├── recorder.py            # writes per-step metrics
│   ├── computer.py            # aggregates into Stage 9 signals
│   └── exporter.py            # exports to dashboard
└── config/
    └── loader.py              # loads models.yaml, stage9_thresholds.yaml
```

---

## 4. Data model (key types)

```python
# models/exception_event.py
class ExceptionEvent(BaseModel):
    case_id: str
    source_system: Literal["SAP", "Ariba", "ServiceNow", "Email", "Manual"]
    received_at: datetime
    po_id: str | None
    invoice_id: str | None
    raw_payload: dict[str, Any]
    documents: list[DocumentRef]  # references to attached files
    metadata: dict[str, Any]

# models/classification.py
class Classification(BaseModel):
    case_id: str
    class_label: ExceptionCategory  # enum of 12 categories
    confidence: float  # 0.0–1.0
    evidence: list[EvidenceItem]
    model_used: str
    timestamp: datetime

class ExceptionCategory(str, Enum):
    NONE = "none"                                          # clean 3-way match (baseline)
    THREE_WAY_MATCH_PRICE_VARIANCE = "three_way_match_price_variance"
    THREE_WAY_MATCH_QUANTITY_VARIANCE = "three_way_match_quantity_variance"
    MISSING_PO = "missing_po"
    MISSING_GOODS_RECEIPT = "missing_goods_receipt"
    MISSING_APPROVAL = "missing_approval"
    DUPLICATE_INVOICE = "duplicate_invoice"
    FRAUD_SIGNAL = "fraud_signal"
    VENDOR_MASTER_GAP = "vendor_master_gap"
    CROSS_CURRENCY_MISMATCH = "cross_currency_mismatch"
    TAX_FIELD_MISMATCH = "tax_field_mismatch"
    PAYMENT_TERM_MISMATCH = "payment_term_mismatch"
    OTHER = "other"

# models/recommendation.py
class Recommendation(BaseModel):
    case_id: str
    action: RecommendedAction
    rationale: str
    counterfactual: str | None
    confidence: float
    evidence_citations: list[Citation]
    model_used: str

class RecommendedAction(str, Enum):
    AUTO_RESOLVE = "auto_resolve"
    APPROVE_PENDING_REVIEW = "approve_pending_review"
    REQUEST_SUPPLIER_CREDIT_MEMO = "request_supplier_credit_memo"
    REQUEST_SUPPLIER_CORRECTION = "request_supplier_correction"
    REQUEST_MISSING_PO_FROM_SUPPLIER = "request_missing_po_from_supplier"
    REQUEST_PO_AMENDMENT = "request_po_amendment"
    ROUTE_TO_VENDOR_MASTER_ONBOARDING = "route_to_vendor_master_onboarding"
    ROUTE_TO_VP_FINANCE_APPROVAL = "route_to_vp_finance_approval"
    ESCALATE_TO_BUYER_FOR_SHORT_DELIVERY = "escalate_to_buyer_for_short_delivery"
    ESCALATE_TO_BUYER_FOR_RETROACTIVE_PO = "escalate_to_buyer_for_retroactive_po"
    ESCALATE_TO_FRAUD = "escalate_to_fraud"
    HALT_REQUIRE_SUPERVISOR = "halt_require_supervisor"
    ESCALATE_FOR_FX_REVIEW = "escalate_for_fx_review"
    NOTIFY_BUYER_OF_SUPPLIER_DELAY = "notify_buyer_of_supplier_delay"
    HOLD_FOR_GOODS_RECEIPT = "hold_for_goods_receipt"
    OTHER = "other"
```

---

## 5. The orchestrator state graph

LangGraph `StateGraph` with the following nodes and transitions:

```
ingest → extract → classify → retrieve → decide → [hitl_route OR auto_execute]
                                                       │
                                                       ├─► hitl_queue (Tier 1/2/3)
                                                       │       │
                                                       │       └─► after_approval ─► draft (if needed) ─► execute
                                                       │
                                                       └─► auto_execute (only for Tier 1 auto-pass)

execute → stage9_record → end
```

Each node is a pure async function: `state → new_state`. State is the `AgentState` pydantic model. Checkpoints write to Postgres after every node.

The graph is built in `orchestrator/graph.py`. Conditional edges decide the HITL route based on the recommendation's confidence + class. The `hitl_queue` node is a special "wait" node — the graph pauses and resumes when a human approves or rejects.

---

## 6. The LLM client abstraction

**Status:** Implemented 2026-05-11. Real, not stub.

Every model call goes through `src/p2p_agent/llm/client.py`. Signature:

```python
class ModelClient:
    async def complete(
        self,
        task: str,                                      # e.g. "exception_classification"
        messages: list[dict[str, str]],
        response_model: type[BaseModel] | None = None,  # TODO: pydantic validation
        max_cost_usd: float | None = None,
        model_override: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        case_id: str | None = None,
    ) -> CompletionResult:
        """Send a completion request, log cost, return output text."""
```

Behavior (current):
- Looks up the default model for `task` from `config/models.yaml`.
- If `model_override` is set or `MODEL_OVERRIDE_<task>` env var is set, uses that instead.
- Routes through the provider configured for that model (default: OpenRouter via `AsyncOpenAI` with `base_url=https://openrouter.ai/api/v1`).
- Direct-provider routes (DeepSeek-direct, Moonshot-direct, Anthropic-direct, OpenAI-direct) supported via `config/models.yaml` `providers:` entries; the wire model id is stripped of the provider prefix for non-OpenRouter providers.
- Cost computed from response `usage.prompt_tokens` and `usage.completion_tokens` against the `prices:` table in `config/models.yaml`. Logged to `logs/llm_calls.jsonl` as JSON lines (timestamp, task, model, provider, input/output tokens, cost_usd, latency_ms, case_id).
- Retries on transient errors via `tenacity` — `APIConnectionError`, `APITimeoutError`, `RateLimitError`, `InternalServerError`. 4 attempts, exponential backoff (2–30s).
- Cost ceiling enforcement: if computed cost exceeds the configured hard ceiling (`LLM_CALL_HARD_CEILING_USD`, default $1.00) or the per-call `max_cost_usd`, raises `CostCeilingExceeded` post-call. (Pre-call estimation is on the deferred list — see "Deferred" below.)

**Deferred (TODO, not blocking the build):**
- `response_model` pydantic validation. Callers parse JSON manually for now. Wire structured output via `response_format={"type": "json_schema", ...}` when the first archetype node needs it.
- Pre-call cost estimation using max_tokens × output price. Today the ceiling check runs after the call returns; for V4-Flash costs that's fine, but for any closed-model override we'll want pre-call estimation.
- Streaming support. Not needed for the test phase.

No other code in the project calls `openai.chat.completions.create` directly. This abstraction is what makes the open-source-first discipline enforceable. Today the ModelClient is exercised in production by `scripts/generate_invoices.py` and (next session) `scripts/run_golden_set.py`.

---

## 7. RAG retrieval design

The decision-support node uses RAG over four corpora:

| Corpus | Source | Update cadence |
|---|---|---|
| Buyer policy documents | Buyer-uploaded PDFs / Word docs | Per engagement, on change |
| Historical exception resolutions | Postgres — past resolved cases for the same supplier or category | Continuous (on resolution) |
| Master data | Vendor master, GL accounts, approval matrix | Daily sync from buyer ERP |
| Original PO context | Pulled at retrieval time from SAP / Ariba via connector | On-demand |

Embeddings: `bge-large-en-v1.5` (open-source). Stored in pgvector. Re-ranking via `bge-reranker-v2-m3` cross-encoder.

Retrieval quality is itself a Stage 9 signal — fraction of recommendations where retrieval returned evidence above the relevance threshold. Below 80% sustained = retrieval architecture problem, not model problem.

---

## 8. HITL design

Three tiers as defined in PRD §3.6. Implementation:

**Tier 1 — Auto-action.** When `HITLRouter.route(...)` returns `tier=1`, the pipeline does NOT enqueue. The case is auto-resolved (in the mock executor: a `SAP POST` step). Auto-pass is gated by classifier confidence ≥ 0.85 AND recommendation confidence ≥ 0.85; otherwise the router downgrades to Tier 2 even when the action is `auto_resolve`.

**Tier 2 — Human approval.** The pipeline enqueues the case into `hitl_items` (SQLite via SQLAlchemy). The reviewer opens `/queue` in the demo console, clicks into the item, and chooses Approve / Reject / Edit-and-approve. The action executor fires after approval (mock today; real connector when SAP credentials land). The case detail page renders the simulated execution steps after approval.

**Tier 3 — Escalation.** Same flow as Tier 2 but routed to a different role (ap_fraud_team, treasury, vp_finance, vendor_master_team). Execution recipes for Tier 3 actions include heavier downstream steps — for `escalate_to_fraud`, the executor runs SAP `HALT_PAY_RUN` + PagerDuty critical-severity NOTIFY + an audit-trail record. Full cross-system audit captured in `hitl_audit_entries`.

Implementation files:
- Router (pure rules, no LLM): `src/p2p_agent/hitl/router.py` — 16-action routing table + auto-pass guards.
- Queue (SQLAlchemy ORM): `src/p2p_agent/hitl/queue.py` — enqueue / list / get / approve / reject / approve_with_edit / mark_executed / stats / clear.
- ORM models: `src/p2p_agent/hitl/models.py` — `HITLItem`, `HITLAuditEntry`, `PipelineRun`.
- Demo console: `src/p2p_agent/hitl/webapp/server.py` — FastAPI app with 25 routes (HTML + JSON parallels).
- Templates: `src/p2p_agent/hitl/webapp/templates/` — `base.html`, `queue_list.html`, `item_detail.html`, `stats.html`, `stage9.html`, `demo_upload.html`, `demo_runs.html`, `demo_run_detail.html`.

The queue is SQLite-backed for solo/demo scale; the same SQLAlchemy ORM swaps to Postgres via a `db_url` change. Slack notifications + per-engagement buyer-tool embedding are reserved for pilot.

---

## 9. Connector pattern

All connectors implement `ConnectorBase`:

```python
class ConnectorBase(ABC):
    @abstractmethod
    async def read_po(self, po_id: str) -> PO: ...

    @abstractmethod
    async def read_invoice(self, invoice_id: str) -> Invoice: ...

    @abstractmethod
    async def write_action(self, action: Action) -> ActionResult: ...

    @abstractmethod
    async def list_events(self, since: datetime) -> list[Event]: ...
```

Per-system implementations live in `connectors/sap.py` etc. Each one wraps the system's specific auth / API quirks behind the common interface. The orchestrator calls only the common interface.

Build order:
1. SAP S/4HANA (most common P2P buyer) — depth.
2. ServiceNow (exception routing leg) — depth.
3. Microsoft Dynamics 365 Finance — working stub (validate connector abstraction).
4. Ariba — working stub.
5. Email (MS Graph + Gmail) — depth.
6. Concur / Workday / Oracle — deferred.

"Working stub" means: connector implements the abstract interface, against a mock or the sandbox API; passes integration tests; not necessarily optimized.

---

## 10. Stage 9 instrumentation

Per-LLM-call recording into `logs/llm_calls.jsonl` (written by `ModelClient`); per-pipeline-run snapshots in the `pipeline_runs` SQLite table; per-HITL-item status transitions in `hitl_audit_entries`. Aggregation surfaces all three at `/stage9` in the demo console.

```
src/p2p_agent/stage9/
├── recorder.py      # Stage9Reader — parses logs/llm_calls.jsonl with mtime-cached windowing
│                    # (cost summary by task + model, latency p50/p95/p99 per task, tail)
└── aggregator.py    # Stage9Aggregator — reads HITLQueue + PipelineRunStore
                     # (auto-pass rate, HITL resolution breakdown, classification + action mix, tier breakdown)
```

The dashboard at `/stage9` (rendered by `src/p2p_agent/hitl/webapp/server.py`) shows:
- Summary tiles: cases processed, auto-pass rate, LLM call count, total spend, overall p95 latency
- Cost breakdown table per task + per model
- Latency table per task (p50 / p95 / p99 / max / mean)
- Classification mix bar chart (Chart.js via CDN)
- HITL resolution doughnut chart
- Recent calls tail (last 20)

Time-window selector on the dashboard: 1h / 1d / 7d / 30d / all. JSON parallels at `/api/stage9/cost`, `/api/stage9/latency`, `/api/stage9/ops`, `/api/stage9/tail`.

Quarterly exporter (PDF / CSV per-buyer reports) deferred until first pilot engagement.

---

## 11. Deployment topology (reference implementation)

```
┌──────────────────────────┐
│  Operator-controlled cloud │ (AWS / Azure / GCP per-engagement)
│                          │
│  ┌────────────────────┐  │
│  │ Agent runtime      │  │   Python / FastAPI / LangGraph
│  │   (Docker)         │  │
│  └─────────┬──────────┘  │
│            │             │
│  ┌─────────▼──────────┐  │
│  │ Postgres + pgvect. │  │   State, RAG store, HITL queue, Stage 9 traces
│  └────────────────────┘  │
│                          │
│  ┌────────────────────┐  │
│  │ HITL Web UI         │ │   Thin React app for reviewers
│  └────────────────────┘  │
│                          │
└─────┬────────────────────┘
      │ HTTPS / mTLS
      ▼
┌────────────────────────────────────────────┐
│  BUYER SYSTEMS (SAP / Ariba / SNow / Email)│
└────────────────────────────────────────────┘
```

For air-gapped or on-prem buyers, the entire stack ports to buyer infrastructure. Models swap to self-hosted vLLM. State and RAG stay in the buyer's database.

---

## 12. What's NOT in this design (yet)

- Multi-tenant deployment shape — for now, one buyer per agent instance. Multi-tenant comes when we have 3+ paying customers.
- Streaming responses on Drafting — deferred until first reviewer-facing UI feedback shows it matters.
- Real-time policy updates — buyers re-upload policy docs and the embedder runs on a schedule. Push-update on policy changes is a phase 2.
- Multi-language drafting — English-only for first reference. Adds via per-task model override in production.
- Vendor portal connectors (Coupa, Tradeshift, Basware) — deferred until first engagement requires.

---

## 13. Open questions — Phase 9 status

Resolved during the build:

1. ~~Hosting model default — operator-controlled vs buyer-controlled?~~ — **Decided per engagement.**
2. ~~Embedding model — bge-large-en vs text-embedding-3-small?~~ — **bge-large-en-v1.5 local via sentence-transformers.** Free at runtime, portable.
3. ~~Vector store — pgvector or dedicated?~~ — **In-memory numpy cosine for v1** (75 mock policies fit in memory). Pgvector reserved for pilot scale.
4. ~~HITL UI — minimal React or embed in buyer tool?~~ — **FastAPI + Jinja + Chart.js.** Single web app, no SPA overhead. Per-engagement embedding is reserved for pilot.
5. ~~SAP connector depth — read-write from day 1?~~ — **Mock-first, real-write second.** Mock executor in place today; real backend swaps in once SAP credentials land.

Remaining:

6. **Real action executor behavior on failure.** When the real SAP write returns 500 / 409, what does the executor do? Options: retry with exponential backoff, surface to operator, halt pay run defensively. Decision when we start the real-backend implementation.
7. **Classifier accuracy ceiling on real-buyer data.** Synthetic corpus is at ~58.5%; real-buyer data may behave differently (better extraction signal vs more edge-case noise). Will be re-baselined at first paid engagement.

---

## Appendix — references

- Test corpus design — `test_corpus_design.md` (this folder)
- Model strategy — `model_strategy.md` (this folder)
- PRD — `PRD.md` (this folder)
