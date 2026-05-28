"""End-to-end pipeline evaluation.

For each sampled invoice in the corpus:
  1. Run `run_invoice_pipeline(pdf)` → (extraction, classification).
  2. Diff extraction against the ground-truth JSON sidecar (per-field).
  3. Compare classification against the persona-error-mode → category mapping.
  4. Compute joint accuracy ("both correct").
  5. Report per-field, per-category, and joint metrics + total cost.

This is the broader-signal eval that exercises the full PDF → classification
flow. Cost ~$0.0013 per invoice on V4-Flash (one extract + one classify).

Usage:
    uv run python scripts/eval_pipeline.py                # 100-invoice random sample
    uv run python scripts/eval_pipeline.py --full         # all 490
    uv run python scripts/eval_pipeline.py --persona P003
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from p2p_agent.llm.client import ModelClient  # noqa: E402
from p2p_agent.orchestrator import run_invoice_pipeline  # noqa: E402

# Reuse the diff machinery from the extractor eval (field-aware matchers).
from eval_extractor import diff_invoice  # noqa: E402

CORPUS_DIR = REPO_ROOT / "test_corpus" / "synthetic" / "invoices"
MAPPING_PATH = REPO_ROOT / "config" / "error_label_to_category.yaml"
LOG_PATH = REPO_ROOT / "logs" / "llm_calls.jsonl"

# Category -> set of acceptable actions for the corpus eval. Multiple are
# allowed because some categories can resolve via either of two reasonable
# paths (e.g. quantity variance via credit memo OR buyer escalation).
ACCEPTABLE_ACTIONS_BY_CATEGORY: dict[str, set[str]] = {
    "none": {"auto_resolve", "approve_pending_review"},
    "three_way_match_price_variance": {"request_supplier_credit_memo", "approve_pending_review"},
    "three_way_match_quantity_variance": {
        "request_supplier_credit_memo",
        "escalate_to_buyer_for_short_delivery",
    },
    "missing_po": {
        "request_missing_po_from_supplier",
        "escalate_to_buyer_for_retroactive_po",
        "request_po_amendment",
    },
    "missing_goods_receipt": {"hold_for_goods_receipt"},
    "missing_approval": {"route_to_vp_finance_approval", "approve_pending_review"},
    "duplicate_invoice": {"escalate_to_fraud", "halt_require_supervisor"},
    "fraud_signal": {"escalate_to_fraud", "halt_require_supervisor"},
    "vendor_master_gap": {"route_to_vendor_master_onboarding", "request_po_amendment"},
    "cross_currency_mismatch": {"escalate_for_fx_review", "approve_pending_review"},
    "tax_field_mismatch": {"request_supplier_correction", "request_supplier_credit_memo"},
    "payment_term_mismatch": {"request_supplier_correction"},
    "other": {"other", "notify_buyer_of_supplier_delay"},
}


def load_error_mapping() -> dict[str | None, str]:
    raw = yaml.safe_load(MAPPING_PATH.read_text())["mapping"]
    out: dict[str | None, str] = {k: v for k, v in raw.items()}
    out.setdefault(None, "none")
    return out


def discover_invoices(persona: str | None = None) -> list[Path]:
    pattern = f"{persona}_*.json" if persona else "*.json"
    return sorted(p for p in CORPUS_DIR.glob(pattern) if not p.name.startswith("_"))


def ground_truth_category(invoice: dict, mapping: dict) -> str:
    return mapping.get(invoice.get("error_injected"), "other")


async def evaluate(
    sidecars: list[Path],
    concurrency: int,
    mapping: dict,
    include_decision: bool,
) -> dict[str, Any]:
    client = ModelClient()
    semaphore = asyncio.Semaphore(concurrency)

    # Build a single shared retriever (model loads once) when decision included.
    retriever = None
    if include_decision:
        from p2p_agent.retrieval import PolicyRetriever
        retriever = PolicyRetriever()

    field_totals: dict[str, list[bool]] = defaultdict(list)
    classification_total = 0
    classification_correct = 0
    action_total = 0
    action_vs_predicted_correct = 0   # legacy "compatibility" metric
    action_vs_truth_correct = 0       # the real action quality
    save_eligible = 0                 # cases where classification was wrong
    save_count = 0                    # ...of which the action was correct vs truth anyway
    action_confusion: dict[tuple[str, str], int] = defaultdict(int)
    joint_total = 0
    joint_correct = 0
    confusion: dict[tuple[str, str], int] = defaultdict(int)
    per_persona_joint: dict[str, list[bool]] = defaultdict(list)
    failures: list[dict[str, Any]] = []

    async def process(sidecar: Path) -> None:
        nonlocal classification_total, classification_correct
        nonlocal action_total, action_vs_predicted_correct, action_vs_truth_correct
        nonlocal save_eligible, save_count
        nonlocal joint_total, joint_correct
        truth = json.loads(sidecar.read_text())
        pdf = sidecar.with_suffix(".pdf")
        if not pdf.exists():
            failures.append({"path": sidecar.name, "error": "PDF missing"})
            return

        async with semaphore:
            try:
                result = await run_invoice_pipeline(
                    pdf_path=pdf,
                    client=client,
                    retriever=retriever,
                    include_decision=include_decision,
                    case_id=sidecar.stem,
                )
            except Exception as e:  # noqa: BLE001
                failures.append({"path": pdf.name, "error": f"{type(e).__name__}: {e}"[:200]})
                return

        # Extraction diff
        diff = diff_invoice(result.extraction.model_dump(), truth)
        extraction_all_match = all(info["match"] for info in diff.values())
        for field, info in diff.items():
            field_totals[field].append(bool(info["match"]))

        # Classification accuracy
        expected_cls = ground_truth_category(truth, mapping)
        actual_cls = result.classification.class_label.value
        cls_match = (expected_cls == actual_cls)
        classification_total += 1
        if cls_match:
            classification_correct += 1
        confusion[(expected_cls, actual_cls)] += 1

        # Action metrics — compute both "vs predicted" (legacy) and "vs truth" (real).
        action_match_for_joint = True
        if include_decision and result.recommendation is not None:
            actual_action = result.recommendation.action.value
            action_total += 1
            in_predicted_set = actual_action in ACCEPTABLE_ACTIONS_BY_CATEGORY.get(actual_cls, set())
            in_truth_set = actual_action in ACCEPTABLE_ACTIONS_BY_CATEGORY.get(expected_cls, set())
            if in_predicted_set:
                action_vs_predicted_correct += 1
            if in_truth_set:
                action_vs_truth_correct += 1
            if not cls_match:
                save_eligible += 1
                if in_truth_set:
                    save_count += 1
            action_confusion[(expected_cls, actual_action)] += 1
            action_match_for_joint = in_truth_set   # joint uses the "real" action quality

        # Joint accuracy
        joint_total += 1
        joint = extraction_all_match and cls_match and (
            action_match_for_joint if include_decision else True
        )
        if joint:
            joint_correct += 1

        per_persona_joint[truth.get("persona_id", "?")].append(joint)

    await asyncio.gather(*(process(s) for s in sidecars))

    return {
        "field_totals": field_totals,
        "classification_total": classification_total,
        "classification_correct": classification_correct,
        "action_total": action_total,
        "action_vs_predicted_correct": action_vs_predicted_correct,
        "action_vs_truth_correct": action_vs_truth_correct,
        "save_eligible": save_eligible,
        "save_count": save_count,
        "action_confusion": action_confusion,
        "joint_total": joint_total,
        "joint_correct": joint_correct,
        "confusion": confusion,
        "per_persona": per_persona_joint,
        "failures": failures,
    }


def cost_since(start_ts_iso_local: str) -> tuple[float, int]:
    if not LOG_PATH.exists():
        return 0.0, 0
    total = 0.0
    count = 0
    with LOG_PATH.open() as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get("timestamp", "")
            if ts and ts >= start_ts_iso_local and rec.get("task") in (
                "invoice_extraction", "exception_classification",
                "decision_support_reasoning",
            ):
                total += float(rec.get("cost_usd", 0))
                count += 1
    return total, count


def print_report(sidecars: list[Path], summary: dict, start_ts_iso_local: str) -> None:
    print()
    print(f"Evaluated {len(sidecars)} invoices ({summary['joint_total']} processed; "
          f"{len(summary['failures'])} failed before diff).")

    print()
    print("Extraction — per-field accuracy:")
    field_totals = summary["field_totals"]
    print(f"  {'field':38s}  {'samples':>8s}  {'correct':>8s}  {'accuracy':>9s}")
    for field in [
        "po_reference", "invoice_date", "currency", "payment_terms",
        "subtotal", "total",
        "header_fields.vendor_name", "header_fields.vendor_tax_id",
        "header_fields.vendor_address", "header_fields.buyer_name",
        "header_fields.buyer_address", "header_fields.buyer_po_contact",
        "line_items.count", "line_items.all_match",
        "tax.count", "tax.all_match",
    ]:
        results = field_totals.get(field, [])
        if not results:
            continue
        correct = sum(1 for r in results if r)
        n = len(results)
        print(f"  {field:38s}  {n:>8d}  {correct:>8d}  {correct/n:>9.2%}")

    cls_total = summary["classification_total"]
    cls_correct = summary["classification_correct"]
    print()
    if cls_total:
        print(f"Classification accuracy: {cls_correct}/{cls_total} = {cls_correct/cls_total:.2%}")

    confusion = summary["confusion"]
    off_diag = [(t, p, c) for (t, p), c in confusion.items() if t != p]
    if off_diag:
        off_diag.sort(key=lambda x: -x[2])
        print()
        print("Top classification confusions (truth → predicted, count):")
        for t, p, c in off_diag[:8]:
            print(f"  {c:>4d}  {t:38s}  →  {p}")

    action_total = summary.get("action_total", 0)
    action_vs_pred = summary.get("action_vs_predicted_correct", 0)
    action_vs_truth = summary.get("action_vs_truth_correct", 0)
    save_eligible = summary.get("save_eligible", 0)
    save_count = summary.get("save_count", 0)
    if action_total:
        print()
        print(f"Action vs predicted class (compatibility): "
              f"{action_vs_pred}/{action_total} = {action_vs_pred/action_total:.2%}")
        print(f"Action vs TRUTH class (real action quality): "
              f"{action_vs_truth}/{action_total} = {action_vs_truth/action_total:.2%}")
        if save_eligible:
            print(f"Decision-support save rate (action right when classifier wrong): "
                  f"{save_count}/{save_eligible} = {save_count/save_eligible:.2%}")

        action_conf = summary.get("action_confusion", {})
        non_match = []
        for (cls, action), c in action_conf.items():
            acceptable = ACCEPTABLE_ACTIONS_BY_CATEGORY.get(cls, set())
            if action not in acceptable:
                non_match.append((cls, action, c))
        non_match.sort(key=lambda x: -x[2])
        if non_match:
            print()
            print("Top truth-category vs action mismatches (truth_class → action, count):")
            for cls, action, c in non_match[:8]:
                print(f"  {c:>4d}  {cls:38s}  →  {action}")

    joint_total = summary["joint_total"]
    joint_correct = summary["joint_correct"]
    print()
    if joint_total:
        joint_label = ("extraction all-fields + classification + action"
                       if action_total else "extraction all-fields + classification")
        print(f"Joint ({joint_label}): "
              f"{joint_correct}/{joint_total} = {joint_correct/joint_total:.2%}")

    print()
    print("Per-persona joint accuracy:")
    per_persona = summary["per_persona"]
    for persona in sorted(per_persona):
        results = per_persona[persona]
        if not results:
            continue
        correct = sum(1 for r in results if r)
        n = len(results)
        print(f"  {persona}:  {correct}/{n} = {correct/n:.2%}")

    total_cost, n_calls = cost_since(start_ts_iso_local)
    print()
    print(f"API cost for this run: ${total_cost:.4f} across {n_calls} pipeline calls")

    if summary["failures"]:
        print()
        print(f"Failures ({len(summary['failures'])}):")
        for f in summary["failures"][:8]:
            print(f"  {f['path']}: {f['error']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=100)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--persona", help="Filter by persona id (e.g. P003)")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument(
        "--no-decision", action="store_true",
        help="Skip retrieve + decide steps (M1 mode — extract + classify only).",
    )
    args = parser.parse_args()

    mapping = load_error_mapping()
    all_sidecars = discover_invoices(persona=args.persona)
    if not all_sidecars:
        print(f"No invoices found in {CORPUS_DIR}", file=sys.stderr)
        sys.exit(1)

    if args.full or args.sample >= len(all_sidecars):
        sidecars = all_sidecars
    else:
        rng = random.Random(args.seed)
        sidecars = rng.sample(all_sidecars, args.sample)

    print(f"Plan: pipeline on {len(sidecars)} invoices "
          f"(persona={args.persona or 'all'}, concurrency={args.concurrency})")

    start_ts_iso_local = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    summary = asyncio.run(evaluate(
        sidecars, args.concurrency, mapping, include_decision=not args.no_decision,
    ))
    print_report(sidecars, summary, start_ts_iso_local)


if __name__ == "__main__":
    main()
