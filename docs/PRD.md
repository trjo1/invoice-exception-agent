# Product Requirements — P2P Exception Orchestrator

**Status:** v1 — refined against build experience through Phase 10
**Owner:** Tribhuvan Joshi
**Date:** Originally 2026-05-10; revised 2026-05-14
**Companion docs:** `technical_design.md`, `architecture.md`, `model_strategy.md`, `test_corpus_design.md`, `CHANGELOG.md`

---

## 1. Problem statement

Procure-to-pay (P2P) processes at Fortune 1000 enterprises generate continuous exception traffic — invoices that don't match purchase orders, quantity variances, missing approvals, duplicate documents, vendor-master gaps, and fraud signals. Operating teams currently absorb this exception load through manual review queues, ad-hoc Slack threads, and email back-and-forth with suppliers. The cost is real: at scale, P2P exception handling consumes 30-40% of an AP team's time, slows working capital cycles by 5-15 days, and creates leakage in the form of paid duplicate invoices, missed early-pay discounts, and supplier-relationship friction.

Existing solutions are partial. Document AI vendors (Botminds, AI Builder) extract invoice fields but stop there. ERP-native workflow tools (SAP S/4 Workflow, Ariba Approvals) handle structured approvals but don't reason across unstructured supplier comms. RPA solutions follow rules but break on long-tail patterns. None hold workflow state across the full P2P lifecycle, none reason about why an exception arose, and none draft the supplier communication needed to resolve it.

**The agent's job is to hold the end-to-end exception lifecycle**: classify the exception, decide what should happen next, route to the right human at the right tier, draft the supplier-facing or internal communication, execute the cross-system actions once approved, and instrument every step for Stage 9 measurement.

---

## 2. Users and personas

### Primary users (inside the buyer organization)

| Persona | What they need from the agent |
|---|---|
| **AP Clerk (Tier 1)** | Cases that auto-pass with high confidence flow past them. Lower-confidence cases land in their queue pre-classified, with the agent's reasoning visible. They confirm or correct; corrections become training signal. |
| **Buyer / Procurement Lead (Tier 2)** | Price and quantity variances above threshold land here. They see the variance, the agent's recommended action (approve, request credit memo, escalate), and the draft supplier comm. They approve, modify, or escalate. |
| **AP Supervisor / Finance Ops Manager (Tier 3)** | Novel patterns, fraud signals, threshold violations, and cross-system inconsistencies escalate here. They see the full audit trail across systems. |
| **Engagement Lead / Sponsor (operator side)** | Reads Stage 9 quarterly reports — auto-pass rate trend, HITL queue depth, cost per task, cross-system error rate, audit-finding rate. |

### Secondary users (operator internal)

| Persona | What they need |
|---|---|
| **Solution Architect** | Reference architecture documentation, deployment playbook, integration runbook per ERP system, prompt registry. |
| **Engineer** | Test harness that runs the golden cases, Stage 9 dashboard, ability to swap models per task via config. |

---

## 3. Functional requirements

### 3.1 Exception detection and ingestion

- **FR-1.** The agent ingests events from SAP S/4HANA (PO creation, goods receipt, invoice posting attempt), Ariba (invoice receipt), and ServiceNow (manual exception tickets).
- **FR-2.** The agent ingests unstructured documents — invoice PDFs (multi-format, multi-language), supplier emails, scanned forms — through the extractors module.
- **FR-3.** Ingestion is event-driven (webhooks where supported) with a polling fallback. Events normalize to a common `Exception` pydantic model with source-system identifier, timestamp, original payload, and a stable case ID.

### 3.2 Exception classification (Routing & Triage)

- **FR-4.** Every exception is classified into one of 13 named categories (12 exception types + a clean baseline): `none` (clean 3-way match), `three_way_match_price_variance`, `three_way_match_quantity_variance`, `missing_po`, `missing_goods_receipt`, `missing_approval`, `duplicate_invoice`, `fraud_signal`, `vendor_master_gap`, `cross_currency_mismatch`, `tax_field_mismatch`, `payment_term_mismatch`, `other`. Defined in `src/p2p_agent/models/classification.py::ExceptionCategory`.
- **FR-5.** Classification produces a class label, confidence score (0-1), and supporting evidence (which fields drove the decision).
- **FR-6.** Class-aware confidence thresholds — fraud / compliance classes use tighter thresholds (because misroute cost is high) than routine price variances.

### 3.3 Decision-support and recommendation

- **FR-7.** For each classified exception, the agent produces a recommended action with rationale. The action is one of 16 values defined in `src/p2p_agent/models/recommendation.py::RecommendedAction`: `auto_resolve`, `approve_pending_review`, `request_supplier_credit_memo`, `request_supplier_correction`, `request_missing_po_from_supplier`, `request_po_amendment`, `route_to_vendor_master_onboarding`, `route_to_vp_finance_approval`, `escalate_to_buyer_for_short_delivery`, `escalate_to_buyer_for_retroactive_po`, `escalate_to_fraud`, `halt_require_supervisor`, `escalate_for_fx_review`, `notify_buyer_of_supplier_delay`, `hold_for_goods_receipt`, `other`.
- **FR-8.** Recommendations include a counterfactual surface — "if the price variance were under 3%, this would auto-resolve" — to build human trust faster than accuracy claims alone.
- **FR-9.** Recommendations are based on RAG retrieval over: relevant policy documents, historical exception resolutions for the same supplier, master-data records for the vendor, and the original PO context.

### 3.4 Coordination across systems

- **FR-10.** The agent holds workflow state in a durable store. Implementation today: SQLite (`logs/hitl_queue.db`) carries the HITL approval queue + pipeline-runs table. SQLAlchemy ORM means swap to Postgres is a `db_url` change. LangGraph wrap is deferred until pilot — pipeline is a plain async function today (`src/p2p_agent/orchestrator/pipeline.py`).
- **FR-11.** Cross-system actions (write-back to SAP, ticket creation in ServiceNow, supplier portal updates) execute only after HITL approval on consequential actions.
- **FR-12.** Permission model is per-action, not per-agent-identity. The agent's auth scopes are explicitly bounded; if a buyer requires a scope not granted, the agent halts and raises an explicit permission gap.

### 3.5 Drafting (sub-component, supplier and internal comms)

- **FR-13.** The agent drafts supplier communications (credit memo requests, PO clarification requests, payment confirmations) in editable form for the human reviewer to send.
- **FR-14.** The agent drafts internal communications (status notes, escalation summaries) for the approving human's review.
- **FR-15.** External counterparty communications NEVER auto-send. Internal informational notes may auto-send when configured.

### 3.6 Human-in-the-loop (HITL)

- **FR-16.** Three HITL tiers:
  - **Tier 1 (Auto-action)** — read-only operations and low-risk writes execute without review.
  - **Tier 2 (Human approval)** — any cross-system action that writes to a customer-facing or money-moving system requires human approval, regardless of confidence.
  - **Tier 3 (Escalation)** — novel patterns, suspected fraud, threshold violations, cross-system inconsistencies.
- **FR-17.** HITL queue is a first-class artifact — has its own SLA, named owner, and dashboard. Not a side queue inside an existing tool.
- **FR-18.** Approvals, modifications, and escalations all feed retraining. Override-with-rationale is captured as the highest-value training signal.

### 3.7 Stage 9 measurement

- **FR-19.** The agent instruments six signals from day 1:
  1. Task success rate (full multi-step flow completed without escalation)
  2. Escalation rate by step
  3. Time-per-task (avg and P95)
  4. Cost-per-task (model spend + infra)
  5. Cross-system error rate (actions that succeeded in one system but produced inconsistent state in another)
  6. Audit-finding count from weekly cross-process review
- **FR-20.** Metrics export to a Stage 9 dashboard at quarterly cadence. Drift on any signal raises an alert before customer-visible failure.

---

## 4. Non-functional requirements

### 4.1 Performance

| Metric | Target | Notes |
|---|---|---|
| Time to classify an exception | < 30 seconds (P95) | From event ingestion to classification surfaced |
| Time to recommend action | < 60 seconds (P95) | Includes RAG retrieval + ranking |
| Time end-to-end (excluding HITL wait) | < 5 minutes (P95) | Auto-pass cases |
| Throughput | 100 exceptions/hour at first reference; 1000/hour with scale-out | Per agent instance |

### 4.2 Accuracy

| Metric | Target (test phase) | Target (production) |
|---|---|---|
| Classification accuracy on golden set | > 90% | > 95% |
| Auto-pass false-positive rate | < 5% | < 2% (per regulated-industry SLA) |
| Recommended-action acceptance rate by humans | > 70% | > 85% |
| Citation accuracy in drafts (factual claims trace to source) | > 95% | > 99% |

### 4.3 Cost

| Metric | Target (test phase) | Notes |
|---|---|---|
| Cost per exception processed (avg) | < $0.50 | Open-source models via OpenRouter; aggressive context caching |
| Cost per golden-set run (40 cases) | < $10 | Test runs happen many times per day; cost discipline matters |
| Single-call cost ceiling | $0.10 | Anything above this raises an alert |

### 4.4 Security and compliance

- All buyer data is encrypted in transit and at rest.
- Per-action permission scopes; no agent runs with blanket write access.
- Audit trail per action with source document hash, model version, confidence, reviewer (if any), timestamp.
- PII redaction layer before any data leaves the buyer environment for model inference (when buyer requires it).
- SOC 2 Type II posture eventually; not a launch blocker for reference implementation.

### 4.5 Portability

- The agent runs on a buyer's cloud (AWS / Azure / GCP) OR in an operator-controlled inference layer — decided per engagement.
- Model provider is swappable per task via config — no code change required to swap DeepSeek for Anthropic on a specific step.

---

## 5. Integrations required

| System | Required for first reference | Connector depth |
|---|---|---|
| **SAP S/4HANA** | Yes | Full read + write on PO / Invoice / GR. OData v2/v4. |
| **Ariba** | Yes (P2A flagship anchor) | Read on invoice intake, write-back to status. |
| **ServiceNow** | Yes | Ticket creation, status update, comment, escalation. |
| **Microsoft Dynamics 365 Finance** | Yes (alternative ERP) | Same surface as SAP. |
| **Concur** | Phase 2 | Expense-side P2P shape. |
| **Workday Financials** | Phase 2 | Alternative ERP. |
| **Oracle Fusion ERP** | Phase 3 | Defer until first paying customer requires it. |
| **Email (Microsoft / Google)** | Yes — supplier comms intake and drafting | Graph API + Gmail API. |
| **Supplier portals** | Phase 2 | Coupa, Tradeshift, Basware — connector built per engagement. |

---

## 6. Out of scope (for first reference implementation)

- **Order-to-cash (O2C) processes** — different shape, different downstream systems. Defer to a sibling agent if pipeline justifies.
- **Treasury and cash management** — adjacent but different decision surface.
- **Procurement spend analytics** — Celonis territory; the agent uses spend analytics as input, doesn't produce it.
- **Supplier onboarding workflow** — adjacent; agent emits "new supplier needed" signal, doesn't run the onboarding itself.
- **Tax determination at line-item level** — vendor territory (Vertex, Avalara); agent consumes tax outputs, doesn't compute them.
- **Pure document extraction without exception context** — if a buyer needs only invoice extraction, route to Botminds; don't deploy this agent.
- **Customer-service-style chatbots** — Decagon / Forethought territory.

---

## 7. Success metrics (first 12 months post-build)

| Metric | Target |
|---|---|
| First paid engagement signed | Within 6 months of reference implementation completing |
| Three paid engagements running | Within 12 months |
| Stage 9 quarterly value report produced for at least one customer | Within 9 months |
| Reference-implementation re-use rate (engagements that start from the reference vs from scratch) | 100% — every engagement starts from the reference |
| Documented IP — patentable orchestration patterns | 2-3 specific patterns documented and reviewed for protection |

---

## 8. Open questions

Resolved through Phase 9:

1. **Hosting model.** ~~Buyer cloud vs operator-controlled inference~~ — **DECIDED PER ENGAGEMENT.** Operator-controlled for reference / demo; buyer-hosted when SLA or compliance requires.
2. **Embedding model.** ~~OSS bge-large-en vs OpenAI text-embedding-3-small~~ — **LOCKED: bge-large-en-v1.5 (local via sentence-transformers).** Free at runtime, portable on-prem. Decision held against the runtime cost ledger.
3. **Vector store.** ~~Postgres+pgvector vs dedicated (Pinecone, Weaviate)~~ — **PHASE 9 USES IN-MEMORY NUMPY COSINE.** 75 mock policies fits in memory; pgvector swap reserved for production scale.
4. **Workflow engine.** ~~LangGraph vs Temporal~~ — **LangGraph wrap deferred.** Pipeline is a plain async function today (`src/p2p_agent/orchestrator/pipeline.py`); LangGraph swap is mechanical when pilot durability needs arise. Temporal not needed.
5. **Drafting model.** ~~DeepSeek V3 vs Claude Haiku~~ — **DeepSeek V4-Flash in production today.** Quality has been acceptable; swap to Claude Haiku per-task in production only if buyer SLA requires it.

Remaining open:

6. **Real action executor backend shape.** Mock today (`src/p2p_agent/executor/action_executor.py`); real backend swaps connector calls into the same step-recipe interface. Decision: build SAP-first depth, then Ariba + ServiceNow as working stubs.
7. **Classifier accuracy ceiling.** Currently ~58.5% on the synthetic corpus (vs 66% pre-cross-case-context baseline). Cycle #2 of prompt iteration may close the gap; if not, the residual is a synthetic-data artifact and real-buyer data may behave differently.

---

## 9. Anti-requirements (what we won't build, even if asked)

- The agent will not auto-send external supplier communications. Ever. No config flag overrides this.
- The agent will not execute money-moving actions without HITL approval. The Tier 2 gate is non-negotiable.
- The agent will not run without Stage 9 instrumentation enabled. If the metrics-export endpoint is unreachable, the agent halts.
- The agent will not store buyer PII outside the buyer-designated region.
- The agent will not be deployed as a "black box" — every recommendation includes the rationale and citation; the buyer can always inspect why.

---

## Appendix — references

- Companion docs: `technical_design.md`, `architecture.md`, `model_strategy.md`, `test_corpus_design.md`, `CHANGELOG.md` (all in this folder)
- Engineering context: `AGENTS.md` (repo root)
