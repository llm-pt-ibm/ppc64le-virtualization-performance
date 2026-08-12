# Methodology (summary)

This document summarizes the experimental methodology to guide anyone
reproducing or auditing this repository. For the full, formal write-up,
see the Methodology section of the paper (link/DOI to be filled in).

## Context

Research project on virtualization on the IBM Power9 (ppc64le)
architecture, conducted at the Distributed Systems Lab (LSD/UFCG) in
partnership with IBM (IBM-MULTIARQUITETURAS project), using an IBM
Power9 AC922 server. It also serves as a deliverable for two graduate
courses (Distributed Systems/Virtualization and Statistical Inference).

## Objective

Characterize the performance overhead introduced by two virtualization
mechanisms — **KVM** (hardware-accelerated virtualization) and **plain
QEMU** (TCG emulation, no acceleration) — relative to a **bare-metal
(physical host)** baseline, on representative HPC workloads: CPU,
memory, cache, disk and GPU.

## Environments compared

| Environment | Description | Replicates |
|---|---|---|
| `host` | Direct execution on the physical Power9 server | 15 runs |
| `kvm` | 3 distinct VMs (`gabrielly`, `lucas`, `ramalho`) under KVM | 5 runs x 3 VMs = 15 |
| `qemu` | The same 3 VMs under plain QEMU (TCG, no KVM) | 5 runs x 3 VMs = 15 |

> **VM identifiers** (`vm_id`), used in the folder names `kvm-lucas/`,
> `kvm-ramalho/`, `qemu-lucas/`, etc. in `data/raw/`.
> The full author list is in [`CITATION.cff`](../CITATION.cff);
> "lucas" and "ramalho" are neither authors nor collaborators.

The nested structure (3 VMs x 5 replicates per treatment) is handled
explicitly in the statistical pipeline — see
`bootstrap_ci_estratificado_por_vm` and `checar_heterogeneidade_vms` in
`src/analysis/analyze_benchmarks.py` — so the 15 KVM/QEMU runs aren't
treated as i.i.d. when there's real heterogeneity between the source VMs
(pseudo-replication).

## Benchmark tools and what each metric measures

- **stress-ng** (`--cpu`, `--vm`, `--cache`): CPU, memory and cache, via
  a count of "bogo operations" per second (real time and CPU time).
  See `man stress-ng` / the official documentation for the exact
  definition of each stressor.
- **STREAM**: memory bandwidth (Copy/Scale/Add/Triad, MB/s). Under
  QEMU/TCG, the output is numerically invalid (lack of reliable timer
  resolution under emulation) and is discarded by design — see the
  comment in the STREAM block of `parse_file()` in `analyze_benchmarks.py`.
- **fio**: disk (`ext4` on the VMs, physical device on the host) —
  sequential read/write, random IOPS (70/30 read/write mix) and latency
  (single queue, QD=1, `ioengine=libaio`, `iodepth=1`, `numjobs=1` —
  **not** `ioengine=sync`; even at QD=1 it still goes through
  `io_submit()`/`io_getevents()`).
- **CUDA Samples (matrixMul)** and **NCCL (nccl-tests)**: GPU — GFlop/s
  and bus bandwidth (`Avg bus bandwidth`) for `all_reduce`,
  `all_gather` and `sendrecv`. Only collected on `host` and `kvm`
  (plain QEMU/TCG doesn't do PCIe/GPU passthrough).

## Statistical pipeline (`src/analysis/analyze_benchmarks.py`)

Per metric, comparing the 3 environments:

1. Descriptive statistics + 95% CI via bootstrap (VM-stratified when
   there's more than one VM in the treatment).
2. Normality test per group (Shapiro-Wilk) and variance homogeneity
   test between groups (Levene).
3. Global test: ANOVA if all groups are normal and homoscedastic;
   otherwise, **Kruskal-Wallis**.
4. Dunn post-hoc (Bonferroni) when the global test rejects H0, with
   more than 2 groups.
5. Effect size (epsilon²) for Kruskal-Wallis.
6. **Benjamini-Hochberg (FDR) correction** on `teste_global_p` across
   ALL metrics simultaneously (`p_fdr_corrigido` column) — controls the
   false-positive rate when testing multiple independent metrics, added
   in the 2026-07-15 reorganization.
7. Heterogeneity check between source VMs (Kruskal-Wallis VM A vs. B
   vs. C), to flag when the "difference between environments" might be
   confounded with a difference between specific VMs.

## Anomalies investigated

### Cache (RESOLVED)

`stress-ng --cache` initially reported physically implausible gains
under virtualization (KVM ~+324%, QEMU ~+46% relative to host). The
hypothesis of a PMU (Performance Monitoring Unit) not exposed to the
guest was tested and didn't explain the effect on its own. The root
cause identified was an **incomplete cache topology exposed to the
guest**: the Power9 host exposes 4 cache levels (L1i, L1d, L2, L3), but
the VM only sees L1i/L1d. Forcing `stress-ng --cache-level 1` (already
incorporated into `src/collect/benchmark-host.sh` and
`benchmark-vm.sh`) reduced the effect from +324% to ~+132% — the
remaining residual doesn't yet have a fully closed root cause, but most
of the discrepancy was explained by a cache-topology limitation in
PAPR/POWER9 environments, not documented in the references consulted so
far.

### QD=1 disk latency (OPEN)

In the single-queue test (synchronous random read, QD=1), both KVM and
QEMU showed **lower** average latency and **higher** IOPS than the
physical host — counterintuitive, since virtualization should add
overhead, not remove it. Hypotheses formulated (none of them come from
cited literature — they are operational hypotheses to be confirmed
experimentally):

- **Jitter/low contention**: the QD=1 test may be more sensitive to
  transient system noise than to the structural cost of virtualization.
- **Asymmetric contention via Slurm**: the host benchmark runs via
  `sbatch` without `--exclusive` (doesn't guarantee node exclusivity),
  while the VM benchmark runs as a manual script — if other Slurm jobs
  compete for the physical disk during the host run, that artificially
  inflates host latency, without any real virtualization advantage
  existing.
- **No vCPU pinning**: confirmed via the VM's XML (`virsh dumpxml`) —
  without `<cputune><vcpupin.../></cputune>`, the host scheduler is
  free to move vCPUs between physical cores. This neither rules out nor
  confirms the hypothesis, but it is an uncontrolled confounding
  variable.
- **Snapshot/backing-file indirection** on the VM's disk (qcow2 with a
  backing store) adds a layer of indirection absent on the host — but
  this effect would tend to *worsen*, not improve, the VM's latency, so
  it doesn't explain the anomaly on its own.

This investigation remains **open** and is recorded as an inconclusive
threat to validity.
