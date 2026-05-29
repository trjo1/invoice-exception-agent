# Decision log

Five non-obvious PM calls I made building the invoice-exception agent. For
each: the tradeoff I was actually weighing, the call I made, what I gave up,
and what would make me reconsider. Written after the fact, but each
decision is real and the artifacts referenced are in the codebase.

The point of this doc isn't "here are my features." It's "here is how I
think when I'm holding a tradeoff." A hiring reader can verify each call
against the running demo at <https://web-production-45a73.up.railway.app>
and the source at <https://github.com/trjo1/invoice-exception-agent>.

---

## 1. Three HITL routing tiers, not two or five

**The tradeoff.** The agent makes consequential calls (credit memos,
vendor escalations, GL corrections) and a human has to sign off on the
risky ones. Confidence is a continuous signal — the model gives me 0.0
to 1.0. Routing is a discrete decision — I have to bucket. How many
buckets earn their cost?

**The call.** Three: auto-pass, approver review, supervisor escalation.
Auto-pass for high-confidence + low-stakes (clean invoices under a
threshold). Approver review for the middle band (tax mismatches,
recurring-vendor variances within tolerance). Supervisor for low
confidence OR high stakes (cross-currency mismatches, fraud signals,
strategic-vendor exceptions).

Two tiers would have lost the cost/risk distinction between routine
ambiguity and a case that needs to escalate. Five tiers — which I drafted
on a Friday — collapsed by Monday because the routing rules were
producing decisions I couldn't justify with a single sentence to a hypothetical
ops manager. If I can't explain the rule, the operator won't trust it.

**What I gave up.** False precision. The continuous confidence score
becomes three buckets. I'm throwing away information that might matter at
the edges.

**What would make me reconsider.** Real ops data showing that Tier 2
cases cluster bimodally into "obvious approve" vs "actually contested" —
that's a fourth tier earning its cost. Right now I have no such evidence,
so three holds.

---

## 2. Browse-only public demo, no user file upload

**The tradeoff.** Standard SaaS demo UX is "upload your own data, see
what the product does." That conversion path works because the product is
data-agnostic. An agent that depends on a closed-world data context
(vendor master, PO records, GR receipts) doesn't have that luxury — a
random visitor's PDF won't match any vendor in the synthetic master data,
so the agent will reliably flag `missing_po` or `vendor_master_gap` and
the demo will look broken.

**The call.** Removed the upload form. Replaced it with a hint-free
browse page: 20 invoices, click to preview the PDF, click to run. No
expected-outcome labels in the browse view. A separate curated-scenarios
dropdown coexists for the "watch this specific behavior land" use case.

**What I gave up.** The "anyone can try with their own data" feeling
that drives demo conversion in normal SaaS. Also the ability to learn
from real-world invoice distributions hitting the demo.

**What would make me reconsider.** A real vendor master + PO pipeline
behind the demo (not synthetic). Then upload becomes safe because OOD
inputs are now in-distribution. Or: a dedicated "BYO data" flow with a
matched synthetic vendor master generated on the fly. Both are bigger
than what the demo needs to do its job right now, which is convince a
reader the agent works.

---

## 3. Open-source models default plus daily spend cap, not feature-gating

**The tradeoff.** A public demo with LLM calls behind it can cost a lot
if it gets traction. Two ways to bound spend: (a) gate features behind
sign-up + per-user throttling, (b) pick models cheap enough that the
unbounded version stays bounded, plus a daily cap as the failsafe.

**The call.** DeepSeek V4-Flash for extraction / classification / drafting,
DeepSeek R1 for decision reasoning, both via OpenRouter. Per-run cost
about $0.005. Daily spend cap at the model-client layer; runs after the
cap show a friendly "back tomorrow" message instead of erroring.

The friction model matters. Sign-up gates kill demos at the click. The
conversion I cared about was "recruiter clicks demo → recruiter sees the
full pipeline land in 60 seconds." Per-user attribution mattered less
than that conversion.

**What I gave up.** Analytics on who's using the demo. No funnel, no
emails collected, no per-user usage caps. If someone burns through the
daily budget I lose visibility for the rest of the day.

**What would make me reconsider.** A specific employer / sponsor wanting
demo attribution for hiring purposes. Even then I'd add a Vercel-side
thin auth layer rather than gate the actual agent — keep the product
frictionless, push the friction up to the wrapper.

---

## 4. Mock action executor as the safety boundary, not waiting for real creds

**The tradeoff.** The last node in the pipeline is the action executor
— it actually does the thing the agent recommends (file the credit memo,
send the supplier email, post the GL correction). Real execution needs
SAP / Ariba / ServiceNow credentials I don't have. Options: (a) wait
until I get creds, (b) ship a mock pattern that produces a complete
audit log without real side effects.

**The call.** Mock. The mock executor implements the same
`ActionExecutor` abstract base class as the real one will. It receives
a `RecommendedAction`, runs a "would have done X" recipe, writes a
structured audit-log entry, and returns. When real creds arrive, swap
the implementation — every consumer (tests, golden cases, demo, audit
log readers) keeps working unchanged.

The hidden benefit: building the mock first forced me to design the
action-execution interface clean. If I'd built the SAP-specific version
first, I'd have leaked SAP semantics into the orchestrator. With the
mock as the reference implementation, the orchestrator only knows about
`RecommendedAction` enums and their effects.

**What I gave up.** Live demo of actual production-system effects. I
can show "agent decided to request a credit memo" but not "agent sent
the request to SAP."

**What would make me reconsider.** Nothing. The mock pattern was the
right call even with hindsight. The swap to real creds is a 1-day job
when they arrive.

---

## 5. Hint-free browse vs labeled curated dropdown (the 2026-05-28 UX call)

**The tradeoff.** A working agent demo has two distinct selling motions
that pull the UX in opposite directions. Motion A: confirm an *expected*
behavior — the visitor wants to see "the agent catches the cross-currency
mismatch I primed them to look for." Motion B: surprise with an
*unexpected* finding — the visitor plays the role of an ops user who
doesn't yet know what's in the invoice, and the agent's first useful
output is itself the demo.

Labels help Motion A and ruin Motion B. A label that says "auto-pass
expected" is great if the visitor was going to verify auto-pass and
otherwise can't tell. It's terrible if the demo's job is to surprise.

**The call.** Two paths from `/demo`. (a) A curated scenario dropdown
with full labels and expected-behavior text — same as it was. (b) A new
"browse the library" page that lists 20 invoices with just invoice ID +
vendor name, no chips, no badges, no expected-outcome text. Click to
preview the PDF, click to select, click to run.

**What I gave up.** Simplicity. One UX is cheaper to maintain than two.
Adding the second path also expanded the bundled library from 10 to 20
invoices, which is +500KB in the public repo. Also: the testing surface
doubled.

**What would make me reconsider.** Traffic data showing one path eats
>95% of runs. Today I have no telemetry on the split — informally both
look used, but informal observation is not evidence. If real data ever
shows one path is vestigial, retire it. Two paths is a hypothesis, not
a commitment.

---

*If you want the full set of architectural decisions (model choice per
task, data persistence, eval framework, etc.), see [AGENTS.md](https://github.com/trjo1/invoice-exception-agent/blob/main/AGENTS.md)
for the engineering context. This doc is the subset of decisions where
the call wasn't obvious.*
