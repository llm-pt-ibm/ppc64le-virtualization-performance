# Methodological glossary: pseudo-replication

This document explains, in plain language, what the
`(caution: pseudo-replication)` warning that appears in
`data/processed/report.txt` for some metrics means, with a real
numeric example taken from this very experiment.

## The experimental design: why 3 VMs, not 15 independent samples

For the KVM and QEMU treatments, this experiment didn't collect "15
independent runs" — it collected **3 different VMs** (identified by
`vm_id`: `gabrielly`, `lucas`, `ramalho`), each running the benchmark
**5 times**. `3 x 5 = 15`, the same `n` used in the host vs. KVM vs.
QEMU tests — but the internal structure of those 15 numbers isn't the
same as it would be if we had provisioned 15 different VMs and run the
benchmark once on each.

This is called a **nested design** (or block design): "run" is nested
within "VM", which is in turn nested within "treatment" (KVM/QEMU). This
is a **standard and correct** practice in systems performance
evaluation — not a shortcut or a collection error. The alternative
(provisioning 15 different VMs) would cost 5x more machine time for the
same `n`, without necessarily adding new information, since what varies
between runs WITHIN the same VM (measurement noise, scheduler jitter,
etc.) tends to be much smaller than what can vary BETWEEN VMs (small
differences in NUMA/vCPU allocation, cache state, boot order, etc.).

## What pseudo-replication is

**Pseudo-replication** is the error of treating replicates that are NOT
statistically independent (e.g. 5 runs of the same VM) as if they were
`n` independent samples of a generic "KVM treatment". The concrete risk:
if VM `gabrielly` happens to have systematically different performance
from the other two (not because of KVM, but because of some
particularity of that specific VM — a noisy neighbor at collection
time, a NUMA allocation difference, etc.), and you simply stack the 15
runs as if they were 15 i.i.d. samples of "KVM", the statistical test
can:

- **underestimate the treatment's real variance** (because the
  variation *between VMs* — which is a real source of uncertainty about
  "what's typical of KVM" — is hidden inside an inflated "run"-only
  variance), and
- as a consequence, **overestimate confidence in the observed
  difference** between KVM and host/QEMU (artificially small p-value,
  artificially narrow CI) — reporting a result as "more significant"
  than the data actually supports.

The term (in this general statistical sense) is discussed in depth in
Hurlbert, S. H. (1984), *"Pseudoreplication and the Design of Ecological
Field Experiments"*, and the specific way this applies to systems
benchmarking — multiple runs on the same machine/VM not being
independent samples of the "treatment" — is covered in the experimental
design chapter of Jain, R. (1991), *The Art of Computer Systems
Performance Analysis* (Wiley), especially in the sections on variance
between replicates and the need to correctly identify the experimental
unit before applying a hypothesis test.

## Real numeric example: `mem_bogo_ops_s_real` under KVM

In `data/processed/extracted_data.csv`, the 15 runs of
`mem_bogo_ops_s_real` under KVM break down by source VM as follows:

| VM | n | mean (bogo ops/s) | std dev |
|---|---|---|---|
| gabrielly | 5 | 437,331 | 13,623 |
| lucas | 5 | 462,739 | 11,371 |
| ramalho | 5 | 459,983 | 13,246 |

VM `gabrielly` runs, on average, ~5-6% slower than `lucas` and
`ramalho` on this metric. This is exactly the kind of systematic
difference **between VMs** (not between runs) that pseudo-replication
would hide if you only looked at the mean of the 15 combined runs.

`checar_heterogeneidade_vms()` (in `src/analysis/analyze_benchmarks.py`)
tests this directly: it runs a Kruskal-Wallis comparing `gabrielly` vs.
`lucas` vs. `ramalho` (only against each other, within the KVM
treatment) for each metric. For `mem_bogo_ops_s_real` under KVM
(`data/processed/vm_heterogeneity.csv`):

```
metrica=mem_bogo_ops_s_real, ambiente=kvm, n_vms=3, kruskal_h=6.02, kruskal_p=0.0493, heterogeneo=True
```

`p=0.0493 < 0.05` -> the 3 VMs **differ from each other** in a
statistically detectable way on this metric, under KVM. That's why
`report.txt` prints, for this metric/environment:

```
Heterogeneity between VMs (kvm): H=6.020, p=0.0493 -> differ from each other? YES (caution: pseudo-replication)
```

For comparison, the same check under **QEMU**, for the same metric,
gives `kruskal_h=0.98, kruskal_p=0.613, heterogeneo=False` — the 3
QEMU VMs **don't** differ from each other in a detectable way on this
metric, so there the "15 runs ~ i.i.d." assumption is more defensible.

## What the pipeline already does about it (nothing extra needed)

This is **not an unresolved problem** — it's a known limitation of the
nested design, and the pipeline already has two answers for it:

1. **VM-stratified bootstrap CI**
   (`bootstrap_ci_estratificado_por_vm` in `analyze_benchmarks.py`):
   instead of resampling the 15 runs as a single pool, the bootstrap
   first draws VMs (with replacement), then replicates within each
   drawn VM (with replacement) — a 2-level bootstrap that respects the
   nested structure and produces a wider (more honest) CI when there's
   real heterogeneity between VMs, instead of an artificially narrow CI.
2. **The heterogeneity warning itself**: whenever
   `checar_heterogeneidade_vms` detects `p<0.05` for a metric x
   treatment, that's recorded in
   `data/processed/vm_heterogeneity.csv`,
   [vm_heterogeneity_heatmap.png](../results/figures/heatmaps/vm_heterogeneity_heatmap.png),
   and as the "(caution: pseudo-replication)" warning in `report.txt` —
   so any reader knows exactly which metrics/treatments have a more
   fragile i.i.d.-replicates assumption, instead of only finding out by
   reading the code.

In short: pseudo-replication itself is an expected and methodologically
correct feature of the nested design (3 VMs x 5 runs); what the pipeline
guarantees is that, when it actually manifests as detectable
heterogeneity between VMs, that's made visible and handled with the
appropriate statistical method (stratified bootstrap), instead of being
ignored.
