# Study v2 — agent operation with and without CADAQUES (DRAFT, not frozen)

Every comparative number in Section 5 of the agent-first paper traces to
this protocol. It is frozen **before** any main run: the commit hash, the
two briefs, the harness and the fault schedule are archived (Zenodo) and
the archive DOI is cited in the paper. Changes after the freeze require a
failed verification, not a preference, and are logged in §12.

## 1. Question

When a language-model agent operates a budgeted discovery campaign on a
batch cluster, does operating it through CADAQUES tools, instead of
writing its own campaign loop, change

1. whether the campaign's accounting is correct,
2. whether the campaign survives operational faults,
3. what operating the campaign costs the agent, and
4. the quality of the scientific answer?

The study tests a narrow claim: **the kernel delivers its guarantees
through an agent, under the faults those guarantees exist for.** It does
not test whether CADAQUES finds better optima; on the chosen task it
should not (§9, note 1).

## 2. Hypotheses (directions fixed in advance)

| | Hypothesis | Test direction |
|---|---|---|
| H1 (primary) | More campaigns end with correct accounting with CADAQUES than without. | two-sided |
| H2 | Fewer accounting defects per campaign with CADAQUES. | two-sided |
| H3 | After a mid-campaign session kill, more campaigns resume correctly with CADAQUES. | two-sided |
| H4 | Answer quality with CADAQUES is not worse than without (non-inferiority, margin 10 pp). | one-sided NI |
| H5 | Agent cost (tokens, wall time) differs between arms. **No direction is predicted**: tools may save code writing or cost verbose calls. | two-sided |

## 3. Task (identical in all arms)

**Physics.** One-dimensional photonic crystal of two lossless
dielectrics, ε_H = 13 and ε_L = 1, period a. Maximize the gap-to-midgap
ratio Δω/ω_m of the fundamental gap at normal incidence over the
relative thickness d_H/a ∈ [0.05, 0.95].

**Reference (graders only, never shown to agents).** Quarter-wave stack,
d_H/a = n_L/(n_H + n_L) = 0.21713, Δω/ω_m = (4/π) arcsin((n_H − n_L)/(n_H + n_L))
= 0.76564. Verified by a transfer-matrix sweep at 961 points plus
bounded refinement: the numerical argmax and maximum agree with the
formula to 5 decimals for ε = 13/1, 12/2 and 4/1
(`reference/qw_check.py`, to be vendored with the freeze).

**Evaluation.** One MPB band-structure calculation per job, submitted to
Slurm with `sbatch`. Fidelity knob: MPB `resolution` ∈ {16, 32, 64},
chosen by the agent per job.

**Cost.** The cost of a job is its `CPUTimeRAW` in Slurm accounting
(core-seconds), for every job that started, whatever its end state.
Declared cost before submission is c₀ · (resolution / 16), with c₀ set
in the pilot (§8) as the median `CPUTimeRAW` at resolution 16.

**Budget.** B = 40 c₀ core-seconds per campaign (≈ 40 low- or 10
high-fidelity jobs before faults). At most 4 jobs in flight.

**Deliverables (both arms).** (i) `report.json`: best d_H/a, its measured
Δω/ω_m, total core-seconds spent, number of jobs, stop reason; (ii) a
campaign record from which every job can be audited, each entry carrying
its Slurm job id, parameters, fidelity, declared and settled cost and
terminal status. In arm A the CADAQUES event log is that record
(Slurm ids go in `artifacts`); in arm B the agent writes
`ledger.jsonl` in the format given in its brief.

## 4. Arms

| Arm | The agent has | Role |
|---|---|---|
| **A — CADAQUES** | CADAQUES v0.4.0 tools over MCP (campaign pre-created with `ExternalOracle(tariff = c₀ s, scaling = {resolution: 1}, reference = {resolution: 16})` and `BayesianDriver`), shell, MPB, Slurm | treatment |
| **B — scratch** | shell, MPB, Slurm, Python with NumPy, SciPy, scikit-learn; **no** CADAQUES | control |
| **C — library, no tools** | as B, plus CADAQUES installed as a Python package with its documentation, no MCP | exploratory ablation: kernel without the agent interface |

All arms: no network beyond the cluster (an agent in B cannot install
CADAQUES or look anything up), same container image, same per-run caps:
4 h agent wall time and 5 M total tokens (finalized in pilot). In every
arm the agent writes its own job scripts and output parsers: the
periphery is the same work everywhere.

**Briefs.** Arms share one brief (Appendix A): the same task, the same
accounting contract stated in full, the same deliverables. They differ
only in one paragraph on how to operate the campaign (Appendix B). Arm B
therefore receives every rule it is graded on; it is not handicapped by
missing information. The diff between briefs is published.

## 5. Fault scenarios

Faults are injected by a scheduler shim (`sbatch`, `squeue`, `sacct`,
`scancel` wrappers on the agent's PATH) that forwards to the real
scheduler and logs every call. The schedule is a pure function of
(scenario, seed), so paired runs see the same faults at the same
submission indices.

| ID | Scenario | Injection | Guarantee exercised |
|---|---|---|---|
| S0 | clean | none | baseline; no difference expected |
| S1 | job failures | submission #3 exits non-zero at once; #7 is killed after 0.55–0.65 × t_ref, the calibrated runtime at resolution 16 | failures settle cost (ADR-0006) |
| S2 | preemption | submission #2 is requeued 0.65–0.75 × t_ref after it starts running (two accounting records, both charged) | settled ≠ declared; no double-counting |
| S3 | session kill | the agent process is killed when ≥ 4 jobs have completed **and** ≥ 1 is in flight; a fresh session starts 60 s later with the same brief plus the resume note (Appendix A.4) | resumability, reservations survive |
| S4 | slow node | from submission #3 on, every other job holds its allocation for 2× its own runtime after finishing (settled ≈ 3× its normal cost) | admission under overrun, declared vs settled |
| S5 | tight concurrency | budget B = 12 c₀; up to 4 jobs in flight; the cap binds while jobs are running | admission counts outstanding reservations |

Indices count successful submissions as seen by the shim, in both
arms. Faults are injected by rewriting the job script after its
`#SBATCH` header (`shim/inject.py`); a job whose payload replaces the
shell with `exec` escapes the slow-node fault, which the manipulation
check reports. Array jobs are forwarded without faults and graded as
`unsupported_submission`; the brief asks for one job per evaluation.

## 6. Matrix and sample size

- **Primary comparison (A vs B):** 6 scenarios × 2 models × 4 seeds = 48
  matched pairs; 96 runs. A pair shares model, scenario and seed.
- **Models:** M1, a frontier Claude model; M2, a frontier model from
  another vendor (to separate the framework from one vendor's habits).
  Exact versions are pinned at freeze. One agent harness for both
  models, pinned version, default sampling settings.
- **Arm C:** 6 scenarios × M1 × 2 seeds = 12 runs, exploratory.
- **Ecological replication:** A and B × M1 × {S0, S3} × 3 seeds = 12 runs
  on the shared production cluster, real queue, descriptive only.
- **Total:** 120 runs.

**Power.** Simulated exact McNemar test, two-sided α = 0.05
(`reference/power.py`). With 48 pairs, power is 0.84 if CADAQUES fixes
30 % of pairs and breaks 5 %, 0.97 at 40 % / 5 %, and only 0.64 at
20 % / 3 %. The study is sized for a large effect; a smaller true effect
is likely to come out inconclusive, and will be reported as such.

## 7. Outcomes (all graded from ground truth, never from an arm's own record)

Ground truth = the shim's call log + Slurm `sacct` dump + harness logs.
Each arm's record is graded *against* it.

**O1 (primary): accounting-correct campaign** — binary, true iff all of:
- (a) **no admission violation**: no job submitted when its declared
  cost + the declared cost of jobs in flight or ended less than 60 s
  before + the settled cost of jobs that ended earlier exceeds B (the
  60 s grace lets the agent use a declaration until accounting has
  caught up);
- (b) **complete record**: every Slurm job the campaign started appears
  exactly once, with the correct terminal status;
- (c) **correct settled costs**: each entry within 1 % of `CPUTimeRAW`,
  never tighter than 1 core-second (the resolution of Slurm accounting);
  requeued jobs: sum of all records (`sacct -D`);
- (d) **correct total**: reported total within the same tolerance of the
  true total.

The declared cost of each job, needed for (a), is computed by the
grader from the resolution the job actually ran at, read from MPB's own
output (`Grid size is N…`), else from the submitted script; never from
the arm's record. A job with neither is priced at the lowest resolution
seen, which can only make a violation less likely.

**Manipulation check.** For every run the grader confirms that each
scheduled fault the campaign reached actually happened (the job failed,
was requeued, carried the slow-node preamble; the session was killed).
Runs that fail the check are flagged for review before analysis and
reported, never silently graded.

**O2: defect count** per campaign — the sum of violations across
(a)–(d) plus misclassified statuses, coded with the taxonomy of
Appendix C by an automated grader; ambiguous cases are coded by two
raters, and Cohen's κ is reported.

**O3: resume correctness** (S3 only) — binary: after the kill, no
in-flight job is lost (it is settled or cancelled with cost), no
completed job is re-run unknowingly, and accounting continues from the
true spent amount.

**O4: answer quality** — success iff the true Δω/ω_m at the reported
d_H/a (transfer matrix) is within 1 % of 0.76564. Also reported:
|d* − 0.21713| as a continuous outcome.

**O5: agent cost** — input, cached-input and output tokens (from API
usage logs), agent wall time, number of turns and tool calls.

**Descriptive:** lines of code the agent wrote, share of arm-A runs that
bypassed the tools, auditability (can an independent script rebuild
spending from the record alone: arm A by `replay`, arm B by its ledger).

## 8. Procedure

1. **Build** the harness, shim, grader and container image (MPB + Slurm)
   and the briefs. Shim and grader: done (`shim/`, `grader/`, 30 tests
   on `fakeslurm`, including one planted defect per code).
2. **Pilot** on a *different* task (a single-parameter analytic objective
   run as Slurm jobs), 2 runs per arm per scenario. Used to fix harness
   bugs, calibrate c₀, and set the wall-time and token caps. Pilot data
   are never analyzed. CADAQUES may be fixed during the pilot; the
   version is then frozen.
3. **Freeze:** release CADAQUES v0.4.0 (Zenodo DOI); tag the study
   commit; archive briefs, harness, shim, schedule, grader, model
   identifiers; record the archive DOI here.
4. **Run** the 120 runs in a randomized order (randomization seeded and
   archived), pairs interleaved so that time-varying cluster load does
   not align with arm.
5. **Grade** automatically; second-rater coding of ambiguous defects.
6. **Analyze** per §10; publish transcripts, records, `sacct` dumps and
   grades.

**Exclusions.** Only infrastructure failures not caused by the shim
(cluster outage, provider API outage) are excluded; the run is repeated
with the same seed and the exclusion is logged. An agent that gives up,
hits a cap or refuses is a **result**, graded on its state at the end.
Analysis is intention-to-treat: an arm-A agent that ignores the tools is
still arm A.

## 9. Honesty notes (pre-registered)

1. The scenarios were chosen to exercise what the kernel guarantees. A
   positive H1 shows that those guarantees reach the campaign when an
   agent operates it; it does not show that CADAQUES is better in
   general. S0 is expected to show no difference, and the paper will
   say so.
2. The authors built arm A. Mitigations: this freeze; identical
   accounting rules stated in both briefs; ground-truth grading
   independent of either arm's record; a second model vendor; all
   transcripts published; no prompt or brief tuning on the evaluation
   task.
3. The task is easy: answer quality (H4) is expected to be similar, and
   48 pairs give a wide non-inferiority interval. It is reported as an
   interval, not as a proof of equivalence.
4. Agent cost (H5) may favour arm B. It is reported whatever its sign.
5. Results hold for the pinned models and harness; later models may
   close the gap, and the paper will say this.
6. Inconclusive comparisons are results and will be reported as such.

## 10. Analysis plan

- **H1:** exact McNemar on the 48 pairs; paired difference in
  proportions with 95 % CI (Newcombe). α = 0.05.
- **Secondary family (Holm, α = 0.05):** H2 Wilcoxon signed-rank on
  defect counts; H3 exact McNemar on the 8 S3 pairs; H5 paired bootstrap
  CI (10 k resamples) for the ratio of median tokens and of median wall
  time. Computed with `cadaques.stats` and cross-checked with SciPy.
- **H4:** one-sided 95 % lower bound of the paired success-rate
  difference (A − B) compared with −10 pp.
- **Heterogeneity:** results by scenario and by model, descriptive only,
  no tests.
- **Arm C and the replication:** descriptive; no hypothesis tests.

## 11. Decisions to make before freezing

- M2: which non-Claude model; the harness that runs both models with MCP
  and a shell.
- Cluster for the main runs (containerized Slurm on a dedicated node is
  assumed) and for the replication.
- Final caps after the pilot; whether to add the 2D square-rod task as an
  exploratory second task in S0 and S3.

## 12. Change log after freeze

(empty)

---

## Appendix A — shared brief (draft)

**A.1 Task.** (as §3, written for the agent, without the reference
answer)

**A.2 Accounting rules.** "Each Slurm job costs its `CPUTimeRAW`
core-seconds, whatever its end state, including failures, preemptions
and cancellations; a requeued job costs the sum of its runs. Before you
submit a job, its declared cost is c₀ × resolution/16 core-seconds. You
may submit a job only if its declared cost, plus the declared cost of
every job still in flight, plus everything already spent, fits within
the budget of B core-seconds. Never run more than 4 jobs at once. A job
may end up costing more than declared: you must still record and count
what it really cost. When no further job can be admitted, stop."

**A.3 Deliverables.** `report.json` with the keys `best_d_H`,
`best_gap_ratio`, `total_cost` (core-seconds), `n_jobs`, `stop_reason`;
and the campaign record (§3), each entry with its Slurm job id. Submit
one Slurm job per evaluation (no job arrays).

**A.4 Resume note (S3 only).** "A previous session was operating this
campaign and was interrupted. Its files are in this directory. Continue
the campaign; jobs it submitted may still be running."

## Appendix B — operating paragraph per arm (draft)

- **A:** "The campaign is already registered in CADAQUES as `pc1d`. Use the
  CADAQUES tools to decide each next evaluation (`reserve_evaluation`),
  then run it yourself as a Slurm job and report it with
  `settle_evaluation` (or `cancel_evaluation`), including the Slurm job
  id in `artifacts` as `slurm:<id>` and the job's core-seconds as
  `settled_cost.seconds`."
- **B:** "Write and run your own campaign loop in Python. Keep the
  campaign record in `ledger.jsonl`, one JSON object per job, with the
  fields `slurm_job_id, d_H, resolution, declared_cost, settled_cost,
  status, value`."
- **C:** "CADAQUES is installed as a Python package (`import cadaques`;
  documentation under `docs/`). Use it however you see fit, or not at
  all."

## Appendix C — defect taxonomy

`admission_violation`, `missing_job`, `duplicate_job`, `wrong_status`,
`settled_cost_error`, `uncharged_failure`, `uncharged_cancellation`,
`requeue_double_count`, `requeue_undercount`, `total_mismatch`,
`lost_in_flight_job` (S3), `unknowing_rerun` (S3), `concurrency_violation`,
and three record-level codes the grader needs for real output:
`unattributed_entry` (an entry matching no submitted job),
`unparseable_record`, `missing_record`, plus `missing_report` and
`unsupported_submission`. Families for O1: admission, record, cost,
total (`grader/grade.py`, `DEFECT_CODES`). An arm-A entry whose job
failed and was closed with `cancel_evaluation` is not a status defect:
O1 distinguishes completed from not completed only.
