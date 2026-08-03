#!/usr/bin/env python3
"""
resumo_benchmarks_overhead.py

Evolução do resumo_benchmarks.py original. Continua extraindo as mesmas
métricas (CPU, memória, cache, STREAM, fio, matrixMul, NCCL) de um conjunto
de arquivos de log, mas agora:

  1. Aceita qualquer padrão de nome de arquivo via --pattern (host, vm, qemu...)
  2. Calcula não só a média, mas também mediana, desvio padrão e IQR
  3. Se você passar --baseline com os arquivos do host, calcula o overhead
     percentual de cada métrica em relação ao baseline:
         overhead(%) = (média_ambiente / média_host - 1) * 100
     (mesma fórmula da Seção 2.6 do artigo)
  4. --output-csv salva o resumo em CSV, pronto pra ser consumido por
     scripts de plotagem (R, pandas, etc.) sem precisar re-parsear texto.

Uso:
  # Comparando KVM contra o host (overhead) — imprime + salva CSV
    python3 resumo_benchmarks_overhead.py \
  --pattern "kvm-results/benchmark_vm_localhost.localdomain_*" \
  --baseline "host/benchmark_host_ibm-power9_20260724_0*" \
  --baseline-disk-label "DISCO_HOST" \
  --label "KVM" \
  --output-csv resumo_kvm.csv

  # Resumo simples de um ambiente (sem overhead)
    python3 resumo_benchmarks_overhead.py \
  --pattern "host/benchmark_host_ibm-power9_20260724_0*" \
  --disk-label "DISCO_HOST" \
  --label "Host" \
  --output-csv resumo_host.csv
"""
import re
import glob
import csv
import argparse
import statistics as st


# ---------------------------------------------------------------------------
# Mesmas métricas do script original — nada removido, só reorganizado.
# ---------------------------------------------------------------------------
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
    "matrix_gflops":    "matrixMul (GFlop/s)",
    "nccl_allreduce":   "NCCL all_reduce busbw (GB/s)",
    "nccl_allgather":   "NCCL all_gather busbw (GB/s)",
    "nccl_sendrecv":    "NCCL sendrecv busbw (GB/s)",
}

# Para todas as métricas, "maior é melhor" exceto latência, onde "menor é
# melhor". Isso só afeta como interpretamos o sinal do overhead na hora de
# imprimir uma nota — não afeta o cálculo.
LOWER_IS_BETTER = {"lat_clat_avg_us"}


def parse_iops(value):
    """Converte strings como '22.1k' ou '1325967' para float."""
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


def load_metrics(pattern, disk_label="DISCO_EXT4"):
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
    parser.add_argument("--disk-label", default="DISCO_EXT4")
    parser.add_argument("--baseline-disk-label", default=None)
    parser.add_argument("--label", default="Ambiente")
    parser.add_argument(
        "--output-csv", default=None,
        help="Se informado, salva o resumo em CSV (uma linha por métrica) para consumo por scripts de plotagem.",
    )
    args = parser.parse_args()

    metrics, files = load_metrics(args.pattern, disk_label=args.disk_label)
    if not files:
        print(f"Nenhum arquivo encontrado para o padrão: {args.pattern}")
        return

    baseline_metrics, baseline_files = ({}, [])
    if args.baseline:
        baseline_disk_label = args.baseline_disk_label or args.disk_label
        baseline_metrics, baseline_files = load_metrics(
            args.baseline, disk_label=baseline_disk_label
        )
        if not baseline_files:
            print(f"AVISO: nenhum arquivo de baseline encontrado para: {args.baseline}")
            print("Seguindo sem cálculo de overhead.\n")

    print(f"\n================ RESUMO — {args.label} ================\n")
    print(f"Arquivos analisados ({args.label}): {len(files)}")
    if baseline_files:
        print(f"Arquivos analisados (baseline/host): {len(baseline_files)}")
    print()

    header = f"{'Métrica':32s} {'n':>3s} {'média':>14s} {'mediana':>14s} {'desvio':>12s} {'IQR':>12s}"
    if baseline_files:
        header += f" {'overhead %':>12s}"
    print(header)
    print("-" * len(header))

    # Coleta linhas pra tanto imprimir quanto (se pedido) gravar CSV
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
                line += f" {'sem baseline':>12s}"

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
        print("Overhead = (média_ambiente / média_host - 1) × 100, conforme Seção 2.6 do artigo.")
        print("Latência: overhead positivo = ambiente mais lento (esperado).")
        print("Demais: overhead positivo = ambiente com throughput MAIOR que host — revise se faz sentido físico.")
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
        print(f"CSV salvo em: {args.output_csv}")


if __name__ == "__main__":
    main()
