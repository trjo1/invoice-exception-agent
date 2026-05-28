"""Download + structure the BPI Challenge datasets.

Source: 4TU.ResearchData (data.4tu.nl). CC BY 4.0.
Datasets:
- BPI Challenge 2019: Dutch government P2P, 1.6M events, ~250K POs
- BPI Challenge 2020: Travel permit / expense P2P, 5 sub-logs

Output: test_corpus/bpi_data/ — structured CSV / Parquet files ready for
ingestion into the agent's event stream.

Status: SKELETON. Implementation lands in week 1 of the corpus build.

Usage:
    uv run python scripts/ingest_bpi.py [--dataset 2019|2020|both]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# pm4py imports — only when actually running, not at import time
# (it's a heavy import)

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_CORPUS = REPO_ROOT / "test_corpus" / "bpi_data"

DATASETS = {
    "2019": {
        "name": "BPI Challenge 2019",
        "url": "https://data.4tu.nl/articles/dataset/BPI_Challenge_2019/12715853",
        "description": "Dutch government P2P, 1.6M events, ~250K POs",
        "expected_file": "BPI_Challenge_2019.xes.gz",
    },
    "2020": {
        "name": "BPI Challenge 2020",
        "url": "https://data.4tu.nl/articles/dataset/BPI_Challenge_2020_Travel_Permits/12721292",
        "description": "Travel permit / expense P2P, 5 sub-logs",
        "expected_file": "DomesticDeclarations.xes.gz",
    },
}


def download_dataset(dataset_id: str) -> Path:
    """Download the dataset to test_corpus/bpi_data/.

    TODO — implement. For now, prompt the user to download manually
    and place the .xes.gz file in test_corpus/bpi_data/.
    """
    info = DATASETS[dataset_id]
    target = TEST_CORPUS / info["expected_file"]
    if target.exists():
        print(f"[{dataset_id}] Already present: {target}")
        return target
    print(
        f"[{dataset_id}] Manual download needed.\n"
        f"  Visit: {info['url']}\n"
        f"  Save as: {target}\n"
        f"  Then re-run this script.",
    )
    sys.exit(1)


def parse_xes(xes_path: Path) -> None:
    """Parse the XES event log and write structured CSV / Parquet.

    TODO — implement with pm4py. Output schema:
      - events.csv: one row per event (case_id, activity, timestamp, attributes)
      - cases.csv: one row per case (case_id, start, end, outcome)
      - master_data: vendor, GL, currency, etc. extracted as side artifacts
    """
    print(f"Would parse: {xes_path}")
    raise NotImplementedError("pm4py-based parsing not yet implemented.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        choices=["2019", "2020", "both"],
        default="both",
        help="Which BPI Challenge dataset to ingest",
    )
    args = parser.parse_args()

    TEST_CORPUS.mkdir(parents=True, exist_ok=True)

    datasets_to_run = ["2019", "2020"] if args.dataset == "both" else [args.dataset]
    for dataset_id in datasets_to_run:
        xes_path = download_dataset(dataset_id)
        parse_xes(xes_path)


if __name__ == "__main__":
    main()
