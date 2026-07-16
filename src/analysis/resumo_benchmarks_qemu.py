#!/usr/bin/env python3
"""
resumo_benchmarks_qemu.py

Versão dedicada para os logs do benchmark-qemu.sh (QEMU puro / TCG), que
não roda nenhum teste de GPU. Diferente do resumo_benchmarks_overhead.py
(que apenas ignora métricas de GPU ausentes), este script nem tenta
procurá-las — é mais simples e serve como conferência rápida específica
para essa rodada de QEMU.

Cobre: CPU, memória, cache, STREAM e os quatro testes de disco do fio
(leitura seq., escrita seq., IOPS aleatório, latência).

Uso:
  python3 resumo_benchmarks_qemu.py
      (usa o padrão de arquivo padrão: benchmark_qemu_*.txt no diretório atual)

  python3 resumo_benchmarks_qemu.py --pattern "qemu_results/benchmark_qemu_*.txt"

  # Com overhead em relação ao host:
  python3 resumo_benchmarks_qemu.py \
      --pattern "qemu_results/benchmark_qemu_*.txt" \
      --baseline "host_results/benchmark_host_*.txt"
"""
import re
import glob
import argparse
import statistics as st


METRIC_LABELS = {
    "cpu_bogo":         "CPU (bogo ops/s)",
    "mem_bogo":         "Memória (bogo ops/s)",
    "cache_bogo":       "Cache (bogo ops/s)",
    "stream_copy":      "STREAM Copy (MB/s)",
    "stream_scale":     "STREAM Scale (MB/s)",
    "stream_add":       "STREAM Add (MB/s)",
    "stream_triad":     "STREAM Triad (MB/s)",
    "seq_read_bw":      "Leitura seq. (MiB/s)",
    "seq_write_bw":     "Escrita seq. (MiB/s)",
    "rand_read_iops":   "IOPS aleatório leitura",
    "rand_write_iops":  "IOPS aleatório escrita",
    "lat_read_iops":    "IOPS latência (read)",
    "lat_clat_avg_us":  "Latência média (us)",
}

DEFAULT_PATTERN = "benchmark_qemu_*.txt"
DISK_LABEL = "DISCO_EXT4"  # rótulo padrão usado pelo benchmark-qemu.sh (confirmado igual ao benchmark-vm.sh)


def parse_iops(value):
    value = value.lower().replace(",", "")
    if value.endswith("k"):
        return float(value[:-1]) * 1000
    return float(value)


def extract_metrics(txt, disk_label="DISCO_EXT4"):
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

    m = re.search(
        rf'=== \[{disk_label}\] LEITURA SEQUENCIAL ===.*?read: IOPS=.*?BW=([\d.]+)MiB/s',
        txt, re.S,
    )
    if m:
        result["seq_read_bw"] = float(m.group(1))

    m = re.search(
        rf'=== \[{disk_label}\] ESCRITA SEQUENCIAL ===.*?write: IOPS=.*?BW=([\d.]+)MiB/s',
        txt, re.S,
    )
    if m:
        result["seq_write_bw"] = float(m.group(1))

    m = re.search(
        rf'=== \[{disk_label}\] IOPS ALEATÓRIO.*?read: IOPS=([0-9.k]+)',
        txt, re.S,
    )
    if m:
        result["rand_read_iops"] = parse_iops(m.group(1))

    m = re.search(
        rf'=== \[{disk_label}\] IOPS ALEATÓRIO.*?write: IOPS=([0-9.k]+)',
        txt, re.S,
    )
    if m:
        result["rand_write_iops"] = parse_iops(m.group(1))

    m = re.search(
        rf'=== \[{disk_label}\] LATÊNCIA.*?read: IOPS=([\d.]+)',
        txt, re.S,
    )
    if m:
        result["lat_read_iops"] = float(m.group(1))

    m = re.search(
        rf'=== \[{disk_label}\] LATÊNCIA.*?clat .*?avg=([\d.]+)',
        txt, re.S,
    )
    if m:
        result["lat_clat_avg_us"] = float(m.group(1))

    return result


def load_metrics(pattern, disk_label="DISCO_EXT4"):
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
                         help=f"Glob dos logs do QEMU (padrão: {DEFAULT_PATTERN})")
    parser.add_argument("--baseline", default=None,
                         help="Glob dos logs do host, para calcular overhead (opcional)")
    parser.add_argument("--disk-label", default=DISK_LABEL,
                         help=f"Rótulo de fase de disco nos logs do QEMU (padrão: {DISK_LABEL})")
    parser.add_argument("--baseline-disk-label", default="DISCO_HOST",
                         help='Rótulo de fase de disco nos logs do --baseline/host (padrão: DISCO_HOST, já que o benchmark-host.sh usa esse rótulo)')
    args = parser.parse_args()

    metrics, files = load_metrics(args.pattern, disk_label=args.disk_label)
    if not files:
        print(f"Nenhum arquivo encontrado para o padrão: {args.pattern}")
        return

    baseline_metrics, baseline_files = ({}, [])
    if args.baseline:
        baseline_metrics, baseline_files = load_metrics(
            args.baseline, disk_label=args.baseline_disk_label
        )
        if not baseline_files:
            print(f"AVISO: nenhum arquivo de baseline encontrado para: {args.baseline}")
            print("Seguindo sem cálculo de overhead.\n")

    print("\n================ RESUMO — QEMU PURO (TCG) ================\n")
    print(f"Arquivos analisados (QEMU): {len(files)}")
    if baseline_files:
        print(f"Arquivos analisados (baseline/host): {len(baseline_files)}")
    print("Nota: este script não processa métricas de GPU — benchmark-qemu.sh não inclui esses testes.\n")

    header = f"{'Métrica':28s} {'n':>3s} {'média':>14s} {'mediana':>14s} {'desvio':>12s} {'IQR':>12s}"
    if baseline_files:
        header += f" {'overhead %':>12s}"
    print(header)
    print("-" * len(header))

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

        if baseline_files:
            base_values = baseline_metrics.get(key, [])
            base_stats = summarize(base_values)
            if base_stats and base_stats["media"] != 0:
                overhead = (stats["media"] / base_stats["media"] - 1) * 100
                line += f" {overhead:>11.2f}%"
            else:
                line += f" {'sem baseline':>12s}"

        print(line)

    print()
    if baseline_files:
        print("Overhead esperado MUITO maior aqui do que no KVM, já que QEMU em modo TCG")
        print("não tem aceleração de hardware — é tradução de instrução pura em software.")
    print()


if __name__ == "__main__":
    main()
