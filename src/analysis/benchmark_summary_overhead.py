#!/usr/bin/env python3
"""
benchmark_summary_overhead.py

Evolution of the original resumo_benchmarks.py. Still extracts the same
metrics (CPU, memory, cache, STREAM, fio, matrixMul, NCCL) from a set of
log files, but now:

  1. Accepts any file-name pattern via --pattern (host, vm, qemu...)
  2. Computes not just the mean, but also median, standard deviation and IQR
  3. If you pass --baseline with the host files, computes the percentage
     overhead of each metric relative to the baseline:
         overhead(%) = (environment_mean / host_mean - 1) * 100
     (same formula as Section 2.6 of the paper)
  4. --output-csv saves the summary to CSV, ready to be consumed by
     plotting scripts (R, pandas, etc.) without needing to re-parse text.

Usage:
  # Comparing KVM against the host (overhead) — prints + saves CSV
    python3 benchmark_summary_overhead.py \
  --pattern "kvm-results/benchmark_vm_localhost.localdomain_*" \
  --baseline "host/benchmark_host_ibm-power9_20260724_0*" \
  --baseline-disk-label "DISK_HOST" \
  --label "KVM" \
  --output-csv kvm_summary.csv

  # Simple summary of one environment (no overhead)
    python3 benchmark_summary_overhead.py \
  --pattern "host/benchmark_host_ibm-power9_20260724_0*" \
  --disk-label "DISK_HOST" \
  --label "Host" \
  --output-csv host_summary.csv
"""
import re
import glob
import csv
import argparse
import statistics as st


# ---------------------------------------------------------------------------
# Same metrics as the original script — nothing removed, just reorganized.
# ---------------------------------------------------------------------------
METRIC_LABELS = {
    "cpu_bogo":         "CPU (bogo ops/s)",
    "mem_bogo":         "Memory (bogo ops/s)",
    "cache_bogo":       "Cache (bogo ops/s)",
    "stream_copy":      "STREAM Copy (MB/s)",
    "stream_scale":     "STREAM Scale (MB/s)",
    "stream_add":       "STREAM Add (MB/s)",
    "stream_triad":     "STREAM Triad (MB/s)",
    "seq_read_bw":      "Seq. read (MiB/s)",
    "seq_write_bw":     "Seq. write (MiB/s)",
    "rand_read_iops":   "Random read IOPS",
    "rand_write_iops":  "Random write IOPS",
    "lat_read_iops":    "Latency IOPS (read)",
    "lat_clat_avg_us":  "Average latency (us)",
    "matrix_gflops":    "matrixMul (GFlop/s)",
    "nccl_allreduce":   "NCCL all_reduce busbw (GB/s)",
    "nccl_allgather":   "NCCL all_gather busbw (GB/s)",
    "nccl_sendrecv":    "NCCL sendrecv busbw (GB/s)",
}

# For all metrics, "higher is better" except latency, where "lower is
# better". This only affects how we interpret the overhead's sign when
# printing a note — it doesn't affect the computation.
LOWER_IS_BETTER = {"lat_clat_avg_us"}


def parse_iops(value):
    """Converts strings like '22.1k' or '1325967' to float."""
    value = value.lower().replace(",", "")
    if value.endswith("k"):
        return float(value[:-1]) * 1000
    return float(value)


def extract_metrics(txt, disk_label="DISK_EXT4"):
    result = {}

    m = re.search(r'cpu\s+\d+\s+\S+\s+\S+\s+\S+\s+([\d.]+)', txt)
    if m:
        result["cpu_bogo"] = float(m.group(1))

    m = re.search(r'vm\s+\d+\s+\S+\s+\S+\s+\S+\s+([\d.]+)', txt)
    if m:
        result["mem_bogo"] = float(m.group(1))

    m = re.search(r'cache\s+\d+\s+\S+\s+\S+\s+\S+\s+([\d.]+)', txt)
    if m:
        result["cache_bogo"] = float(m.group(1))

    for key, label in [
        ("stream_copy", "Copy"),
        ("stream_scale", "Scale"),
        ("stream_add", "Add"),
        ("stream_triad", "Triad"),
    ]:
        m = re.search(rf'{label}:\s+([\d.]+)', txt)
        if m:
            result[key] = float(m.group(1))

    # Section headers below match both the Portuguese wording used by the
    # collection scripts before their English rewrite and the current
    # English wording, so this still parses older logs correctly.
    m = re.search(
        rf'=== \[{disk_label}\] (?:LEITURA SEQUENCIAL|SEQUENTIAL READ) ===.*?read: IOPS=.*?BW=([\d.]+)MiB/s',
        txt, re.S,
    )
    if m:
        result["seq_read_bw"] = float(m.group(1))

    m = re.search(
        rf'=== \[{disk_label}\] (?:ESCRITA SEQUENCIAL|SEQUENTIAL WRITE) ===.*?write: IOPS=.*?BW=([\d.]+)MiB/s',
        txt, re.S,
    )
    if m:
        result["seq_write_bw"] = float(m.group(1))

    m = re.search(
        rf'=== \[{disk_label}\] (?:IOPS ALEATÓRIO|RANDOM IOPS).*?read: IOPS=([0-9.k]+)',
        txt, re.S,
    )
    if m:
        result["rand_read_iops"] = parse_iops(m.group(1))

    m = re.search(
        rf'=== \[{disk_label}\] (?:IOPS ALEATÓRIO|RANDOM IOPS).*?write: IOPS=([0-9.k]+)',
        txt, re.S,
    )
    if m:
        result["rand_write_iops"] = parse_iops(m.group(1))

    m = re.search(
        rf'=== \[{disk_label}\] (?:LAT[ÊE]NCIA|LATENCY).*?read: IOPS=([\d.]+)',
        txt, re.S,
    )
    if m:
        result["lat_read_iops"] = float(m.group(1))

    m = re.search(
        rf'=== \[{disk_label}\] (?:LAT[ÊE]NCIA|LATENCY).*?clat .*?avg=([\d.]+)',
        txt, re.S,
    )
    if m:
        result["lat_clat_avg_us"] = float(m.group(1))

    vals = re.findall(r'Performance=\s*([\d.]+)\s*GFlop/s', txt)
    if vals:
        result["matrix_gflops"] = st.mean(float(v) for v in vals)

    for key, section in [
        ("nccl_allreduce", "ALL_REDUCE"),
        ("nccl_allgather", "ALL_GATHER"),
        ("nccl_sendrecv", "SENDRECV"),
    ]:
        vals = re.findall(
            rf'{section}.*?# Avg bus bandwidth\s+:\s+([\d.]+)',
            txt, re.S,
        )
        if vals:
            result[key] = float(vals[0])

    return result


def load_metrics(pattern, disk_label="DISK_EXT4"):
    metrics = {key: [] for key in METRIC_LABELS}
    files = sorted(glob.glob(pattern))
    if not files:
        return metrics, files

    for fname in files:
        with open(fname, "r", errors="ignore") as f:
            txt = f.read()
        parsed = extract_metrics(txt, disk_label=disk_label)
        for key, value in parsed.items():
            metrics[key].append(value)

    return metrics, files


def summarize(values):
    n = len(values)
    if n == 0:
        return None
    media = st.mean(values)
    mediana = st.median(values)
    desvio = st.stdev(values) if n > 1 else 0.0
    if n >= 4:
        quartis = st.quantiles(values, n=4, method="inclusive")
        iqr = quartis[2] - quartis[0]
    else:
        iqr = float("nan")
    return {
        "n": n,
        "media": media,
        "mediana": mediana,
        "desvio": desvio,
        "iqr": iqr,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pattern", required=True)
    parser.add_argument("--baseline", default=None)
    parser.add_argument("--disk-label", default="DISK_EXT4")
    parser.add_argument("--baseline-disk-label", default=None)
    parser.add_argument("--label", default="Environment")
    parser.add_argument(
        "--output-csv", default=None,
        help="If given, saves the summary to CSV (one row per metric) for consumption by plotting scripts.",
    )
    args = parser.parse_args()

    metrics, files = load_metrics(args.pattern, disk_label=args.disk_label)
    if not files:
        print(f"No files found for pattern: {args.pattern}")
        return

    baseline_metrics, baseline_files = ({}, [])
    if args.baseline:
        baseline_disk_label = args.baseline_disk_label or args.disk_label
        baseline_metrics, baseline_files = load_metrics(
            args.baseline, disk_label=baseline_disk_label
        )
        if not baseline_files:
            print(f"WARNING: no baseline files found for: {args.baseline}")
            print("Continuing without overhead calculation.\n")

    print(f"\n================ SUMMARY — {args.label} ================\n")
    print(f"Files analyzed ({args.label}): {len(files)}")
    if baseline_files:
        print(f"Files analyzed (baseline/host): {len(baseline_files)}")
    print()

    header = f"{'Metric':32s} {'n':>3s} {'mean':>14s} {'median':>14s} {'std dev':>12s} {'IQR':>12s}"
    if baseline_files:
        header += f" {'overhead %':>12s}"
    print(header)
    print("-" * len(header))

    # Collect rows to both print and (if requested) write to CSV
    csv_rows = []
    for key, label in METRIC_LABELS.items():
        values = metrics.get(key, [])
        stats = summarize(values)
        if stats is None:
            continue

        line = (
            f"{label:32s} {stats['n']:>3d} "
            f"{stats['media']:>14.2f} {stats['mediana']:>14.2f} "
            f"{stats['desvio']:>12.2f} {stats['iqr']:>12.2f}"
        )

        overhead = None
        if baseline_files:
            base_values = baseline_metrics.get(key, [])
            base_stats = summarize(base_values)
            if base_stats and base_stats["media"] != 0:
                overhead = (stats["media"] / base_stats["media"] - 1) * 100
                line += f" {overhead:>11.2f}%"
            else:
                line += f" {'no baseline':>12s}"

        print(line)

        csv_rows.append({
            "ambiente":   args.label,
            "metrica_id": key,
            "metrica":    label,
            "n":          stats["n"],
            "media":      round(stats["media"], 4),
            "mediana":    round(stats["mediana"], 4),
            "desvio":     round(stats["desvio"], 4),
            "iqr":        round(stats["iqr"], 4) if stats["iqr"] == stats["iqr"] else "",  # NaN check
            "overhead_pct": round(overhead, 4) if overhead is not None else "",
            "lower_is_better": "1" if key in LOWER_IS_BETTER else "0",
        })

    print()
    if baseline_files:
        print("Overhead = (environment_mean / host_mean - 1) x 100, per Section 2.6 of the paper.")
        print("Latency: positive overhead = slower environment (expected).")
        print("Others: positive overhead = environment with HIGHER throughput than host — check whether that's physically plausible.")
    print()

    if args.output_csv:
        fieldnames = [
            "ambiente", "metrica_id", "metrica", "n",
            "media", "mediana", "desvio", "iqr",
            "overhead_pct", "lower_is_better",
        ]
        with open(args.output_csv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(csv_rows)
        print(f"CSV saved to: {args.output_csv}")


if __name__ == "__main__":
    main()
