#!/bin/bash
# =============================================================================
# BENCHMARK QEMU PURO — IBM Power9 (ppc64le)
# Cobre: CPU, Memória, Cache, Disco (fio/ext4) e STREAM
# (Sem testes de GPU — QEMU puro/TCG não suporta passthrough PCIe)
#
# Uso:
#   chmod +x benchmark-qemu.sh
#   ./benchmark-qemu.sh
#
# Pré-requisitos na VM:
#   - stress-ng     (dnf install -y stress-ng)
#   - fio           (dnf install -y fio)
#   - ~/stream      compilado
# =============================================================================

set -uo pipefail

# ---------------------------------------------------------------------------
# CAMINHOS E VARIÁVEIS GERAIS
# ---------------------------------------------------------------------------
HOST=$(hostname)
DATE=$(date +%Y%m%d_%H%M%S)
OUTDIR="/root/experimentos"
OUT="${OUTDIR}/benchmark_qemu_${HOST}_${DATE}.txt"

mkdir -p "$OUTDIR"

# ---------------------------------------------------------------------------
# PARÂMETROS — CPU / Memória / Cache
# ---------------------------------------------------------------------------
NCPUS=16
VM_WORKERS=8
VM_BYTES="8G"

# ---------------------------------------------------------------------------
# PARÂMETROS — Disco (fio)
# ---------------------------------------------------------------------------
FIO_FILE="/root/fio_testfile"
FIO_SIZE="80G"
FIO_JOBS=4
FIO_IODEPTH=32
FIO_IODEPTH_RAND=64

# ---------------------------------------------------------------------------
# PARÂMETROS — STREAM
# ---------------------------------------------------------------------------
STREAM_BIN="/root/stream"

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
        --group_reporting 2>&1 | tee -a "$OUT" || true

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
        --group_reporting 2>&1 | tee -a "$OUT" || true

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
        --group_reporting 2>&1 | tee -a "$OUT" || true

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
        --group_reporting 2>&1 | tee -a "$OUT" || true

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
        --group_reporting 2>&1 | tee -a "$OUT" || true

    log ""
    log "=== [$label] REMOVENDO ARQUIVO DE TESTE ==="
    rm -f "$FIO_FILE" && log "Arquivo removido: $FIO_FILE" || true
}

# =============================================================================
# CABEÇALHO
# =============================================================================
log "=== BENCHMARK QEMU PURO: $HOST — $DATE ==="
log "Ambiente: QEMU puro (TCG) — IBM Power9 ppc64le — SEM testes de GPU"
separator

# =============================================================================
# INFO DO SISTEMA
# =============================================================================
log "=== INFO DO SISTEMA ==="
uname -a                              2>&1 | tee -a "$OUT"
log "CPUs disponíveis: $(nproc)"
free -h                               2>&1 | tee -a "$OUT"
log "Swap: $(swapon --show 2>/dev/null | tail -n +2 | wc -l) dispositivo(s)"
separator

# =============================================================================
# INFO DO FILESYSTEM
# =============================================================================
log "=== INFO DO FILESYSTEM ==="
df -Th /root                          2>&1 | tee -a "$OUT"
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
          --timeout 60s 2>&1 | tee -a "$OUT" || true
separator

# =============================================================================
# MEMÓRIA
# =============================================================================
log "=== MEMÓRIA ==="
stress-ng --vm "$VM_WORKERS" \
          --vm-bytes "$VM_BYTES" \
          --metrics-brief \
          --timeout 60s 2>&1 | tee -a "$OUT" || true
separator

# =============================================================================
# CACHE
# =============================================================================
log "=== CACHE ==="
stress-ng --cache "$NCPUS" \
          --metrics-brief \
          --timeout 60s 2>&1 | tee -a "$OUT" || true
separator

# =============================================================================
# STREAM — LARGURA DE BANDA DE MEMÓRIA
# =============================================================================
log "=== STREAM — LARGURA DE BANDA DE MEMÓRIA ==="
if [ -x "$STREAM_BIN" ]; then
    "$STREAM_BIN" 2>&1 | tee -a "$OUT" || true
else
    log "AVISO: STREAM não encontrado em $STREAM_BIN"
    log "Compile: gcc -O3 -mcpu=native -fopenmp -DSTREAM_ARRAY_SIZE=80000000 stream.c -o stream"
fi
separator

# =============================================================================
# DISCO (fio — ext4)
# =============================================================================
run_fio_suite "DISCO_EXT4"
separator

# =============================================================================
# FIM
# =============================================================================
log ""
log "=== FIM ==="
log "Resultados salvos em: $OUT"
