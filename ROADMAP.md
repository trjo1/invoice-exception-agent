# Roadmap — invoice exception agent

**Read `CLAUDE.md` first for the job-hunt frame.** This file covers where
the build is, what ships next, and what the application phase looks like.

---

## Current state (2026-05-28)

The agent substrate is at a deliberate pause point. The current phase is
**shipping PM-shape artifacts on top of the substrate**, not adding more
engineering features.

### What's shipped as substrate

- **10-node async pipeline** — extract → cross-case context → classify →
  retrieve → decide → route → draft → enqueue. Median 30-60s per invoice.
- **HITL approval queue** — SQLite, 3-tier routing (auto-pass / approver /
  supervisor), audit log
- **Stage 9 measurement dashboard** — cost, latency, classification mix,
  auto-pass rate, per-task and per-model breakdowns
- **24 golden cases** — pass/fail end-to-end against real LLMs, all 13
  ExceptionCategory values covered
- **Test corpus** — 490 synthetic invoices + JSON sidecars (20 ship in the
  public repo for the demo library)
- **Public Railway demo** — https://web-production-45a73.up.railway.app
- **Vercel portfolio integration** — https://tj-joshi-portfolio.vercel.app
  surfaces the demo via rewrites/redirects
- **DATA_DIR-based persistence** — single env var configures DB, uploads,
  and cost-ledger paths
- **Demo UX** — `/demo` browse (hint-free picker, 20 invoices) + curated
  scenario dropdown (10 labeled patterns)
- **85/85 tests pass**

### What's intentionally deferred

- Real action executor backend (gated on SAP S/4HANA Cloud trial creds)
- Webhook / event-ingestion layer (same blocker)
- LangGraph wrap of the orchestrator
- Postgres swap from SQLite
- Real SAP / Ariba / ServiceNow connectors

These are not blockers for the job-hunt. The substrate is sufficient.

---

## Phase 1 — Ship 3 PM-shape artifacts (1-3 weeks, ~22-28 hours)

Priority is by signal-density per hour invested.

### Artifact 1 — Decision log (~4 hours, highest signal-density per hour)

Single markdown page. 5 entries × ~250 words each. For each: the tradeoff,
the choice made, what was given up, what to reconsider. Universal value
across all four target companies — this is the "product taste demonstrated"
criterion from the Anthropic JDs.

The 5 entries (each a real PM call from the build):
- Why 3 HITL routing tiers, not 2 or 5
- Why no real user-upload UX (browse-only) for the public demo
- Why open-source models default + daily cost cap (not feature-gating)
- Why mock executor pattern as a safety boundary (not waiting on real SAP creds)
- Why hint-free browse vs labeled curated dropdown (the 2026-05-28 UX call)

Voice model: Scott White (Head of Product, Claude, Anthropic). Concrete,
first-person, principle-style, no hype.

Lives at: `docs/decision_log.md` → linked from `/demo` home + docs landing.

### Artifact 2 — Eval framework writeup + 3-model comparison (~12-16 hours)

**The Principal-vs-Senior differentiator.** Reframes Stage 9 from
"dashboard" to "eval system as product spec."

Spec doc covers:
- 4-5 metrics that matter (classification accuracy, recommendation-action
  accuracy, routing-tier accuracy, p95 latency, cost-per-run)
- SLO per metric (defensible, not aspirational)
- Decision protocol when SLO crossed (rollback / hold / ship-with-monitoring)
- Rollout plan for a model change (canary → 10% → 100% with metric checks
  at each gate)

Then RUN: DeepSeek V4-Flash vs Claude Sonnet 4.6 vs GPT-5 on the 24 golden
cases. Publish numbers including the ones that look bad.

**Ships as a public page in the docs nav.** When Artifact 2 lands, a new
`/docs/evals` page joins the docs landing alongside the decision log —
showing real evaluation results of the agent (per-class accuracy,
per-model comparison, drift over time when re-runs accumulate). This is
the "real evals can be seen here" surface a hiring reader gets without
having to clone the repo. The accompanying `docs/eval_framework.md` spec
doc lives as a peer artifact at `/docs/eval_framework`.

Lives at: `docs/eval_framework.md` (spec) + `/docs/evals` (live results
page) + `/stage9/comparison` (technical detail page on the demo app).

Highest leverage at:
- **LangChain** — LangSmith JD: "designing evaluation systems that scale
  from 10 to 10,000 test cases"
- **Anthropic** — eval design fluency criterion
- **Cursor** — Agent Harness JD: "strong metrics and evaluation intuition"

### Artifact 3 — Build-with-Claude-Code essay (~6-8 hours)

~1,500 words on building a non-trivial agent solo by orchestrating Claude
Code as pair programmer. The single most on-message piece for an
**Anthropic** application specifically — Cat Wu and Scott White's teams
would recognize the exact problems.

Concrete examples (each lived through in this build):
- Where Claude got it right first try (SSE streaming UI; DATA_DIR refactor)
- Where it got it wrong (the 2026-05-28 framing-drift incident — Claude
  reverted to TruVs framing despite explicit redirection; overwrote
  CLAUDE.md without realizing prior content had existed locally; falsely
  asserted "nothing was lost" based on incomplete git evidence)
- What that taught about how agentic tools should be built FOR users
  (verification before assertion; context that survives compaction; framing
  rules that propagate across sessions)

Lives at: `docs/build_with_claude_code.md` + linked from demo + portfolio.

---

## Phase 2 — Apply (after Phase 1 ships, parallel)

Four target companies, same week, tailored cover notes pointing at
different artifacts:

| Company | Role | URL | Lead artifact for cover note |
|---|---|---|---|
| **Cursor** | PM, Agent Harness | https://cursor.com/careers | Eval framework + decision log |
| **Anthropic** | PM, Claude Code (Platform) | https://job-boards.greenhouse.io/anthropic | Build-with-Claude-Code essay + decision log |
| **Harvey** | Staff PM, Agent Platform | https://jobs.ashbyhq.com/harvey/39c40209-798d-47e9-a600-742c876c536b | Decision log (HITL + cross-system orchestration) |
| **LangChain** | PM, LangSmith | https://jobs.ashbyhq.com/langchain/27af5f96-b287-4bcc-8679-f96686dc7c8d | Eval framework writeup |

Cover-note template (~150 words): "Here is an end-to-end agent I built
solo via Claude Code [demo URL]. Here are the trade-offs I made [decision
log URL]. Here is the eval framework I designed on top of it [eval
framework URL]. Here is what I'd build next at [team]."

**Application gate:** all 3 artifacts shipped, linked, and live on Railway
before any cover note goes out.

---

## Honest ceiling read

From research run 2026-05-28. Without founder / FAANG-PM / AI-brand
credentials on the resume:

- Senior PM at Anthropic, OpenAI, Vercel, Cursor: **achievable**
- Staff PM at Cursor, Harvey, LangChain: **achievable with the agent +
  3 artifacts as portfolio**
- Principal PM at Anthropic: **stretch** (likely down-leveled to Staff at offer)
- Principal PM at OpenAI / Google DeepMind / Meta AI: **unlikely** without
  one of the missing credentials

The unlock against the credential gap is the working agent + opinionated
written artifacts, not more credentials. Most PMs cannot ship that combo —
that's the signal.

---

## Primary-source reading list (for voice + framing calibration)

Read these before writing artifacts. Don't paraphrase or summarize —
absorb the voice.

1. [Cat Wu — Product Management on the AI Exponential](https://claude.com/blog/product-management-on-the-ai-exponential) — single most important PM-craft read
2. [Scott White (Head of Product, Claude) on Creator Economy](https://creatoreconomy.so/p/inside-the-best-ai-model-for-coding-claude-scott-white) — the cleanest verbatim articulation of the embedded-PM thesis
3. [Lenny's Newsletter — How Anthropic's Product Team Moves (Cat Wu)](https://www.lennysnewsletter.com/p/how-anthropics-product-team-moves)
4. [Lenny's Newsletter — Mike Krieger CPO](https://www.lennysnewsletter.com/p/anthropics-cpo-heres-what-comes-next)
5. [Cursor — PM Agent Harness JD](https://cursor.com/careers) — read as a spec for self-positioning
6. [Harvey — Staff PM Agent Platform JD](https://jobs.ashbyhq.com/harvey/39c40209-798d-47e9-a600-742c876c536b)
7. [LangChain — PM LangSmith JD](https://jobs.ashbyhq.com/langchain/27af5f96-b287-4bcc-8679-f96686dc7c8d)

---

## Out of scope (do not propose)

The following are NOT directions for this project. If a session drifts
into recommending any of these, stop and re-read `CLAUDE.md`:

- Adding more features to the agent (substrate is sufficient)
- Pitch decks / generic "AI PM" blog posts
- MCP server wrapper, retroactive PRD, platform-vs-vertical reframe —
  deferred to "if interviewer asks" not "ship first"
- TruVs go-to-market materials (this is no longer a TruVs deliverable)
- Sales/marketing assets, pricing frameworks, partner-reactivation memos
- Internal/customer case studies of this agent
- Anything aimed at enterprise procurement buyers
- Applications to OpenAI / Cognition / Replit / Codeium / Glean — research
  deprioritized these for this profile; revisit only if Phase 2 returns
  no Phase-3 interviews
