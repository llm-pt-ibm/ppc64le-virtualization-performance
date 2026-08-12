#!/usr/bin/env python3
"""
benchmark_summary_qemu.py

Dedicated version for benchmark-qemu.sh logs (plain QEMU / TCG), which
doesn't run any GPU test. Unlike benchmark_summary_overhead.py (which
just ignores missing GPU metrics), this script doesn't even try to look
for them — it's simpler and works as a quick, QEMU-specific check for
this run.

Covers: CPU, memory, cache, STREAM and the four fio disk tests
(seq. read, seq. write, random IOPS, latency).

--output-csv saves the summary to CSV ready to be consumed by plotting
scripts (R, pandas), without re-parsing text.

Usage:
  python3 benchmark_summary_qemu.py
      (uses the default file pattern: benchmark_qemu_*.txt in the current directory)

  python3 benchmark_summary_qemu.py --pattern "qemu_results/benchmark_qemu_*.txt"

  # With overhead relative to host + CSV:
  python3 benchmark_summary_qemu.py \
      --pattern "qemu_results/benchmark_qemu_*.txt" \
      --baseline "host_results/benchmark_host_*.txt" \
      --output-csv data/processed/qemu_summary.csv
"""
import re
import glob
import csv
import argparse
import statistics as st


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
}

# Consistent with benchmark_summary_overhead.py: latency is the only
# metric where "lower is better" — only used to flag this in the CSV,
# doesn't change the computation.
LOWER_IS_BETTER = {"lat_clat_avg_us"}

DEFAULT_PATTERN = "benchmark_qemu_*.txt"
DISK_LABEL = "DISK_EXT4"


def parse_iops(value):
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

    return result


def load_metrics(pattern, disk_label="DISK_EXT4"):
    metrics = {key: [] for key in METRIC_LABELS}
    files = sorted(glob.glob(pattern))
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
    return {"n": n, "media": media, "mediana": mediana, "desvio": desvio, "iqr": iqr}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pattern", default=DEFAULT_PATTERN,
                         help=f"Glob for the QEMU logs (default: {DEFAULT_PATTERN})")
    parser.add_argument("--baseline", default=None,
                         help="Glob for the host logs, to compute overhead (optional)")
    parser.add_argument("--disk-label", default=DISK_LABEL,
                         help=f"Disk-phase label in the QEMU logs (default: {DISK_LABEL})")
    parser.add_argument("--baseline-disk-label", default="DISK_HOST",
                         help='Disk-phase label in the --baseline/host logs (default: DISK_HOST, since benchmark-host.sh uses that label)')
    parser.add_argument("--output-csv", default=None,
                         help="If given, saves the summary to CSV for consumption by plotting scripts.")
    args = parser.parse_args()

    metrics, files = load_metrics(args.pattern, disk_label=args.disk_label)
    if not files:
        print(f"No files found for pattern: {args.pattern}")
        return

    baseline_metrics, baseline_files = ({}, [])
    if args.baseline:
        baseline_metrics, baseline_files = load_metrics(
            args.baseline, disk_label=args.baseline_disk_label
        )
        if not baseline_files:
            print(f"WARNING: no baseline files found for: {args.baseline}")
            print("Continuing without overhead calculation.\n")

    print("\n================ SUMMARY — PLAIN QEMU (TCG) ================\n")
    print(f"Files analyzed (QEMU): {len(files)}")
    if baseline_files:
        print(f"Files analyzed (baseline/host): {len(baseline_files)}")
    print("Note: this script doesn't process GPU metrics — benchmark-qemu.sh doesn't include those tests.\n")

    header = f"{'Metric':28s} {'n':>3s} {'mean':>14s} {'median':>14s} {'std dev':>12s} {'IQR':>12s}"
    if baseline_files:
        header += f" {'overhead %':>12s}"
    print(header)
    print("-" * len(header))

    csv_rows = []
    for key, label in METRIC_LABELS.items():
        values = metrics.get(key, [])
        stats = summarize(values)
        if stats is None:
            continue

        line = (
            f"{label:28s} {stats['n']:>3d} "
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
            "ambiente":     "QEMU",
            "metrica_id":   key,
            "metrica":      label,
            "n":            stats["n"],
            "media":        round(stats["media"], 4),
            "mediana":      round(stats["mediana"], 4),
            "desvio":       round(stats["desvio"], 4),
            "iqr":          round(stats["iqr"], 4) if stats["iqr"] == stats["iqr"] else "",
            "overhead_pct": round(overhead, 4) if overhead is not None else "",
            "lower_is_better": "1" if key in LOWER_IS_BETTER else "0",
        })

    print()
    if baseline_files:
        print("Overhead here is expected to be MUCH higher than on KVM, since QEMU in TCG mode")
        print("has no hardware acceleration — it's pure software instruction translation.")
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
