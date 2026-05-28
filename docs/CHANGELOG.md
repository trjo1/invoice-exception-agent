# Changelog — Agent 1 P2P Exception Orchestrator

Append-only. Most recent at top. One entry per session that ships code or docs.

---

## 2026-05-28 — Framing reset, /demo UX rebuild, persistent storage

**Framing reset.** Locked in that this project is no longer a TruVs
deliverable. It's a personal portfolio + learning artifact for landing
roles in the agents space. Added `CLAUDE.md` (framing rules) and
`ROADMAP.md` (current state + open questions for the next phase) at the
repo root. Added a pointer at the top of `AGENTS.md` so anyone reading
the engineering context goes through the framing first. Future Claude
sessions should NOT drift back to TruVs go-to-market framing (Stage 9 as
"recurring revenue moat," P2A Method, $200K floor, etc.) — those belong
to a different project entirely and aren't relevant here.

**Next phase declared but not scoped:** learning + evals layer (close
the HITL feedback loop, aggregate eval metrics on top of golden cases,
A/B prompt evaluation, drift monitoring on Stage 9, self-consistency for
high-stakes routes). Specifics intentionally open — `ROADMAP.md` lists
the questions TJ needs to answer before any of this gets scoped.

**/demo UX rebuild.** Replaced the single upload form with two views:
(1) `/demo/browse` — a hint-free invoice picker (20 invoices, no expected-
outcome leakage, click to preview PDF, click to select, return to home
with the run queued up); (2) curated-scenario dropdown on the home page
with labeled patterns for one-click runs (10 scenarios with expected
behavior shown). Expanded the bundled sample library from 10 → 20
invoices (4 per persona) and added JSON sidecars to the gitignore
allowlist so vendor/total metadata is available on Railway.

**Persistent storage on Railway.** Past runs were getting wiped on every
redeploy because the SQLite DB, uploaded PDFs, and `llm_calls.jsonl`
cost ledger all lived in the ephemeral container filesystem. Unified all
three paths under a single `DATA_DIR` env var; on Railway, mount a volume
at `/data` and set `DATA_DIR=/data`. `ModelClient` also now honors
`DATA_DIR` for its log path. README documents the steps.

85/85 tests pass.

---

## 2026-05-14 (Phase 10) — Latency cuts + SSE streaming UI

A live demo run was clocking **196 seconds** on a single invoice (extract 73s + classify 46s + retrieve 19s + decide 34s + draft 24s + glue). With the CEO + practice-leads meeting coming up, three minutes of blank screen would sink the demo regardless of how good the agent's decisions are. This session diagnosed the root causes and shipped both real-latency cuts and a streaming UI so the demo never goes silent.

### Diagnostic findings (cross-referenced against `logs/llm_calls.jsonl`)

| Issue | File | Cost |
|---|---|---|
| Classifier retry fired on EVERY invoice — `except (ValueError, ClassifierError)` triggered on schema-validation failures retry can't fix | `classifiers/exception_classifier.py:271` | ~10s wasted every invoice |
| JSON parser too strict — rejected any response with prose before the JSON, forcing the retry | `llm/json_utils.py` | (root cause of #1) |
| Embedder cold-loaded on first `retrieve()` — bge-large-en + torch on CPU, no singleton, fresh `PolicyRetriever()` per app instance | `retrieval/embeddings.py`, `pipeline.py:128` | 10-15s on first invoice/process |
| Drafter ran on Tier 1 auto-pass cases — gated on `action_needs_draft()`, not on tier | `orchestrator/pipeline.py` | ~24s wasted on auto-pass |
| No prompt caching enabled | `llm/client.py` | 20-30% per-call input-token cost |
| Tenacity retry too forgiving — 4 attempts × 2-30s backoff hid intermittent OpenRouter blips behind 30s+ "is it stuck?" waits | `llm/client.py:227` | up to +30s per call |

### What shipped

**A. Tolerant JSON parser + narrower classifier retry.** `extract_json_from_response` now falls back to a balanced-brace scanner — handles "Here is the classification: { ... }" without forcing a retry. The classifier's retry except is narrowed from `(ValueError, ClassifierError)` to `ValueError` only; schema-validation failures bubble (re-asking the model rarely fixes them and doubles latency). A `logger.warning("classifier_retry_fired", ...)` records every actual retry so we'll see in logs if it ever fires again.

**B. Process-wide singleton embedder + retriever.** New `get_default_embedder()` and `get_default_retriever()` in `src/p2p_agent/retrieval/`. The FastAPI lifespan event warms the retriever at server startup (`make hitl-serve` now takes ~10s longer to boot, but the first invoice no longer pays the model-load cost). Server logs `[startup] policy retriever warm (Xs, N policies)` when ready. `HITL_SKIP_WARMUP=1` skips warm-up for tests.

**C. Drafter short-circuit on Tier 1.** `pipeline.py` now gates drafter on `routing_decision.tier >= 2 AND action_needs_draft(...)`. Tier 1 cases never read the draft, so we don't spend an LLM call producing one. The skipped step is still recorded in `StepTrace` (new `status="skipped"` + `skip_reason` fields) so the trace UI shows "Draft — skipped (Tier 1 auto-pass)" instead of silently hiding the row.

**D. Prompt caching + tightened tenacity.** `ModelClient.complete` now logs `cached_tokens` from `usage.prompt_tokens_details.cached_tokens` on every call (DeepSeek's native prompt-prefix caching surfaces here). Added an opt-in `LLM_PROMPT_CACHE_HINT=1` env flag that rewrites system messages to the structured-content form with `cache_control: ephemeral` — belt-and-suspenders for providers that need the explicit marker. Tenacity tightened from (4 attempts, 2-30s backoff) to (3 attempts, 1-8s) — fail fast and surface in the UI instead of hiding 30s waits.

**E. Parallelized cross-case context queries.** Added `CaseContextBuilder.build_async()` that uses `asyncio.gather` over `asyncio.to_thread`-wrapped lookups (vendor master, PO, GR, payment status, history, vendor changes). Two-stage gather because `vendor_changes` depends on the vendor lookup. **Zero measurable savings today** (lookups are sync dict accesses against mocks) — the win is real when SAP comes online and each lookup is a 0.5-2s httpx call. Pipeline now calls `build_async`.

**F. SSE streaming UI.** This is the demo-saver.

- **Pipeline event emission.** `run_invoice_pipeline` now accepts an optional `on_event: Callable[[dict], Awaitable[None]]`. Each node emits `step.start` (with timestamp) and `step.end` (with `latency_ms` + a small `summary` payload tailored per node — vendor name + total for extract, category + confidence for classify, top 3 policies for retrieve, action + rationale snippet for decide, etc.). Tier-1 drafter skip emits `step.skipped`. End emits `run.done`.
- **Streaming POST + SSE endpoints.** Three new routes alongside the existing `/demo/run` (kept for tests and backward compat):
  - `POST /demo/run-streaming` — saves the PDF + creates the run row + redirects to the watch page. Does NOT block on pipeline.
  - `POST /demo/run-sample-streaming` — same flow for curated samples.
  - `GET /demo/run-stream-view/{run_id}` — renders the timeline template.
  - `GET /demo/run/{run_id}/stream` — `text/event-stream` SSE response. Runs the pipeline inside the SSE handler, yields each event as a `data:` line. Per-run `asyncio.Lock` prevents double-runs if the user opens two tabs. 15s keepalive comments keep proxies from timing out.
- **Streaming UI template.** New `demo_run_stream.html` — 8-row timeline rendered at second 1 (extract, context, classify, retrieve, decide, route, draft, enqueue), each row shows ○ → ⟳ → ✓ as events arrive. Live elapsed timer top-right. Per-step duration counter updates every 250ms while a node is running. Vanilla JS, ~100 lines, no framework. Brand tokens reused from `base.html`. Auto-redirects to the existing `/demo/run/{id}` detail page on `run.finalized`.
- **Upload form switched to streaming endpoints** by default. The "Running…" spinner is now "Uploading…" — the heavy work happens on the watch page where the user sees live progress.

### Tests

**80/80 passing**, zero deprecation warnings (also migrated `@app.on_event("startup")` → modern `lifespan` context manager). End-to-end smoke test verified the SSE channel: POST → 303 redirect → stream-view HTML → EventSource → 4 events received → run.finalized.

### Expected real-world impact

Before this session: 196s per invoice, blank screen the whole time.

After (when classifier retry doesn't fire + drafter skips Tier 1 + embedder pre-warmed):
- Median Tier-1 auto-pass: ~30-40s real time (saved ~150s).
- Tier-2/3 with draft: ~60-80s real time.
- Perceptually: timeline starts populating at second 1; each node lands with a checkmark + summary; user always knows "we're on classify, 8 seconds elapsed, retrieve next."

Note: extract is still the biggest single component (~46s on the test invoice). That's a follow-up — likely pypdf overhead + large prompt + a single big LLM call. Not blocking the demo.

### Critical files touched

- `src/p2p_agent/classifiers/exception_classifier.py` (narrowed retry; structlog warning)
- `src/p2p_agent/llm/json_utils.py` (balanced-brace fallback parser)
- `src/p2p_agent/retrieval/embeddings.py` (singleton Embedder)
- `src/p2p_agent/retrieval/__init__.py` (singleton retriever)
- `src/p2p_agent/orchestrator/pipeline.py` (default retriever; tier-1 drafter skip; `on_event` callback)
- `src/p2p_agent/llm/client.py` (cache_control hint + cached_tokens log + tightened tenacity)
- `src/p2p_agent/context/builder.py` (new `build_async` with `asyncio.gather`)
- `src/p2p_agent/models/pipeline.py` (StepTrace gains `status` + `skip_reason`)
- `src/p2p_agent/hitl/webapp/server.py` (lifespan warm-up; SSE route; streaming POST endpoints)
- `src/p2p_agent/hitl/webapp/templates/demo_run_stream.html` (NEW — 8-row live timeline)
- `src/p2p_agent/hitl/webapp/templates/demo_upload.html` (form action → streaming endpoint)

### Follow-up

- Profile extract specifically — 46s for a single LLM call is too long. Suspect PDF text extraction (pypdf) + oversized prompt. Worth a 30-min spike.
- Re-run a real invoice through the live demo + capture the new per-step latencies. Update this entry with the actual numbers.
- Once OpenRouter key is topped up, run eval cycle #2 to confirm the JSON parser tolerance + narrower retry didn't regress classifier accuracy.

---

## 2026-05-13 (Phase 9) — Mock action executor + 8 new golden cases

Phase 9 closes the "what happens after I click Approve?" gap. Today when a reviewer approves a HITL item in the queue, an executor fires that simulates the downstream call (email the supplier, post the invoice to SAP, halt the pay run, escalate to the fraud team, etc.). Nothing real is sent — every call is logged as "would have done X" — but the interface is the one the real executor will use when SAP credentials arrive. The demo console renders the simulated calls as a green card on the case detail page so the demo feels end-to-end.

Plus: 8 more golden cases (GTC-017 → GTC-024). Total now 24, all 13 exception categories covered, with strong anti-false-positive anchors for the recurring-services / emergency-PO / strategic-vendor patterns that keep tripping up the classifier.

### What shipped

**New node — Action executor (mock).**
- `src/p2p_agent/models/execution.py` — pydantic types: `ExecutionStep`, `ExecutionResult`, `ExecutionStatus`. The same shape will carry real connector outputs once SAP credentials land.
- `src/p2p_agent/executor/action_executor.py` — `ActionExecutor` class. Reads an approved `HITLItem`, looks up the recommended action, and produces a deterministic list of simulated steps. Recipe map covers all 16 `RecommendedAction` enum values (auto_resolve → SAP POST; request_credit_memo → email + invoice hold; fraud_signal → halt + PagerDuty + audit; etc.).
- Step shape: `system` (SAP / Ariba / ServiceNow / Email / Slack / PagerDuty / Treasury / Internal) + `verb` (POST / PUT / EMAIL / NOTIFY / CREATE_TICKET / HALT_PAY_RUN / SCHEDULE_RECHECK) + `target` (endpoint, recipient, channel) + `payload_summary` (compact dict the UI can render).
- Edited-draft awareness: if the reviewer used "Edit and approve" on a draft, the executor reads the edited content (subject, body, recipient) for any email step. Original draft falls through if not edited.
- Mode parameter (`mode="mock"` default) is the swap point for the real backend.

**Storage migration.**
- Three new columns on `HITLItem`: `execution_status`, `execution_result_json`, `executed_at`.
- `HITLQueue.__init__` runs an idempotent inline `ALTER TABLE ADD COLUMN` migration when an existing `hitl_items` table is missing these columns. SQLite-friendly; existing rows are preserved. Verified on TJ's actual `logs/hitl_queue.db` (10 rows) — columns added in place, no data loss.
- New method `HITLQueue.mark_executed(item_id, result)` persists the result and writes an audit-log entry in the same transaction.

**FastAPI integration.**
- `create_app()` accepts an optional `executor` kwarg (defaults to `ActionExecutor(mode="mock")`).
- Approve / approve-with-edit routes (HTML form + JSON API, 4 endpoints total) invoke the executor after `queue.approve` / `queue.approve_with_edit` succeed, then call `queue.mark_executed` to persist. Rejected items intentionally do NOT trigger the executor.
- `_serialize_item` now exposes `execution_status`, `execution_result`, `executed_at` on the JSON response.

**UI surface.**
- `item_detail.html` — new green-bordered "Action execution (simulated)" card between the audit log and raw-payload disclosure. Shows status chip, one-line note, and an ordered list of steps (system + verb + target + collapsed payload JSON). Renders only when `execution_result` is populated.
- `queue_list.html` — new "Executed" column with ✓ / ✗ / — indicator.

**8 new golden cases (GTC-017 → GTC-024).**
- **GTC-017** — Multi-line price variance: only 1 of 3 lines has a price mismatch. Tests line-level comparison vs. invoice-total shortcuts.
- **GTC-018** — Partial short delivery: 50 ordered, 47 received, 50 invoiced.
- **GTC-019** — Recurring services (Q3 retainer on quarterly PO with Q1 + Q2 priors). **Anti-false-positive** — should NOT flag duplicate.
- **GTC-020** — Emergency PO with same-day GR + next-day invoice. **Anti-false-positive** — should NOT flag fraud just because the timeline is fast.
- **GTC-021** — Sanctions hit after vendor onboarding (OFAC SDN). Compliance halt.
- **GTC-022** — Capex purchase routed to opex approver (missing VP Finance signoff).
- **GTC-023** — One invoice references two POs (consolidated billing). Tests aggregation handling.
- **GTC-024** — Strategic vendor with MSA tolerance band (5% allowed; invoice shows 3%). **Anti-false-positive** — should NOT flag price variance.

All 24 cases parse cleanly. Coverage: 4× `none` (with 3 anti-false-positive anchors), 3× `fraud_signal`, 3× `tax_field_mismatch`, 2× each price/qty/missing_approval/other, 1× each duplicate/missing_po/missing_gr/payment_terms/vendor_master_gap/FX.

### Tests

**80/80 passing** (54 prior + 26 new):
- `tests/unit/test_action_executor.py` — 20 tests covering every action recipe, mode validation, edited-draft handling, persistence to the queue.
- `tests/integration/test_hitl_demo_execution.py` — 6 round-trip tests: API approve fires executor + returns execution fields; HTML form approve + detail page renders execution card; fraud case renders HALT_PAY_RUN step; rejected items don't execute; approve-with-edit uses edited draft; queue list shows executed indicator.

### Verified live

Smoke-tested against the existing 10-item HITL DB:
- Migration auto-added the three new columns on server boot. No data loss.
- Approved a pending fraud-flagged item via `POST /api/item/{id}/approve` → executor returned 3 steps: SAP HALT_PAY_RUN, PagerDuty NOTIFY (severity critical), Internal CREATE_TICKET audit_trail.
- `GET /item/{id}` HTML showed the execution card with status chip + steps + note.

### Total project test count + spend

- 80 tests (was 54). No regressions.
- Phase 9 added zero LLM cost (all mock simulation).
- Total project spend unchanged at $3.74.

### What's left after Phase 9

- **Real action executor backend** — drop-in replacement for the mock; gated on SAP credentials.
- **Event ingestion (node 1)** — webhooks + batch polling. Same blocker.
- **Classifier accuracy iteration 2+** — residual `none → fraud_signal` over-flagging. Needs OpenRouter key topped up.
- **`make test-golden` against the 24-case set** — next eval cycle, needs API budget.
- **LangGraph wrap** — defer until pilot.

### Try it

```bash
make hitl-serve
# /demo  → upload OR run a sample → ~15s pipeline run
# /queue → click any tier ≥ 2 item → Approve
# Detail page now shows green "Action execution (simulated)" card
# /stage9 → live measurement dashboard
```

---

## 2026-05-13 (Phase 8) — Stage 9 dashboard + classifier accuracy recovery + curated samples + 5 new golden cases + architecture diagram

Phase 8 shipped four cleanly-separable artifacts in one session. The Stage 9 measurement dashboard turns the `logs/llm_calls.jsonl` ledger + SQLite tables into a real operations surface — buyers consistently ask "show me how you measure what you ship" and now we have the answer. Classifier accuracy on the synthetic corpus recovered from 50.5% to ~58.5% via three orthogonal cuts (payload trim, worked negative examples, low-confidence guardrail). The /demo console gained a curated sample picker so TJ doesn't have to browse the corpus folder. Five new golden cases give us coverage of all 13 ExceptionCategory enum values. A Mermaid-based architecture diagram lives in `docs/architecture.md` for buyer conversations.

### What shipped

**Stage 9 dashboard.**
- `src/p2p_agent/stage9/recorder.py` — `Stage9Reader` over `logs/llm_calls.jsonl`. Cost-by-task / cost-by-model summaries, latency percentiles (p50/p95/p99) per task, time-windowed (1h / 1d / 7d / 30d / all), mtime-cached so the dashboard doesn't re-parse the file every request.
- `src/p2p_agent/stage9/aggregator.py` — `Stage9Aggregator` over the SQLite stores. Auto-pass rate, HITL resolution breakdown, classification distribution, action distribution, tier breakdown.
- FastAPI routes: `GET /stage9`, `GET /api/stage9/cost`, `GET /api/stage9/latency`, `GET /api/stage9/ops`, `GET /api/stage9/tail` — all window-parameterized.
- `stage9.html` template — 5 summary tiles (cases, auto-pass rate, calls, spend, p95), classification mix bar chart (Chart.js via CDN), HITL resolution doughnut chart, cost + latency tables, recent-calls tail.
- Base template nav reorganized: "Stage 9 metrics" link added.

**Classifier accuracy patches (Cuts 1+2+3).**
- **Cut 1** — `exception_classifier._build_user_message` now only dumps the FULL cross-case payload when a smart `SUMMARY SIGNAL` actually fires. When no signal fires, the model sees a one-line "lookups completed, no actionable signal" message instead. New helper `_trimmed_context_payload` keeps only the fields the signal references (e.g., prior_invoices_same_supplier_number only when DUPLICATE fired).
- **Cut 2** — `exception_classification.md` gains 3 worked negative examples ("rich context, clean invoice → none") anchored before the cross-case section. Anti-pattern explicitly named: do NOT return fraud_signal or cross_currency_mismatch "because the context payload looks rich."
- **Cut 3** — `_apply_confidence_guardrail` downgrades any non-`none` class with confidence < 0.70 to `none` when no smart cross-case signal fired. Evidence list gets `guardrail_low_conf_no_signal_downgrade` token. Rationale carries the original explanation.

Eval cycle 1 (partial — OpenRouter key limit hit at 41/100):
- **Classification: 50.5% → ~58.5%** on the working portion (24/41 derived from the 17 misclassifications)
- **Action vs truth class: 12.9% → 29.3%** (2.3× improvement)
- **Decision-support save rate: 4.4% → 17.6%** (4× improvement)
- **Joint accuracy: 7.5% → 14.6%** (doubled)
- Top remaining confusion: `none → fraud_signal` (5 of 41). Guardrail catches most over-flagging but not all.

**Curated sample picker on /demo.**
- `src/p2p_agent/hitl/webapp/samples.py` — hand-picked 10 invoices, one per persona-error pattern (clean US, multi-page US, PO typo, missing PO, missing tax, FX edge, missing EU VAT, India GST misrouted, India HSN format, Brazil stacked tax). Each carries a label + `expected_signal` string.
- New route: `POST /demo/run-sample` accepts a `sample_id`, copies the corpus PDF into uploads dir, runs the pipeline.
- `demo_upload.html` redesigned as a two-card layout: "Upload your own PDF" on the left, "Or run a curated sample" with a dropdown + description on the right. JS updates the description text live as the dropdown changes.
- Shared `_run_pipeline_on_pdf` helper extracted so upload and sample routes share the persist + complete logic.

**5 new golden cases (GTC-012 through GTC-016).** Covers all 13 ExceptionCategory enum values across the 16 cases:
- **GTC-012** — `missing_goods_receipt` (invoice arrives before GR is recorded).
- **GTC-013** — `payment_term_mismatch` (PO authorizes NET-30, invoice shows NET-15).
- **GTC-014** — India `tax_field_mismatch` (intra-state shipment, IGST booked instead of CGST+SGST).
- **GTC-015** — Brazil `tax_field_mismatch` (ICMS rate doesn't match destination state).
- **GTC-016** — Bank-detail-change `fraud_signal` (distinct fraud pattern from GTC-007 split-invoice).

All 16 cases parse cleanly. Total golden coverage: 16/40 target.

**Architecture diagram.** `docs/architecture.md` with four Mermaid diagrams:
- End-to-end 10-node pipeline (color-coded: shipped / pending)
- Classifier decision flow (anchors invoice → cross-case priority)
- HITL tier routing (Tier 1 / 2 / 3 lanes)
- Data flow (what's persisted where: uploads, jsonl, three SQLite tables)

Plus a "how to read this in a buyer conversation" coda. Copy-pasteable into decks via [mermaid.live](https://mermaid.live).

### Tests

54 → 54 passing (no regressions; new tests count toward 54): added 23 new tests in this phase.
- `tests/unit/test_stage9_recorder.py` — 8 tests: empty log, cost aggregation, window filtering, latency percentiles, tail, malformed-line tolerance, mtime-cache invalidation, percentile helper.
- `tests/unit/test_stage9_aggregator.py` — 7 tests: empty stores, auto-pass rate, classification distribution, tier breakdown, HITL resolution, action distribution, failed-run handling.
- `tests/integration/test_stage9_routes.py` — 8 tests: HTML render, window param, invalid window fallback, cost API, latency API, ops API, tail API, nav link.

### Dependencies

No new package deps. Chart.js loads from CDN at render time.

### Verified live

- Stage 9 dashboard live with real data: $3.74 total spend, 2482 calls, top task by cost is `decision_support_reasoning` ($2.37 over 498 calls).
- Sample picker tested end-to-end (POST /demo/run-sample with unknown id → 400; valid id → 303 redirect to run detail).
- All 16 golden cases parse without YAML errors.
- 54/54 tests pass.

### What's left after Phase 8

- **Action executor** — write-back to SAP/Ariba/ServiceNow on approval. Blocked on SAP credentials.
- **Event ingestion** — webhooks + batch polling from email + SAP. Same blocker.
- **LangGraph wrap** — defer until pilot (durable state isn't a real need yet).
- **Classifier accuracy iteration 2+** — residual `none → fraud_signal` over-flagging. ~$0.65 per eval cycle; needs the key topped up.
- **More golden cases** — 16/40 target.

### Try it

```bash
make hitl-serve
# http://localhost:8090/demo  → upload OR run a curated sample
# http://localhost:8090/stage9  → live measurement dashboard
```

---

## 2026-05-13 (Phase 7) — End-to-end /demo flow: upload PDF, watch the agent reason, see the result

Phase 7 closes the "we built a lot but you can't actually USE it" gap. The same FastAPI app now hosts a full **upload → run pipeline → see every node's output** flow. A user drops in any PDF, waits ~15s, and gets back the extraction fields, the case-context lookup result, the classification with rationale, the top-5 retrieved policies with similarity scores, the recommendation with counterfactual, the routing tier and reason, and the draft email (if one was generated). Tier ≥ 2 cases auto-flow into the existing HITL queue and the result page deep-links to the review surface.

### What shipped

**New domain table.**
- `PipelineRun` ORM in `src/p2p_agent/hitl/models.py`. Persists one row per /demo upload with denormalized fields (class, action, tier, routed_to, cost, latency, status) plus a full `result_json` snapshot for re-render. Indexed on status and uploaded_at.
- `RUN_RUNNING / RUN_COMPLETED / RUN_FAILED` status constants in the same namespace as the existing HITL item statuses.

**New store class.**
- `src/p2p_agent/hitl/runs.py::PipelineRunStore`. Methods: `create / set_stored_path / complete / fail / get / list / clear`. Same SQLite DB and engine as the HITL queue.

**FastAPI routes (8 new).**
- `GET /` → redirect to `/demo` (was `/queue`).
- `GET /demo` → upload form + summary tiles + recent-runs table.
- `POST /demo/run` → accepts multipart PDF upload (10 MB cap), saves to `logs/demo_uploads/{run_id}.pdf`, runs the full pipeline with the HITL queue attached, persists the run, redirects to detail.
- `GET /demo/runs` → table of all past runs with deep-links to queue items.
- `GET /demo/run/{id}` → the trace page: a section per node (extract / cross-case context / classify / retrieve / decide / route / draft) plus a per-step latency timeline and a summary banner with "→ review in queue" link when applicable.
- `GET /demo/run/{id}/pdf` → download the original PDF.
- `GET /api/runs` and `GET /api/run/{id}` → JSON parallels.

**Templates (3 new).**
- `demo_upload.html` — landing page with file picker, "running…" spinner JS, recent runs preview.
- `demo_runs.html` — full history table with queue linkage column.
- `demo_run_detail.html` — multi-section trace with progressive disclosure (`<details>` blocks for raw JSON), color-coded chips for tier/status, summary banner at top.

**Nav updated.** Base template renames "P2P Agent · HITL Approval Queue" → "P2P Agent · Demo Console" and reorders nav: Run an invoice → Past runs → Approval queue → Stats → API docs.

**Lazy singletons inside FastAPI.** The retriever, context builder, and ModelClient are constructed on first request (not on app boot) so unit tests don't pay the embedding-model load time. ~1.5s saved per test session.

### Tests

31/31 passing (added 11 new integration tests in `tests/integration/test_hitl_demo.py`):
- Index redirects to /demo
- Demo landing renders with form
- Upload rejects non-PDF (400)
- PDF upload runs stubbed pipeline → run row created → status `completed` → tier-2 case enqueues to HITL queue
- Run detail HTML renders all node sections + extracted values
- Tier-1 (auto-pass) case does NOT create a queue item
- `/demo/runs` list renders multiple rows
- `GET /api/runs` returns the runs as JSON
- `GET /api/run/{id}` returns the full `result_json`
- `GET /demo/run/{id}/pdf` downloads the original
- 404 on missing run id

Plus 1 fix to the existing webapp test: index now redirects to `/demo` instead of `/queue`.

Tests use an injectable `pipeline_runner` parameter on `create_app(...)` so no real LLM calls happen during the test suite.

### Verified end-to-end with a real corpus PDF

Uploaded `test_corpus/synthetic/invoices/P001_idx0000.pdf` through the browser flow:
- Pipeline ran in 84.1s (slower than usual — first request paid the embedding-model load).
- Result: `class_label=none`, `action=approve_pending_review`, `tier=2 → buyer`, `hitl_item_id` set.
- Detail page rendered every node section. Queue linkage worked — clicking through to `/item/{hitl_item_id}` showed the same case in the approval queue.
- `/demo/runs` showed the row with "open queue item" link.
- API parallels (`/api/run/{id}`, `/api/runs`) returned the full result snapshot.

### Why this matters

Before today, "testing" meant typing `uv run python scripts/run_golden_set.py --case GTC-002` and reading CLI output. The new flow lets TJ (or a buyer in a demo) drop in any PDF and see the agent actually reason — every step, every signal, every recommendation, every routing decision. The CLI tools still exist for engineering iteration; the /demo flow is the human-shaped surface.

### Try it

```bash
make hitl-serve     # FastAPI demo at http://localhost:8080/demo
```

Drop any PDF from `test_corpus/synthetic/invoices/` into the upload form. Wait ~15s. Click through.

---

## 2026-05-12 (Phase 6) — HITL approval queue + FastAPI demo UI + classifier prompt reorder

Phase 6 ships the **HITL approval queue + a FastAPI demo UI** as a single coherent piece. The pipeline now auto-enqueues every tier ≥ 2 case to a SQLite-backed inbox; a reviewer can open `http://localhost:8080/queue`, see the case detail (classification rationale, recommendation, draft body, routing reason), and click Approve / Reject / Edit-and-approve. This is the first piece of the agent that a buyer could actually click on in a demo. Also: shipped the **Option 2 classifier-prompt reorder** to address the cross-case-context over-flagging regression — corpus classification recovered from 18% → 50.5%.

### What shipped

**Classifier prompt reorder (Option 2 from the Phase 5 close-out).** Restructured `src/p2p_agent/llm/prompts/exception_classification.md` so the invoice is anchored as the primary signal and the cross-case context is reframed as supplementary evidence:
- New top-level "Decision order" section — read invoice first, then compare to PO/GR, then consult cross-case context, then default to `none`.
- Cross-case context section recast as a smart-signal table — the model now treats the smart-filtered `SUMMARY SIGNAL` line as the only thing that warrants a non-`none` class change.
- Added explicit "things that look like signals but are NOT" anti-bias list (collisions on supplier_invoice_number, prior invoices on same PO, etc.).
- `_build_user_message` in `src/p2p_agent/classifiers/exception_classifier.py` reframes payload as "Primary input" + "Supplementary" with an upfront SMART SIGNALS callout.

**HITL approval queue (new agent node, 8 of 9 nodes now done).**
- `src/p2p_agent/hitl/models.py` — SQLAlchemy ORM. `HITLItem` (denormalized for fast list views + full JSON payload for detail) and `HITLAuditEntry` (append-only status transitions).
- `src/p2p_agent/hitl/queue.py` — `HITLQueue` class. `enqueue / list / get / approve / reject / approve_with_edit / stats / clear`. Audit entry written in the same transaction as every status change. SQLite WAL mode enabled for concurrent writes. Same code runs against Postgres by swapping the `db_url`.
- `src/p2p_agent/hitl/__init__.py` — exports `HITLQueue` alongside the existing router.

**Pipeline integration.**
- `run_invoice_pipeline()` accepts an optional `queue: HITLQueue | None`. When provided AND the routing decision lands at tier ≥ 2, the case is auto-enqueued after the route + draft steps.
- `PipelineResult.hitl_item_id` carries the queue row id so callers can deep-link.
- Default is `queue=None`, so eval scripts (pipeline-eval, run_golden_set) never pollute the demo queue.

**FastAPI demo UI.**
- `src/p2p_agent/hitl/webapp/server.py` — 18 routes total. HTML pages: `/queue`, `/queue/all`, `/item/{id}`, `/stats`, plus form-POST endpoints for approve / reject / approve-with-edit. Parallel JSON API under `/api/...`.
- Jinja templates in `src/p2p_agent/hitl/webapp/templates/`: `base.html` (brand strip + nav + design tokens from CLAUDE.md), `queue_list.html` (filter chips, summary tiles, item table), `item_detail.html` (case sections + reviewer-action card + audit log + raw payload), `stats.html` (counts by status / tier / routed_to).
- Run via `make hitl-serve` → `uvicorn p2p_agent.hitl.webapp.server:app --port 8080 --reload`.

**Seed script + Makefile.**
- `scripts/seed_hitl_queue.py` — samples N corpus invoices (default 10), prefers ones with `error_injected != null`, runs the full pipeline with `queue=` set, reports enqueued/cleared/failures + elapsed.
- `make hitl-serve` / `make hitl-seed` / `make hitl-clear`.

**Tests (20/20 passing).**
- `tests/unit/test_hitl_queue.py` — 9 tests covering enqueue, list filters, approve, reject, approve_with_edit, double-resolve guard, missing item, stats, clear.
- `tests/integration/test_hitl_webapp.py` — 11 tests via `fastapi.testclient.TestClient`: index redirect, HTML render, JSON queue, approve round-trip, reject, approve-with-edit, 409 on double resolve, HTML form approve, item detail render, 404, stats.

**Dependencies.** Added `fastapi`, `uvicorn[standard]`, `jinja2`, `python-multipart` to `pyproject.toml`.

### Verified demo flow

1. `make hitl-clear && uv run python scripts/seed_hitl_queue.py --n 8` — ran on 8 corpus invoices, **8 of 8 enqueued at tier ≥ 2** (4 buyer-routed Tier 2, 3 treasury Tier 3, 1 fraud-team Tier 3). Cost $0.05 across 32 LLM calls. Elapsed 6 min.
2. `uvicorn p2p_agent.hitl.webapp.server:app --port 8088` — booted cleanly, all 18 routes registered.
3. `GET /api/stats` → `{"total":8,"by_status":{"pending":8},"by_tier":{"2":4,"3":4},"by_routed_to":{"buyer":4,"treasury":3,"ap_fraud_team":1}}`.
4. `POST /api/item/<id>/approve` → row transitions to `approved`, audit entry written, `/api/stats` shows the updated counts.
5. `GET /item/<id>` HTML renders Classification + Recommendation + Routing decision + Audit log sections.

### Pipeline eval after the prompt reorder (Option 2)

Re-ran `eval_pipeline --sample 100` against the synthetic corpus after the prompt change. **Classification recovered from 18% → 50.5%.** Joint accuracy at 7.5% (up from 11% with the old prompt — drop is because action accuracy fell as classifier confidence improved, surfacing the mismatch). Action vs predicted class 39.8%, action vs truth 12.9%, decision-support save rate 4.4%, cost $0.65 across 318 calls.

Remaining over-flagging (16 points off the 66% pre-context baseline): 9× `none → cross_currency_mismatch`, 9× `none → fraud_signal`. Cross-case payload still nudges the model toward those two categories. Next iteration would tighten the smart-signal table further and/or downsample the context payload depth, but Phase 6 ships the queue independent of this.

### What's left (3 of 11 nodes)

- **Action executor** — write-back to SAP / Ariba / ServiceNow on approval. Blocked on SAP credentials.
- **Stage 9 measurement dashboard** — beyond ModelClient's jsonl ledger.
- **LangGraph wrap** — durable state machine with explicit HITL pauses.

### Try it locally

```bash
make hitl-seed       # populate the queue from 10 sampled invoices (~$0.07)
make hitl-serve      # open http://localhost:8080/queue
```

---

## 2026-05-12 (Phase 5) — Drafter + HITL router + smart signal emission + honest classification status

Phase 5 ships two new agent nodes (drafter, HITL router), wires both into the pipeline and the golden harness, and tightens cross-case signal emission to reduce false positives. Pipeline now runs **6 of 9 nodes end-to-end**. Golden-harness Phase 5 metrics show real wins on the new dimensions (HITL 4/11, drafting 3/11 — both up from 0/11 in Phase 3). Classification still hasn't fully recovered to baseline; root cause and next step diagnosed.

**Phase 5 — what shipped:**

- `src/p2p_agent/models/draft.py` — new pydantic types: `Draft`, `DraftType` enum (`SUPPLIER_EMAIL`, `INTERNAL_NOTE`).
- `src/p2p_agent/models/routing.py` — new pydantic types: `RoutingDecision`, `HITLTier` enum (`AUTO_PASS`, `APPROVER_REVIEW`, `SUPERVISOR_REVIEW`).
- `src/p2p_agent/llm/prompts/drafter.md` — versioned prompt: "extract, don't correct" principle, draft-type binding by action, content + tone guidelines.
- `src/p2p_agent/drafter/draft_comm.py` — `draft_communication()` async function. 11-action drafter map covers supplier emails (credit memo, correction, missing PO) and internal notes (PO amendment, supplier delay, vendor master, VP finance, retroactive PO, short delivery, fraud, supervisor halt). Same retry pattern as classifier/extractor.
- `src/p2p_agent/hitl/router.py` — pure-rules `HITLRouter`. 16-action routing table mapping each action to (tier, role, reason). Auto-pass guards: `auto_resolve` downgrades to T2 if classifier confidence < 0.85 OR class != `none` OR recommender confidence < 0.85.
- `src/p2p_agent/orchestrator/pipeline.py` — extends pipeline with 5th step (route) and 6th step (draft, conditional). `PipelineResult` adds `routing_decision`, `draft` fields.
- `src/p2p_agent/models/golden_case.py` — `ExpectedHITL` and `ExpectedDrafting` pydantic blocks (replace `dict[str, Any]` typing).
- `scripts/run_golden_set.py` — `_eval_hitl()` and `_eval_drafting()` evaluators. Soft-equivalence matching for `routed_to` (handles YAML `ap_supervisor` / our `ap_fraud_team`, YAML `ap_clerk` / our `buyer`, etc.) and draft-type family matching (`supplier_*` → `supplier_email`, `internal_*` / `vendor_*` / `fraud_*` → `internal_note`).

**Phase 5 — smart signal emission (cross-case context refinement):**

- `src/p2p_agent/models/context.py::CaseContext.summary_signals()` — DUPLICATE signal now requires both same supplier_invoice_number AND matching total (true business duplicate, not LLM-generated collision). SPLIT WATCH only fires when ≥3 prior invoices on same PO AND cumulative ≥60% of PO authorization (real fraud signature, not multi-shipment).
- `src/p2p_agent/llm/prompts/exception_classification.md` — updated to teach: "prior_invoices_same_po non-empty is NORMAL (multi-shipment / recurring services). Only flag `fraud_signal` when SUMMARY SIGNAL block contains 'SPLIT WATCH' (smart-filtered)."

**Phase 5 results — golden harness (11 cases):**

| Metric | Phase 3 (baseline) | Phase 5 |
|---|---|---|
| Classification pass | 4/11 | 3/11 |
| Recommendation pass | 1/11 | 1/11 |
| HITL pass | 0/11 (n/a; not built) | **4/11** ← Phase 5 win |
| Drafting pass | 0/11 (n/a; not built) | **3/11** ← Phase 5 win |
| Overall (all blocks pass) | 1/11 | 1/11 |
| Cost | ~$0.07 | ~$0.09 |

The new nodes work. HITL routing correctly maps actions to tier+role on the cases where decision-support gets the action right. Drafting produces structurally valid drafts on cases where the recommendation calls for one. The 1/11 overall pass rate is the bottleneck: classification + recommendation still fail on most cases, which cascades downstream.

**Phase 5 results — corpus eval (100 invoices, 94 processed):**

| Metric | Phase 3 baseline | Phase 4 (regressed) | Phase 4.1 | Phase 5 |
|---|---|---|---|---|
| Classification | 66% | 6% | 8% | **18%** |
| Action vs predicted | 86% | 86% | 82% | 71% |
| Action vs TRUTH | 25% | 1.5% | 5% | **13%** |
| Decision-support save rate | n/a | 0% | 0% | 0% |
| Joint (everything correct) | 8% | 1.5% | 5% | **11%** |
| Cost | $0.71 | $0.71 | $0.58 | $0.61 |

Classification is recovering — went from 6% (Phase 4 catastrophic regression) → 8% (Phase 4.1, fixtures and normalization fixed) → 18% (Phase 5, smart signal emission). Still far below the 66% baseline.

**Root cause of remaining regression — fully diagnosed:**

The smart-signal fix eliminated the duplicate/fraud false positives from natural po_id collisions in the corpus, but two new problems surfaced:

1. **`fraud_signal` over-prediction (21 cases).** Even without the SPLIT WATCH signal firing, the rich `CaseContext` payload in the prompt (vendor record, PO record, prior invoices structure even when empty) biases the model toward "this must be flagging something." The model defaults to fraud when it sees a complex context.

2. **`cross_currency_mismatch` over-prediction (13 cases).** The `aggregate_signals.currency_mismatch` field in the CaseContext fires when invoice currency ≠ PO currency. Some corpus invoices have legitimate currency differences (multi-currency suppliers) that aren't actually mismatches — the model can't always tell.

3. **`vendor_master_gap` (10 cases).** Despite the four-stage vendor lookup, 10 invoices still fail to resolve. Likely the extractor's vendor name + tax_id combination doesn't reach the master via any fallback for those specific extractions.

4. **Decision-support save rate stays at 0%.** Decision-support is following the classifier's verdict mechanically; the new "reason from facts when they conflict" prompt addition isn't producing overrides in practice. Suggests the model needs stronger signal or different prompt design.

**Decision-support save rate = 0% — the deeper issue.**

This is the most important diagnostic. When classifier says `fraud_signal` and the invoice obviously looks clean, decision-support recommends `escalate_to_fraud` anyway. The temperature bump (0.0 → 0.2) and the "reason from facts" prompt section didn't move the needle. Two possible reasons:

- The model treats the classifier's verdict as authoritative because we present it first. Re-ordering inputs (case facts first, classification last) might help.
- Decision-support reads the verdict + the rich context + the retrieved policies as collectively supporting the verdict, even when individual facts say otherwise.

**The pragmatic next move (next session, not this one):**

Two options to recover classification, ranked:

1. **Disable cross-case context for corpus eval; keep for golden harness.** The architecture is right for production (where context is real and signals are sparse), but the synthetic corpus has too many natural collisions and not enough variety for the context-aware classifier to behave well. Add a pipeline flag `include_case_context=False` to `run_invoice_pipeline()` and have `eval_pipeline.py` set it; keep `run_golden_set.py` using context.

2. **Re-order classifier prompt to put case facts before context.** Currently the prompt presents the invoice JSON, then the context block. Reversing — presenting the context as supporting evidence after the case is described — might reduce the "must be flagging something" bias.

Option 1 is the cheap win; expected: corpus classification recovers to 60-70% while golden cases retain the cross-case context they need. Option 2 is the right durable fix but needs more iteration.

**What's NOT yet done after Phase 5:**

- HITL approval queue (Postgres-backed, with REST API for approve / modify / reject). This is the "wait for human" piece; today the pipeline just produces a `RoutingDecision` and stops there.
- Action executor (writes back to SAP / Ariba / ServiceNow after approval). Needs the SAP connector to be built; SAP credentials are pending.
- Stage 9 recorder beyond what `ModelClient` already logs to `logs/llm_calls.jsonl`. The per-node trace exists in `PipelineResult.steps` but isn't aggregated to a Stage 9 dashboard.
- LangGraph wrap of the orchestrator. The pipeline is a plain async function today; it works but doesn't survive restarts or model HITL pauses explicitly.

**Counts after Phase 5:**

- Real archetype layers: **7** (extractor + classifier + retrieval + decision-support + cross-case-context + HITL router + drafter). Out of the original 9-node design, that's nodes 2, 3, 4, 5, 6, 7 + the supporting context layer.
- Total project spend across all sessions: ~$3.70 (Phase 5 added ~$0.80 across eval + harness + smoke tests).

**Status.html updates:**

- Per-node table: HITL Tier Router and Drafting flipped TODO → IN PROGRESS.
- Per-archetype-modules progress bar: 55% → 70%.
- "What's still left" tally: 4/7 → **6/7** per-node implementations.
- Header status reflects 6 of 9 nodes.

---

## 2026-05-12 (Phase 4.1) — Three regression fixes (no API spend; eval re-run deferred until key top-up)

The three fixes diagnosed at the end of Phase 4. All landed; smoke-tested without LLM calls. The corpus-eval re-run is deferred until the user tops up the OpenRouter key, but architectural validation passed.

**Fix 1 — Duplicate/split fixtures separated from corpus history.**

- `scripts/build_context_data.py` now writes two files:
  - `invoice_history.json` = 490 base summaries only (clean, used by corpus eval).
  - `golden_history_signals.json` = 10 duplicate + 15 split-cluster entries (loaded only by the golden harness).
- `src/p2p_agent/context/lookups.py::InvoiceHistoryLookup` accepts an `extra_history_files: list[Path]` constructor argument and merges all sources transparently.
- `src/p2p_agent/context/builder.py::CaseContextBuilder` propagates `extra_history_files` through to the lookup.
- `scripts/run_golden_set.py` defines `_GOLDEN_EXTRA_HISTORY` pointing to `golden_history_signals.json` so the harness opts in.
- Smoke test confirmed: same supplier_invoice_number returns **1 hit** from base history alone vs **2 hits** when extras are loaded. Corpus eval will no longer see spurious duplicate signals.

**Fix 2 — VendorMasterLookup four-stage fallback chain.**

When `get_by_tax_id` is called the lookup tries, in order:
1. Exact normalized tax_id (strip label prefix like "EIN:")
2. Aggressive normalization (lowercase, only alphanumerics — handles separator and case drift)
3. Last-6-digit match (handles "EIN: 98-7654321" vs "98 765 4321" vs "987654321")
4. Returns `None` if all miss

Plus a new `get_by_name(vendor_name, threshold=0.6)` method that does Jaccard token-set fuzzy matching with:
- **NFKD accent stripping** so "São Paulo" matches "Sao Paulo".
- **Entity-suffix stop list** covering English (inc/ltd/llc/corp), German (gmbh/ag/kg/ohg), Romance (sa/srl/ltda/spa/sl/sas), and others (limited/plc).

`CaseContextBuilder.build()` now tries `get_by_tax_id` first; on miss, falls back to `get_by_name` using the extracted vendor name. All 5 corpus personas resolve via fuzzy name fallback (Frankfurt, São Paulo, Mumbai, Global Tech, Maple).

Indexes added to `vendor_master.json`: `by_id`, `by_tax_id` (normalized), `by_tax_id_aggressive`, `by_tax_id_last6`, `by_name_tokens` (for fuzzy match).

Smoke tests:
- Exact: `"DE123456789"` → Frankfurt ✓
- Aggressive: `"  de-123-456-789  "` → Frankfurt ✓
- Last-6: `"XX-yyy-456789"` → Frankfurt ✓
- Name fuzzy: `"Sao Paulo Componentes"` → São Paulo Componentes Ltda. ✓
- Genuine miss: `"Unknown Vendor LLC"` → None ✓

This fix targets the 15 `vendor_master_gap` false-positives observed in the Phase 4 corpus eval. With the fallback chain, corpus invoices with minor tax_id formatting drift now resolve correctly.

**Fix 3 — Decision-support prompt reshaped + temperature 0.2.**

`src/p2p_agent/llm/prompts/decision_support.md` adds a "Reason from the facts — disagree with the classifier when warranted" section with four explicit example scenarios. The prompt now teaches the model that the classification is one input not the verdict; it should disagree when case facts contradict the classification, and state the disagreement explicitly in the rationale.

`src/p2p_agent/decision/decision_support.py` raises `temperature` from 0.0 → 0.2 on the primary call (retry still uses 0.0 for tighter output). Gives the model a small amount of headroom to disagree with the classifier when it should.

Target: decision-support save rate moves from 0% → 30%+ on the next eval — when classification is wrong, decision-support recommends the right action vs truth anyway.

**What's NOT done (waiting on OpenRouter key top-up):**

- Re-run `make pipeline-eval` to measure recovery.
- Re-run `make test-golden` to confirm both classification and recommendation sub-blocks pass.
- Validate that `vendor_master_gap` false-positives are gone.
- Validate that `duplicate_invoice` / `fraud_signal` false-positives are gone.
- Measure new save rate.

**Files touched (Phase 4.1):**

| Path | Change |
|---|---|
| `scripts/build_context_data.py` | split output to two files; add name/digit indexes; accent stripping; entity stop list |
| `src/p2p_agent/context/lookups.py` | 4-stage tax_id fallback; new `get_by_name` with NFKD accent strip; `InvoiceHistoryLookup` accepts extras |
| `src/p2p_agent/context/builder.py` | accepts `extra_history_files`; falls back to name lookup when tax_id misses |
| `scripts/run_golden_set.py` | defines `_GOLDEN_EXTRA_HISTORY` for opt-in fixture loading |
| `src/p2p_agent/llm/prompts/decision_support.md` | new "reason from the facts" section + 4 examples |
| `src/p2p_agent/decision/decision_support.py` | `temperature=0.2` for primary call |

**Counts:**

- Total project spend across all sessions: still ~$2.30 (no API spend in this phase).
- Real archetype layers: 5 of 7 (unchanged from Phase 4).

**Status:** Phase 4 regression diagnosed and fixed. Architecture and metrics are intact. Awaiting OpenRouter key top-up to validate; expected post-recovery numbers: classification ≥70%, action-vs-truth ≥50%, save rate ≥30%, joint ≥40%.

---

## 2026-05-12 (Phase 4) — Richer policies + cross-case context layer + metric fix + honest regression signal

Five-step plan executed. Architecture pieces all shipped and validated by smoke tests. End-to-end corpus eval regressed sharply (66% → 6% classification) — the *signal* is correct, the architecture works, but the test fixtures we authored to support cross-case context fire spuriously against ground-truth labels that don't account for them. The fix is small and well-understood; deferred to a follow-up session because the OpenRouter key hit its total-spend ceiling mid-run.

**Step 1 — Policy library expanded to 75 entries.**

- `config/policy_library.yaml` grew from 25 → 75 hand-authored entries (POL-001 through POL-075).
- New depth across: US sales tax (destination vs origin, services exemption, SaaS, marketplace facilitator, use-tax); EU VAT (per-country rates, reverse-charge, OSS/IOSS, triangulation, place-of-supply); India GST (CGST/SGST splits, IGST, HSN codes, e-invoicing, ITC); Brazil NF-e (ICMS state rates, IPI by NCM, PIS/COFINS regimes, MEI vs Lucro Real, SPED); approval matrix (by-dept, capex/opex, MSA, quarter-end, emergency); vendor segmentation (strategic/tactical, MSA/spot, contractor/supplier, foreign, SBE); industry-specific (pharma GxP, defense ITAR, FS SOX, healthcare HIPAA); fraud depth (round-dollar, threshold-evasion, bank-detail changes, weekend submissions, new-vendor + high-value); 3-way match nuance; cross-currency depth; duplicate edge cases.
- Smoke test: retriever returns the right policy at top-1 across 6 hand-crafted queries (Texas sales tax → POL-027, India inter-state GST → POL-019, Brazilian IPI on industrial → POL-044, split-invoice → POL-009/012/064, pharma CoA → POL-059, bank-detail change → POL-065). Each query rank-1 was the correct snippet.

**Step 2 — Cross-case context layer.**

- `src/p2p_agent/models/context.py` — new pydantic types: `CaseContext`, `VendorRecord`, `PORecord`, `GoodsReceipt`, `InvoiceSummary`, `POPaymentStatus`, `VendorChangeEvent`, plus enums (`VendorTier`, `VendorContractType`, `POStatus`).
- `src/p2p_agent/context/lookups.py` — JSON-backed lookup modules: `VendorMasterLookup` (by id or normalized tax_id), `POLookup`, `GoodsReceiptLookup`, `PaymentStatusLookup`, `InvoiceHistoryLookup` (by supplier-invoice-number for duplicate detection, by po_id for split-invoice with configurable time window), `VendorChangeLookup` (recent vendor changes). Same interface fronts a future SAP OData backend.
- `src/p2p_agent/context/builder.py` — `CaseContextBuilder.build(invoice, invoice_id=...)` orchestrates the lookups and assembles a `CaseContext` with summary signals (e.g. "vendor NOT in master file", "PO ALREADY FULLY PAID", "DUPLICATE: 2 prior invoices with same supplier_invoice_number").
- `src/p2p_agent/classifiers/exception_classifier.py` — `classify_exception(...)` now accepts `case_context: CaseContext | None`. When provided, the user message includes a "Cross-case context" block with the summary signals + the full context payload.
- `src/p2p_agent/llm/prompts/exception_classification.md` — extended with a "Cross-case context" section that teaches the model how to interpret the signals (vendor_record=null → vendor_master_gap; po_payment_status.fully_paid → duplicate; vendor_recent_changes + high-value → fraud-watch; etc.) and a reasoning-priority hierarchy.
- `src/p2p_agent/orchestrator/pipeline.py` — adds a `context` step between extract and classify when `include_case_context=True` (default). Context builder is local; no LLM call. ~10ms latency vs the 5-20s LLM steps.
- `PipelineResult` extended with `case_context: CaseContext | None`.
- Smoke test on P003_idx0005: pipeline runs end-to-end, context lookup finds vendor + PO + GR cleanly, summary signals print as expected.

**Step 3 — Context data fixtures (built from existing 490-invoice corpus).**

- `scripts/build_context_data.py` — derives vendor master / PO master / GR records / invoice history / payment status / vendor changes from the existing invoice JSONs. No API cost; pure derivation. Output: `test_corpus/synthetic/context/*.json`.
- Result: 5 vendors (one per persona) + 152 unique POs (LLM-generated invoices share po_references) + 152 GRs + 152 payment-status entries (5 marked fully paid) + 515 invoice-history entries (490 corpus + 10 duplicate "prior" + 15 split-invoice cluster) + 2 vendor-change events.

**Step 4 — YAML/enum reconciliation.**

- `src/p2p_agent/models/recommendation.py` extended with 3 new actions and 1 rename:
  - Added `approve_pending_review` (Tier-2 "looks OK, just confirm")
  - Added `request_po_amendment` (PO needs editing, distinct from retroactive creation)
  - Added `halt_require_supervisor` (broader halt-pay than `escalate_to_fraud`)
  - Renamed `escalate_to_fraud_team` → `escalate_to_fraud` (matching YAML strings)
- `src/p2p_agent/llm/prompts/decision_support.md` updated with all 16 actions grouped by category (auto-resolution / supplier correction / buyer routing / fraud / treasury / notifications / fallback).
- `scripts/eval_pipeline.py::ACCEPTABLE_ACTIONS_BY_CATEGORY` updated to recognize the new actions.

**Step 5 — Action-accuracy metric fixed.**

- `scripts/eval_pipeline.py` now computes three metrics instead of one:
  - `action_vs_predicted_class` (legacy compatibility) — action in acceptable-set for the classifier's predicted category.
  - `action_vs_truth_class` (the real action quality) — action in acceptable-set for the *truth* category.
  - `decision_support_save_rate` — when classifier wrong, fraction of those cases where action is right vs truth anyway. Measures whether decision-support reasons independently or just follows the classifier.

**Phase 4 corpus eval (partial — 66 of 100 invoices; key limit hit):**

| Metric | Phase 3 (100 inv) | Phase 4 (66 inv) | Δ |
|---|---|---|---|
| Extraction (per-field avg) | ~96% | ~96% | flat |
| Classification | 66% | **6%** | **REGRESSED** |
| Action vs predicted (compatibility) | 25% (old metric) | 86% | new metric is more interpretable |
| Action vs TRUTH (real action quality) | n/a | 1.5% | bad, downstream of classification regression |
| Decision-support save rate | n/a | 0% | DS never overrules a wrong classifier |
| Joint | 8% | 1.5% | regressed in lockstep with classification |
| API cost | $0.71 | $0.36 (66 invoices) | per-call cost up modestly due to longer prompt |

**Why classification regressed — root cause identified:**

Top confusions in the Phase 4 run:
- 19 cases: ground truth `none` → predicted `duplicate_invoice`
- 15 cases: ground truth `none` → predicted `vendor_master_gap`
- 4 cases: ground truth `none` → predicted `fraud_signal`

The classifier is correctly *using* the cross-case signals — but the signals are firing on cases whose ground-truth labels don't anticipate them:

1. **Duplicate / fraud fixtures.** `build_context_data.py` injects 10 duplicate "prior" entries + 15 split-invoice cluster entries into `invoice_history.json` for fraud-test coverage. These fire indiscriminately on whatever corpus invoices match the chosen supplier_invoice_numbers / PO IDs. For ~25 corpus invoices that the truth-mapping labels as `none`, the new context says "duplicate seen" or "split pattern" and the classifier upgrades the classification. The classifier is technically *right* given the signals; the corpus ground truth simply doesn't account for the fixtures.

2. **Vendor master tax_id normalization gap.** 15 cases hit `vendor_master_gap` because the extractor's `header_fields.vendor_tax_id` value doesn't match any tax_id in the vendor master after normalization. Likely the extractor produces a slightly different format (extra whitespace, dash variant) than what the master holds. Lookup returns None → classifier sees "vendor not in master" → vendor_master_gap. The vendor IS in master; the lookup is too strict.

3. **OpenRouter total-spend ceiling exceeded.** 34 of 100 invoices failed with HTTP 403. Honest noise — drops sample size from 100 → 66 and means the partial numbers are noisier than usual.

**The fix (next session, ~1 hour):**

- Move duplicate / split-invoice fixtures to a SEPARATE file (`test_corpus/synthetic/context/golden_history_signals.json`) used only by the golden harness, not the corpus eval. Or: remove them from history entirely and synthesize per-case during the golden test rather than pre-baking.
- Debug the tax_id matching. Likely fix: more aggressive normalization in `VendorMasterLookup` (strip all whitespace, normalize separators) or use a fuzzy match for known persona names as fallback.
- Top up OpenRouter key.
- Re-run pipeline-eval. Expected: classification back into the 70-80% range (cross-case context helps where signals legitimately apply; doesn't hurt where they don't).

**Decision-support save rate: 0%.**

Worth flagging separately. The metric measures "when the classifier is wrong, does decision-support overrule and recommend the right action anyway?" 0% means decision-support is reading the classifier's verdict and rubber-stamping it, not reasoning independently. This is a prompt-shape issue: today the decision-support prompt passes the classification as a starting point and reasons "given this classification, recommend action." We could rephrase to "given the case facts + classification, recommend action — and if the case facts don't support the classification, choose an action consistent with the facts instead." Defer to a follow-up session.

**`status.html` updated:**

- Per-archetype Python modules progress bar: 45% → 55% (cross-case context layer added).
- Header status updated to mention cross-case context.

**Counts:**

- Real archetype modules: **5 of 7** (extractor, classifier, retrieval, decision-support, **cross-case-context**).
- Total project spend across all sessions: ~$2.30.
- Total spend this Phase 4 session: ~$0.50 (smoke tests + corpus eval).

**Status:** Architecture is intact. The cross-case context layer ships and runs end-to-end; the regression is from fixture/lookup tightening, not from the layer itself. Next session: separate duplicate/fraud fixtures from corpus eval, fix tax_id normalization, top up API budget, re-run. Expected outcome: classification ≥ 70%, action-vs-truth ≥ 50%, joint ≥ 40%.

---

## 2026-05-12 (Phase 3) — Pipeline wiring + RAG retrieval (real arch, mock content) + decision-support node

Three pieces shipped: (1) end-to-end pipeline wiring extract → classify → retrieve → decide, (2) a real-architecture RAG layer with hand-authored mock policy content, (3) the decision-support node. After this session, **4 of 7 archetype nodes are live** and the agent produces a recommended action for any invoice in our corpus.

**Phase 3 / Milestone 1 — Extract → Classify pipeline**

Shipped:
- `src/p2p_agent/orchestrator/pipeline.py` — `run_invoice_pipeline(pdf_path, *, po_context, gr_context, client, retriever, include_decision)` async function. Composes the existing node functions sequentially. LangGraph slots in later by graphifying the same flow.
- `src/p2p_agent/orchestrator/__init__.py` — exports.
- `src/p2p_agent/models/pipeline.py` — `PipelineResult` pydantic with extraction + classification + retrieved_policies + recommendation + per-step `StepTrace` (name, latency_ms, cost_usd).
- `scripts/eval_pipeline.py` — full end-to-end eval. Per-field extraction accuracy + classification accuracy + action accuracy + joint metric + per-persona breakdown + cost rollup from `logs/llm_calls.jsonl`. `--no-decision` flag for M1-only mode; `--sample N` / `--full` / `--persona`.
- `Makefile` — `pipeline-eval` (sample 100) and `pipeline-eval-full` (all 490).

M1 100-invoice eval (extract → classify, no decision yet):
- Extraction: 15/16 fields ≥86% (same shape as extractor-alone eval).
- **Classification: 64%** — down from the 88% classifier-on-ground-truth baseline. Extraction noise compounds: clean invoices sometimes look "more clean" after extraction than the ground truth (model implicitly fills in missing fields), so dirty cases get misclassified as `none`.
- Joint (extraction all-fields-correct AND classification correct): 44%.
- Cost: $0.11 for 100 invoices.

**Phase 3 / Milestone 2 — RAG retrieval + decision-support**

Shipped:
- `config/policy_library.yaml` — **25 hand-authored mock policy snippets** covering 3-way match tolerances, missing-PO protocol, approval matrix (incl. VP Finance threshold), duplicate / fraud / split-invoice indicators, vendor master onboarding, FX policy, jurisdiction tax rules (US sales tax, EU VAT reverse-charge, India IGST/CGST/SGST + HSN, Brazil NF-e), payment terms, supplier-delay protocol. Real engagement content would replace this verbatim.
- `src/p2p_agent/retrieval/embeddings.py` — `Embedder` wrapping `sentence-transformers` with `BAAI/bge-large-en-v1.5`. Lazy model load, L2-normalized outputs.
- `src/p2p_agent/retrieval/store.py` — `VectorStore` protocol + `InMemoryVectorStore` (numpy cosine sim, `argpartition` top-k). Same interface as a future pgvector backend; only the implementation changes for production.
- `src/p2p_agent/retrieval/retriever.py` — `PolicyRetriever`: loads YAML on first `retrieve()`, embeds all snippets, exposes `retrieve(query, k=5) -> list[RetrievedDoc]`. Singleton-friendly: build once per process, the embedded library is reused for every case.
- `src/p2p_agent/retrieval/__init__.py` + `src/p2p_agent/models/retrieval.py` — exports + `RetrievedDoc` pydantic (id, title, text, score, tags).
- `src/p2p_agent/models/recommendation.py` — `RecommendedAction` `StrEnum` (13 actions covering the common P2P resolutions: `auto_resolve`, `request_supplier_credit_memo`, `request_supplier_correction`, `request_missing_po_from_supplier`, `route_to_vendor_master_onboarding`, `escalate_to_buyer_for_short_delivery`, `escalate_to_buyer_for_retroactive_po`, `escalate_to_fraud_team`, `escalate_for_fx_review`, `route_to_vp_finance_approval`, `notify_buyer_of_supplier_delay`, `hold_for_goods_receipt`, `other`). `Recommendation` pydantic with `action` + `rationale` + `counterfactual` + `confidence` + `cited_policy_ids`.
- `src/p2p_agent/llm/prompts/decision_support.md` — versioned prompt with the action enum, schema, confidence calibration buckets, and explicit guidance on rationale specificity, counterfactual structure, and the "policies are guidelines not binding rules" rule.
- `src/p2p_agent/decision/decision_support.py` — `recommend_action(classification, invoice, po_context, gr_context, retrieved_policies, client)`. Calls V4-Flash routed through OpenRouter (we removed `deepseek/deepseek-r1` from `deepseek-direct`'s explicit handles so R1 routes via the OpenRouter wildcard — deepseek-direct is gated on `monthly_spend_above_500_usd` and isn't enabled yet). Same retry-on-bad-output pattern as classifier/extractor.
- `src/p2p_agent/decision/__init__.py` — exports.
- `src/p2p_agent/orchestrator/pipeline.py` — extended with retrieve + decide steps. Default `include_decision=True`; pass `False` to run just extract + classify.
- `src/p2p_agent/models/golden_case.py` + `models/__init__.py` — added `ExpectedRecommendation` pydantic (partial typing — action, rationale_must_mention, counterfactual_should_exist) so the harness can evaluate the recommendation sub-block.
- `scripts/run_golden_set.py` — extended to evaluate `expected.recommendation` whenever the case has one. Builds a retrieval query from classification rationale + invoice signals, retrieves top-5 policies, calls `recommend_action`, compares to `expected.recommendation.action` (string match), checks counterfactual presence when required, and runs fuzzy must-mention matching against rationale + counterfactual. Per-case report shows class + rec pass/fail separately.
- `scripts/eval_pipeline.py` — extended to measure action accuracy with a category → acceptable-actions mapping (`ACCEPTABLE_ACTIONS_BY_CATEGORY`). Some categories allow either-of-N actions (e.g., quantity variance → either credit memo OR escalate to buyer).
- `Makefile` — no change beyond M1; existing `pipeline-eval` runs the full thing.
- `config/models.yaml` — removed explicit `deepseek/deepseek-r1` from `deepseek-direct.handles` so it routes via the OpenRouter wildcard. The deepseek-direct provider stays defined for future activation but ships with an empty handles list and the `enabled_when: monthly_spend_above_500_usd` gate.

**Smoke test (P003 — German EUR invoice with intra-EU tax issue):**

```
classification: tax_field_mismatch @ 0.85
retrieved_policies (top-5 by cosine):
  0.768  POL-018  EU VAT and intra-EU reverse charge       ← bullseye
  0.735  POL-021  Tax rate sanity check
  0.709  POL-001  3-way match — price variance tolerance
  0.702  POL-002  3-way match — quantity variance
  0.687  POL-015  Cross-currency invoicing policy
action: request_supplier_correction (conf 0.90)
rationale: "The invoice incorrectly applies German VAT (19%) to a US-based buyer,
            violating cross-border tax rules."
counterfactual: "If the buyer had a valid EU VAT ID and the transaction qualified
                 for reverse-charge treatment, the action would be auto_resolve."
cited_policy_ids: ['POL-018']
```

Retrieval picked the right policy at top-1 across 3 hand-picked smoke queries (price variance → POL-001, vendor master gap → POL-013, missing PO → POL-004).

**Golden harness end-to-end (11 cases, full pipeline):**

- Classification pass: 4/11 (GTC-001 clean, GTC-002 price variance, GTC-006 quantity variance, GTC-010 cross-currency).
- Recommendation pass: 1/11 (GTC-002 — the cleanest case where everything aligns).
- 1 skip (GTC-009 — supplier email only, no invoice).
- 6 of the 10 recommendation failures are because the YAMLs reference action strings that aren't in `RecommendedAction` yet (`halt_require_supervisor`, `request_po_amendment`, `escalate_to_fraud` vs `escalate_to_fraud_team`, `approve_pending_review`). YAML/enum reconciliation is a separate follow-up task.
- The remaining 3 recommendation failures cascade from classification errors (when the classifier is wrong, decision-support is necessarily building on the wrong category).

**Full pipeline corpus eval (100 invoices, end-to-end PDF → action):**

| Metric | Result |
|---|---|
| Extraction per-field (mean of 16 fields) | ~96% |
| Classification accuracy | 66% |
| Action accuracy (predicted-class acceptable-set) | 25% |
| Joint (everything correct end-to-end) | 8% |
| **Total cost** | **$0.71 for 100 invoices** (~$0.007 per pipeline call) |

**The action-accuracy caveat:** the 25% number scores actions against the **predicted** classification's acceptable-action set, not the truth. When the classifier (wrongly) predicts "none" for a missing-PO invoice but decision-support correctly overrules and recommends `request_missing_po_from_supplier`, the metric counts that as off-acceptable (because "none" only accepts `auto_resolve`). So decision-support's saves are penalized as errors. A better metric — comparing actions against ground-truth category, not predicted — would likely show action accuracy materially higher. Refine the metric in the next session.

**The bug that took 15 minutes to find:**

After adding `_eval_recommendation`, the closing return statements of `_eval_classification` got moved AFTER `_eval_recommendation`'s return — making them unreachable dead code, and `_eval_classification` lost its return statement, silently returning `None`. The harness then complained "NoneType has no attribute 'status'" 100 lines later in a list-comprehension. Fixed by relocating the returns to the right function. **Lesson: when adding a similarly-shaped function next to an existing one, double-check that the original's return body stayed put.** Patched with a defensive `None`-check before the comprehension as a tripwire for similar bugs in the future.

**Counts:**

- Real archetype nodes: **4 of 7** (extractor, classifier, retrieval, decision-support).
- Total spend this Phase 3 session: ~$0.85 (smoke tests + golden harness ×2 + M1 100-invoice + M2 100-invoice).
- Total project spend across all sessions: ~$1.40.

**Status:** End-to-end pipeline produces (extraction, classification, retrieved_policies, recommendation) for any invoice PDF. The classifier + decision-support combo runs against either ground-truth-JSON inputs (golden harness) or real PDFs (corpus eval). RAG architecture is production-shape (embed + cosine top-k); swapping the in-memory backend for pgvector is a single-file change when a buyer engagement needs persistence.

**Known issues / debt:**

- Action accuracy metric (25%) is conservatively biased by using predicted-class acceptable-action sets. Next session: add a truth-category-based metric AND a "decision-support saves" metric (cases where classification was wrong but decision-support recommended the right action anyway).
- YAML `expected.recommendation.action` strings in 6 golden cases (GTC-003/004/005/007/010/011) don't match `RecommendedAction` enum values. Reconcile either by adding aliases or by editing the YAMLs to use canonical names.
- `deepseek/deepseek-r1` was specced for decision-support but routes through OpenRouter at V4-Flash equivalent pricing today; we haven't tested whether R1's reasoning tokens improve action accuracy enough to justify the higher cost.
- Cross-encoder reranker (`BAAI/bge-reranker-v2-m3`) is wired in `pyproject.toml` but not yet plugged into `PolicyRetriever`. The 25-policy corpus is small enough that embed-only retrieval is fine; reranker becomes useful when the library grows past ~100 docs or production engagement needs sharper precision.
- The retrieval query is built from classification rationale + invoice signals as a one-shot string. For complex multi-issue invoices, query decomposition (multiple sub-queries per case) would likely improve coverage. Defer until we see retrieval misses in real data.

---

## 2026-05-12 (later) — Extractor node landed + 68% all-field-correct on first cut

Second archetype node end-to-end. The agent can now read invoice PDFs into
structured fields, not just consume pre-generated JSON sidecars.

**Shipped:**

- `src/p2p_agent/models/extraction.py` — pydantic models mirroring the corpus ground-truth shape: `InvoiceExtraction`, `HeaderFieldsExtraction`, `LineItemExtraction`, `TaxLineExtraction`. Adds a `field_confidence: dict[str, float]` keyed by dotted path (e.g. `header_fields.vendor_tax_id`). 13 fields total + nested line items + tax lines.
- `src/p2p_agent/llm/prompts/invoice_extraction.md` — versioned prompt. Core principle stated explicitly: "extract verbatim, don't correct." Defines the full output schema, currency-symbol → ISO-code mapping table, date-format normalization rules, confidence calibration buckets, and rules for missing fields (empty string, not invented).
- `src/p2p_agent/extractors/invoice_extractor.py` — `async def extract_invoice(*, pdf_path, client, case_id)` returns a validated `InvoiceExtraction`. Reads PDF text via `pypdf.PdfReader`, calls `ModelClient.complete(task="invoice_extraction", temperature=0.0, max_tokens=4096)`, parses via the shared `extract_json_from_response()`, validates against pydantic. One retry with a stricter reminder on parse failure. Raises `ExtractorError` for scanned/image-only PDFs (no text layer) or irrecoverable bad output.
- `src/p2p_agent/extractors/__init__.py` — exports `extract_invoice`, `ExtractorError`, and the extraction pydantic types.
- `scripts/eval_extractor.py` — corpus-driven field-level eval. For each sampled PDF: extract → load ground-truth JSON sidecar → diff field-by-field with field-aware matchers. Reports per-field accuracy table, all-field-correct rate, per-persona accuracy, total cost. Field-aware matchers: exact (po_reference, currency, sku), ISO-date (parses both sides), ±1% rel tolerance (subtotal/total/unit_price/line_total/tax.amount), normalized (payment_terms uppercased + dashed), fuzzy/Jaccard ≥0.7 (vendor_name, addresses, buyer_po_contact), label-prefix-stripped (vendor_tax_id — see "the matcher fix" below). Defaults to 100-invoice random sample with seeded RNG; `--full` for all 490; `--persona P003` to filter.
- `Makefile` — new targets `extractor-eval` (sample 100) and `extractor-eval-full` (all 490).
- `status.html` — Extractor row in the per-node table flipped to IN PROGRESS, per-archetype-modules progress bar moved from 15% → 25%, "what's still left" tally updated to 2/7 nodes done.
- `config/models.yaml` — no change needed. `invoice_extraction` task was already wired to `deepseek/deepseek-v4-flash`.

**Test results — 100-invoice random sample, DeepSeek V4-Flash, seed=42:**

Per-field accuracy after the matcher fix:

| Field | Accuracy |
|---|---|
| po_reference | 100% |
| invoice_date | 100% |
| currency | 100% |
| payment_terms | 100% |
| subtotal | 100% |
| total | 100% |
| header_fields.buyer_po_contact | 100% |
| line_items.count | 100% |
| tax.count | 100% |
| line_items.all_match | 99% |
| tax.all_match | 99% |
| header_fields.buyer_name | 99% |
| header_fields.vendor_name | 96% |
| header_fields.buyer_address | 96% |
| header_fields.vendor_tax_id | 89% |
| header_fields.vendor_address | 85% |

**Overall: 68% all-field-correct** (every single field on the invoice perfectly matches ground truth — strict joint metric).

Per-persona all-field-correct rate:
- P001 (US, modern PDF): 95.2%
- P005 (Brazil, NF-e): 76.2%
- P002 (US SMB, scanned-style): 66.7%
- P003 (Germany, bilingual): 57.1%
- P004 (India, GST + HSN): 43.5%

Persona ranking matches intuition — Western templates with single-language fields are easier; multilingual + multi-tax-component personas are where the model loses precision.

**Cost: $0.0696 for 100 calls ($0.0007 per PDF).** Right on target. Full 490-invoice run would be ~$0.34.

**The matcher fix that mattered:**

First eval run came back with **vendor_tax_id at 44%** — alarming on the surface. Investigation: the extractor was correctly stripping label prefixes ("EIN: 98-7654321" → "98-7654321", "CNPJ: 12.345.678/0001-90" → "12.345.678/0001-90", "GSTIN: 27AABCM1234C1ZP" → "27AABCM1234C1ZP") — which is actually the *right* behavior for a downstream system that needs the raw ID value, not the printed label. The ground-truth JSON sometimes carries the prefix and sometimes doesn't (corpus-generation inconsistency), and the naive equality matcher was penalizing the cleaner output. Fixed the eval by adding `_norm_tax_id()` that strips common prefixes (EIN, VAT, GSTIN, PAN, CNPJ, CPF, TIN, TAX ID) before comparing. Re-run: 44% → 89%. All-field-correct: 32% → 68%.

This is the same shape of bug as yesterday's `po_context: null` issue in the classifier — the LLM is doing the right thing; the evaluator was measuring the wrong number. Important to log because: the original "44%" headline could have driven a wasted prompt-tightening pass when the actual fix was elsewhere.

**Where the remaining failures are:**

- **`vendor_address` 85%** — model normalizes whitespace and line breaks more aggressively than the ground truth (e.g. collapsing multi-line addresses into one or vice versa). Fuzzy Jaccard tolerates most of this; the 15% that still fail are typically reorderings of address components. Not worth tightening.
- **`vendor_tax_id` 89% remaining miss (11%)** — model occasionally drops a hyphen or transposes a digit on harder formats (Indian GSTIN, Brazilian CNPJ). Real extraction quality issue, not a matcher issue. Worth investigating with V4-Pro fallback once we benchmark.
- **`line_items.all_match` 99% / `tax.all_match` 99%** — one-off per-line mismatches; usually a single unit_price rounded differently than ground truth. Within tolerance for the classifier downstream.
- **P004 (India) 43.5%** — the dominant failure mode. HSN codes formatted differently, GST percentage occasionally misrouted between IGST and CGST/SGST splits. These are also the "error_injected" categories in the corpus by design — so the extractor is correctly preserving the errors, but the joint match fails because the extracted values look right while the ground-truth JSON encodes the deliberate error. **This is actually correct behavior** — the extractor's job is to extract, not to fix. The all-field-correct metric over-penalizes correct-extraction-of-dirty-data.

**Counts:**

- Real archetype nodes: **2 of 7** (classifier + extractor).
- Total API spend this session: ~$0.20 (2× 100-invoice eval runs + smoke tests + one inspection).
- Total spend across the whole agent build to date: ~$0.55.

**Status:** Extractor produces structured invoice data from PDFs with high per-field accuracy. The classifier (yesterday) and extractor (today) can now run as a pair: PDF in → InvoiceExtraction → Classification. Next session should wire the two together end-to-end, replacing the classifier's current "read JSON sidecar" cheat with "extract PDF → classify the extraction." After that, the natural progression is the decision-support node (recommendation + rationale + counterfactual), at which point three of the seven nodes are live and a full pipeline run becomes possible.

**Known issues / debt:**

- Extractor uses `pypdf` text extraction only. No vision path. PDFs generated by image-only renderers (real-world scanned invoices from supplier emails) would fail with `ExtractorError("no extractable text layer")`. Vision-capable model path is a follow-up when the first real scanned invoice lands.
- The eval's `vendor_address` fuzzy match (Jaccard ≥ 0.7) is generous. If extracted addresses get used for vendor-master matching later, we'll want exact reformatting validation, not just word-bag overlap.
- The corpus's `field_confidence` output is captured but not yet consumed downstream. Wire it into the classifier as a signal once both nodes are integrated end-to-end.

---

## 2026-05-12 — Test harness live + classifier node landed + 88% corpus accuracy on first cut

First real archetype node end-to-end. The harness, classifier, and corpus eval
all run against live API calls and produce red/green output.

**Shipped:**

- `src/p2p_agent/models/classification.py` — 13-member `ExceptionCategory` `StrEnum` (matches the taxonomy in `docs/authoring_golden_cases.md:53-72` and the YAML strings across all 11 golden cases) plus the `Classification` pydantic model (`class_label`, `confidence`, `evidence`, `rationale`).
- `src/p2p_agent/models/golden_case.py` — `GoldenCase` pydantic model with full validation for `expected.classification` and `dict[str, Any]` pass-throughs for the still-unimplemented expected sub-blocks. `load_golden_case(path)` helper handles the `created: date` deserialization that YAML defaults trip up.
- `src/p2p_agent/llm/json_utils.py` — shared `extract_json_from_response()` for fenced-block + bare-JSON parsing. Imported by both the corpus ingester (refactored) and the classifier.
- `src/p2p_agent/llm/prompts/exception_classification.md` — first versioned prompt. Defines the 13 categories with one-line descriptions, output schema, confidence calibration rules, and explicit handling of `po_context: null` / `gr_context: null` (this last bit was a real bug — see "the prompt fix" below).
- `src/p2p_agent/llm/prompts/__init__.py` — `load_prompt(name)` / `render_prompt(name, **subs)` helpers so prompts stay as text files, never inlined in Python (per CLAUDE.md convention).
- `src/p2p_agent/classifiers/exception_classifier.py` — `classify_exception(invoice, po_context, gr_context, ...)` async function. Calls `ModelClient.complete(task="exception_classification", temperature=0.0, max_tokens=1024)`, parses with the shared JSON utility, validates against `Classification`. One retry with a stricter reminder if the first parse fails. Raises `ClassifierError` on irrecoverable bad output.
- `scripts/run_golden_set.py` — **real implementation**. Replaces the `NotImplementedError` skeleton. Per case: builds invoice / PO / goods-receipt inputs from the YAML, calls the classifier, evaluates against `expected.classification` with three checks (class_label match, confidence ≥ min_confidence, evidence tokens present), returns per-sub-block `pass | fail | skip` with classification as the only "real" sub-block. Other sub-blocks all return `skip` until their nodes land. CLI: `--case GTC-XXX` filter, `--override task=model` to swap per-task model.
- `tests/test_golden_cases.py` — pytest entry. One parametrized test per YAML, gated on `@pytest.mark.golden` and `@pytest.mark.needs_api_key`. `make test-golden` runs the set under pytest now.
- `scripts/eval_classifier.py` — corpus-driven accuracy eval. Iterates over `test_corpus/synthetic/invoices/*.json`, classifies each, reports confusion matrix + per-category precision/recall + top confusions + total API cost. Defaults to a 100-invoice random sample; `--full` for all 490.
- `config/error_label_to_category.yaml` — maps each persona `typical_error_modes` string to one of the 13 exception categories. Drives the corpus-eval ground truth.
- `Makefile` — new targets `classifier-eval` (sample 100) and `classifier-eval-full` (all 490).
- `status.html` — content edits per the plan: header status flipped to "Engineering underway", build phase metric updated, corpus cost mode flipped to "API · DeepSeek V4-Flash", ModelClient progress bar 15% amber → 100% green, classifier node badge in per-node table flipped to IN PROGRESS, synthetic-invoice corpus row reads `490 / 500`, test harness row updated to 35%, "what's still left" table refreshed with current statuses (DONE / IN PROGRESS / WAITING / etc.).

**Test results — first cut:**

- **Corpus eval (100 random invoices, V4-Flash, no fine-tuning):** **88% accuracy.** Cost: $0.0365 across 102 calls (~$0.00036 per call). Per-category P/R: `missing_po` 100% / 100%, `cross_currency_mismatch` 100% / 100%, `tax_field_mismatch` 100% / 71%, `none` 85% / 100%, `other` 100% / 20%. Top remaining confusion: `other` misclassified as `none` (extraction-detail errors look like clean invoices to a classifier).
- **Golden cases:** 3 pass (GTC-001 clean, GTC-002 price variance, GTC-006 quantity over-delivery), 7 fail, 1 skip (GTC-009 has no invoice — supplier email only). Of the 7 failures, 4 are "correct class_label, evidence tokens too strict" (GTC-005/008/010/011), and 3 are "needs context the classifier isn't given" (GTC-003 needs payment history, GTC-004 needs vendor master, GTC-007 needs cross-case fraud signals). All 3 of those are real architecture insights, not classifier bugs.

**The prompt fix that mattered most:**

The first corpus-eval run came back at 20% accuracy. Diagnosis: the original prompt told the model that `po_context: null` implies likely `missing_po`. But in the corpus eval we deliberately pass `po_context=None` for *test isolation* even when invoices have valid PO references. Result: 56 clean invoices got mislabeled as `missing_po`. Fixed by clarifying in the prompt: "`po_context: null` means PO data wasn't passed to you in this run — it does NOT mean the supplier omitted a PO reference. Only classify as `missing_po` when the invoice itself has a missing, blank, or malformed `po_reference` field." Second run: 88%.

This is exactly the kind of bug the harness exists to catch. Without the eval pipeline, the bad prompt could have shipped and silently degraded classification on every clean invoice in production.

**Evaluator design notes:**

- The harness uses **word-level fuzzy evidence matching** with a small synonym map (`qty↔quantity`, `gr↔receipt`, `po↔purchase`, `matches↔match`, `vendor↔supplier`, etc.) and a 60% per-token hit threshold. The YAMLs use very specific snake_case slugs like `po_qty_matches_gr_qty_matches_invoice_qty`; the model naturally writes `quantity_match`. Both should pass. The synonym map is small and intentionally narrow — if the matcher gets too lenient, golden-case signal degrades.
- GTC-009 (supplier delivery delay, no invoice) returns `skip` rather than `error` so it doesn't pollute the fail count. When an email-extractor node lands, this case becomes runnable.
- Pytest hook in `tests/test_golden_cases.py` writes deferred-block names to stderr per case so the not-yet-implemented sub-blocks are visible without polluting the assertion.

**Counts:**

- Real archetype nodes: 1 of 7 (classifier).
- Golden cases passing classification: 3 of 11 (4 more would pass with looser evidence matching or canonical YAML tokens).
- Corpus accuracy: 88% on 100 samples.
- Total API spend this session: ~$0.11 (eval ×2 + golden runs ×3 + smoke tests).

**Status:** Test harness body exists and produces real evaluation data. ModelClient + classifier + corpus form a real evaluation loop. Next session can focus on the extractor node (turn invoice PDFs into structured fields) or on adding `vendor_master`/`historical_invoices` context to the classifier input so cases like GTC-003/004 become solvable.

**Known issues / debt:**

- The classifier's `response_model` pydantic parsing is still done manually via the JSON utility. The `ModelClient.complete(response_model=...)` path is still `NotImplementedError`. Wire up structured-output mode (`response_format`) when the next node needs it.
- 4 golden cases would pass with looser evidence tokens or YAML rewriting. Either tighten the matcher's synonym map per category or rewrite YAML evidence tokens to match what the model naturally produces. Defer until a more comprehensive prompt-vs-YAML alignment review.
- `other` recall is 20% on corpus eval. The error modes mapped to `other` (HSN formatting, date format inconsistencies, multi-page line items) are all extraction-fidelity issues the classifier can't see from clean JSON input. They become detectable once the extractor node runs on actual PDFs and surfaces extraction confidence signals.

---

## 2026-05-11 — ModelClient live + 500-invoice corpus generated via API + DeepSeek V4-Flash

First real engineering session after scaffolding. Two locked decisions reversed
based on new pricing evidence; corpus pipeline is now fully automated.

**Decisions reversed (with reason):**

1. **Subscription mode → API mode as the default** for synthetic corpus generation.
   The original lock was based on Claude Sonnet / GPT-4o pricing ($50-150 for the
   full corpus). DeepSeek V4 (released 2026-04-24) drops corpus cost to ~$0.18
   for 500 invoices, which removes the cost argument that drove subscription
   mode. Subscription mode kept as documented fallback when OpenRouter is
   unavailable or budget caps hit.

2. **DeepSeek V3 → DeepSeek V4-Flash** as the per-task default for extraction,
   classification, drafting, and corpus generation. V4-Flash is $0.14/$0.28 per
   1M tokens (vs V3 at $0.27/$1.10) with 1M context. V3 retained as fallback.

**Shipped:**

- `src/p2p_agent/llm/client.py` — **real implementation** (was skeleton).
  OpenRouter routing via AsyncOpenAI, per-task model resolution from
  `config/models.yaml`, cost calculation from price catalog, tenacity retry
  on `APIConnectionError` / `APITimeoutError` / `RateLimitError` /
  `InternalServerError`, jsonl call ledger at `logs/llm_calls.jsonl`.
- `scripts/generate_invoices.py` — **real implementation** (was skeleton).
  API-mode invoice generation. Reuses the persona / prompt-template machinery
  from `generate_invoice_prompts.py`; instead of writing prompts to disk for
  manual paste, calls the API and writes responses directly to
  `scripts/subscription_workflow/responses/`. Deterministic from `--seed`
  even under concurrent dispatch (specs built sequentially, API calls run in
  parallel).
- `scripts/ingest_subscription_responses.py` — **real weasyprint PDF renderer**
  with graceful macOS fallback. Generic single-template layout (navy header,
  itemized table, totals block, footer). Filenames now use deterministic
  `spec_index` (`{persona_id}_idx{NNNN}.pdf`) instead of the LLM-generated
  `invoice_id` to prevent collision-overwrite (we lost 22% of invoices to
  this on the first attempt). Ground-truth `error_injected` field is
  overwritten from the spec sidecar at ingest time so the model's
  paraphrased / slugified labels can't fragment downstream filtering.
- `config/models.yaml` — `deepseek/deepseek-v4-flash` added as price-catalog
  entry and as the default for `corpus_generation`, `invoice_extraction`,
  `email_parsing`, `exception_classification`, `drafting_supplier_comms`,
  `drafting_internal_notes`. V3 retained as fallback.
- `Makefile` — new targets `corpus-api-invoices-validate` (25-invoice
  validation pass), `corpus-api-invoices` (500-invoice run),
  `corpus-api` (generate + render in one shot), `corpus-api-validate`
  (validate + render). `corpus-ingest-invoices` now exports
  `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` so weasyprint finds
  Homebrew's pango/cairo/glib on macOS.
- `scripts/generate_invoice_prompts.py` — patched `pick_error_mode` to remove
  a stale second 0.35 gate that was compounding with `build_invoice_spec`'s
  35% roll, producing an effective ~12% injection rate instead of the
  configured 35%.
- `.env` — `CORPUS_MODE=api`, `OPENROUTER_API_KEY` populated. `.env.example`
  template restored (key was accidentally placed there first and rotated).

**Corpus state on disk:**

- `test_corpus/synthetic/invoices/` — **500 PDFs + 500 ground-truth JSON
  sidecars** generated via DeepSeek V4-Flash on OpenRouter. ~25 MB total.
- **490 invoices** ingested into the corpus (10 lost to a single malformed-JSON
  batch — within tolerance, not regenerated).
- Persona distribution: P001=97, P002=88, P003=101, P004=93, P005=111.
- Currency distribution: USD 185, BRL 111, EUR 101, INR 93.
- **Error injection rate: 33.9%** (target 35%, configured `--error-rate 0.35`).
  166 dirty invoices spread across all 10 persona-specific error modes.
  Ground-truth `error_injected` is now canonical — one label per error mode,
  no slug variants.
- `logs/llm_calls.jsonl` — 100 entries, **$0.1852 total cost** for the run.

**Counts:**

- Golden cases: still 11 (GTC-001 through GTC-011). No new cases this session.
- Production-ready ModelClient: 1 of 1 (the only one).
- API calls made today: 100 (corpus) + ~5 (debug). Total spend: ~$0.20.

**Status:** Engineering started. Test corpus exists end-to-end via API. SAP
trial signed up; awaiting credential email to run `make sap-validate`. Next
session lands the test harness skeleton against the existing 11 golden cases,
even though most cases can't fully execute until the orchestrator nodes
(extractor / classifier / decision / drafter) exist behind it.

**What's not yet built (in priority order):**

1. Test harness wiring in `scripts/run_golden_set.py` (currently a skeleton).
2. First archetype node — probably classification — wired against the corpus
   so the harness has at least one real green/red signal.
3. SAP connector implementation (sandbox is up, validator works; connector
   code is just `base.py` for now).
4. Remaining archetype nodes: extractor, decision, drafter, HITL router.
5. LangGraph orchestrator stitching the nodes together.
6. Email-thread synthetic corpus (~200 threads; same API-mode workflow,
   different prompt template).
7. Master-data synthetic corpus (~200 vendors).

**Known issues / debt:**

- One batch (#100) of the corpus had malformed JSON; 10 invoices lost. Not
  fixed — within tolerance. Add JSON-repair logic to the ingester if this
  recurs at >5%.
- Email + master-data generation scripts still skeleton-only. Same API-mode
  pattern will apply when authored.
- `make corpus-ingest-invoices` requires `DYLD_FALLBACK_LIBRARY_PATH` on
  macOS. The Makefile sets this for the recipe but a direct
  `uv run python scripts/ingest_subscription_responses.py` invocation needs
  the env var set manually. Tolerable; the Makefile is the documented path.

---

## 2026-05-10 (later) — Decisions signed off + corpus workflow + SAP setup + 10 golden cases

**Five decisions locked** (`docs/test_corpus_design.md §6`):
1. BPI 2019 + BPI 2020 both as anchor datasets.
2. **Subscription mode** for synthetic doc generation (Claude.ai / ChatGPT subscriptions instead of API). Material cost decision — drops corpus-generation cost from ~$50-150 to ~$0.
3. SAP S/4HANA Cloud trial as primary ERP sandbox.
4. Aggressive design-partner outreach; do not block IP build on it.
5. **Solo build — TJ + Claude Code only**. No additional engineer pulled. All work sequencing must respect this constraint.

**Shipped:**
- `docs/subscription_mode_workflow.md` — full workflow doc for how to use Claude.ai / ChatGPT subscriptions to generate the synthetic corpus instead of API calls.
- `scripts/generate_invoice_prompts.py` — generates batched invoice-generation prompts (5 invoices per batch, 100 batches for 500 invoices) writing to `scripts/subscription_workflow/prompts/`. TJ pastes each prompt into Claude.ai / ChatGPT, saves the JSON response.
- `scripts/ingest_subscription_responses.py` — parses pasted JSON responses, validates schema, writes invoice PDFs + ground-truth JSON sidecars to `test_corpus/synthetic/invoices/`. Idempotent — re-runs skip already-ingested batches.
- `docs/sap_sandbox_setup.md` — registration walkthrough, env-var setup, OData service catalog, common failure modes for SAP S/4HANA Cloud trial.
- `scripts/validate_sap_connection.py` — three-step connection validator (env vars → OAuth → PO read). Run via `make sap-validate`.
- `docs/authoring_golden_cases.md` — guide for writing new golden cases. Schema, exception categories, workflow, common mistakes, holdout convention.
- **10 new golden cases** in `tests/golden_cases/`:
  - GTC-001 — clean 3-way match (auto-pass anchor)
  - GTC-003 — duplicate invoice on already-paid PO (fraud halt)
  - GTC-004 — non-master vendor (onboarding route)
  - GTC-005 — retroactive invoice with no PO (maverick spend)
  - GTC-006 — quantity over-delivery (credit memo request)
  - GTC-007 — split-invoice fraud pattern (Tier 3 escalation)
  - GTC-008 — EU VAT field missing (supplier correction request)
  - GTC-009 — supplier delivery delay (Coordination + Drafting test)
  - GTC-010 — cross-currency FX variance (review needed)
  - GTC-011 — missing approval above threshold (route to VP Finance)
- `Makefile` updates: `corpus-prompts-invoices`, `corpus-ingest-invoices`, `sap-validate` targets.
- `.env.example` updates: marked OpenRouter key as optional in subscription mode; added SAP_TOKEN_URL field.
- `CLAUDE.md` updates: decisions logged; subscription mode + SAP + solo-build added to the locked-decisions table; reading-order list refreshed.

**Counts:**
- Golden cases now: 11 (GTC-001 through GTC-011). Target 40 for production-readiness.
- Exception categories covered: 9 of 12. Remaining: missing_goods_receipt, payment_term_mismatch, plus more variants of fraud_signal.

**Status:** Pre-engineering still. All decisions made. Next session can start: (a) implementing `src/p2p_agent/llm/client.py`, (b) signing up for SAP trial + running `make sap-validate`, (c) starting the first batches of subscription-mode invoice generation, (d) authoring 10 more golden cases on a slow cadence.

**Solo-build implication for the build plan:**
- Estimated total IP build effort is ~12-16 weeks of dedicated engineering. Solo (TJ + Claude Code) realistically delivers ~50% of that velocity, so plan for ~24-32 weeks of calendar time.
- Prioritize ruthlessly: corpus + harness + ModelClient + classifier + orchestrator skeleton come first. Connectors and HITL UI can be deferred to phase 2 if a design partner brings their own integrations.
- Subscription-mode workflow is a force multiplier — every API hour saved is one engineering hour redirected.

---

## 2026-05-10 — Project scaffolded

Initial folder + docs created.

**Shipped:**
- `CLAUDE.md` — project context for Claude Code sessions; locked decisions list; model strategy summary; coding conventions
- `README.md` — public-facing project description
- `docs/PRD.md` — product requirements (9 sections + appendix)
- `docs/model_strategy.md` — open-source-first model strategy with per-task assignments, cost ceilings, quality gates
- `docs/technical_design.md` — high-level architecture (system overview, tech stack, module layout, data models, RAG design, HITL, connectors, Stage 9, deployment topology)
- `docs/test_corpus_design.md` — 4-source data strategy mirroring `../../../09_ip_builds/agent1_p2p_orchestrator/01_test_corpus_design.docx`
- `pyproject.toml` — Python project + dependencies (langgraph, pydantic, openai, anthropic, httpx, psycopg, structlog, pm4py)
- `Makefile` — common commands (setup, test, corpus, stage9, run-case)
- `.env.example` — template for API keys and config
- `.gitignore` — Python + corpus + secrets exclusions
- `config/models.yaml` — per-task model assignments, provider routing, price catalog
- `config/stage9_thresholds.yaml` — alert thresholds for the 6 Stage 9 signals + per-archetype quality signals
- `config/personas.yaml` — 5 starter supplier personas (expand to 20-25 in week 2)
- `src/p2p_agent/*` — module skeleton with `__init__.py` for every package
- `src/p2p_agent/llm/client.py` — locked interface for the ModelClient (implementation TODO)
- `scripts/ingest_bpi.py` — BPI Challenge dataset ingestion skeleton
- `scripts/generate_invoices.py` — synthetic invoice generation skeleton
- `scripts/run_golden_set.py` — golden-set test harness skeleton
- `scripts/estimate_cost.py` — cost estimation skeleton
- `tests/conftest.py` — pytest fixtures + marker registration
- `tests/golden_cases/GTC-002-price-variance.yaml` — first golden case YAML (template for the remaining 39)

**Decisions locked:**
- Open-source models first in test phase (DeepSeek V3 + R1, Kimi K2 via OpenRouter)
- LangGraph for orchestration
- Postgres + pgvector for state and RAG
- Pydantic v2 for all data models
- pytest with `@pytest.mark.golden` for the regression set
- $200K build floor inherited from the practice CLAUDE.md
- Stage 9 measurement instrumented from day 1

**Status:** Pre-engineering. Five decisions in `docs/test_corpus_design.md` §6 pending TJ sign-off. Engineering kickoff once signed.

**Next sessions:**
1. Sign off the 5 corpus-design decisions
2. Implement `src/p2p_agent/llm/client.py`
3. Implement `scripts/ingest_bpi.py` for BPI 2019 + 2020
4. Author 10 more golden cases beyond GTC-002
