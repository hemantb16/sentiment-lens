"""Print a short table from Locust's CSV output: python scripts/load_summary.py <prefix> ..."""

import csv
import sys
from pathlib import Path


def summarise(prefix: str) -> None:
    print(f"\n== {Path(prefix).name} ==")
    with open(f"{prefix}_stats.csv", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    print(f"{'endpoint':34}{'requests':>9}{'failed':>8}{'median ms':>11}{'p95 ms':>8}{'max ms':>8}")
    for row in rows:
        name = f"{row['Type']} {row['Name']}".strip()
        print(
            f"{name[:33]:34}{row['Request Count']:>9}{row['Failure Count']:>8}"
            f"{float(row['Median Response Time']):>11.0f}{float(row['95%']):>8.0f}"
            f"{float(row['Max Response Time']):>8.0f}"
        )
    failures = Path(f"{prefix}_failures.csv")
    if failures.exists():
        with failures.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                print(
                    f"  FAILURE {row['Method']} {row['Name']}: {row['Error']} x{row['Occurrences']}"
                )


if __name__ == "__main__":
    for prefix in sys.argv[1:]:
        summarise(prefix)
