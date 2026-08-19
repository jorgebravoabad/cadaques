# Studies — frozen protocol v1 (the 0.3 vertical paper)

Every comparative number in the paper traces to this directory. The
protocol below was frozen **before** the full experiments were run;
changes require a failed verification, not a preference.

## The quartet (four datasets exactly)

| Dataset | Source | Space | Objective | Cost model |
|---|---|---|---|---|
| `ising` | in-package `Ising2DOracle` (exact ground truth) | T ∈ [1,4] | max ⟨susceptibility proxy⟩ | declared: fidelity formula; settled: measured |
| `ocm291` | Nguyen et al., *ACS Catal.* 10, 921 (2020) — 291-catalyst HTE summary, featurized (see `ocm/featurize.py`) | 22-D: slot-wise (EN, ionic radius, Z) × M1/M2/M3 + one-hot support + 4 conditions | max C2 yield (%) | **uniform tariff** (HTE per-point effort ≈ constant) |
| `snar` | Olympus `snar` (Häse et al., *MLST* 2, 035021 (2021)); flow SNAr | 4-D conditions | min e-factor | **real per-row**: 60 s × residence_time |
| `fullerenes` | Olympus `fullerenes`; o-xylenyl adduct synthesis | 3-D conditions | max product (mol %) | **real per-row**: 60 s × reaction_time (3–31 min: 10× heterogeneity) |

Retrospective mode is **nearest-serve** (`tolerance = inf`): the table is
a discretized landscape; every query is served by its nearest measured
point (its recorded cost is settled). Failure semantics are exercised by
the package test suite, not injected into the benchmark.

## Matrix

- Seeds: `range(20)`, paired across drivers (a seed is a campaign seed:
  ADR-0012 makes the pairing valid).
- Drivers: `random`, `annealed`, `bo_cost_aware`, `bo_cost_oblivious`
  (BayesianDriver, n_initial=5, n_candidates=256).
- Budgets (settled seconds): ising max_queries=40; ocm291 40; snar 1800
  (~24 runs); fullerenes 18000 (~18 runs).
- Metering: `meter_driver=False` for the benchmark (driver wall time is
  physical and machine-dependent; the *thinking cost* study is reported
  separately from recommendation-mode provenance).
- Statistics: `cadaques.stats.compare` — paired bootstrap CI (10k
  resamples) + Wilcoxon signed-rank; each driver vs `random`
  (one-sided, greater-is-better after sign normalization) plus
  `bo_cost_aware` vs `bo_cost_oblivious` (two-sided).

## Honesty notes (pre-registered)

1. On `ocm291` the tariff is uniform, so `bo_cost_aware` and
   `bo_cost_oblivious` are **identical by construction** (EI/cost
   reduces to EI). Their exact equality in the results is a sanity
   check of the implementation, not a finding. Heterogeneous-cost
   claims rest on `snar` and `fullerenes`.
2. Smoke runs (`--smoke`: 6 seeds, reduced budgets) validate machinery
   only; no smoke number is evidence.
3. The `ocm291` table is the published per-catalyst best-performance
   summary. The conditions-resolved study over the full ~12,708-point
   grid requires the ACS Supporting Information (institutional access):
   see `ocm/prepare_ocm_full.py`.
4. Inconclusive comparisons are results and will be reported as such.

## Running

```bash
python studies/run_study.py --smoke      # machinery check (~15 min)
python studies/run_study.py              # the paper matrix (hours; run on your machine)
```

Outputs land in `studies/results/` (git-ignored): per-seed CSVs and
paired-comparison summaries. Every cell is a full spec'd Campaign;
`results_*.csv` carries settled/declared/overrun per campaign.

## Provenance

Pinned datasets and checksums: `data/PROVENANCE.md`. The two Olympus
tables are vendored with headers added from each dataset's
`config.json`; `ocm291.csv` is the community-circulated CSV of the
published 291-catalyst table (re-derivable from the paper's SI).
