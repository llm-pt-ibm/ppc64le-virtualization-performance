#!/bin/bash
# =============================================================================
# BENCHMARK VM — IBM Power9 (ppc64le) | KVM Guest
# Covers: CPU, Memory, Cache, Disk (fio/ext4), STREAM and GPU (CUDA/NCCL)
#
# Usage:
#   chmod +x benchmark_vm.sh
#   ./benchmark_vm.sh
#
# Prerequisites on the VM:
#   - stress-ng     (dnf install -y stress-ng)
#   - fio           (dnf install -y fio)
#   - ~/stream      compiled
#   - CUDA 12.2     at /usr/local/cuda-12.2
#   - NCCL 2.21.5   at /lib64
#   - bandwidthTest and matrixMul at ~/cuda-samples/bin/ppc64le/linux/release/
#   - all_reduce_perf / all_gather_perf / sendrecv_perf at ~/nccl-tests/build/
# =============================================================================

# NOTE: no -e, to avoid aborting on a GPU test failure
set -uo pipefail

# ---------------------------------------------------------------------------
# PATHS AND GENERAL VARIABLES
# ---------------------------------------------------------------------------
HOST=$(hostname)
DATE=$(date +%Y%m%d_%H%M%S)
OUTDIR="/root/experimentos"
OUT="${OUTDIR}/benchmark_vm_${HOST}_${DATE}.txt"

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
# PARAMETERS — GPU
# No auto-detection — nvidia-smi only runs inside run_gpu_suite()
# ---------------------------------------------------------------------------
export PATH=/usr/local/cuda-12.2/bin${PATH:+:$PATH}
export LD_LIBRARY_PATH=/usr/local/cuda-12.2/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}

CUDA_BIN="$HOME/cuda-samples/bin/ppc64le/linux/release"
NCCL_BIN="$HOME/nccl-tests/build"

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------
log()       { echo "$1" | tee -a "$OUT"; }
separator() { log ""; log "$(printf '=%.0s' {1..72})"; log ""; }

# Runs a command and logs it — never aborts the script on failure
run_logged() {
    local label="$1"
    shift
    log ""
    log "--- $label ---"
    # The || true keeps -e from aborting on failure
    "$@" 2>&1 | tee -a "$OUT" || {
        log "[WARNING] '$label' returned an error — continuing benchmark."
    }
}

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

# ---------------------------------------------------------------------------
# FUNCTION: GPU test suite
# nvidia-smi is only called in here — never at script start
# Every test has || true so it never aborts the whole benchmark
# ---------------------------------------------------------------------------
run_gpu_suite() {

    # Checks binaries before any GPU call
    local missing=0
    for bin in \
        "$CUDA_BIN/bandwidthTest" \
        "$CUDA_BIN/matrixMul" \
        "$NCCL_BIN/all_reduce_perf" \
        "$NCCL_BIN/all_gather_perf" \
        "$NCCL_BIN/sendrecv_perf"; do
        if [[ ! -x "$bin" ]]; then
            log "WARNING: binary not found: $bin"
            missing=1
        fi
    done

    if [[ $missing -eq 1 ]]; then
        log "WARNING: one or more GPU binaries missing — aborting GPU suite."
        return
    fi

    # --- GPU info -------------------------------------------------------
    log ""
    log "=== GPU — INFO (nvidia-smi) ==="
    nvidia-smi 2>&1 | tee -a "$OUT" || {
        log "[ERROR] nvidia-smi failed — aborting GPU suite."
        return
    }

    # --- Topology — nvidia-smi topo can hang under VFIO, guarded with a timeout
    log ""
    log "=== GPU — TOPOLOGY / NVLink ==="
    timeout 15 nvidia-smi topo -m 2>&1 | tee -a "$OUT" || \
        log "[WARNING] nvidia-smi topo -m failed or timed out — skipping."

    # --- Bandwidth Test ------------------------------------------------------
    # H2D/D2H: bare-metal V100 SXM2 reference ~12-16 GB/s | via VFIO ~1-6 GB/s
    # D2D HBM2: bare-metal reference ~700-800 GB/s | VFIO essentially no overhead
    log ""
    log "=== GPU — BANDWIDTH TEST (H2D / D2H / D2D) ==="
    log "--- GPU 0 ---"
    "$CUDA_BIN/bandwidthTest" --device=0 --mode=range \
        --start=1048576 --end=268435456 --increment=33554432 \
        2>&1 | tee -a "$OUT" || log "[WARNING] bandwidthTest GPU 0 failed."

    log ""
    log "--- GPU 1 ---"
    "$CUDA_BIN/bandwidthTest" --device=1 --mode=range \
        --start=1048576 --end=268435456 --increment=33554432 \
        2>&1 | tee -a "$OUT" || log "[WARNING] bandwidthTest GPU 1 failed."

    # --- MatrixMul -----------------------------------------------------------
    # 5 runs for a statistical average; each individually guarded
    log ""
    log "=== GPU — MATRIXMUL — Compute Throughput (GFlop/s) ==="
    for i in 1 2 3 4 5; do
        log "-- Run $i --"
        "$CUDA_BIN/matrixMul" 2>&1 | tee -a "$OUT" || \
            log "[WARNING] matrixMul run $i failed."
    done

    # --- NCCL all_reduce -----------------------------------------------------
    # Ref bare-metal NVLink 2.0 AC922: peak ~100-130 GB/s (busbw)
    # Expected via VFIO: ~60-80% of bare metal (~60-66 GB/s measured)
    log ""
    log "=== GPU — NCCL ALL_REDUCE (inter-GPU NVLink) ==="
    log "--- -b 8 -e 256M -f 2 -g 2 ---"
    "$NCCL_BIN/all_reduce_perf" \
        -b 8 -e 256M -f 2 -g 2 \
        2>&1 | tee -a "$OUT" || log "[WARNING] all_reduce_perf failed."

    # --- NCCL all_gather -----------------------------------------------------
    log ""
    log "=== GPU — NCCL ALL_GATHER (inter-GPU NVLink) ==="
    log "--- -b 8 -e 256M -f 2 -g 2 ---"
    "$NCCL_BIN/all_gather_perf" \
        -b 8 -e 256M -f 2 -g 2 \
        2>&1 | tee -a "$OUT" || log "[WARNING] all_gather_perf failed."

    # --- NCCL sendrecv — point-to-point latency ------------------------------
    # Low latency = NVLink active; high latency = PCIe fallback
    log ""
    log "=== GPU — NCCL SENDRECV (latency GPU0<->GPU1) ==="
    log "--- -b 8 -e 4M -f 2 -g 2 ---"
    "$NCCL_BIN/sendrecv_perf" \
        -b 8 -e 4M -f 2 -g 2 \
        2>&1 | tee -a "$OUT" || log "[WARNING] sendrecv_perf failed."

    # --- Post-benchmark state ------------------------------------------------
    log ""
    log "=== GPU — POST-BENCHMARK STATE ==="
    nvidia-smi --query-gpu=index,name,temperature.gpu,power.draw,\
memory.used,memory.total,utilization.gpu \
        --format=csv,noheader 2>&1 | tee -a "$OUT" || true
}

# =============================================================================
# HEADER
# =============================================================================
log "=== BENCHMARK VM: $HOST — $DATE ==="
log "Environment: KVM VM — IBM Power9 ppc64le"
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
          --cache-level 1 \
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
# GPU (CUDA / NCCL)
# =============================================================================
log "=== GPU ==="
run_gpu_suite
separator

# =============================================================================
# END
# =============================================================================
log ""
log "=== END ==="
log "Results saved to: $OUT"
