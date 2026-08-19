"""Convert the full Nguyen 2020 OCM grid (ACS SI) into study CSVs.

The full high-throughput grid (~12,708 points: ~300 catalysts × a
systematic condition grid) lives in the Supporting Information of
doi:10.1021/acscatal.9b04293 (institutional access) and in the CADS
repository. Download the SI spreadsheet, then:

    python studies/ocm/prepare_ocm_full.py path/to/cs9b04293_si_00X.xlsx

This writes studies/data/ocm_full.csv with normalized column names and
prints the sha256 to pin in PROVENANCE.md. The conditions-resolved
study (variant (a): per-catalyst condition optimization, fully
continuous, no featurization needed) becomes available once this file
exists — see the TODO at the bottom for the dataset builder to add to
run_study.py.
"""

from __future__ import annotations

import csv
import hashlib
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "ocm_full.csv"


def main(xlsx_path: str) -> None:
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        sys.exit("pip install openpyxl (only needed for this one-time conversion)")
    import openpyxl

    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    header = [str(h).strip() for h in rows[0]]
    print("Detected columns:", header)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for r in rows[1:]:
            if any(v is not None for v in r):
                w.writerow(r)
    digest = hashlib.sha256(OUT.read_bytes()).hexdigest()
    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes)")
    print(f"sha256: {digest}  <- pin this in studies/data/PROVENANCE.md")
    print("Next: add a dataset_ocm_full() builder to run_study.py "
          "(conditions as parameters, catalyst fixed or filtered).")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
