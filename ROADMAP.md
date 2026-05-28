# Roadmap — invoice exception agent

**Read `CLAUDE.md` first for the project framing.** This file covers
where the build currently is and what the next phase looks like.

---

## Current state (2026-05-28)

The build is at a deliberate pause point. The demo runs end-to-end and is
publicly accessible. The next phase (learning + evals) is intentionally
NOT scoped yet — see "Next phase" below.

### What's shipped end-to-end

- **10-node async pipeline** — extract → cross-case context → classify →
  retrieve → decide → route → draft → enqueue. Median ~30-60s per invoice.
- **HITL approval queue** — SQLite, 3-tier routing (auto-pass / approver /
  supervisor), audit log
- **Stage 9 measurement dashboard** — cost, latency, classification mix,
  auto-pass rate, per-task and per-model breakdowns
- **24 golden cases** (`tests/golden/`) — pass/fail end-to-end against real
  LLMs, covers all 13 ExceptionCategory values
- **Test corpus** — 490 synthetic invoices + JSON sidecars, 33.9%
  error-injection rate, 10 canonical error labels (locally generated; only
  20 ship in the public repo for the demo library)
- **Public Railway demo** — https://web-production-45a73.up.railway.app
- **Vercel portfolio integration** — https://tj-joshi-portfolio.vercel.app
  surfaces the demo via rewrites/redirects
- **DATA_DIR-based persistence** — single env var configures DB, uploads,
  and cost-ledger paths; Railway volume mount at `/data` makes everything
  survive redeploys
- **Demo UX** — `/demo` has two paths: (1) browse 20 invoices (hint-free
  picker, no expected-outcome leakage) → preview → select → run, (2) curated
  scenario dropdown (10 labeled patterns) → one-click run
- **85/85 tests pass**

### What's intentionally deferred (not blockers, just out of scope for now)

- Real action executor backend (gated on SAP S/4HANA Cloud trial creds)
- Webhook / event-ingestion layer (same blocker)
- LangGraph wrap of the orchestrator (plain async function is fine for the
  current footprint; durable state isn't a real need yet)
- Postgres swap from SQLite (one-env-var change when needed)
- Real SAP / Ariba / ServiceNow connectors (stubs await creds)

---

## Next phase — Learning + Evals layer

### Why this is the right direction (portfolio framing)

Most candidates applying to agents-shaped roles have shipped a demo. Far
fewer can show an instrumented agent with real eval discipline and a
closed feedback loop from operator approvals back into training signal.
**That's the muscle that separates "AI-curious" from "ready to ship
agents in production"** — and it's what hiring managers in the agents
space are screening for.

### Themes likely in scope (NOT a committed plan)

These are the threads that look highest-leverage from where I sit. They
are NOT a roadmap to start executing — they're a menu to scope against
once the open questions below are answered.

1. **Close the HITL feedback loop.** Operator approvals/edits sit in
   SQLite today and go nowhere. Mine them into (a) regression-test
   additions, (b) few-shot example candidates, (c) fine-tune seeds. This
   is the closest analogue to how real-world agents improve over time.
2. **Aggregate eval metrics on top of golden cases.** Pass/fail per case
   is a thin signal. Add per-class confusion matrix, recommendation-action
   accuracy, routing-tier accuracy, rationale-quality grading (LLM-as-judge
   or rubric), and track over time.
3. **A/B prompt evaluation harness.** Compare two prompt variants on the
   same held-out set; report cost + quality + latency diff. Without this,
   every prompt change is vibes-based.
4. **Drift monitoring layered on Stage 9.** Today Stage 9 is a passive
   dashboard. Add thresholded alerts — "classification mix shifted 20%
   week-over-week," "P95 latency doubled," "auto-pass rate dropped."
5. **Self-consistency / multi-sample for high-stakes routes.** For Tier 3
   (supervisor) cases, run classify+decide twice with different
   temperatures and flag disagreements. Cheap safety net.

### Open questions — answer these BEFORE scoping any of the above

These are TJ's calls, not Claude's. Do not start work on this phase
until TJ has answered:

1. **Which role shape is the artifact optimizing for?**
   - AI engineer (lean into code depth, testing infra, ML systems)
   - Agents-focused technical/product PM (lean into system design,
     tradeoffs, eval framework as a product surface)
   - Applied AI / research-adjacent (lean into measurement rigor,
     comparison studies, writeup quality)
   The right thread mix differs across these three.
2. **Breadth or depth?**
   - Breadth: 5 shallow threads, one short writeup per thread →
     shows range
   - Depth: one thread built to publication quality (one really good
     blog post, one really good demo screen) → shows depth
   Both are defensible. Pick one before starting; mid-stream changes
   are expensive.
3. **What's the real goal?**
   - "More interview material to point at" (output-driven)
   - "Actually learn techniques I'm weak on" (skill-driven)
   These look the same on day 1 and diverge by day 10. Be honest about
   which one.

---

## Out of scope (do not propose)

The following are NOT directions for this project. If a session drifts
into recommending any of these, stop and re-read `CLAUDE.md`:

- TruVs go-to-market materials (this is not a TruVs deliverable)
- Sales/marketing assets, pitch decks, pricing frameworks, partner-
  reactivation memos, target-account lists
- The Process-to-Agent Method, 9-stage operating model, $200K floor,
  4-bundle pricing, or any framework from the parent TruVs CLAUDE.md
- Stage 9 framed as a "recurring revenue moat" — it's a measurement
  dashboard, technically interesting, commercially out of scope
- Internal/customer case studies of this agent
- Anything aimed at enterprise procurement buyers; the audience is
  engineering teams hiring for agents work
