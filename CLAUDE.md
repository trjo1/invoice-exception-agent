# Project framing — invoice exception agent

**Read this first, every session. The framing rules below are not optional.**

This file scopes how Claude Code should think about THIS project. The parent
directory's CLAUDE.md (`../../CLAUDE.md`, the TruVs AI Practice context) is
for a different body of work and does NOT apply here. If the two ever
conflict, this file wins.

---

## What this project is

A personal portfolio + learning project owned by TJ (Tribhuvan Joshi). The
codebase started as a reference implementation built while TJ was AI
Practice Lead at TruVs in 2026 — but **TJ is no longer at TruVs and the
project is no longer a TruVs deliverable**. It is now:

1. A learning artifact — TJ uses it to build depth in agent-shaped systems
2. A portfolio piece — to land Staff or Principal PM IC-track roles at
   builder-friendly AI companies

The agent itself (procure-to-pay exception orchestration, 10 logic nodes,
HITL queue, Stage 9 measurement dashboard, etc.) is the substrate. The
story being told around it is about **TJ's craft as a technical PM who
ships**, not about anyone's go-to-market motion.

---

## The job-hunt frame (this drives every decision)

**Optimizing for Staff/Principal PM IC-track roles at builder-friendly AI
companies. Current target list: Cursor, Anthropic, Harvey, LangChain.**

What those companies screen for (from primary-source research, see
`ROADMAP.md` for full citations):

- **Product taste, demonstrated.** Cat Wu (Head of Product, Claude Code,
  Anthropic): *"product taste is still a very rare skill to have and we'll
  pretty much hire anyone who we feel has demonstrated this strongly."*
- **Eval design fluency.** This is the L6→L7 differentiator. Senior PMs
  *use* evals; Principal PMs *design* the framework and use it as the
  high-bandwidth interface to research. Scott White (Head of Product,
  Claude): *"upskilling on evals is really important — learning that
  language and framework."*
- **Technical fluency that earns engineering respect.** Python/SQL working
  proficiency in JDs. Many PMs at these cos are ex-engineers. Building
  prototypes on the weekends is the Krieger hire bar.
- **Working artifact > deck.** Anthropic explicitly does not use pitch
  decks for PM evaluation. Cursor's PM application asks for GitHub URL +
  project portfolio note.
- **Voice: concrete, technical, first-person, no hype, principle-style
  sentences.** Read Cat Wu's blog post on the AI exponential, or Scott
  White's Creator Economy interview, before writing anything PM-shaped on
  this project. That voice is the target.

**Implication for every session on this project:** every piece of work
should produce either (a) a tangible PM-shape artifact you can point at
in an interview, or (b) deepen a skill you can credibly speak to.
**Pure engineering polish is the wrong allocation of time.**

---

## Framing rules — what NOT to do

- **DO NOT** treat TruVs as the project's audience, owner, or beneficiary
- **DO NOT** invoke the Process-to-Agent (P2A) Method, 9-stage operating
  model, 5-phase customer view (SEE/SIZE/STITCH/SHIP/SUSTAIN), $200K floor,
  4-bundle pricing, AI Process Diagnostic, or any other framework from the
  parent TruVs CLAUDE.md
- **DO NOT** frame Stage 9 (the measurement dashboard) as a "recurring
  revenue moat" or any other GTM concept. It is a measurement system that
  doubles as eval infrastructure — that's the framing
- **DO NOT** propose work because "Sridhar wants X" or "TruVs would pay
  for Y"
- **DO NOT** generate sales assets, pitch decks, pricing sheets, case-study
  marketing, partner-reactivation memos, target-account lists, or anything
  aimed at enterprise procurement buyers
- **DO NOT** quietly drift back to TruVs framing in a roadmap, writeup, or
  recommendation. If you catch yourself doing this mid-response, stop and
  rewrite

The fact that the agent was originally built at TruVs is true historical
context. It belongs as **one sentence** in the origin section of public
docs (`README.md`, `agent_overview.html`, `docs_index.html`) — and that's
where it already lives. Don't re-introduce TruVs framing anywhere else.

---

## Framing rules — what to do

- **Frame work around the job-hunt frame above.** "Would this be a more
  defensible artifact in front of a hiring manager at Cursor / Anthropic /
  Harvey / LangChain?" If no, deprioritize.
- **Default to depth over breadth.** One well-built eval framework with a
  writeup beats five surface-level features. Principal-PM-shaped readers
  are screening for depth.
- **Honest signal over polish.** If something is a stub, say so. If a
  number looks bad, publish it anyway — that's the eval. Hiring teams
  here value "I shipped X, here's where it breaks, here's how I'd extend
  it" over glossy claims.
- **Voice: Scott White / Cat Wu.** Concrete, first-person, principle-style
  sentences. No hype words. No frameworks-for-frameworks'-sake. No
  "leveraging synergies."
- **Audience for everything public-facing:** hiring managers and
  engineering teams at AI/agents-shaped companies. NOT enterprise
  procurement buyers.

---

## Project status

The codebase is at a deliberate pause point on engineering work. See
`ROADMAP.md` for the current phase (Phase 1: ship 3 PM-shape artifacts on
top of the existing substrate) and the four target companies for the
application phase that follows.

What's shipped end-to-end as substrate: 10-node async pipeline, HITL
3-tier queue, Stage 9 dashboard, 24 golden cases, public Railway demo,
Vercel portfolio integration, DATA_DIR-based persistence. 85/85 tests pass.

What's NOT in scope right now: more agent features, real SAP connectors,
LangGraph wrap, Postgres migration. The substrate is sufficient.

---

## When you start a session

1. Read this file (`CLAUDE.md`) — the framing rules above
2. Read `ROADMAP.md` for current phase + the artifact-shipping order
3. Read `AGENTS.md` for engineering context (architecture, locked decisions,
   model strategy) ONLY if you'll touch code
4. If a conversation drifts toward TruVs framing — Stage 9 as a moat, P2A
   Method, $200K floor, partner reactivation, target accounts — **STOP**
   and re-read this file before continuing
5. If a recommendation drifts toward "let's add features" instead of
   "let's ship the next PM-shape artifact" — **STOP** and re-read the
   job-hunt frame above
