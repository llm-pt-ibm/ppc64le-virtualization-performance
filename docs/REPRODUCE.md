# How to reproduce

This work's reproducibility has two levels, with very different hardware
requirements:

1. **Reproducing the ANALYSIS** from the already-published raw data
   (`data/raw/`) — runs on **any machine** with Python 3.13 (no
   architecture/hardware requirement). This is the level **guaranteed**
   by the artifact kit.
2. **Reproducing the original COLLECTION** — requires specific
   Power9/ppc64le hardware (see section 2 below for the exact
   requirements). Documented here for methodological transparency; it's
   an external-validity limitation already noted in the paper, not a
   restriction of this kit itself.

## 1. Set up the environment

### Python

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r environment/requirements.txt --break-system-packages
```

(`--break-system-packages` is only needed if your Python is managed by
the distro/PEP 668; inside an activated venv it's normally not needed,
but that's how this environment was originally set up for this project.)

### R (only needed if you're regenerating the paper's final figures)

```bash
Rscript -e 'install.packages(c("ggplot2", "dplyr", "tidyr", "readr", "tibble"))'
```

Versions used when this reorganization was verified (2026-07-15): R 4.6.0,
ggplot2 4.0.3, dplyr 1.2.1, tidyr 1.3.2 — see `environment/r_packages.txt`.

## 2. Collecting the raw benchmarks

The scripts in `src/collect/` were used to collect the logs that are in
`data/raw/`:

- `benchmark-host.sh` — runs on the physical Power9 server via Slurm
  (`sbatch benchmark-host.sh`), generates `data/raw/host/benchmark_host_*.txt`.
- `benchmark-vm.sh` — runs inside each KVM VM (`./benchmark-vm.sh`),
  writes to `/root/experimentos/` on the guest VM itself; the resulting
  files need to be copied manually to `data/raw/kvm-<vm>/`
  (`kvm-gabrielly`, `kvm-lucas`, `kvm-ramalho`) — see the "Required
  manual step" section right below, **this step is not automatic**.
- `benchmark-qemu.sh` — same, for plain QEMU (TCG); copied manually to
  `data/raw/qemu-<vm>/`.
- `submit_benchmarks_slurm.sh` — loops `sbatch benchmark-host.sh` 15
  times in sequence, waiting for each job to finish before submitting
  the next one.
- `swap_gpu.sh` — reallocates the GPU (VFIO/hostdev) from one VM to
  another in libvirt; an operational utility, doesn't generate benchmark
  data.

**Hardware requirements:** these collection scripts require a server
with **ppc64le** architecture, an **IBM POWER9** processor, and (for the
GPU tests) **NVIDIA GPUs with NVLink2 and VFIO support** for
passthrough — plus `stress-ng`/`fio`/`STREAM`/CUDA Samples/nccl-tests
compiled at the paths the scripts expect (e.g.
`/usr/local/cuda-12.2`, `~/cuda-samples/...`, `~/nccl-tests/build/...`),
Slurm configured (for `benchmark-host.sh`), and KVM/QEMU VMs already
provisioned (see [snapshot_vm.md](snapshot_vm.md) for the manual cloning
procedure via `virsh` used in this experiment; for provisioning and
managing KVM VMs via Ansible instead of manually, see
https://github.com/llm-pt-ibm/kvm-power9-ansible.git). **It doesn't have
to be specifically the experiment's original server** — any
Power9/ppc64le hardware with those characteristics allows the collection
to be re-run. Exactly reproducing the absolute values, however, depends
on equivalent hardware; reproducing the statistical ANALYSIS from the
already-collected raw data (`data/raw/`) has no such restriction and
runs on any machine with Python 3.13 (section 3 below).

### Required manual step: copying logs from the VM to the host

`benchmark-vm.sh` and `benchmark-qemu.sh` run **inside the guest** and
write to `OUTDIR=/root/experimentos` — a directory **inside the VM**,
with no automatic correspondence to `data/raw/` in this repository's
checkout (which lives on the host, or on your local machine). **Without
this manual step, `data/raw/` stays incomplete and
`analyze_benchmarks.py` simply won't find those logs** (the pipeline
doesn't know the VM exists — it only reads what's already in
`data/raw/`).

After running the collection inside the VM, copy the generated `.txt`
files to the host/repository checkout, adjusting `vm_id` and environment
as needed:

```bash
# running from inside the VM after collection is done (example: KVM, VM "gabrielly"):
scp -r root@<vm-ip>:/root/experimentos/*.txt \
    ./data/raw/kvm-gabrielly/

# example: plain QEMU, VM "lucas":
scp -r root@<vm-ip>:/root/experimentos/*.txt \
    ./data/raw/qemu-lucas/
```

The same applies to the `lucas` and `ramalho` VMs (both under KVM and
QEMU) — repeat for each of the 6 VM x environment combinations before
running the analysis in section 3.

## 3. Running the analysis from the already-collected raw data

With the Python environment activated and from the repository root:

```bash
python3 src/analysis/analyze_benchmarks.py --logs-dir data/raw --out-dir data/processed
```

Generates in `data/processed/`:

- `extracted_data.csv` — tidy format, one row per (file, metric)
- `descriptive_summary.csv` — mean/median/std dev/IQR per metric x environment
- `statistical_results.csv` — normality, homogeneity, global test
  (ANOVA/Kruskal-Wallis), Dunn post-hoc, effect size, bootstrap CI and
  `p_fdr_corrigido` (Benjamini-Hochberg across all metrics)
- `vm_heterogeneity.csv` — heterogeneity between the 3 VMs per
  metric x treatment
- `parsing_warnings.csv` — cases where an expected metric wasn't found
  in some file (e.g. explains n differences between
  metrics/environments)
- `duplicate_statistics_alert.csv` — metric pairs whose global
  statistical test produced an exactly identical result. This can be
  benign (equally-sized groups with complete separation between
  environments produce the same Kruskal-Wallis H by mathematical
  construction, regardless of the raw values) or point to a computation
  bug — review manually before accepting any new case that shows up
  here. Generated automatically on EVERY pipeline run (not a one-off/
  manual check).
- `metadata.json` — git commit, timestamp and seed of the run
- `report.txt` — human-readable summary
- `plots/*.png` — exploratory boxplots per metric (small multiples,
  1 subplot per environment, independent Y scale per panel)
- `heatmaps/*.png` — 6 files, in curated/full pairs across three
  comparison scopes:
  - `significance_heatmap.png` / `vm_heterogeneity_heatmap.png` —
    CURATED version, with 1 metric per experimental domain (CPU, memory,
    cache, STREAM, disk, GPU — see `METRICAS_PRINCIPAIS` in
    `analyze_benchmarks.py`), meant for the paper body.
  - `significance_heatmap_full.png` / `vm_heterogeneity_heatmap_full.png`
    — the same figure with all ~19 metrics, for audit/appendix.
  - `significance_heatmap_3env.png` / `significance_heatmap_2env.png` —
    the significance heatmap split by comparison scope: metrics
    compared across all 3 environments (host/KVM/QEMU) vs. metrics only
    comparable across host/KVM (STREAM, GPU — QEMU-TCG doesn't run them
    validly). Mixing both scopes in one figure lets QEMU's huge overhead
    dominate the Kruskal-Wallis test and hide KVM's more subtle effects.
- `confidence_intervals/*.png` — 4 files: mean +- 95% CI per metric,
  curated/full x full-scale (3 environments)/zoomed (host/KVM only, no
  QEMU — for the same reason as the heatmap split above).

  In `significance_heatmap*.png`, the columns are visually grouped into
  "Assumption checks" (Shapiro host/kvm/qemu, Levene) and "Hypothesis
  test" (raw global test and `p_fdr_corrigido`) — these are different
  natures of p-value (a low p on assumption checks motivates the
  non-parametric test; a low p on the hypothesis test indicates a real
  difference between environments), so they don't share a single x-axis
  label. They complement the boxplots, not replace them.

```bash
rm -rf results/figures/plots results/figures/heatmaps
mv data/processed/plots results/figures/plots
mv data/processed/heatmaps results/figures/heatmaps
```

## 4. Regenerating the paper's final figures

The final figures (`results/figures/fig1..5_*_variation.pdf`) are
generated by **R**, not Python. `generate_plots.R` reads them from
`data/processed/kvm_summary.csv` and `data/processed/qemu_summary.csv`,
which are produced by the Python summary scripts
(`benchmark_summary_overhead.py`, `benchmark_summary_qemu.py`) — see the
usage examples in the header of `generate_plots.R`. If `data/raw/` is
updated (new collection, new replicate), re-run those Python scripts to
refresh the summary CSVs before regenerating the figures.

From the repository ROOT (the script uses relative paths
`results/figures/...`):

```bash
Rscript src/analysis/generate_plots.R
```

The exploratory plots in `results/figures/plots/*.png` (one PNG per
metric) are generated by **Python**, in step 3 above — those are
computed automatically from the data, with no manual transcription.
