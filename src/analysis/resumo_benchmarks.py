#!/usr/bin/env python3

import re
import glob
from statistics import mean

metrics = {
    "cpu_bogo": [],
    "mem_bogo": [],
    "cache_bogo": [],
    "stream_copy": [],
    "stream_scale": [],
    "stream_add": [],
    "stream_triad": [],
    "seq_read_bw": [],
    "seq_write_bw": [],
    "rand_read_iops": [],
    "rand_write_iops": [],
    "lat_read_iops": [],
    "lat_clat_avg_us": [],
    "matrix_gflops": [],
    "nccl_allreduce": [],
    "nccl_allgather": [],
    "nccl_sendrecv": [],
}


def parse_iops(value):
    value = value.lower().replace(",", "")
    if value.endswith("k"):
        return float(value[:-1]) * 1000
    return float(value)


files = sorted(glob.glob("benchmark_vm_localhost.localdomain_*.txt"))

if not files:
    print("Nenhum arquivo encontrado.")
    exit(1)

for fname in files:
    with open(fname, "r", errors="ignore") as f:
        txt = f.read()

    # CPU
    m = re.search(r'cpu\s+\d+\s+\S+\s+\S+\s+\S+\s+\S+\s+([\d.]+)', txt)
    if m:
        metrics["cpu_bogo"].append(float(m.group(1)))

    # MEM
    m = re.search(r'vm\s+\d+\s+\S+\s+\S+\s+\S+\s+\S+\s+([\d.]+)', txt)
    if m:
        metrics["mem_bogo"].append(float(m.group(1)))

    # CACHE
    m = re.search(r'cache\s+\d+\s+\S+\s+\S+\s+\S+\s+\S+\s+([\d.]+)', txt)
    if m:
        metrics["cache_bogo"].append(float(m.group(1)))

    # STREAM
    for key, label in [
        ("stream_copy", "Copy"),
        ("stream_scale", "Scale"),
        ("stream_add", "Add"),
        ("stream_triad", "Triad"),
    ]:
        m = re.search(rf'{label}:\s+([\d.]+)', txt)
        if m:
            metrics[key].append(float(m.group(1)))

    # Seq Read
    m = re.search(
        r'=== \[DISCO_EXT4\] LEITURA SEQUENCIAL ===.*?read: IOPS=.*?BW=([\d.]+)MiB/s',
        txt,
        re.S,
    )
    if m:
        metrics["seq_read_bw"].append(float(m.group(1)))

    # Seq Write
    m = re.search(
        r'=== \[DISCO_EXT4\] ESCRITA SEQUENCIAL ===.*?write: IOPS=.*?BW=([\d.]+)MiB/s',
        txt,
        re.S,
    )
    if m:
        metrics["seq_write_bw"].append(float(m.group(1)))

    # RandRW
    m = re.search(
        r'=== \[DISCO_EXT4\] IOPS ALEATÓRIO.*?read: IOPS=([0-9.k]+)',
        txt,
        re.S,
    )
    if m:
        metrics["rand_read_iops"].append(parse_iops(m.group(1)))

    m = re.search(
        r'=== \[DISCO_EXT4\] IOPS ALEATÓRIO.*?write: IOPS=([0-9.k]+)',
        txt,
        re.S,
    )
    if m:
        metrics["rand_write_iops"].append(parse_iops(m.group(1)))

    # Latência
    m = re.search(
        r'=== \[DISCO_EXT4\] LATÊNCIA.*?read: IOPS=([\d.]+)',
        txt,
        re.S,
    )
    if m:
        metrics["lat_read_iops"].append(float(m.group(1)))

    m = re.search(
        r'=== \[DISCO_EXT4\] LATÊNCIA.*?clat .*?avg=([\d.]+)',
        txt,
        re.S,
    )
    if m:
        metrics["lat_clat_avg_us"].append(float(m.group(1)))

    # MatrixMul
    vals = re.findall(r'Performance=\s*([\d.]+)\s*GFlop/s', txt)
    if vals:
        metrics["matrix_gflops"].append(
            mean(float(v) for v in vals)
        )

    # NCCL
    vals = re.findall(
        r'ALL_REDUCE.*?# Avg bus bandwidth\s+:\s+([\d.]+)',
        txt,
        re.S,
    )
    if vals:
        metrics["nccl_allreduce"].append(float(vals[0]))

    vals = re.findall(
        r'ALL_GATHER.*?# Avg bus bandwidth\s+:\s+([\d.]+)',
        txt,
        re.S,
    )
    if vals:
        metrics["nccl_allgather"].append(float(vals[0]))

    vals = re.findall(
        r'SENDRECV.*?# Avg bus bandwidth\s+:\s+([\d.]+)',
        txt,
        re.S,
    )
    if vals:
        metrics["nccl_sendrecv"].append(float(vals[0]))


print("\n================ MÉDIA DAS EXECUÇÕES ================\n")

for metric, values in metrics.items():
    if values:
        print(f"{metric:25s}: {mean(values):.2f}")

print("\nArquivos analisados:", len(files))
