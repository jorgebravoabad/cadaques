# Data provenance (frozen with protocol v1)

| File | sha256 (first 16) | Rows | Source |
|---|---|---|---|
| `ocm291.csv` | 0de1a0328a023249 | 291 | Nguyen, Nhat, Takimoto, Thakur, Nishimura, Ohyama, Miyazato, L. Takahashi, Fujima, K. Takahashi, Taniike, *ACS Catal.* 10, 921–932 (2020), doi:10.1021/acscatal.9b04293 — per-catalyst best-performance summary (M1/M2/M3/support, conditions, C2 yield). CSV as circulated in the ML-catalysis community; re-derivable from the paper's Supporting Information. |
| `olympus_snar.csv` | 043f0f23f26cc920 | 66 | Olympus datasets (Häse et al., *Mach. Learn.: Sci. Technol.* 2, 035021 (2021); github.com/aspuru-guzik-group/olympus, MIT). Header added from `dataset_snar/config.json`: residence_time, ratio, concentration, temperature → e_factor (minimize). |
| `olympus_fullerenes.csv` | e07160db9e40966d | 246 | Same source, `dataset_fullerenes`: reaction_time, sultine, temperature → product_mole_percent (maximize). |

Cost columns are derived at load time (`run_study.py`):
`cost_s = 60 × residence_time` (snar), `cost_s = 60 × reaction_time`
(fullerenes) — the physical duration of each measured run.

Checksums are over the repository files as stored (LF line endings;
git-normalized from the CRLF written by the vendoring script). Verify with:
`python -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest()[:16])" studies/data/<file>`.
