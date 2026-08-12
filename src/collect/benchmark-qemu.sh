#!/bin/bash
# =============================================================================
# BENCHMARK PLAIN QEMU — IBM Power9 (ppc64le)
# Covers: CPU, Memory, Cache, Disk (fio/ext4) and STREAM
# (No GPU tests — plain QEMU/TCG doesn't support PCIe passthrough)
#
# Usage:
#   chmod +x benchmark-qemu.sh
#   ./benchmark-qemu.sh
#
# Prerequisites on the VM:
#   - stress-ng     (dnf install -y stress-ng)
#   - fio           (dnf install -y fio)
#   - ~/stream      compiled
# =============================================================================

set -uo pipefail

# ---------------------------------------------------------------------------
# PATHS AND GENERAL VARIABLES
# ---------------------------------------------------------------------------
HOST=$(hostname)
DATE=$(date +%Y%m%d_%H%M%S)
OUTDIR="/root/experimentos"
OUT="${OUTDIR}/benchmark_qemu_${HOST}_${DATE}.txt"

mkdir -p "$OUTDIR"

# ---------------------------------------------------------------------------
# PARAMETERS — CPU / Memory / Cache
# ---------------------------------------------------------------------------
NCPUS=16
VM_WORKERS=8
VM_BYTES="8G"

# ---------------------------------------------------------------------------
# PARAMETERS — Disk (fio)
# ---------------------------------------------------------------------------
FIO_FILE="/root/fio_testfile"
FIO_SIZE="80G"
FIO_JOBS=4
FIO_IODEPTH=32
FIO_IODEPTH_RAND=64

# ---------------------------------------------------------------------------
# PARAMETERS — STREAM
# ---------------------------------------------------------------------------
STREAM_BIN="/root/stream"

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
log()       { echo "$1" | tee -a "$OUT"; }
separator() { log ""; log "$(printf '=%.0s' {1..72})"; log ""; }

# ---------------------------------------------------------------------------
# FUNCTION: fio test suite
# ---------------------------------------------------------------------------
run_fio_suite() {
    local label="$1"

    log ""
    log "=== [$label] PRE-ALLOCATING TEST FILE (${FIO_SIZE}) ==="
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
    log "=== [$label] SEQUENTIAL READ ==="
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
    log "=== [$label] SEQUENTIAL WRITE ==="
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
    log "=== [$label] RANDOM IOPS (4K randrw 70/30) ==="
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
    log "=== [$label] LATENCY (4K randread synchronous) ==="
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
    log "=== [$label] REMOVING TEST FILE ==="
    rm -f "$FIO_FILE" && log "File removed: $FIO_FILE" || true
}

# =============================================================================
# HEADER
# =============================================================================
log "=== BENCHMARK PLAIN QEMU: $HOST — $DATE ==="
log "Environment: Plain QEMU (TCG) — IBM Power9 ppc64le — NO GPU tests"
separator

# =============================================================================
# SYSTEM INFO
# =============================================================================
log "=== SYSTEM INFO ==="
uname -a                              2>&1 | tee -a "$OUT"
log "CPUs available: $(nproc)"
free -h                               2>&1 | tee -a "$OUT"
log "Swap: $(swapon --show 2>/dev/null | tail -n +2 | wc -l) device(s)"
separator

# =============================================================================
# FILESYSTEM INFO
# =============================================================================
log "=== FILESYSTEM INFO ==="
df -Th /root                          2>&1 | tee -a "$OUT"
mount | grep -E " / "                 2>&1 | tee -a "$OUT"
separator

# =============================================================================
# PROCESS CGROUP
# =============================================================================
log "=== PROCESS CGROUP ==="
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
# MEMORY
# =============================================================================
log "=== MEMORY ==="
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
# STREAM — MEMORY BANDWIDTH
# =============================================================================
log "=== STREAM — MEMORY BANDWIDTH ==="
if [ -x "$STREAM_BIN" ]; then
    "$STREAM_BIN" 2>&1 | tee -a "$OUT" || true
else
    log "WARNING: STREAM not found at $STREAM_BIN"
    log "Compile with: gcc -O3 -mcpu=native -fopenmp -DSTREAM_ARRAY_SIZE=80000000 stream.c -o stream"
fi
separator

# =============================================================================
# DISK (fio — ext4)
# =============================================================================
run_fio_suite "DISK_EXT4"
separator

# =============================================================================
# END
# =============================================================================
log ""
log "=== END ==="
log "Results saved to: $OUT"
