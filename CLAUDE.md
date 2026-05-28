# Project framing — invoice exception agent

**Read this first, every session. The framing rules below are not optional.**

This file scopes how Claude Code should think about THIS project. The parent
directory's CLAUDE.md (`../../CLAUDE.md`, the TruVs AI Practice context) is
for a different body of work and does NOT apply here. If the two ever
conflict, this file wins.

---

## What this project is

A personal portfolio + learning project owned by TJ. The codebase started as
a reference implementation built while TJ was AI Practice Lead at TruVs in
2026 — but TJ is no longer at TruVs and the project is no longer a TruVs
deliverable. It is maintained now as:

1. A learning artifact — TJ uses it to build depth in agents-shaped systems
2. A portfolio piece — to land roles in the agents space (AI engineer,
   agents-focused technical/product PM, applied AI roles)

The agent itself (procure-to-pay exception orchestration, 10 logic nodes,
HITL queue, Stage 9 measurement dashboard, etc.) is the substrate. The
story being told around it is about **TJ's craft as an AI builder**,
NOT about anyone's go-to-market motion.

---

## Framing rules — what NOT to do

When proposing work, writing docs, or framing recommendations:

- **DO NOT** treat TruVs as the project's audience, owner, or beneficiary
- **DO NOT** invoke the Process-to-Agent (P2A) Method, the 9-stage operating
  model, the 5-phase customer view (SEE/SIZE/STITCH/SHIP/SUSTAIN), the
  $200K floor, the 4-bundle pricing model, the AI Process Diagnostic, or
  any other framework from the parent TruVs CLAUDE.md
- **DO NOT** frame Stage 9 (the measurement dashboard) as "the recurring
  revenue moat" or any other GTM concept. It's a measurement dashboard —
  technically interesting, commercially out of scope here
- **DO NOT** propose work because "Sridhar wants X" or "TruVs would pay for Y"
- **DO NOT** generate sales assets, pitch decks, pricing sheets, case study
  marketing, partner reactivation memos, or anything aimed at enterprise
  procurement buyers
- **DO NOT** quietly drift back to TruVs framing in a roadmap, a writeup,
  or a recommendation. If you catch yourself doing this mid-response, stop
  and rewrite

The fact that the agent was originally built at TruVs is true historical
context. It belongs as **one sentence** in the origin section of public
docs (`README.md`, `agent_overview.html`, `docs_index.html`) — and that's
where it already lives. Don't re-introduce TruVs framing anywhere else.

---

## Framing rules — what to do

- **Frame work around interview-credibility:** "Would this be a more
  defensible artifact in front of a hiring manager?" "What skill does this
  let TJ demonstrate that he can't currently demonstrate?"
- **Optimize for depth over breadth:** one well-built eval harness with a
  writeup beats five surface-level features. The portfolio reader's question
  is "how deep does this person actually go?"
- **Honest signal over polish:** if something is a stub, say so. The
  audience (engineering teams hiring for agents work) values "I shipped X,
  here's where it breaks, here's how I'd extend it" over glossy claims.
- **Audience:** hiring managers and engineering teams at AI/agents-shaped
  companies (foundation-model labs, AI-native startups, applied AI teams
  at larger firms). NOT enterprise procurement buyers.

---

## Project status

The codebase is at a pause point. See `ROADMAP.md` for what's shipped and
what the next phase looks like. The high-leverage next direction is the
**learning + evals layer** — closing the HITL feedback loop, building
aggregate eval metrics on top of golden cases, A/B prompt evaluation,
drift monitoring on Stage 9. **Specifics intentionally not scoped yet.**
The roadmap captures the open questions that need TJ's input before work
begins on that phase.

---

## When you start a session

1. Read this file (`CLAUDE.md`) — the framing rules above
2. Read `ROADMAP.md` for current phase + what's open
3. Read `AGENTS.md` for engineering context (architecture, locked decisions,
   model strategy) if you'll touch code
4. If a conversation drifts toward TruVs framing — Stage 9 as a moat, P2A
   Method, $200K floor, partner reactivation, target accounts — **STOP**
   and re-read this file before continuing
