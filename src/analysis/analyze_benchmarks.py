#!/usr/bin/env python3
"""
analyze_benchmarks.py

Parser + statistical pipeline for the benchmark logs
(host / KVM / plain QEMU) of the ppc64le virtualization experiment.

USAGE:
    python3 analyze_benchmarks.py --logs-dir /path/to/folder_with_host_kvm_qemu \
                                   --out-dir /path/to/output

Expects three subfolders inside --logs-dir:
    host/           -> benchmark_host_*.txt
    kvm-results/     -> benchmark_vm_*.txt
    qemu-results/    -> benchmark_qemu_*.txt

DEPENDENCIES (install with pip):
    pip install pandas numpy scipy scikit-posthocs matplotlib seaborn statsmodels --break-system-packages

OUTPUTS (in --out-dir):
    extracted_data.csv       -> one row per (file, metric), "tidy" format
    descriptive_summary.csv  -> mean, median, std dev, IQR per metric x environment
    statistical_results.csv -> normality, homogeneity, global test (+ p_fdr_corrected
                                    via Benjamini-Hochberg across all metrics), post-hoc,
                                    bootstrap CI
    parsing_warnings.csv        -> files where an expected metric was not found
    duplicate_statistics_alert.csv -> metric pairs with EXACTLY identical
                                           global_test_stat/p/epsilon² (see checar_estatisticas_duplicadas())
    metadata.json                -> git version/commit, timestamp and seed of the run
    report.txt              -> human-readable summary (VM heterogeneity warnings
                                    mention "pseudo-replication" — see
                                    docs/METHODOLOGICAL_GLOSSARY.md for what that means)
    plots/*.png              -> boxplot/stripplot per metric (small multiples, 1 subplot per
                                    environment, independent Y scale per panel)
    heatmaps/*.png              -> compact summary of the formal tests and of VM
                                    heterogeneity, as curated/full pairs: significance_heatmap.png
                                    and vm_heterogeneity_heatmap.png (1 metric per
                                    experimental domain, see METRICAS_PRINCIPAIS) + the
                                    "_full" versions with all ~19 metrics (audit/appendix)

METHODOLOGICAL NOTE — pseudo-replication: the KVM/QEMU treatments use 3 VMs
x 5 runs each (not 15 independent VMs). This is a correct and expected
nested design (see docs/METHODOLOGICAL_GLOSSARY.md, with a real numeric
example), not a collection error — the pipeline already accounts for it via
VM-stratified bootstrap (bootstrap_ci_estratificado_por_vm) and via
checar_heterogeneidade_vms, which explicitly flags when the VMs of a
treatment diverge from each other enough to warrant caution.
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import warnings
from datetime import datetime, timezone
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch
from scipy import stats
from statsmodels.stats.multitest import multipletests

warnings.filterwarnings("ignore")

# --------------------------------------------------------------------------
# Expected metrics per environment, used by `parsing_warnings` to detect
# when a file should have produced a metric and didn't (e.g. a host file
# missing "Avg bus bandwidth" for NCCL). GPU and STREAM are only expected
# on host/kvm: plain QEMU (TCG) doesn't do GPU passthrough, and STREAM
# output under QEMU is deliberately discarded for being numerically
# invalid (see comment in the STREAM block of parse_file) — so the
# absence of these metrics in QEMU files is not a parsing warning.
METRICAS_ESPERADAS_COMUNS = [
    "cpu_bogo_ops_s_real", "cpu_bogo_ops_s_cpu",
    "mem_bogo_ops_s_real", "mem_bogo_ops_s_cpu",
    "cache_bogo_ops_s_real", "cache_bogo_ops_s_cpu",
    "disk_seqread_MiBs", "disk_seqwrite_MiBs",
    "disk_iops_randread", "disk_iops_randwrite",
    "disk_iops_qd1", "disk_lat_avg_usec",
]
METRICAS_ESPERADAS_HOST_KVM = [
    "stream_copy_mbs", "stream_scale_mbs", "stream_add_mbs", "stream_triad_mbs",
    "gpu_matrixmul_gflops",
    "gpu_nccl_allreduce_busbw_gbs", "gpu_nccl_allgather_busbw_gbs", "gpu_nccl_sendrecv_busbw_gbs",
]

# --------------------------------------------------------------------------
# 1. EXTRACTION REGEXES
# --------------------------------------------------------------------------
# stress-ng metrics line, e.g.:
# stress-ng: info:  [8864] cpu             123456     60.01    409.02     10.27     16463.20        2356.34
STRESSNG_LINE = re.compile(
    r"^stress-ng:\s*info:\s*\[\d+\]\s+(?P<stressor>\S+)\s+"
    r"(?P<bogo_ops>\d+)\s+(?P<real_time>[\d.]+)\s+(?P<usr_time>[\d.]+)\s+"
    r"(?P<sys_time>[\d.]+)\s+(?P<bogo_ops_s_real>[\d.]+)\s+(?P<bogo_ops_s_cpu>[\d.]+)\s*$"
)

# fio: IOPS and latency lines (format "read:" / "write:" followed by bw=...)
FIO_IOPS_LINE = re.compile(
    r"^\s*(read|write):\s+IOPS=([\d.]+k?),\s+BW=([\d.]+)(KiB|MiB)/s"
)
FIO_LAT_LINE = re.compile(
    r"^\s*(?:clat|lat)\s*\(usec\):\s*min=\d+,\s*max=\d+,\s*avg=\s*([\d.]+)"
)
FIO_LAT_LINE_MSEC = re.compile(
    r"^\s*(?:clat|lat)\s*\(msec\):\s*min=\d+,\s*max=\d+,\s*avg=\s*([\d.]+)"
)

# STREAM: Copy:  117565.93 ...   (adjust to match your log's actual format)
STREAM_LINE = re.compile(
    r"^(Copy|Scale|Add|Triad):\s+([\d.]+)"
)

# CUDA matrixMul: looks for something like "GFlop/s" in the line
MATRIXMUL_LINE = re.compile(r"([\d.]+)\s*GFlop/s", re.IGNORECASE)

NCCL_AVG_LINE = re.compile(r"Avg bus bandwidth\s*:\s*([\d.]+)")


def parse_stressng_block(lines: list[str], stressor_name: str) -> Optional[dict]:
    """Looks for the stress-ng metrics line for a specific stressor
    (cpu, memory/vm, cache) within a list of lines."""
    for ln in lines:
        m = STRESSNG_LINE.match(ln.strip())
        if m and stressor_name in m.group("stressor"):
            return {
                "bogo_ops": float(m.group("bogo_ops")),
                "real_time_s": float(m.group("real_time")),
                "bogo_ops_s_real": float(m.group("bogo_ops_s_real")),
                "bogo_ops_s_cpu": float(m.group("bogo_ops_s_cpu")),
            }
    return None


def parse_fio_block(lines: list[str]) -> dict:
    """Extracts IOPS (read/write), BW and average latency (usec) from a
    fio output block. Returns a dict with whatever it finds."""
    out = {}
    for ln in lines:
        m = FIO_IOPS_LINE.match(ln)
        if m:
            direction, iops_raw, bw, bw_unit = m.groups()
            iops = float(iops_raw.replace("k", "")) * (1000 if "k" in iops_raw else 1)
            bw_val = float(bw) * (1024 if bw_unit == "MiB" else 1)  # normalize to KiB/s
            out[f"iops_{direction}"] = iops
            out[f"bw_kibs_{direction}"] = bw_val
        m2 = FIO_LAT_LINE.match(ln)
        if m2:
            out.setdefault("lat_avg_usec", float(m2.group(1)))
        m3 = FIO_LAT_LINE_MSEC.match(ln)
        if m3:
            out.setdefault("lat_avg_usec", float(m3.group(1)) * 1000)
    return out


def extract_section(text: str, header_regex: str, next_header_regex: str = r"^={10,}\s*$") -> list[str]:
    """Returns the lines between a '=== ... ===' header matching
    header_regex and the next '===...===' separator OR the next header
    of any kind, '=== [...] ===' / '=== ... ===' (important: in the logs,
    consecutive disk subsections -- e.g. RANDOM IOPS followed by
    LATENCY -- don't have a '========' separator between them, only
    between larger blocks; without this extra check the parser leaks
    from one subsection into the next)."""
    lines = text.splitlines()
    collecting = False
    block = []
    for ln in lines:
        is_any_header = bool(re.match(r"^===.*===\s*$", ln.strip()))
        if re.search(header_regex, ln):
            collecting = True
            block = []
            continue
        if collecting and (re.match(next_header_regex, ln) or is_any_header):
            break
        if collecting:
            block.append(ln)
    return block


def parse_file(path: str, ambiente: str, vm_id: str = "host",
                avisos: Optional[list[dict]] = None) -> list[dict]:
    """Parses a full log file and returns a list of dicts in tidy
    format: {arquivo, ambiente, vm_id, metrica, valor}.

    If `avisos` (a list) is passed, any metric expected for this
    environment that doesn't show up in the lines extracted from this
    file is recorded there (see METRICAS_ESPERADAS_COMUNS/_HOST_KVM), to
    become data/processed/parsing_warnings.csv in main()."""
    with open(path, "r", errors="ignore") as f:
        text = f.read()

    fname = os.path.basename(path)
    m_ts = re.search(r"(\d{8}_\d{6})", fname)
    timestamp = m_ts.group(1) if m_ts else fname

    rows = []

    def add(metric, value):
        if value is not None:
            rows.append({"arquivo": fname, "ambiente": ambiente, "vm_id": vm_id,
                          "timestamp": timestamp, "metrica": metric, "valor": value})

    # --- CPU ---
    cpu_block = extract_section(text, r"^=== CPU\b")
    cpu_res = parse_stressng_block(cpu_block, "cpu")
    if cpu_res:
        add("cpu_bogo_ops_s_real", cpu_res["bogo_ops_s_real"])
        add("cpu_bogo_ops_s_cpu", cpu_res["bogo_ops_s_cpu"])

    # --- MEMORY (logs before this translation say "MEMÓRIA") ---
    mem_block = extract_section(text, r"^=== (?:MEMÓRIA|MEMORY)\b")
    mem_res = None
    for stressor_guess in ("vm", "memhotplug", "malloc", "mem"):
        mem_res = parse_stressng_block(mem_block, stressor_guess)
        if mem_res:
            break
    if mem_res:
        add("mem_bogo_ops_s_real", mem_res["bogo_ops_s_real"])
        add("mem_bogo_ops_s_cpu", mem_res["bogo_ops_s_cpu"])

    # --- CACHE ---
    cache_block = extract_section(text, r"^=== CACHE\b")
    cache_res = parse_stressng_block(cache_block, "cache")
    if cache_res:
        add("cache_bogo_ops_s_real", cache_res["bogo_ops_s_real"])
        add("cache_bogo_ops_s_cpu", cache_res["bogo_ops_s_cpu"])

    # --- STREAM ---
    stream_block = extract_section(text, r"^=== STREAM")
    for ln in stream_block:
        m = STREAM_LINE.match(ln.strip())
        if m:
            valor = float(m.group(2))
            # STREAM under QEMU/TCG often "runs" but produces numerically
            # invalid output (Best Rate = 0.0 MB/s, Max time overflowing
            # to ~3.4e38) due to the lack of reliable timer resolution
            # under TCG emulation. This is NOT real bandwidth data — it's
            # a measurement failure and must be discarded, as already
            # documented in Section 6.1 of the paper.
            if valor <= 0.0:
                continue
            add(f"stream_{m.group(1).lower()}_mbs", valor)

    # --- DISK (host uses [DISCO_HOST]/[DISK_HOST], VMs use
    # [DISCO_EXT4]/[DISK_EXT4] — both spellings matched below since older
    # logs predate the English rewrite of the collection scripts) ---
    disk_prefix = "DISCO_HOST|DISK_HOST" if ambiente == "host" else "DISCO_EXT4|DISK_EXT4"

    seq_read = extract_section(text, rf"^=== \[(?:{disk_prefix})\] (?:LEITURA SEQUENCIAL|SEQUENTIAL READ)")
    fr = parse_fio_block(seq_read)
    if "bw_kibs_read" in fr:
        add("disk_seqread_MiBs", fr["bw_kibs_read"] / 1024)

    seq_write = extract_section(text, rf"^=== \[(?:{disk_prefix})\] (?:ESCRITA SEQUENCIAL|SEQUENTIAL WRITE)")
    fw = parse_fio_block(seq_write)
    if "bw_kibs_write" in fw:
        add("disk_seqwrite_MiBs", fw["bw_kibs_write"] / 1024)

    randrw = extract_section(text, rf"^=== \[(?:{disk_prefix})\] (?:IOPS ALEAT|RANDOM IOPS)")
    frw = parse_fio_block(randrw)
    if "iops_read" in frw:
        add("disk_iops_randread", frw["iops_read"])
    if "iops_write" in frw:
        add("disk_iops_randwrite", frw["iops_write"])

    latblk = extract_section(text, rf"^=== \[(?:{disk_prefix})\] (?:LAT[ÊE]NCIA|LATENCY)")
    flat = parse_fio_block(latblk)
    if "iops_read" in flat:
        add("disk_iops_qd1", flat["iops_read"])
    if "lat_avg_usec" in flat:
        add("disk_lat_avg_usec", flat["lat_avg_usec"])

    # --- GPU (host and KVM only) ---
    if ambiente in ("host", "kvm"):
        mm_block = extract_section(text, r"^=== GPU — MATRIXMUL")
        valores_mm = [float(m.group(1)) for ln in mm_block
                      for m in [MATRIXMUL_LINE.search(ln)] if m]
        if valores_mm:
            add("gpu_matrixmul_gflops", sum(valores_mm) / len(valores_mm))

        for nccl_name, header in [
            ("allreduce", r"^=== GPU — NCCL ALL_REDUCE"),
            ("allgather", r"^=== GPU — NCCL ALL_GATHER"),
            ("sendrecv", r"^=== GPU — NCCL SENDRECV"),
        ]:
            block = extract_section(text, header)
            for ln in block:
                m = NCCL_AVG_LINE.search(ln)
                if m:
                    add(f"gpu_nccl_{nccl_name}_busbw_gbs", float(m.group(1)))
                    break

    if avisos is not None:
        metricas_encontradas = {r["metrica"] for r in rows}
        esperadas = list(METRICAS_ESPERADAS_COMUNS)
        if ambiente in ("host", "kvm"):
            esperadas += METRICAS_ESPERADAS_HOST_KVM
        for metrica_esperada in esperadas:
            if metrica_esperada not in metricas_encontradas:
                avisos.append({
                    "arquivo": fname, "ambiente": ambiente, "vm_id": vm_id,
                    "metrica_esperada": metrica_esperada,
                    "motivo": "matching section/regex found no valid data in the file",
                })

    return rows


# --------------------------------------------------------------------------
# 2. STATISTICAL PIPELINE
# --------------------------------------------------------------------------
def _ci_delta_star_a_partir_de(M: float, boot_means: np.ndarray, alpha: float) -> tuple[float, float, float]:
    """Core shared by the two bootstrap functions below: given the vector
    of resampled means and the observed mean M, applies the delta*
    method (M - u*) to get (M, ci_lower, ci_upper). Extracted to avoid
    repeating the same math in bootstrap_ci_delta_star and
    bootstrap_ci_estratificado_por_vm."""
    if np.allclose(boot_means, boot_means[0]):
        return M, M, M
    delta_star = boot_means - M
    lo = np.quantile(delta_star, alpha / 2)
    hi = np.quantile(delta_star, 1 - alpha / 2)
    return M, M - hi, M - lo


def bootstrap_ci_delta_star(
    sample: np.ndarray, n_boot: int = 10000, alpha: float = 0.05, seed: int = 42
) -> tuple[float, float, float]:
    """Bootstrap CI, delta* method (M - u*), following the course
    material (slide 'Calculating Confidence Intervals').
    Returns (M, ci_lower, ci_upper)."""
    rng = np.random.default_rng(seed)
    sample = np.asarray(sample, dtype=float)
    M = sample.mean()
    n = len(sample)
    if n < 2 or np.allclose(sample, sample[0]):
        # degenerate sample (n<2 or all values identical) -> CI collapses to the value itself
        return M, M, M
    boot_means = np.empty(n_boot)
    for i in range(n_boot):
        boot_sample = rng.choice(sample, size=n, replace=True)
        boot_means[i] = boot_sample.mean()
    return _ci_delta_star_a_partir_de(M, boot_means, alpha)


def bootstrap_ci_estratificado_por_vm(
    df_metric: pd.DataFrame, n_boot: int = 10000, alpha: float = 0.05, seed: int = 42
) -> tuple[float, float, float]:
    """Bootstrap CI RESPECTING the nested structure (3 VMs x 5 replicates):
    each iteration draws VMs WITH replacement (level 1), then draws
    replicates WITH replacement within each drawn VM (level 2). This
    avoids treating the 15 runs as i.i.d. when there's real heterogeneity
    between VMs (see checar_heterogeneidade_vms). Expects df_metric with
    columns ['vm_id', 'valor']. Falls back to the plain
    bootstrap_ci_delta_star when there's only 1 VM (e.g. host)."""
    vms = df_metric["vm_id"].unique()
    if len(vms) < 2:
        return bootstrap_ci_delta_star(df_metric["valor"].values, n_boot, alpha, seed)

    rng = np.random.default_rng(seed)
    valores_por_vm = {vm: df_metric[df_metric["vm_id"] == vm]["valor"].values for vm in vms}
    M = df_metric["valor"].mean()
    n_vms = len(vms)

    boot_means = np.empty(n_boot)
    for i in range(n_boot):
        vms_sorteadas = rng.choice(vms, size=n_vms, replace=True)
        amostra_iter = []
        for vm in vms_sorteadas:
            vals = valores_por_vm[vm]
            amostra_iter.extend(rng.choice(vals, size=len(vals), replace=True))
        boot_means[i] = np.mean(amostra_iter)

    return _ci_delta_star_a_partir_de(M, boot_means, alpha)


def analisar_metrica(df_metric: pd.DataFrame, alpha: float = 0.05,
                      metrica_nome: str = "?") -> dict:
    """Takes a DataFrame with columns ['ambiente', 'vm_id', 'valor'] for
    ONE metric, and runs the pipeline: descriptive stats, Shapiro-Wilk,
    Levene, ANOVA or Kruskal-Wallis, Dunn post-hoc (if applicable),
    bootstrap CI per environment (VM-stratified when applicable).

    `metrica_nome` is only used to identify the metric in console
    warnings when Shapiro-Wilk/Levene fail (e.g. degenerate sample) —
    previously these failures were silent (falling straight back to
    "not normal"/"heteroscedastic" with no warning at all)."""
    resultado = {}
    grupos = {amb: g["valor"].values for amb, g in df_metric.groupby("ambiente")}

    # Descriptive stats + bootstrap CI per environment (VM-stratified if >1 VM)
    for amb, g in df_metric.groupby("ambiente"):
        vals = g["valor"].values
        if len(vals) < 2:
            continue
        if g["vm_id"].nunique() > 1:
            M, lo, hi = bootstrap_ci_estratificado_por_vm(g[["vm_id", "valor"]])
            resultado[f"{amb}_ic_metodo"] = "estratificado_por_vm"
        else:
            M, lo, hi = bootstrap_ci_delta_star(vals)
            resultado[f"{amb}_ic_metodo"] = "simples"
        resultado[f"{amb}_n"] = len(vals)
        resultado[f"{amb}_media"] = np.mean(vals)
        resultado[f"{amb}_mediana"] = np.median(vals)
        resultado[f"{amb}_desvio"] = np.std(vals, ddof=1)
        resultado[f"{amb}_iqr"] = np.percentile(vals, 75) - np.percentile(vals, 25)
        resultado[f"{amb}_ic95_lo"] = lo
        resultado[f"{amb}_ic95_hi"] = hi

    # Normality (Shapiro-Wilk) per group
    normal_flags = []
    for amb, vals in grupos.items():
        if len(vals) >= 3:
            try:
                w, p = stats.shapiro(vals)
                resultado[f"{amb}_shapiro_p"] = p
                normal_flags.append(p > alpha)
            except Exception as exc:
                print(f"[warning] Shapiro-Wilk failed on '{metrica_nome}'/{amb} "
                      f"(assuming non-normal): {exc}")
                normal_flags.append(False)

    todos_normais = all(normal_flags) if normal_flags else False

    # Variance homogeneity (Levene)
    vals_list = [v for v in grupos.values() if len(v) >= 2]
    if len(vals_list) >= 2:
        try:
            lev_stat, lev_p = stats.levene(*vals_list)
            resultado["levene_p"] = lev_p
            variancias_homogeneas = lev_p > alpha
        except Exception as exc:
            print(f"[warning] Levene failed on '{metrica_nome}' "
                  f"(assuming heteroscedastic): {exc}")
            resultado["levene_p"] = np.nan
            variancias_homogeneas = False
    else:
        variancias_homogeneas = False

    # Global test: ANOVA if normal + homoscedastic, otherwise Kruskal-Wallis
    if len(vals_list) >= 2:
        if todos_normais and variancias_homogeneas:
            f_stat, p_val = stats.f_oneway(*vals_list)
            resultado["teste_global"] = "ANOVA"
            resultado["teste_global_estat"] = f_stat
            resultado["teste_global_p"] = p_val
        else:
            h_stat, p_val = stats.kruskal(*vals_list)
            resultado["teste_global"] = "Kruskal-Wallis"
            resultado["teste_global_estat"] = h_stat
            resultado["teste_global_p"] = p_val

        # Dunn + Bonferroni post-hoc, only if the global test rejects H0
        if p_val < alpha and len(vals_list) > 2:
            try:
                import scikit_posthocs as sp
                dunn = sp.posthoc_dunn(df_metric, val_col="valor", group_col="ambiente",
                                        p_adjust="bonferroni")
                resultado["posthoc_dunn"] = dunn.to_dict()
            except ImportError:
                resultado["posthoc_dunn"] = "scikit-posthocs not installed (pip install scikit-posthocs --break-system-packages)"

        # Effect size (epsilon-squared for Kruskal-Wallis)
        if resultado["teste_global"] == "Kruskal-Wallis":
            n_total = sum(len(v) for v in vals_list)
            resultado["effect_size_epsilon2"] = h_stat / (n_total - 1) if n_total > 1 else np.nan

    return resultado


def checar_heterogeneidade_vms(df: pd.DataFrame, alpha: float = 0.05) -> pd.DataFrame:
    """For each metric and each treatment (kvm/qemu), tests whether the
    3 source VMs differ from each other (Kruskal-Wallis vm_A vs vm_B vs
    vm_C). Returns a DataFrame with the result per metric x treatment."""
    linhas = []
    for (metrica, ambiente), sub in df[df["ambiente"] != "host"].groupby(["metrica", "ambiente"]):
        vms = sub["vm_id"].unique()
        if len(vms) < 2:
            continue
        grupos = [sub[sub["vm_id"] == vm]["valor"].values for vm in vms]
        grupos = [g for g in grupos if len(g) >= 2]
        if len(grupos) < 2:
            continue
        todos_valores = np.concatenate(grupos)
        if np.allclose(todos_valores, todos_valores[0]):
            # all values identical across all VMs -> no evidence of a difference
            h, p = 0.0, 1.0
        else:
            try:
                h, p = stats.kruskal(*grupos)
            except Exception:
                h, p = np.nan, np.nan
        linhas.append({
            "metrica": metrica, "ambiente": ambiente,
            "n_vms": len(grupos), "kruskal_h": h, "kruskal_p": p,
            "heterogeneo": (p < alpha) if pd.notna(p) else None,
        })
    return pd.DataFrame(linhas)


def aplicar_correcao_fdr(df_stats: pd.DataFrame, alpha: float = 0.05) -> pd.DataFrame:
    """Applies the Benjamini-Hochberg (FDR) correction to teste_global_p
    across ALL metrics simultaneously (distinct from the Dunn post-hoc,
    which already uses Bonferroni but only WITHIN a metric). Adds the
    p_fdr_corrigido column to df_stats (NaN on rows without a global test)."""
    df_stats = df_stats.copy()
    df_stats["p_fdr_corrigido"] = np.nan
    mask = df_stats["teste_global_p"].notna() if "teste_global_p" in df_stats else pd.Series(dtype=bool)
    if mask.sum() > 0:
        _, p_corrigido, _, _ = multipletests(
            df_stats.loc[mask, "teste_global_p"].values, alpha=alpha, method="fdr_bh"
        )
        df_stats.loc[mask, "p_fdr_corrigido"] = p_corrigido
    return df_stats


def checar_estatisticas_duplicadas(df_stats: pd.DataFrame) -> pd.DataFrame:
    """Sanity check: compares teste_global_estat, teste_global_p and
    effect_size_epsilon2 across ALL pairs of distinct metrics. If any
    pair has all three values EXACTLY identical, prints a WARNING
    (identical statistics between conceptually distinct metrics is a
    strong sign of an array-reuse bug, but can also be benign: a
    mathematical property of Kruskal-Wallis under complete separation
    between equally-sized groups — as happens between cache and memory
    in this experiment — produces an identical H regardless of the raw
    values). Returns a DataFrame (one row per colliding pair) that is
    also saved to data/processed/duplicate_statistics_alert.csv."""
    cols = ["teste_global_estat", "teste_global_p", "effect_size_epsilon2"]
    if not all(c in df_stats.columns for c in cols):
        return pd.DataFrame(columns=["metrica_a", "metrica_b"] + cols)

    sub = df_stats.dropna(subset=cols)[["metrica"] + cols].reset_index(drop=True)
    linhas = []
    for i in range(len(sub)):
        for j in range(i + 1, len(sub)):
            a, b = sub.iloc[i], sub.iloc[j]
            if (a[cols] == b[cols]).all():
                linhas.append({
                    "metrica_a": a["metrica"], "metrica_b": b["metrica"],
                    "teste_global_estat": a["teste_global_estat"],
                    "teste_global_p": a["teste_global_p"],
                    "effect_size_epsilon2": a["effect_size_epsilon2"],
                })

    df_alerta = pd.DataFrame(linhas, columns=["metrica_a", "metrica_b"] + cols)
    if not df_alerta.empty:
        print(f"\n[WARNING] {len(df_alerta)} metric pair(s) with IDENTICAL global test "
              f"statistics (teste_global_estat, teste_global_p and effect_size_epsilon2 all "
              f"matching exactly) — see data/processed/duplicate_statistics_alert.csv and review "
              f"manually before assuming it's a benign coincidence:")
        for _, row in df_alerta.iterrows():
            print(f"    {row['metrica_a']}  ==  {row['metrica_b']}  "
                  f"(H={row['teste_global_estat']:.4f}, p={row['teste_global_p']:.4g})")
    return df_alerta


def obter_metadata_execucao(args: argparse.Namespace, seed_bootstrap: int = 42) -> dict:
    """Collects run metadata (script's git commit, UTC timestamp, seed
    used for randomization) for metadata.json alongside the outputs —
    lets you reproduce/audit exactly which code and data version
    produced a given statistical_results.csv."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=script_dir, stderr=subprocess.DEVNULL
        ).decode().strip()
        dirty = bool(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=script_dir, stderr=subprocess.DEVNULL
        ).decode().strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        commit, dirty = None, None

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": commit,
        "git_dirty": dirty,
        "git_commit_nota": (
            "null because the directory was not a git repository at run time"
            if commit is None else None
        ),
        "seed_bootstrap": seed_bootstrap,
        "logs_dir": os.path.abspath(args.logs_dir),
        "out_dir": os.path.abspath(args.out_dir),
    }


# Lean style, in the spirit of a scientific-paper figure: low visual
# noise, adequate contrast, consistent fonts/sizes across all figures.
# Applied once, at import time — not repeated per plot.
plt.rcParams.update({
    "font.size": 10,
    "axes.titlesize": 11,
    "axes.titleweight": "normal",
    "axes.labelsize": 10,
    "axes.edgecolor": "#4d4d4d",
    "axes.linewidth": 0.8,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "xtick.color": "#333333",
    "ytick.color": "#333333",
    "legend.frameon": False,
    "legend.fontsize": 8,
    "figure.facecolor": "white",
    "savefig.facecolor": "white",
})

# ---------------------------------------------------------------------------
# PLOTS — one file per metric, each a small-multiples panel with one
# subplot PER ENVIRONMENT, and an independent Y scale per subplot (see
# _plot_metrica docstring). This replaces an earlier version with a
# single Axes per metric and a shared axis across environments: when
# QEMU is 10-90x slower than host/KVM (CPU, memory, cache), forcing all
# three onto the same axis (even with a log scale) reduced the
# environment(s) with lower absolute variance to a flat line with no
# visible box — a boxplot that told you nothing about that group.
# ---------------------------------------------------------------------------

# Map: internal metric -> (readable label, unit)
ROTULOS = {
    "cpu_bogo_ops_s_real":  ("CPU — real time",         "bogo ops/s"),
    "cpu_bogo_ops_s_cpu":   ("CPU — CPU time",           "bogo ops/s"),
    "mem_bogo_ops_s_real":  ("Memory — real time",       "bogo ops/s"),
    "mem_bogo_ops_s_cpu":   ("Memory — CPU time",        "bogo ops/s"),
    "stream_copy_mbs":      ("STREAM — Copy",            "MB/s"),
    "stream_scale_mbs":     ("STREAM — Scale",           "MB/s"),
    "stream_add_mbs":       ("STREAM — Add",             "MB/s"),
    "stream_triad_mbs":     ("STREAM — Triad",           "MB/s"),
    "cache_bogo_ops_s_real":("Cache — real time",        "bogo ops/s"),
    "cache_bogo_ops_s_cpu": ("Cache — CPU time",         "bogo ops/s"),
    "disk_seqread_MiBs":    ("Sequential read",          "MiB/s"),
    "disk_seqwrite_MiBs":   ("Sequential write",         "MiB/s"),
    "disk_iops_qd1":        ("IOPS QD=1 (read)",         "IOPS"),
    "disk_lat_avg_usec":    ("Average latency",          "µs"),
    "disk_iops_randread":   ("Random IOPS — read",       "IOPS"),
    "disk_iops_randwrite":  ("Random IOPS — write",      "IOPS"),
    "gpu_matrixmul_gflops": ("matrixMul",                "GFlop/s"),
    "gpu_nccl_allreduce_busbw_gbs": ("NCCL all_reduce",  "GB/s (busbw)"),
    "gpu_nccl_allgather_busbw_gbs": ("NCCL all_gather",  "GB/s (busbw)"),
    "gpu_nccl_sendrecv_busbw_gbs":  ("NCCL sendrecv",    "GB/s (busbw)"),
}

# metric -> subfolder (just organizes the on-disk output; each figure is
# still 1 standalone file, with a title that already indicates the paper
# section)
SECAO = {
    "cpu_bogo_ops_s_real": ("cpu", "Section 5.2"), "cpu_bogo_ops_s_cpu": ("cpu", "Section 5.2"),
    "mem_bogo_ops_s_real": ("memory", "Section 5.3"), "mem_bogo_ops_s_cpu": ("memory", "Section 5.3"),
    "stream_copy_mbs": ("memory", "Section 5.3"), "stream_scale_mbs": ("memory", "Section 5.3"),
    "stream_add_mbs": ("memory", "Section 5.3"), "stream_triad_mbs": ("memory", "Section 5.3"),
    "cache_bogo_ops_s_real": ("cache", "Section 5.4"), "cache_bogo_ops_s_cpu": ("cache", "Section 5.4"),
    "disk_seqread_MiBs": ("disk_seq", "Section 5.5"), "disk_seqwrite_MiBs": ("disk_seq", "Section 5.5"),
    "disk_iops_qd1": ("disk_seq", "Section 5.5"), "disk_lat_avg_usec": ("disk_seq", "Section 5.5"),
    "disk_iops_randread": ("disk_iops", "Section 5.6"), "disk_iops_randwrite": ("disk_iops", "Section 5.6"),
    "gpu_matrixmul_gflops": ("gpu", "Section 5.7"),
    "gpu_nccl_allreduce_busbw_gbs": ("gpu", "Section 5.7"),
    "gpu_nccl_allgather_busbw_gbs": ("gpu", "Section 5.7"),
    "gpu_nccl_sendrecv_busbw_gbs": ("gpu", "Section 5.7"),
}

# Reason QEMU is missing from a metric — NOT the same reason for STREAM
# and for GPU, so we use a dict instead of a fixed conditional string:
#   - STREAM: a real build/execution failure under TCG (the metric is
#     collected, but comes out numerically invalid — Best Rate=0.0, Max
#     time overflowing to float — and is discarded in parse_file; see
#     the comment in the STREAM block).
#   - GPU (matrixMul/NCCL): plain QEMU was never tested here by
#     experimental DESIGN (RQ4 restricts GPU to host/KVM), and it also
#     couldn't work without VFIO/passthrough (TCG doesn't expose the
#     NVIDIA driver to the VM) — this is a known scope limitation, not
#     an execution failure.
MOTIVO_EXCLUSAO_QEMU = {
    "stream_copy_mbs": "QEMU excluded — build/execution failure under TCG (SIMD/VSX not implemented)",
    "stream_scale_mbs": "QEMU excluded — build/execution failure under TCG (SIMD/VSX not implemented)",
    "stream_add_mbs": "QEMU excluded — build/execution failure under TCG (SIMD/VSX not implemented)",
    "stream_triad_mbs": "QEMU excluded — build/execution failure under TCG (SIMD/VSX not implemented)",
    "gpu_matrixmul_gflops": "QEMU out of scope (RQ4) — NVIDIA driver unsupported without VFIO/passthrough",
    "gpu_nccl_allreduce_busbw_gbs": "QEMU out of scope (RQ4) — NVIDIA driver unsupported without VFIO/passthrough",
    "gpu_nccl_allgather_busbw_gbs": "QEMU out of scope (RQ4) — NVIDIA driver unsupported without VFIO/passthrough",
    "gpu_nccl_sendrecv_busbw_gbs": "QEMU out of scope (RQ4) — NVIDIA driver unsupported without VFIO/passthrough",
}

# Color per ENVIRONMENT — consistent across the paper (not per VM)
COR_AMBIENTE = {"host": "#4d4d4d", "kvm": "#1f77b4", "qemu": "#d97706"}
ORDEM_AMBIENTES = ["host", "kvm", "qemu"]


def _cor_vm_map(sub: pd.DataFrame) -> dict:
    """VM -> color map, consistent across calls (same VM = same color
    in any figure that colors it)."""
    paleta = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"]
    vms = sorted(v for v in sub["vm_id"].unique() if v != "host")
    return {vm: paleta[i % len(paleta)] for i, vm in enumerate(vms)}


# small horizontal jitter — just enough to separate overlapping points,
# without spreading the cloud so much it competes with the boxplot
_JITTER_STD = 0.035

# Within ONE environment (no longer across environments — see the note
# in _plot_metrica), above this ratio between the largest and smallest
# positive value, the linear scale compresses that environment's own
# distribution too much and we switch to log.
RAZAO_LOG = 8.0


def _pontos_do_ambiente(ax, vals_por_vm: dict, ambiente_heterogeneo: bool,
                         vm_color_map: dict, rng: np.random.Generator, legend_handles: dict) -> None:
    """Draws the individual points of ONE environment, centered at x=1
    (each environment now has its own Axes — see _plot_metrica). Neutral
    (gray) color by default; color by VM only when that environment has
    significant heterogeneity between VMs (`ambiente_heterogeneo`).
    Points sit at a low zorder — they're only there to show spread, the
    boxplot is drawn on top and is the dominant visual element."""
    if ambiente_heterogeneo:
        for vm, vvals in vals_por_vm.items():
            jitter = rng.normal(0, _JITTER_STD, size=len(vvals))
            pontos = ax.scatter(np.full(len(vvals), 1) + jitter, vvals,
                                 color=vm_color_map[vm], s=16, alpha=0.5,
                                 zorder=2, linewidths=0)
            legend_handles[vm] = pontos
    else:
        vals = np.concatenate(list(vals_por_vm.values()))
        jitter = rng.normal(0, _JITTER_STD, size=len(vals))
        ax.scatter(np.full(len(vals), 1) + jitter, vals,
                   color="#404040", s=16, alpha=0.4, zorder=2, linewidths=0)


def _plot_ambiente(ax, sub_amb: pd.DataFrame, amb: str, cor: str, unidade: str,
                    ambiente_heterogeneo: bool, vm_color_map: dict,
                    rng: np.random.Generator, legend_handles: dict) -> None:
    """Draws the boxplot + points of ONE environment in its own Axes,
    with the Y scale sized only from this environment's data (see
    _plot_metrica for why that's essential here).

    `legend_handles` is a dict SHARED across all environments in the
    same figure (populated here, but the legend itself is drawn once,
    outside any specific Axes — see _plot_metrica). This keeps the
    legend for an environment in the middle of the row (e.g. KVM, when
    there are 3 panels) from being pinned to that specific Axes'
    coordinates and ending up touching or overlapping the neighboring
    panel (e.g. QEMU) — in practice most VM-heterogeneity cases in this
    experiment occur under KVM, which sits in the middle panel when all
    3 environments are present."""
    vals = sub_amb["valor"].values
    vals_por_vm = {vm: g["valor"].values for vm, g in sub_amb.groupby("vm_id")}

    _pontos_do_ambiente(ax, vals_por_vm, ambiente_heterogeneo, vm_color_map, rng, legend_handles)

    bp = ax.boxplot([vals], tick_labels=["Host" if amb == "host" else amb.upper()],
                     showfliers=False, widths=0.5, patch_artist=True, zorder=3,
                     boxprops=dict(linewidth=1.6, edgecolor="#333333"),
                     whiskerprops=dict(linewidth=1.6, color="#333333"),
                     capprops=dict(linewidth=1.6, color="#333333"),
                     medianprops=dict(linewidth=2.2, color="black"))
    bp["boxes"][0].set_facecolor(cor)
    bp["boxes"][0].set_alpha(0.6)

    # Log scale only when THIS environment's OWN values vary a lot among
    # themselves (a local decision — each environment has its own axis).
    positivos = vals[vals > 0]
    if len(positivos) >= 2 and (positivos.max() / positivos.min()) > RAZAO_LOG:
        ax.set_yscale("log")

    ax.set_ylabel(unidade)
    ax.grid(axis="y", linestyle="--", linewidth=0.5, alpha=0.35, zorder=0)
    # Each panel has its own Y scale (see _plot_metrica) — the order of
    # magnitude plotted varies between panels, so we add extra margin so
    # the box/whiskers never touch the panel border.
    ax.margins(y=0.08)


def _plot_metrica(df: pd.DataFrame, metrica: str, df_heterog: Optional[pd.DataFrame] = None):
    """Generates the Figure for ONE metric as a small-multiples panel:
    ONE subplot per environment present, each with an INDEPENDENT Y scale.

    Why: a single axis shared across host/KVM/QEMU only works when all
    three have similar magnitude. In this experiment that's the
    exception, not the rule — QEMU is typically 10-90x slower than
    host/KVM on CPU/memory/cache. With a shared linear axis, the
    group(s) with lower absolute variance (usually host) get reduced to
    a flat line with no visible box — a boxplot that tells you nothing
    about that group. Just switching the shared axis's scale (linear ->
    log) doesn't fix it: the problem isn't the scale itself, it's that
    each group's SPREAD needs its own reference frame to stay legible,
    and spread differs between groups just as much as magnitude does.
    The standard solution for this scenario (groups with very different
    location/scale) is small multiples: each environment gets its own
    Axes, sized only for its own data — this way the box, whiskers and
    points of ANY environment, large or small, stay legible.

    The relative-MAGNITUDE comparison between environments (the "how
    many % slower") is already covered by the paper's final figures
    (results/figures/fig1..4, generated by generate_plots.R). This
    figure here is deliberately about the SHAPE of each distribution
    (IQR, tails, outliers, VM heterogeneity), not about comparing box
    heights across panels — hence the note in the title below warning
    that the axes are independent.
    """
    sub = df[df["metrica"] == metrica]
    rotulo, unidade = ROTULOS.get(metrica, (metrica, ""))

    ambientes_presentes = [a for a in ORDEM_AMBIENTES if len(sub[sub["ambiente"] == a]) > 0]
    if not ambientes_presentes:
        return None

    ambientes_heterog = set()
    if df_heterog is not None and not df_heterog.empty:
        hrows = df_heterog[(df_heterog["metrica"] == metrica) & (df_heterog["heterogeneo"] == True)]
        ambientes_heterog = set(hrows["ambiente"])

    n = len(ambientes_presentes)
    fig, axes = plt.subplots(1, n, figsize=(2.8 * n + 1.0, 4.5))
    axes = [axes] if n == 1 else list(axes)

    rng = np.random.default_rng(0)
    vm_color_map = _cor_vm_map(sub)

    # Shared across all environments in this figure — the legend is
    # drawn ONCE, outside any specific Axes (see the _plot_ambiente
    # docstring and the fig.legend further below).
    legend_handles = {}
    for ax, amb in zip(axes, ambientes_presentes):
        sub_amb = sub[sub["ambiente"] == amb]
        _plot_ambiente(ax, sub_amb, amb, COR_AMBIENTE[amb], unidade,
                       amb in ambientes_heterog, vm_color_map, rng, legend_handles)

    titulo_fig = rotulo
    secao = SECAO.get(metrica, (None, None))[1]
    if secao:
        titulo_fig += f" ({secao})"
    if "qemu" not in ambientes_presentes and "kvm" in ambientes_presentes:
        titulo_fig += f"\n{MOTIVO_EXCLUSAO_QEMU.get(metrica, 'QEMU excluded — reason not catalogued')}"
    titulo_fig += "\nindependent Y scale per panel — do not compare box heights across environments"
    fig.suptitle(titulo_fig, fontsize=10.5)

    # VM legend outside the plotting area (to the right of the whole
    # figure), instead of inside a specific Axes — avoids overlapping the
    # box/data points, and avoids colliding with the neighboring panel
    # when the heterogeneous environment is the middle one (typically
    # KVM, when all 3 environments are present). bbox_inches="tight" in
    # savefig (see gerar_graficos) keeps the legend from being cut off.
    if legend_handles:
        fig.legend(legend_handles.values(), legend_handles.keys(),
                   loc="upper left", bbox_to_anchor=(1.0, 0.95), bbox_transform=fig.transFigure,
                   title="VM", title_fontsize=8, fontsize=8, borderaxespad=0.5)

    return fig


def gerar_graficos(df: pd.DataFrame, out_dir: str, df_heterog: Optional[pd.DataFrame] = None) -> None:
    """Generates 1 figure per metric (small-multiples panel, one subplot
    per environment — see _plot_metrica), organized into subfolders by
    paper section: out_dir/plots/<section>/<metrica>.png"""
    graf_dir_base = os.path.join(out_dir, "plots")
    os.makedirs(graf_dir_base, exist_ok=True)

    for metrica in df["metrica"].unique():
        if metrica not in ROTULOS:
            continue  # metric with no mapped label (e.g. a future new metric) — skip
        pasta, _ = SECAO.get(metrica, ("other", None))
        graf_dir = os.path.join(graf_dir_base, pasta)
        os.makedirs(graf_dir, exist_ok=True)

        fig = _plot_metrica(df, metrica, df_heterog=df_heterog)
        if fig is None:
            continue
        fig.tight_layout()
        fig.savefig(os.path.join(graf_dir, f"{metrica}.png"),
                    dpi=200, bbox_inches="tight")
        plt.close(fig)
        print(f"  saved: {pasta}/{metrica}.png")


# ---------------------------------------------------------------------------
# HEATMAPS — a compact summary of the formal tests for all ~19 metrics at
# once (today this only exists scattered across statistical_results.csv/
# report.txt). These heatmaps COMPLEMENT the boxplots from gerar_graficos
# (which show the raw data distribution per environment) — they don't
# replace them.
# ---------------------------------------------------------------------------

# Significance bands used in the two heatmaps below. Code 0 is reserved
# for "no data" (NaN — e.g. test not applicable) and is gray, never
# green/red, so it isn't mistaken for "not significant".
_CORES_P = ["#d9d9d9", "#2ca25f", "#fee08b", "#fc8d59", "#d73027"]
_LEGENDA_P = ["no data", "n.s. (p≥0.05)", "p<0.05", "p<0.01", "p<0.001"]


def _bin_p_valor(p: float) -> int:
    """Buckets a p-value into the _CORES_P/_LEGENDA_P bands."""
    if pd.isna(p):
        return 0
    if p < 0.001:
        return 4
    if p < 0.01:
        return 3
    if p < 0.05:
        return 2
    return 1


def _formatar_p(p: float) -> str:
    if pd.isna(p):
        return "—"
    if p < 0.001:
        return "<0.001"
    return f"{p:.3f}"


def _ordem_metricas_presentes(metricas_disponiveis) -> list[str]:
    """Orders metrics following the same paper-section order used in
    ROTULOS/SECAO (not the CSV's alphabetical order), with any metric
    not catalogued in ROTULOS appended at the end."""
    disponiveis = set(metricas_disponiveis)
    ordenadas = [m for m in ROTULOS if m in disponiveis]
    restantes = sorted(disponiveis - set(ordenadas))
    return ordenadas + restantes


def _rotulo_curto(metrica: str) -> str:
    return ROTULOS.get(metrica, (metrica, ""))[0]


# One metric per experimental domain, for the "main" figure (paper body)
# — the "_full" version with all ~19 metrics is still generated
# separately, for audit/appendix; nothing is hidden, just reordered by
# editorial relevance. Selection criteria (recorded in the text too, not
# just here, because it's a content decision, not only a code one):
#   - cpu/mem: REAL time (not CPU time) — it's the wall-clock time that
#     captures hypervisor scheduling overhead; CPU time tends to
#     underestimate that effect.
#   - cache: ties to the "Cache (RESOLVED)" anomaly already documented in
#     docs/METHODOLOGY.md (incomplete cache topology exposed to the guest).
#   - stream: Triad is the most commonly cited summary metric in the
#     STREAM literature (McCalpin) — combines read+write+multiply into a
#     single number.
#   - disk: QD=1 latency ties to the "OPEN" anomaly also documented in
#     docs/METHODOLOGY.md (lower latency under virtualization — counterintuitive).
#   - gpu: matrixMul is the pure-compute benchmark; the 3 NCCL ones
#     measure inter-GPU interconnect, a distinct question (stays in "_full").
METRICAS_PRINCIPAIS = [
    "cpu_bogo_ops_s_real",
    "mem_bogo_ops_s_real",
    "cache_bogo_ops_s_real",
    "stream_triad_mbs",
    "disk_lat_avg_usec",
    "gpu_matrixmul_gflops",
]

METRICAS_3_AMBIENTES = [
    "cpu_bogo_ops_s_real",
    "mem_bogo_ops_s_real",
    "cache_bogo_ops_s_real",
    "disk_seqread_MiBs",
    "disk_seqwrite_MiBs",
    "disk_iops_randread",
    "disk_iops_randwrite",
    "disk_iops_qd1",
    "disk_lat_avg_usec",
]

METRICAS_2_AMBIENTES = [
    "stream_copy_mbs",
    "stream_scale_mbs",
    "stream_add_mbs",
    "stream_triad_mbs",
    "gpu_matrixmul_gflops",
    "gpu_nccl_allreduce_busbw_gbs",
    "gpu_nccl_allgather_busbw_gbs",
    "gpu_nccl_sendrecv_busbw_gbs",
]


def _heatmap_significancia(df_stats: pd.DataFrame, heat_dir: str,
                            metricas_alvo: Optional[list] = None,
                            sufixo: str = "") -> None:
    """significance_heatmap{sufixo}.png — metrics (row) x formal tests
    (column). The columns do NOT form a single homogeneous axis — they
    are two conceptually different groups, separated by a vertical line
    and group labels:

      1) "Assumption checks" (Shapiro host/kvm/qemu, Levene): here a LOW
         p-value (red) means the assumption was VIOLATED (non-normality
         or heteroscedasticity) — this decides ANOVA vs. Kruskal-Wallis,
         it is not a "real difference" between environments.
      2) "Hypothesis test" (raw global test and p_fdr_corrigido): here a
         LOW p-value (red) means a statistically significant difference
         was detected between host/KVM/QEMU — this is the substantive
         result. p_fdr_corrigido (Benjamini-Hochberg across all metrics)
         is the value that should guide conclusions when comparing
         several metrics at once.

    Mixing the two groups under a single x-axis label (e.g. "p-value")
    would be misleading, because it would have the reader reading "lots
    of red" as "lots of real differences", when part of the red might
    just be "data isn't normal" — hence the explicit visual separation.

    Effect size (ε²) sits in a separate third subplot, with its own
    continuous colormap (it isn't a p-value, it's a magnitude)."""
    disponiveis = _ordem_metricas_presentes(df_stats["metrica"])
    metricas = ([m for m in disponiveis if m in set(metricas_alvo)]
                if metricas_alvo is not None else disponiveis)
    idx = df_stats.set_index("metrica").reindex(metricas)

    colunas_p = {
        "Shapiro\nHost": "host_shapiro_p",
        "Shapiro\nKVM": "kvm_shapiro_p",
        "Shapiro\nQEMU": "qemu_shapiro_p",
        "Levene": "levene_p",
        "Global\ntest (p)": "teste_global_p",
        "FDR-\ncorrected p": "p_fdr_corrigido",
    }
    if "qemu_shapiro_p" not in idx.columns or idx["qemu_shapiro_p"].isna().all():
        colunas_p.pop("Shapiro\nQEMU", None)

    n_pressupostos = 4 if "Shapiro\nQEMU" in colunas_p else 3
    codes = pd.DataFrame({col: idx[src].apply(_bin_p_valor) for col, src in colunas_p.items()},
                         index=metricas)
    textos = pd.DataFrame({col: idx[src].apply(_formatar_p) for col, src in colunas_p.items()},
                          index=metricas)
    effect = idx[["effect_size_epsilon2"]].rename(columns={"effect_size_epsilon2": "Effect size\n(ε²)"})

    rotulos_linha = [_rotulo_curto(m) for m in metricas]
    n = len(metricas)
    # No title/text-legend embedded in the image: in a paper (caption via
    # LaTeX), a title and interpretive text duplicated inside the figure
    # are redundant/unusual.
    # Large font (11-12pt) because the figure tends to be included at a
    # column/page width smaller than its native size (e.g. 2-column ACM).
    fig = plt.figure(figsize=(8.5, 0.40 * n + 1.5))
    gs = fig.add_gridspec(1, 2, width_ratios=[6, 1.1], wspace=0.3)
    ax_p = fig.add_subplot(gs[0, 0])
    # No sharey on purpose: the two `sns.heatmap` calls below already get
    # the same number of rows (n) and the same figure height, so the
    # rows stay aligned regardless — sharing the Y axis here would make
    # the second `sns.heatmap` (called with yticklabels=False) overwrite/
    # hide the metric labels drawn by the first one.
    ax_e = fig.add_subplot(gs[0, 1])

    cmap_p = ListedColormap(_CORES_P)
    norm_p = BoundaryNorm([-0.5, 0.5, 1.5, 2.5, 3.5, 4.5], len(_CORES_P))
    sns.heatmap(codes, annot=textos, fmt="", cmap=cmap_p, norm=norm_p, cbar=False,
                linewidths=0.6, linecolor="white", ax=ax_p,
                yticklabels=rotulos_linha, annot_kws={"fontsize": 11})
    ax_p.set_ylabel("")
    ax_p.tick_params(axis="x", rotation=0, labelsize=11)
    ax_p.tick_params(axis="y", labelsize=11)

    # Visual divider + group labels between "assumption checks" and
    # "hypothesis test" (see docstring above — different natures of p-value).
    n_cols_p = len(colunas_p)
    ax_p.axvline(x=n_pressupostos, color="#333333", linewidth=1.8, clip_on=False)
    ax_p.text(n_pressupostos / 2, -0.55, "Assumption checks",
              ha="center", va="bottom", fontsize=11.5, fontweight="bold", clip_on=False)
    ax_p.text(n_pressupostos + (n_cols_p - n_pressupostos) / 2, -0.55, "Hypothesis test",
              ha="center", va="bottom", fontsize=11.5, fontweight="bold", clip_on=False)

    sns.heatmap(effect, annot=True, fmt=".3f", cmap="Blues", vmin=0,
                vmax=max(0.1, effect.iloc[:, 0].max(skipna=True)),
                mask=effect.isna(), cbar=True, cbar_kws={"label": "ε² (Kruskal-Wallis)"},
                linewidths=0.6, linecolor="white", ax=ax_e,
                yticklabels=False, annot_kws={"fontsize": 11})
    ax_e.set_ylabel("")
    ax_e.tick_params(axis="x", rotation=0, labelsize=11)
    ax_e.text(0.5, -0.55, "Magnitude",
              ha="center", va="bottom", fontsize=11.5, fontweight="bold", clip_on=False)

    legend_elems = [Patch(facecolor=c, edgecolor="#999999", label=l)
                    for c, l in zip(_CORES_P, _LEGENDA_P)]
    fig.legend(handles=legend_elems, loc="lower center", ncol=3,
               bbox_to_anchor=(0.5, -0.14), frameon=False, fontsize=10.5)

    nome_arquivo = f"significance_heatmap{sufixo}.png"
    fig.savefig(os.path.join(heat_dir, nome_arquivo), dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved: heatmaps/{nome_arquivo}")


def _heatmap_heterogeneidade(df_heterog: Optional[pd.DataFrame], heat_dir: str,
                              metricas_alvo: Optional[list] = None,
                              sufixo: str = "") -> None:
    """vm_heterogeneity_heatmap{sufixo}.png — metrics (row) x [KVM Het.,
    QEMU Het.] (column): tests whether the 3 source VMs of each treatment
    differ from each other (see checar_heterogeneidade_vms). Complements
    significance_heatmap{sufixo}.png, which is about host vs. KVM vs.
    QEMU, not about VM vs. VM within the same treatment. Here the two
    columns (KVM, QEMU) are of the same nature — the same test applied to
    two treatments — so, unlike significance_heatmap, there's no need to
    split into groups: a single x-axis label is already enough."""
    if df_heterog is None or df_heterog.empty:
        print(f"  [warning] heterogeneidade_entre_vms empty — vm_heterogeneity_heatmap{sufixo}.png not generated")
        return

    pivot_p = df_heterog.pivot(index="metrica", columns="ambiente", values="kruskal_p")
    pivot_het = df_heterog.pivot(index="metrica", columns="ambiente", values="heterogeneo")

    disponiveis = _ordem_metricas_presentes(pivot_p.index)
    metricas = ([m for m in disponiveis if m in set(metricas_alvo)]
                if metricas_alvo is not None else disponiveis)
    colunas = [c for c in ("kvm", "qemu") if c in pivot_p.columns]
    pivot_p = pivot_p.reindex(index=metricas, columns=colunas)
    pivot_het = pivot_het.reindex(index=metricas, columns=colunas)

    cores = ["#d9d9d9", "#2ca25f", "#d73027"]
    legenda = ["no data (<2 valid VMs)", "homogeneous (p≥0.05)",
               "heterogeneous (p<0.05)"]

    def _bin_het(v):
        if pd.isna(v):
            return 0
        return 2 if bool(v) else 1

    codes = pivot_het.map(_bin_het)
    textos = pivot_p.map(lambda p: "—" if pd.isna(p) else _formatar_p(p))
    rotulos_linha = [_rotulo_curto(m) for m in metricas]
    rotulos_coluna = ["KVM Het." if c == "kvm" else "QEMU Het." for c in colunas]

    n = len(metricas)
    # No embedded title (caption via LaTeX in the paper). Large font (13pt)
    # for the same reason as the significance heatmap: the image tends to
    # be included smaller than its native size.
    fig, ax = plt.subplots(figsize=(5.2, 0.45 * n + 1.3))
    cmap = ListedColormap(cores)
    norm = BoundaryNorm([-0.5, 0.5, 1.5, 2.5], len(cores))
    sns.heatmap(codes, annot=textos, fmt="", cmap=cmap, norm=norm, cbar=False,
                linewidths=0.6, linecolor="white", ax=ax,
                yticklabels=rotulos_linha, xticklabels=rotulos_coluna,
                annot_kws={"fontsize": 13})
    ax.set_ylabel("")
    ax.set_xlabel("")
    ax.tick_params(axis="x", rotation=0, labelsize=13)
    ax.tick_params(axis="y", labelsize=13)

    legend_elems = [Patch(facecolor=c, edgecolor="#999999", label=l) for c, l in zip(cores, legenda)]
    fig.legend(handles=legend_elems, loc="upper left", bbox_to_anchor=(1.0, 0.9),
               bbox_transform=fig.transFigure, frameon=False, fontsize=11)

    nome_arquivo = f"vm_heterogeneity_heatmap{sufixo}.png"
    fig.savefig(os.path.join(heat_dir, nome_arquivo), dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved: heatmaps/{nome_arquivo}")


def _plot_ic_painel(df_stats: pd.DataFrame, metricas: list, out_path: str,
                     ncol: int = 3, apenas_host_kvm: bool = False) -> None:
    """Small-multiples panel with mean +- 95% CI, one subplot per metric,
    host/KVM/QEMU as categories on the X axis. Reuses ROTULOS and
    COR_AMBIENTE already defined in the script. Metrics without QEMU
    (STREAM, GPU) simply don't draw that point.

    apenas_host_kvm=True omits the QEMU point: its overhead forces the Y
    axis to cover such a large range that the more subtle host-vs-KVM
    difference becomes illegible."""
    disponiveis = [m for m in metricas if m in df_stats["metrica"].values]
    if not disponiveis:
        print(f"  [warning] no metric available for {out_path}")
        return
    idx = df_stats.set_index("metrica")
    n = len(disponiveis)
    nrow = -(-n // ncol)
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.2 * ncol, 3.6 * nrow))
    axes = axes.flatten() if n > 1 else [axes]

    for ax, metrica in zip(axes, disponiveis):
        rotulo, unidade = ROTULOS.get(metrica, (metrica, ""))
        row = idx.loc[metrica]
        ambientes_presentes = [a for a in ORDEM_AMBIENTES if pd.notna(row.get(f"{a}_media"))]
        if apenas_host_kvm:
            ambientes_presentes = [a for a in ambientes_presentes if a != "qemu"]
        for i, amb in enumerate(ambientes_presentes):
            media = row[f"{amb}_media"]
            lo = row[f"{amb}_ic95_lo"]
            hi = row[f"{amb}_ic95_hi"]
            ax.errorbar([i], [media], yerr=[[media - lo], [hi - media]], fmt='o',
                        color=COR_AMBIENTE[amb], markersize=9, capsize=6,
                        capthick=1.8, elinewidth=1.8, zorder=3)
        ax.set_xticks(range(len(ambientes_presentes)))
        ax.set_xticklabels(["Host" if a == "host" else a.upper() for a in ambientes_presentes],
                            fontsize=10)
        ax.set_xlim(-0.5, len(ambientes_presentes) - 0.5)
        ax.set_title(rotulo, fontsize=10, fontweight="bold")
        ax.set_ylabel(unidade, fontsize=9)
        ax.grid(axis="y", alpha=0.25, linewidth=0.5)
        ax.spines[["top", "right"]].set_visible(False)

    for ax in axes[n:]:
        ax.axis("off")

    titulo = "Mean with 95% confidence interval (bootstrap)"
    if apenas_host_kvm:
        titulo += " — Host vs. KVM zoom (no QEMU)"
    fig.suptitle(titulo, fontsize=13, fontweight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(out_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved: {out_path}")


def gerar_paineis_ic(df_stats: pd.DataFrame, out_dir: str) -> None:
    """Generates four 95% CI panels: curated x full, each at full scale
    (3 environments) and zoomed (host/KVM only)."""
    ic_dir = os.path.join(out_dir, "confidence_intervals")
    os.makedirs(ic_dir, exist_ok=True)
    todas_metricas = _ordem_metricas_presentes(df_stats["metrica"])
    _plot_ic_painel(df_stats, METRICAS_PRINCIPAIS, os.path.join(ic_dir, "ci95_curated.png"), ncol=3)
    _plot_ic_painel(df_stats, todas_metricas, os.path.join(ic_dir, "ci95_full.png"), ncol=4)
    _plot_ic_painel(df_stats, METRICAS_PRINCIPAIS, os.path.join(ic_dir, "ci95_curated_2env.png"),
                     ncol=3, apenas_host_kvm=True)
    _plot_ic_painel(df_stats, todas_metricas, os.path.join(ic_dir, "ci95_full_2env.png"),
                     ncol=4, apenas_host_kvm=True)


def gerar_heatmap_resumo(df_stats, df_heterog, out_dir: str) -> None:
    """Generates six heatmap figures in out_dir/heatmaps/. The 3-env vs
    2-env split keeps QEMU's overhead from dominating the Kruskal-Wallis
    test and hiding the more subtle KVM effects."""
    heat_dir = os.path.join(out_dir, "heatmaps")
    os.makedirs(heat_dir, exist_ok=True)

    _heatmap_significancia(df_stats, heat_dir, metricas_alvo=None, sufixo="_full")
    _heatmap_significancia(df_stats, heat_dir, metricas_alvo=METRICAS_3_AMBIENTES, sufixo="_3env")
    _heatmap_significancia(df_stats, heat_dir, metricas_alvo=METRICAS_2_AMBIENTES, sufixo="_2env")
    _heatmap_significancia(df_stats, heat_dir, metricas_alvo=METRICAS_PRINCIPAIS, sufixo="")

    _heatmap_heterogeneidade(df_heterog, heat_dir, metricas_alvo=None, sufixo="_full")
    _heatmap_heterogeneidade(df_heterog, heat_dir, metricas_alvo=METRICAS_PRINCIPAIS, sufixo="")



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs-dir", required=True,
                     help="Folder containing host/, kvm-<vm>/, qemu-<vm>/ (e.g. kvm-gabrielly, kvm-lucas, kvm-ramalho, qemu-gabrielly, ...)")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    os.makedirs(os.path.join(args.out_dir, "plots"), exist_ok=True)

    all_rows = []
    avisos_parsing = []
    subpastas = sorted(d for d in os.listdir(args.logs_dir)
                        if os.path.isdir(os.path.join(args.logs_dir, d)))

    for pasta in subpastas:
        full_pasta = os.path.join(args.logs_dir, pasta)
        arquivos = glob.glob(os.path.join(full_pasta, "*.txt"))
        if not arquivos:
            continue

        if pasta == "host":
            ambiente, vm_id = "host", "host"
        elif pasta.startswith("kvm-"):
            ambiente, vm_id = "kvm", pasta[len("kvm-"):]
        elif pasta.startswith("qemu-"):
            ambiente, vm_id = "qemu", pasta[len("qemu-"):]
        else:
            print(f"[warning] folder '{pasta}' not recognized (expected: host, kvm-<vm>, qemu-<vm>) — skipping")
            continue

        print(f"[{ambiente}/{vm_id}] {len(arquivos)} files in {full_pasta}")
        for f in arquivos:
            all_rows.extend(parse_file(f, ambiente, vm_id, avisos=avisos_parsing))

    df = pd.DataFrame(all_rows)
    if df.empty:
        print("ERROR: no data extracted. Check the paths and the parsing regexes.")
        sys.exit(1)

    df_avisos = pd.DataFrame(
        avisos_parsing, columns=["arquivo", "ambiente", "vm_id", "metrica_esperada", "motivo"]
    )
    df_avisos.to_csv(os.path.join(args.out_dir, "parsing_warnings.csv"), index=False)
    if not df_avisos.empty:
        print(f"\n[WARNING] {len(df_avisos)} case(s) of an expected metric not found in some "
              f"file — see {os.path.join(args.out_dir, 'parsing_warnings.csv')} "
              f"(explains any n differences between metrics/environments, e.g. n=14 vs n=15).")

    df.to_csv(os.path.join(args.out_dir, "extracted_data.csv"), index=False)
    print(f"\nExtracted {len(df)} values from {df['arquivo'].nunique()} files.")
    print(df.groupby(["ambiente", "vm_id"])["arquivo"].nunique().to_string())

    # VM heterogeneity — computed BEFORE the plots, because the decision
    # to color points by VM in each subplot depends on this result (we
    # only use per-VM color where the difference between VMs is real).
    df_heterog = checar_heterogeneidade_vms(df)
    df_heterog.to_csv(os.path.join(args.out_dir, "vm_heterogeneity.csv"), index=False)
    n_heterog = df_heterog["heterogeneo"].sum() if not df_heterog.empty else 0
    print(f"\nVM heterogeneity check: {n_heterog}/{len(df_heterog)} "
          f"metric x treatment combinations show a significant difference between VMs (p<0.05)")

    gerar_graficos(df, args.out_dir, df_heterog=df_heterog)
    print(f"Plots saved to {os.path.join(args.out_dir, 'plots')}/")

    # Descriptive stats
    desc = df.groupby(["metrica", "ambiente"])["valor"].agg(
        n="count", media="mean", mediana="median", desvio="std",
        q1=lambda s: s.quantile(0.25), q3=lambda s: s.quantile(0.75)
    )
    desc["iqr"] = desc["q3"] - desc["q1"]
    desc.to_csv(os.path.join(args.out_dir, "descriptive_summary.csv"))

    # Statistical pipeline per metric (environment vs environment, pooling the 3 VMs)
    linhas_stats = []
    for metrica, sub in df.groupby("metrica"):
        if sub["ambiente"].nunique() < 2:
            continue
        res = analisar_metrica(sub[["ambiente", "vm_id", "valor"]], metrica_nome=metrica)
        res["metrica"] = metrica
        linhas_stats.append(res)

    df_stats = pd.DataFrame(linhas_stats)

    # FDR (Benjamini-Hochberg) across ALL metrics simultaneously — distinct
    # from the Dunn post-hoc (Bonferroni), which corrects comparisons
    # within a metric, not across metrics.
    df_stats = aplicar_correcao_fdr(df_stats)

    # Sanity check: conceptually distinct metrics with an EXACTLY
    # identical global test statistic.
    df_alerta_dup = checar_estatisticas_duplicadas(df_stats)
    df_alerta_dup.to_csv(os.path.join(args.out_dir, "duplicate_statistics_alert.csv"), index=False)

    df_stats.to_csv(os.path.join(args.out_dir, "statistical_results.csv"), index=False)

    # Summary heatmaps of the formal tests (complement the boxplots from
    # gerar_graficos — see the gerar_heatmap_resumo docstring).
    gerar_heatmap_resumo(df_stats, df_heterog, args.out_dir)

    gerar_paineis_ic(df_stats, args.out_dir)

    # Run metadata (git commit, timestamp, seed) for audit/reproducibility
    metadata = obter_metadata_execucao(args)
    with open(os.path.join(args.out_dir, "metadata.json"), "w") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    # Human-readable report
    with open(os.path.join(args.out_dir, "report.txt"), "w") as f:
        f.write("STATISTICAL ANALYSIS REPORT — ppc64le BENCHMARKS\n")
        f.write("=" * 70 + "\n\n")
        f.write(
            "NOTE on duplicate_statistics_alert.csv: this file lists pairs of\n"
            "metrics whose global statistical test produced an exactly identical\n"
            "result. This can be benign (equally-sized groups with complete\n"
            "separation between environments produce the same Kruskal-Wallis H by\n"
            "mathematical construction, regardless of the raw values) or point to a\n"
            "computation bug — review manually before accepting any new case that\n"
            "shows up here. It's generated automatically on every run of this\n"
            "pipeline, not a one-off/manual check.\n\n"
            "NOTE on \"(caution: pseudo-replication)\" below: see\n"
            "docs/METHODOLOGICAL_GLOSSARY.md for what that warning means and why it's\n"
            "an expected consequence of the nested design (3 VMs x 5 runs), not an error.\n"
        )
        f.write("-" * 70 + "\n\n")
        for _, row in df_stats.iterrows():
            f.write(f"### {row['metrica']} ###\n")
            for amb in ("host", "kvm", "qemu"):
                if f"{amb}_media" in row and pd.notna(row.get(f"{amb}_media")):
                    f.write(
                        f"  {amb}: n={int(row.get(f'{amb}_n', 0))}, "
                        f"mean={row.get(f'{amb}_media'):.3f}, "
                        f"median={row.get(f'{amb}_mediana'):.3f}, "
                        f"95% CI=[{row.get(f'{amb}_ic95_lo'):.3f}, {row.get(f'{amb}_ic95_hi'):.3f}], "
                        f"Shapiro p={row.get(f'{amb}_shapiro_p', float('nan')):.4f}\n"
                    )
            f.write(f"  Levene p={row.get('levene_p', float('nan')):.4f}\n")
            f.write(f"  Global test: {row.get('teste_global', '-')}, "
                    f"statistic={row.get('teste_global_estat', float('nan')):.4f}, "
                    f"p={row.get('teste_global_p', float('nan')):.4g}\n")
            if pd.notna(row.get("p_fdr_corrigido")):
                f.write(f"  Corrected p (FDR/Benjamini-Hochberg, across all metrics): "
                        f"{row.get('p_fdr_corrigido'):.4g}\n")
            if pd.notna(row.get("effect_size_epsilon2")):
                f.write(f"  Effect size (epsilon^2): {row.get('effect_size_epsilon2'):.4f}\n")
            het_row = df_heterog[df_heterog["metrica"] == row["metrica"]]
            for _, hr in het_row.iterrows():
                flag = "YES (caution: pseudo-replication)" if hr["heterogeneo"] else "no"
                f.write(f"  Heterogeneity between VMs ({hr['ambiente']}): "
                        f"H={hr['kruskal_h']:.3f}, p={hr['kruskal_p']:.4f} -> differ from each other? {flag}\n")
            f.write("\n")

    print(f"\nDone. Outputs in: {args.out_dir}")
    print("  - extracted_data.csv")
    print("  - descriptive_summary.csv")
    print("  - statistical_results.csv (with p_fdr_corrigido)")
    print("  - vm_heterogeneity.csv")
    print("  - parsing_warnings.csv")
    print("  - duplicate_statistics_alert.csv")
    print("  - metadata.json")
    print("  - report.txt")


if __name__ == "__main__":
    main()