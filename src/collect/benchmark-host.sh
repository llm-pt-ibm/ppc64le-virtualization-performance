#!/bin/bash
# =============================================================================
# BENCHMARK HOST — IBM Power9 (ppc64le) | Slurm
# Cobre: CPU, Memória, Cache, Disco (fio/ext4), STREAM e GPU (CUDA/NCCL)
#
# Uso com Slurm:
#   sbatch benchmark-host.sh
#
# Ou direto (sem Slurm):
#   bash benchmark-host.sh
# =============================================================================
#SBATCH --job-name=benchmark_host
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=65536
#SBATCH --time=01:00:00
#SBATCH --output=/tmp/benchmark_host_slurm_%j.txt

set -euo pipefail

# ---------------------------------------------------------------------------
# PARÂMETROS VINDOS DO SLURM (ou fallback manual)
# ---------------------------------------------------------------------------
NCPUS=${SLURM_CPUS_PER_TASK:-16}
MEM_MB=${SLURM_MEM_PER_NODE:-65536}
MEM_GB=$(( MEM_MB / 1024 ))
VM_WORKERS=8
VM_BYTES="8G"

# ---------------------------------------------------------------------------
# CAMINHOS E VARIÁVEIS GERAIS
# ---------------------------------------------------------------------------
HOST=$(hostname)
DATE=$(date +%Y%m%d_%H%M%S)
WORKDIR=${SLURM_TMPDIR:-/tmp}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${SCRIPT_DIR}/../../data/raw/host/benchmark_host_${HOST}_${DATE}.txt"
mkdir -p "$(dirname "$OUT")"

# ---------------------------------------------------------------------------
# PARÂMETROS — Disco (fio)
# ---------------------------------------------------------------------------
FIO_FILE="${WORKDIR}/fio_testfile_host"
FIO_SIZE="80G"
FIO_JOBS=4
FIO_IODEPTH=32
FIO_IODEPTH_RAND=64

# ---------------------------------------------------------------------------
# PARÂMETROS — STREAM
# ---------------------------------------------------------------------------
STREAM_BIN="/home/multiarq/stream/stream_power9"

# ---------------------------------------------------------------------------
# PARÂMETROS — GPU
# ---------------------------------------------------------------------------
export PATH=/usr/local/cuda-12.2/bin:$PATH
export LD_LIBRARY_PATH=/usr/local/cuda-12.2/lib64:$LD_LIBRARY_PATH
export LD_LIBRARY_PATH=/home/multiarq/lucas/pytorch/build/nccl/lib:$LD_LIBRARY_PATH
export CUDA_VISIBLE_DEVICES=0,1

CUDA_BIN="$HOME/cuda-samples/bin/ppc64le/linux/release"
NCCL_BIN="$HOME/nccl-tests/build"
GPU_AVAILABLE=0

if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
    GPU_AVAILABLE=1
fi

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
log()       { echo "$1" | tee -a "$OUT"; }
separator() { log ""; log "$(printf '=%.0s' {1..72})"; log ""; }

# ---------------------------------------------------------------------------
# FUNÇÃO: suite de testes fio
# ---------------------------------------------------------------------------
run_fio_suite() {
    local label="$1"

    log ""
    log "=== [$label] PRÉ-ALOCAÇÃO DO ARQUIVO DE TESTE (${FIO_SIZE}) ==="
    fio --name=precreate \
        --filename="$FIO_FILE" \
        --size="$FIO_SIZE" \
        --rw=write \
        --bs=1M \
        --direct=1 \
        --ioengine=libaio \
        --iodepth="$FIO_IODEPTH" \
        --numjobs=1 \
        --end_fsync=1 \
        --group_reporting 2>&1 | tee -a "$OUT"

    log ""
    log "=== [$label] LEITURA SEQUENCIAL ==="
    fio --name=seq-read \
        --filename="$FIO_FILE" \
        --size="$FIO_SIZE" \
        --rw=read \
        --bs=1M \
        --direct=1 \
        --ioengine=libaio \
        --iodepth="$FIO_IODEPTH" \
        --numjobs="$FIO_JOBS" \
        --time_based \
        --runtime=60 \
        --group_reporting 2>&1 | tee -a "$OUT"

    log ""
    log "=== [$label] ESCRITA SEQUENCIAL ==="
    fio --name=seq-write \
        --filename="$FIO_FILE" \
        --size="$FIO_SIZE" \
        --rw=write \
        --bs=1M \
        --direct=1 \
        --ioengine=libaio \
        --iodepth="$FIO_IODEPTH" \
        --numjobs="$FIO_JOBS" \
        --time_based \
        --runtime=60 \
        --group_reporting 2>&1 | tee -a "$OUT"

    log ""
    log "=== [$label] IOPS ALEATÓRIO (4K randrw 70/30) ==="
    fio --name=rand-rw \
        --filename="$FIO_FILE" \
        --size="$FIO_SIZE" \
        --rw=randrw \
        --rwmixread=70 \
        --bs=4k \
        --direct=1 \
        --ioengine=libaio \
        --iodepth="$FIO_IODEPTH_RAND" \
        --numjobs="$FIO_JOBS" \
        --time_based \
        --runtime=60 \
        --group_reporting 2>&1 | tee -a "$OUT"

    log ""
    log "=== [$label] LATÊNCIA (4K randread síncrono) ==="
    fio --name=lat-read \
        --filename="$FIO_FILE" \
        --size="$FIO_SIZE" \
        --rw=randread \
        --bs=4k \
        --direct=1 \
        --ioengine=libaio \
        --iodepth=1 \
        --numjobs=1 \
        --time_based \
        --runtime=60 \
        --group_reporting 2>&1 | tee -a "$OUT"

    log ""
    log "=== [$label] REMOVENDO ARQUIVO DE TESTE ==="
    rm -f "$FIO_FILE"
    log "Arquivo removido: $FIO_FILE"
}

# ---------------------------------------------------------------------------
# FUNÇÃO: suite de testes GPU
# ---------------------------------------------------------------------------
run_gpu_suite() {

    local missing=0
    for bin in \
        "$CUDA_BIN/bandwidthTest" \
        "$CUDA_BIN/matrixMul" \
        "$NCCL_BIN/all_reduce_perf" \
        "$NCCL_BIN/all_gather_perf" \
        "$NCCL_BIN/sendrecv_perf"; do
        if [[ ! -x "$bin" ]]; then
            log "AVISO: binário GPU não encontrado: $bin"
            missing=1
        fi
    done
    if [[ $missing -eq 1 ]]; then
        log "AVISO: testes GPU ignorados por binários ausentes."
        return
    fi

    log ""
    log "=== GPU — INFO (nvidia-smi) ==="
    nvidia-smi | tee -a "$OUT"

    log ""
    log "=== GPU — TOPOLOGIA / NVLink ==="
    nvidia-smi topo -m | tee -a "$OUT"

    log ""
    log "=== GPU — BANDWIDTH TEST (H2D / D2H / D2D) ==="
    log "--- GPU 0 ---"
    "$CUDA_BIN/bandwidthTest" --device=0 --mode=range \
        --start=1048576 --end=268435456 --increment=33554432 \
        2>&1 | tee -a "$OUT"

    log ""
    log "--- GPU 1 ---"
    "$CUDA_BIN/bandwidthTest" --device=1 --mode=range \
        --start=1048576 --end=268435456 --increment=33554432 \
        2>&1 | tee -a "$OUT"

    log ""
    log "=== GPU — MATRIXMUL — Throughput de Computação (GFlop/s) ==="
    for i in 1 2 3 4 5; do
        log "-- Execução $i --"
        "$CUDA_BIN/matrixMul" 2>&1 | tee -a "$OUT"
    done

    log ""
    log "=== GPU — NCCL ALL_REDUCE (NVLink inter-GPU) ==="
    log "--- -b 8 -e 256M -f 2 -g 2 ---"
    "$NCCL_BIN/all_reduce_perf" \
        -b 8 -e 256M -f 2 -g 2 \
        2>&1 | tee -a "$OUT"

    log ""
    log "=== GPU — NCCL ALL_GATHER (NVLink inter-GPU) ==="
    log "--- -b 8 -e 256M -f 2 -g 2 ---"
    "$NCCL_BIN/all_gather_perf" \
        -b 8 -e 256M -f 2 -g 2 \
        2>&1 | tee -a "$OUT"

    log ""
    log "=== GPU — NCCL SENDRECV (latência ponto a ponto GPU0↔GPU1) ==="
    log "--- -b 8 -e 4M -f 2 -g 2 ---"
    "$NCCL_BIN/sendrecv_perf" \
        -b 8 -e 4M -f 2 -g 2 \
        2>&1 | tee -a "$OUT"

    log ""
    log "=== GPU — ESTADO PÓS-BENCHMARK ==="
    nvidia-smi --query-gpu=index,name,temperature.gpu,power.draw,memory.used,memory.total,utilization.gpu \
               --format=csv,noheader | tee -a "$OUT"
}

# =============================================================================
# INÍCIO — DESATIVAR SWAP
# =============================================================================
log "=== PRÉ-EXECUÇÃO: desativando swap ==="
sudo swapoff -a 2>&1 | tee -a "$OUT" || log "AVISO: swapoff falhou ou swap já estava desativado."
log "Estado do swap após swapoff:"
swapon --show 2>/dev/null | tee -a "$OUT" || log "(nenhum dispositivo de swap ativo)"

# =============================================================================
# CABEÇALHO
# =============================================================================
log "=== BENCHMARK HOST: $HOST — $DATE ==="
log "Ambiente: Host físico — IBM Power9 ppc64le"
log "CPUs alocadas (Slurm): $NCPUS"
log "RAM alocada  (Slurm): ${MEM_GB} GB"
log "GPUs visíveis (CUDA_VISIBLE_DEVICES): ${CUDA_VISIBLE_DEVICES}"
log "GPUs detectadas: $GPU_AVAILABLE"
separator

# =============================================================================
# INFO DO SISTEMA
# =============================================================================
log "=== INFO DO SISTEMA ==="
uname -a                              2>&1 | tee -a "$OUT"
log "CPUs visíveis (nproc): $(nproc)"
log "CPUs em uso (Slurm)  : $NCPUS"
free -h                               2>&1 | tee -a "$OUT"
log "Swap: $(swapon --show 2>/dev/null | tail -n +2 | wc -l) dispositivo(s)"
separator

# =============================================================================
# INFO DO FILESYSTEM
# =============================================================================
log "=== INFO DO FILESYSTEM ==="
df -Th /tmp                           2>&1 | tee -a "$OUT"
mount | grep -E " / "                 2>&1 | tee -a "$OUT"
separator

# =============================================================================
# CGROUP DO PROCESSO
# =============================================================================
log "=== CGROUP DO PROCESSO ==="
cat /proc/self/cgroup                 2>&1 | tee -a "$OUT"
separator

# =============================================================================
# CPU
# =============================================================================
log "=== CPU ==="
stress-ng --cpu "$NCPUS" \
          --cpu-method all \
          --metrics-brief \
          --timeout 60s 2>&1 | tee -a "$OUT"
separator

# =============================================================================
# MEMÓRIA
# =============================================================================
log "=== MEMÓRIA ==="
stress-ng --vm "$VM_WORKERS" \
          --vm-bytes "$VM_BYTES" \
          --metrics-brief \
          --timeout 60s 2>&1 | tee -a "$OUT"
separator

# =============================================================================
# CACHE
# =============================================================================
log "=== CACHE ==="
stress-ng --cache "$NCPUS" \
          --cache-level 1 \
          --metrics-brief \
          --timeout 60s \
          2>&1 | tee -a "$OUT"
separator

# =============================================================================
# STREAM — LARGURA DE BANDA DE MEMÓRIA
# =============================================================================
log "=== STREAM — LARGURA DE BANDA DE MEMÓRIA ==="
if [ -x "$STREAM_BIN" ]; then
    export OMP_NUM_THREADS="$NCPUS"
    "$STREAM_BIN" 2>&1 | tee -a "$OUT"
else
    log "AVISO: binário STREAM não encontrado em $STREAM_BIN"
    log "Compile com: gcc -O3 -mcpu=native -fopenmp -DSTREAM_ARRAY_SIZE=80000000 stream.c -o stream_power9"
fi
separator

# =============================================================================
# DISCO (fio)
# =============================================================================
run_fio_suite "DISCO_HOST"
separator

# =============================================================================
# GPU (CUDA / NCCL) — só executa se GPU disponível
# =============================================================================
if [[ $GPU_AVAILABLE -eq 1 ]]; then
    log "=== GPU ==="
    run_gpu_suite
    separator
else
    log "=== GPU ==="
    log "AVISO: nenhuma GPU detectada — testes GPU ignorados."
    separator
fi

# =============================================================================
# FIM — REATIVAR SWAP
# =============================================================================
log ""
log "=== PÓS-EXECUÇÃO: reativando swap ==="
sudo swapon -a 2>&1 | tee -a "$OUT" || log "AVISO: swapon falhou."
log "Estado do swap após swapon:"
swapon --show 2>/dev/null | tee -a "$OUT" || log "(nenhum dispositivo de swap ativo)"

log ""
log "=== FIM ==="
log "Resultados salvos em: $OUT"
