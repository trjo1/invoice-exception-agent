"""Corpus-driven evaluation of the exception classifier.

Runs the classifier against the synthetic invoice corpus and reports a
confusion matrix + per-category precision/recall. Ground truth comes from
each invoice's `error_injected` field (canonicalized at ingest time) mapped
through `config/error_label_to_category.yaml`.

This is broader-signal than the 11 golden cases — gives a real-world
distribution against ~166 dirty + ~324 clean invoices.

Usage:
    uv run python scripts/eval_classifier.py                    # 100-invoice random sample
    uv run python scripts/eval_classifier.py --sample 50
    uv run python scripts/eval_classifier.py --full             # all 490
    uv run python scripts/eval_classifier.py --persona P003     # only one persona

Cost reference: ~$0.0003 per call on DeepSeek V4-Flash; 100 samples ≈ $0.03.
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

from p2p_agent.classifiers import ClassifierError, classify_exception  # noqa: E402
from p2p_agent.llm.client import ModelClient  # noqa: E402

CORPUS_DIR = REPO_ROOT / "test_corpus" / "synthetic" / "invoices"
MAPPING_PATH = REPO_ROOT / "config" / "error_label_to_category.yaml"
LOG_PATH = REPO_ROOT / "logs" / "llm_calls.jsonl"


def load_mapping() -> dict[str | None, str]:
    raw = yaml.safe_load(MAPPING_PATH.read_text())["mapping"]
    # PyYAML returns "null" key as Python None — wrap explicitly.
    out: dict[str | None, str] = {}
    for k, v in raw.items():
        out[k] = v
    out.setdefault(None, "none")
    return out


def discover_invoices(persona: str | None = None) -> list[Path]:
    pattern = f"{persona}_*.json" if persona else "*.json"
    return sorted(p for p in CORPUS_DIR.glob(pattern) if not p.name.startswith("_"))


def ground_truth_category(invoice: dict[str, Any], mapping: dict[str | None, str]) -> str:
    return mapping.get(invoice.get("error_injected"), "other")


async def evaluate(
    paths: list[Path],
    concurrency: int,
    mapping: dict[str | None, str],
) -> dict[str, Any]:
    client = ModelClient()
    semaphore = asyncio.Semaphore(concurrency)

    confusion: dict[tuple[str, str], int] = defaultdict(int)
    per_truth: Counter[str] = Counter()
    failures: list[dict[str, Any]] = []

    async def process(path: Path) -> None:
        invoice = json.loads(path.read_text())
        truth = ground_truth_category(invoice, mapping)
        per_truth[truth] += 1

        async with semaphore:
            try:
                cls = await classify_exception(
                    invoice=invoice,
                    po_context=None,
                    gr_context=None,
                    client=client,
                    case_id=path.stem,
                )
                pred = cls.class_label.value
            except ClassifierError as e:
                failures.append({"path": path.name, "error": str(e)})
                pred = "<error>"
            except Exception as e:  # noqa: BLE001
                failures.append({"path": path.name, "error": f"{type(e).__name__}: {e}"})
                pred = "<error>"
        confusion[(truth, pred)] += 1

    await asyncio.gather(*(process(p) for p in paths))
    return {"confusion": confusion, "per_truth": per_truth, "failures": failures}


def per_category_stats(confusion: dict[tuple[str, str], int]) -> dict[str, dict[str, float]]:
    truths = {t for t, _ in confusion}
    preds = {p for _, p in confusion}
    labels = sorted(truths | preds)
    stats: dict[str, dict[str, float]] = {}
    for label in labels:
        tp = confusion.get((label, label), 0)
        fp = sum(c for (t, p), c in confusion.items() if p == label and t != label)
        fn = sum(c for (t, p), c in confusion.items() if t == label and p != label)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        stats[label] = {
            "support": sum(c for (t, _), c in confusion.items() if t == label),
            "precision": precision,
            "recall": recall,
            "tp": tp,
            "fp": fp,
            "fn": fn,
        }
    return stats


def cost_since(start_ts: float) -> tuple[float, int]:
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
            # rec timestamps are ISO; quick filter by string comparison
            if ts and ts >= start_ts_iso:
                if rec.get("task") == "exception_classification":
                    total += float(rec.get("cost_usd", 0))
                    count += 1
    return total, count


def print_report(
    paths: list[Path],
    summary: dict[str, Any],
    start_ts_iso_local: str,
) -> None:
    confusion = summary["confusion"]
    per_truth = summary["per_truth"]
    failures = summary["failures"]
    stats = per_category_stats(confusion)

    print()
    print(f"Evaluated {len(paths)} invoices.")
    print()
    print("Per-category precision / recall:")
    print(f"  {'category':38s}  {'support':>8s}  {'TP':>4s}  {'FP':>4s}  {'FN':>4s}  {'P':>6s}  {'R':>6s}")
    for label in sorted(stats.keys()):
        s = stats[label]
        if s["support"] == 0 and s["fp"] == 0:
            continue
        print(
            f"  {label:38s}  "
            f"{int(s['support']):>8d}  {int(s['tp']):>4d}  {int(s['fp']):>4d}  {int(s['fn']):>4d}  "
            f"{s['precision']:>6.2%}  {s['recall']:>6.2%}",
        )

    overall_correct = sum(c for (t, p), c in confusion.items() if t == p)
    overall_total = sum(confusion.values())
    print()
    print(f"Overall accuracy: {overall_correct}/{overall_total} = "
          f"{overall_correct / overall_total:.2%}" if overall_total else "n/a")

    # Top confusions (truth → predicted) excluding diagonal
    off_diag = [(t, p, c) for (t, p), c in confusion.items() if t != p]
    if off_diag:
        off_diag.sort(key=lambda x: -x[2])
        print()
        print("Top confusions (truth → predicted, count):")
        for t, p, c in off_diag[:8]:
            print(f"  {c:>4d}  {t:38s}  →  {p}")

    # Cost
    total_cost, n_calls = cost_since(start_ts_iso_local)
    print()
    print(f"API cost for this run: ${total_cost:.4f} across {n_calls} calls")

    if failures:
        print()
        print(f"Failures ({len(failures)}):")
        for f in failures[:5]:
            print(f"  {f['path']}: {f['error'][:140]}")


# Module-level so the cost-filter closure can see it (start_ts_iso is set in main()).
start_ts_iso = ""


def main() -> None:
    global start_ts_iso

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=100, help="Random sample size")
    parser.add_argument("--full", action="store_true", help="Run all invoices (overrides --sample)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for sampling")
    parser.add_argument("--persona", help="Filter by persona id (e.g. P003)")
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()

    mapping = load_mapping()

    all_paths = discover_invoices(persona=args.persona)
    if not all_paths:
        print(f"No invoices found in {CORPUS_DIR}", file=sys.stderr)
        sys.exit(1)

    if args.full or args.sample >= len(all_paths):
        paths = all_paths
    else:
        rng = random.Random(args.seed)
        paths = rng.sample(all_paths, args.sample)

    print(f"Plan: classify {len(paths)} invoices (persona={args.persona or 'all'}, "
          f"concurrency={args.concurrency})")

    # Capture pre-call cost-ledger position for the run report
    start_ts_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

    summary = asyncio.run(evaluate(paths, args.concurrency, mapping))
    print_report(paths, summary, start_ts_iso)


if __name__ == "__main__":
    main()
