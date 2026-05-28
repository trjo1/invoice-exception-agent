# Authoring Golden Cases — guide

**Status:** Reference (Phase 9). Test harness is shipped; cases run via `make test-golden`.
**Date:** Originally 2026-05-10; revised 2026-05-13
**Owner:** Tribhuvan Joshi

---

## What a golden case is

A single end-to-end test of the agent on one named exception scenario. The
case lives as one YAML file in `tests/golden_cases/`. The test harness runs
every case in this folder against the agent on every build; pass / fail per
case is the regression signal.

**Today there are 24 golden cases (GTC-001 through GTC-024).** Every one of the
13 `ExceptionCategory` enum values has at least one case; some have multiple.
Target for production-readiness: 40 cases minimum, with the remaining 16
authored against a specific buyer's scenarios once a paying engagement is in
flight.

---

## YAML schema — the fields you must fill in

Every case has the same top-level structure (see `GTC-002-price-variance.yaml`
for the canonical template):

```yaml
id: GTC-NNN
title: <one-line plain English scenario description>
exception_category: <one of the 12 categories below>
difficulty: easy | medium | high
created: YYYY-MM-DD
notes: |
  <2-4 sentences explaining what this case tests and why it matters.>

input:
  events: [...]           # the event sequence the agent receives
  documents: [...]        # any invoices / emails / forms attached, with ground truth

expected:
  classification: {...}   # what the agent should classify the case as
  recommendation: {...}   # what the agent should recommend
  hitl: {...}             # which tier, routed to whom
  drafting: {...}         # what (if any) drafts the agent should produce
  execution: {...}        # what cross-system actions should / should not happen
  stage9: {...}           # cost ceiling, latency ceiling, auto-pass flag

pass_criteria:
  - <boolean expression that must be true for the case to pass>
```

---

## The 13 exception categories

Every case is in exactly one (the `exception_category` field). These match
`src/p2p_agent/models/classification.py::ExceptionCategory`.

| Category | When to use |
|---|---|
| `none` | Clean 3-way match, no exception. Anchor for auto-pass tests. |
| `three_way_match_price_variance` | Invoice unit price differs from PO. |
| `three_way_match_quantity_variance` | Invoice quantity differs from goods receipt. |
| `missing_po` | Invoice received with no PO reference, or PO doesn't exist. |
| `missing_goods_receipt` | Invoice arrived before goods receipt was recorded. |
| `missing_approval` | PO authorized but approver chain incomplete. |
| `duplicate_invoice` | Same invoice (or same PO already paid) appears again. |
| `fraud_signal` | Multiple invoices same PO short window; suspicious pattern. |
| `vendor_master_gap` | Vendor not in master file; needs onboarding. |
| `cross_currency_mismatch` | PO and invoice in different currencies with FX variance. |
| `tax_field_mismatch` | Tax line missing, miscoded, or wrong rate for jurisdiction. |
| `payment_term_mismatch` | Invoice payment terms differ from PO authorization. |
| `other` | Non-invoice events inside the P2P process (delivery delays, supplier comms). |

---

## Authoring workflow

### 1. Pick a scenario

Look at:
- What's missing in `docs/test_corpus_design.md §3` (the curated list of 40 named cases)
- What failure modes have been discovered during testing (in `docs/failure_mode_catalog.md` once it exists)
- What buyer pain points come up in sales conversations

Aim for one new case per category until each category has at least 3
representative cases, then go deeper on long-tail edge cases.

### 2. Copy the closest existing case

```bash
cp tests/golden_cases/GTC-002-price-variance.yaml \
   tests/golden_cases/GTC-025-new-scenario.yaml
```

Rename ID, title, category. Change every field. Don't leave any field copied
from the template (the test harness validates field consistency).

### 3. Fill in the input — events + documents

The `events` array is the event sequence the agent would observe in production:
PO created → goods receipt → invoice received, in that order, with realistic
timestamps. Use realistic SAP / Ariba / ServiceNow / Email event types.

The `documents` array references the unstructured artifacts (invoice PDFs,
emails) the agent has to read. For each, include the `ground_truth_fields`
section — what the extractor should pull out. The harness compares the agent's
extraction to this.

You can reference synthetic documents that don't yet exist (they will be
generated through the subscription-mode workflow). The harness will only
fully run the case once the document is in `test_corpus/synthetic/`.

### 4. Fill in expected — what the agent should do

The `expected` block is the test oracle. Every field is what success looks
like.

| Sub-block | What to put |
|---|---|
| `classification` | `class_label` from the 13 categories. `min_confidence`. `must_contain_evidence` — strings that must appear in the agent's evidence array. |
| `recommendation` | `action` from the `RecommendedAction` enum. `rationale_must_mention` — strings that must appear in the rationale text. |
| `hitl` | `tier` (0 = none, 1 = auto-pass, 2 = approver, 3 = supervisor/fraud). `routed_to` — named role. |
| `drafting` | `must_produce_draft` boolean; if true, `draft_type`, `draft_recipient`, `draft_content_must_mention`. |
| `execution` | What cross-system writes are allowed / forbidden. Always set `must_not_post_invoice_before_approval` for non-auto-pass cases. |
| `stage9` | `auto_pass` boolean. `cost_per_case_usd_max` — what the case should cost (use $0.50 default; lower for simple cases, higher for multi-doc reasoning). |

### 5. Write the pass criteria

The `pass_criteria` array is the binary signal. Each item is a boolean
expression evaluated against the agent's output. If all are true, the case
passes.

Conventions:
- Use the same field names as in the `expected` block.
- Express comparisons with `==`, `>=`, `<=`. The harness parses these.
- Be specific, not vague. "agent did the right thing" is not a pass criterion.

### 6. Validate the YAML

The simplest check — does it parse and does the harness pick it up:

```bash
uv run pytest tests/test_golden_cases.py -v -k GTC-025
```

That runs the new case (auto-discovered from the folder) through the live
agent and reports pass/fail per sub-block (classification, recommendation,
hitl, drafting). Sub-blocks that aren't yet implemented are xfailed —
expected failures show in yellow, not red.

### 7. Add the case to the run

The harness auto-discovers any YAML file in `tests/golden_cases/`. No
registration step needed. Run the full set:

```bash
make test-golden
```

Or a single case:

```bash
uv run python scripts/run_golden_set.py --case GTC-025
```

---

## How to think about hard cases

Cases that test the agent's edge behavior — where the difference between
"good agent" and "great agent" lives.

| Difficulty | What to test |
|---|---|
| `easy` | Single archetype; structured signal; little ambiguity. (GTC-001, GTC-013, GTC-018) |
| `medium` | Two archetypes interacting; some ambiguity; one ground-truth answer. (GTC-002, GTC-004, GTC-006, GTC-009, GTC-010, GTC-011, GTC-012, GTC-014, GTC-017, GTC-022) |
| `hard` | Multi-doc reasoning; pattern detection across time; anti-false-positive anchors; high cost of being wrong. (GTC-003, GTC-007, GTC-015, GTC-016, GTC-019, GTC-020, GTC-021, GTC-023, GTC-024) |

Spread cases across difficulty levels so the harness reports a meaningful
mix, not just easy wins.

---

## Common authoring mistakes

1. **Pass criteria too generous.** "classification.class_label != none" is
   not enough — be specific about which category.
2. **Drafting content checks rely on exact strings.** Use `must_mention` lists
   (the harness substring-matches) rather than full-text comparisons.
3. **No counterfactual on high-stakes cases.** Per the reference architecture,
   counterfactuals build trust on consequential decisions. Set
   `counterfactual_should_exist: true` for any HITL Tier 2+ case.
4. **Forgetting the cost ceiling.** Without `cost_per_case_usd_max`, the
   case can pass even if it cost $5 to run. Set the ceiling so cost
   regressions break the case.
5. **Cases that test the LLM, not the agent.** If a case fails just because
   the model is bad at language X, it's not a useful regression test. Aim
   for cases where the agent's discipline (HITL routing, halt logic, audit
   trail) is what's being verified.

---

## Holdout convention

20% of golden cases are held out from the build team — they only run at
integration test time, not during build. Hold out the cases by adding a
`holdout: true` field to the YAML. The harness skips holdout cases unless
explicitly invoked with `--include-holdout`.

This prevents the agent from being overfit to the visible regression set.

Pick the holdout cases pseudo-randomly across categories and difficulties.

---

## Reference

- Canonical template: `tests/golden_cases/GTC-002-price-variance.yaml`
- Existing cases: `tests/golden_cases/GTC-001` through `GTC-024` (24 total covering all 13 categories)
- **Anti-false-positive anchors** (the "should NOT flag" cases): GTC-019 (recurring services), GTC-020 (emergency PO), GTC-024 (strategic vendor MSA tolerance)
- Exception categories: `src/p2p_agent/models/classification.py`
- Test harness: `scripts/run_golden_set.py`
- Recommended actions enum: `src/p2p_agent/models/recommendation.py`
