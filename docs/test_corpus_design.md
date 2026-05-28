# Test Corpus Design — P2P Exception Orchestrator

**Status:** Locked v1. Engineering reference for the test corpus strategy.

**Date:** 2026-05-10
**Owner:** Tribhuvan Joshi
**Companion docs in this folder:** `PRD.md`, `technical_design.md`, `model_strategy.md`

---

## 1. The problem

The agent needs realistic P2P data to test against. Without a paying customer's data, the test data strategy has to be self-contained — otherwise we either ship a demo that works on cherry-picked cases and fails on real ones, or we let the first paying customer disproportionately influence the IP shape.

**Goal:** get to 70-80% of "real" fidelity using public + synthetic data. Close the gap with one design partner providing anonymized exception logs. Ship the IP regardless of whether the design partner lands.

---

## 2. The 4-source strategy

### Source 1 — BPI Challenge datasets (ANCHOR)

Eindhoven University publishes annual anonymized process-mining datasets at `data.4tu.nl` under CC BY 4.0. Two directly relevant:

- **BPI Challenge 2019** — Dutch government P2P data: 1.6M events, ~250K POs, real exception patterns, supplier-side and buyer-side timestamps. XES + CSV.
- **BPI Challenge 2020** — Travel permit / expense P2P from same source. 5 sub-logs.
- **(Optional) Helpdesk-2017 / RTFM-2014** — for testing the ServiceNow leg.

Free. CC BY 4.0. ~1 day to ingest and structure.

### Source 2 — Synthetic supporting documents

LLM-generated to layer realistic surface detail on the BPI event-log skeleton:

| Asset | Count | Generation approach |
|---|---|---|
| Supplier personas | 20-25 | Hand-designed: vary size, region, language, document layout, quirks |
| Invoice PDFs | 500 | LLM-generated from persona templates; ~30-40% seeded with errors |
| Supplier email threads | 200 | LLM-generated dialogues across PO confirmations / disputes / delivery / credit memos / payment chases |
| Master data records | ~200 vendors, ~30 GL accounts | Generated as JSON / CSV; includes edge cases |
| Approval matrix | 1 templated | Standard ERP shape |

Tooling: Python pipeline under `scripts/generate_*.py`. Output to `test_corpus/synthetic/`. Reproducible (seeded). Estimated API spend: $50-150.

### Source 3 — ERP sandbox environments

| Environment | Access | Purpose |
|---|---|---|
| SAP S/4HANA Cloud trial | 30-day, renewable | Real OData APIs and auth |
| Microsoft Dynamics 365 Finance trial | 30-day | Alternative ERP for portability testing |
| ServiceNow developer instance | Free, persistent | Exception-routing leg |
| SAP Ariba sandbox | Developer agreement | Procurement-specific surface |
| ERPNext on Docker | Free, self-hosted | Backup for when trials lapse |

### Source 4 — Design partner (parallel)

Run outreach for ONE friendly F1000 prospect to share 50-100 anonymized P2P exception records. Target candidates via industry contacts. 6-8 week timeline. If no partner lands in the window, ship the IP on public + synthetic data; the first paying engagement becomes validation.

---

## 3. The golden test set

30-50 named end-to-end test cases. Each is a tuple: (input event sequence, input documents, expected agent action, expected HITL trigger, expected Stage 9 signals, pass criteria).

Stored as YAML in `tests/golden_cases/` so the test harness ingests them.

### Sample cases

| ID | Scenario | Expected behavior |
|---|---|---|
| GTC-001 | Standard 3-way match passes | Auto-pass, no HITL |
| GTC-002 | Invoice unit price 8% over PO; quantity matches | HITL Tier 2 (buyer); draft credit-memo request; halt posting |
| GTC-003 | Invoice references already-paid PO (duplicate) | HALT; flag fraud; escalate; do not post |
| GTC-004 | Invoice from non-master vendor | Route to vendor master team; draft onboarding; hold posting |
| GTC-005 | Goods received but PO never created (retroactive) | Route to buyer; flag for policy review |
| GTC-006 | Quantity invoiced > received (over-delivery) | HITL Tier 2; draft credit request |
| GTC-007 | Multiple invoices reference same PO (split-invoice fraud) | HITL Tier 3; halt all involved |
| GTC-008 | Tax field missing on EU VAT invoice | HITL Tier 2; draft tax-correction request |
| GTC-009 | Supplier reports delivery delay; PO open | Update PO ETA; notify buyer; draft acknowledgement |
| GTC-010 | Cross-currency invoice (PO USD, invoice EUR) | HITL Tier 2; validate FX rate |
| ... | ~30 more across price / qty / approval / vendor / fraud / tax / delivery / cross-system / terms | |

### Build cadence

- Weeks 1-2: First 20 cases authored
- Weeks 3-4: Expand to 40 cases
- Continuous: adversarial test cases added as new failure modes are found

---

## 4. Test infrastructure

| Component | Purpose | Effort |
|---|---|---|
| Test harness | Runs the agent against every golden case, compares against expected, writes pass/fail + traces | ~5-7 days |
| Stage 9 metric instrumentation | Computes the 6 signals from execution traces | ~3-5 days (reused in prod) |
| Failure-mode catalog | Living doc capturing every failure + root cause + mitigation + test case that catches it | Ongoing |

---

## 5. Build sequence (first 8 weeks)

| Week | Workstream | Deliverable |
|---|---|---|
| 1 | BPI ingestion + synthetic pipeline scaffolding | BPI 2019 + 2020 loaded; pipeline skeleton |
| 2 | Supplier personas + first 100 synthetic invoices | 20 personas; 100 invoices; 10 golden cases |
| 3 | Email threads + master data + 200 more invoices | 200 emails; master data; 300 invoices; 20 golden cases |
| 4 | ERP sandboxes + test harness | SAP / D365 / SNow sandboxes live; harness running |
| 5-6 | Expand corpus + golden set | 500 invoices; 40 golden cases; Stage 9 metrics computed |
| 7-8 | Design partner outreach (parallel) + adversarial cases | First partner conversations; adversarial set added |

---

## 6. Decisions needed to move

| # | Decision | Recommendation |
|---|---|---|
| 1 | Anchor dataset — BPI 2019, 2020, or both? | Both. Different P2P shapes; combined broader exception variety. |
| 2 | Synthetic-doc generation API budget | $150 / month cap for first 3 months |
| 3 | Primary ERP sandbox | SAP S/4HANA Cloud trial first; D365 in week 4 |
| 4 | Design partner pursuit aggressiveness | 1 partner in 8 weeks target; don't block IP build on it |
| 5 | Engineering capacity | ~6-8 weeks of 1 senior engineer + part-time lead. In the original TruVs build this required pulling an engineer at 50% or partnering with an offshore team for the synthetic pipeline. |

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| BPI data is over-anonymized | Synthetic-doc layer adds realistic surface detail on top of real event patterns |
| Synthetic invoices "feel" synthetic to the agent and inflate auto-pass | Mix in 50-100 real public invoice PDFs (anonymized sample forms); visual noise injection |
| ERP sandbox trials expire mid-build | Renewable trials; ERPNext-on-Docker as durable backup; design-partner sandbox if landed |
| Test corpus drift — agent overfits to golden set | 20% of golden cases held out from build team; quarterly re-authoring of 10-20%; adversarial cases added continuously |

---

## 8. Companion docs

| Doc | Status | Trigger to write |
|---|---|---|
| `02_technical_design.docx` (practice) / `docs/technical_design.md` (here) | Draft v1 written | After §6 decisions |
| `03_build_plan.md` | TBD | After technical design lands |
| `04_stage9_measurement_spec.md` | TBD | In parallel with build plan |
| `05_synthetic_doc_generation_playbook.md` | TBD | After first 100 invoices generated |
| `06_design_partner_pitch.md` | TBD | After §6 decision #4 |

---

## Anchor references

- Companion docs in this folder: `PRD.md`, `technical_design.md`, `model_strategy.md`, `architecture.md`, `CHANGELOG.md`
- BPI Challenge data portal — `data.4tu.nl/categories/business_process_intelligence_challenges`
