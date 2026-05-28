# Archetypes + Patterns Cheatsheet

Quick reference. The full taxonomy lives in `../../../01_foundational_ip/framework_v2.docx` and the named patterns are on slide 11 of `../../../04_pitch/practice_overview_v5.pptx`. This cheatsheet keeps the engineering session from needing to traverse for the basics.

---

## The 5 archetypes — the alphabet

Every process agent maps to one or more of these. Defined by primary cognitive action and primary output into the process.

| # | Archetype | Primary action | Primary output | Default mode (build vs accelerate) |
|---|---|---|---|---|
| 1 | **Extraction** | Reads unstructured input | Structured fields written downstream | Accelerate (Botminds / AI Builder / Document AI) |
| 2 | **Routing & Triage** | Classifies a unit of work | A routing decision (queue / owner / tier / priority) | Accelerate if stack-aligned, else build |
| 3 | **Decision-Support & Decision-Making** | Analyzes a substantive question | A recommendation (or with trust, decision + execution) | **Build** (default) |
| 4 | **Coordination & Orchestration** | Manages cross-system workflow | Completed multi-system workflow with HITL gates | Accelerate if stack-aligned, else build |
| 5 | **Drafting** | Generates content from inputs | Written artifact for human review and send | Accelerate if messaging surface in major cloud |

Coverage data (159-case deep research): Routing 54%, Decision-Support 48%, Drafting 45%, Coordination 41%, Extraction 34%. **92% of cases combine 2+ archetypes.**

Out of scope (explicitly NOT these archetypes):
- Personal productivity (chat with files, summarize my email)
- Pure knowledge-retrieval chatbots
- Pure monitoring / anomaly detection
- Code-gen / dev tooling
- R&D / discovery

---

## The 5 named combination patterns — the words real agents assemble into

Most agents combine 2-3 archetypes. The 5 named patterns cover ~70% of analyzed cases.

| # | Pattern | Building blocks | Where it lives |
|---|---|---|---|
| 1 | **Agent-Assist** | Routing + Drafting (± Coordination) | Customer service, IT support, contact centers. Most common pattern. |
| 2 | **Multi-System Process Agent** | Coordination + Routing (+ often Drafting / Decision-Support) | P2P, O2C, service ops, cross-functional. **This is Agent 1.** |
| 3 | **Document-to-Decision** | Extraction + Decision-Support (± Coordination) | AP exception review, claims adjudication, contract review. **Agent 2.** |
| 4 | **Triage-and-Recommend** | Routing + Decision-Support | IT support, employee help, advisor workflows. |
| 5 | **Document Intake + Routing** | Extraction + Routing | Document-heavy ops, claims intake. **Part of Agent 3.** |

Agent 3 = Pattern 3 + Pattern 4 hybrid for the full claims pipeline.

---

## Where this agent fits

**Agent 1 = Multi-System Process Agent (Pattern 2).**

Archetypes in priority order:
- **Coordination & Orchestration** — primary archetype. Holds workflow state across SAP / Ariba / ServiceNow. Cross-system writes gated by HITL.
- **Routing & Triage** — exception classification (12 named categories per `models/classification.py::ExceptionCategory`).
- **Decision-Support** — recommendation + rationale + counterfactual per exception.
- **Drafting** — sub-component for supplier comms and internal notes when an action requires written output.

NOT in this agent's archetype scope:
- Pure Extraction (Botminds / Document AI handle this upstream when invoice extraction is the goal — see `docs/sap_sandbox_setup.md` for the canonical extraction route).

---

## Build-vs-accelerate decision for THIS agent

Per the framework template in `../../../01_foundational_ip/build_vs_accelerate_decision_template.docx`:

- Cross-system orchestration with bespoke business rules → **BUILD** (the orchestration logic IS the moat)
- No accelerator covers SAP + Ariba + ServiceNow cleanly without locking to MS (SynOps) or Google (Gemini Enterprise)
- This locks Agent 1 as a custom build using LangGraph + open-source models (test phase) / closed models per-task (production overrides)

---

## When you need more depth

This cheatsheet condenses the archetype taxonomy used during the original build. The longer reference architectures and 159-case validation that originally backed it lived in a separate TruVs IP repository and are not part of this public release. The cheatsheet stands alone as a self-contained primer.
