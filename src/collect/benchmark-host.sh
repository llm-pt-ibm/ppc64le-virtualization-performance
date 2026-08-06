#!/bin/bash
# =============================================================================
# BENCHMARK HOST — IBM Power9 (ppc64le) | Slurm
# Covers: CPU, Memory, Cache, Disk (fio/ext4), STREAM and GPU (CUDA/NCCL)
#
# Usage with Slurm:
#   sbatch benchmark-host.sh
#
# Or directly (without Slurm):
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
# PARAMETERS FROM SLURM (or manual fallback)
# ---------------------------------------------------------------------------
NCPUS=${SLURM_CPUS_PER_TASK:-16}
MEM_MB=${SLURM_MEM_PER_NODE:-65536}
MEM_GB=$(( MEM_MB / 1024 ))
VM_WORKERS=8
VM_BYTES="8G"

# ---------------------------------------------------------------------------
# PINNING — same CPUs as vm_gabrielly / vm_lucas / vm_ramalho
# (1 SMT thread per core, cores 20 to 35 of the POWER9)
# ---------------------------------------------------------------------------
CPU_LIST="80,84,88,92,96,100,104,108,112,116,120,124,128,132,136,140"
TASKSET="taskset -c $CPU_LIST"

# ---------------------------------------------------------------------------
# PATHS AND GENERAL VARIABLES
# ---------------------------------------------------------------------------
HOST=$(hostname)
DATE=$(date +%Y%m%d_%H%M%S)
WORKDIR=${SLURM_TMPDIR:-/tmp}
OUT="/home/multiarq/experimentos/host/benchmark_host_${HOST}_${DATE}.txt"

# ---------------------------------------------------------------------------
# PARAMETERS — Disk (fio)
# ---------------------------------------------------------------------------
FIO_FILE="${WORKDIR}/fio_testfile_host"
FIO_SIZE="80G"
FIO_JOBS=4
FIO_IODEPTH=32
FIO_IODEPTH_RAND=64

# ---------------------------------------------------------------------------
# PARAMETERS — STREAM
# ---------------------------------------------------------------------------
STREAM_BIN="/home/multiarq/stream/stream_power9"

# ---------------------------------------------------------------------------
# PARAMETERS — GPU
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
# PRE-CHECK: aborts if a VM is running (would contaminate shared disk)
# ---------------------------------------------------------------------------
mkdir -p "$(dirname "$OUT")"
: > "$OUT"

log "=== PRE-CHECK: active VMs on host ==="
if command -v virsh &>/dev/null; then
    VMS_ATIVAS=$(sudo -n virsh list --state-running --name 2>/dev/null | grep -v '^$' | wc -l)
    if [[ "$VMS_ATIVAS" -gt 0 ]]; then
        log "ERROR: $VMS_ATIVAS active VM(s). Shut down with 'sudo virsh shutdown <name>' and run again."
        exit 1
    fi
    log "OK: no active VMs."
else
    log "WARNING: virsh not available — continuing without the check."
fi
separator

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
        --group_reporting 2>&1 | tee -a "$OUT"

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
        --group_reporting 2>&1 | tee -a "$OUT"

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
        --group_reporting 2>&1 | tee -a "$OUT"

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
        --group_reporting 2>&1 | tee -a "$OUT"

    log ""
    log "=== [$label] LATENCY (4K randread QD=1) ==="
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
    log "=== [$label] REMOVING TEST FILE ==="
    rm -f "$FIO_FILE"
    log "File removed: $FIO_FILE"
}

# ---------------------------------------------------------------------------
# FUNCTION: GPU test suite (no taskset — passthrough handles it)
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
            log "WARNING: GPU binary not found: $bin"
            missing=1
        fi
    done
    if [[ $missing -eq 1 ]]; then
        log "WARNING: GPU tests skipped due to missing binaries."
        return
    fi

    log ""
    log "=== GPU — INFO (nvidia-smi) ==="
    nvidia-smi | tee -a "$OUT"

    log ""
    log "=== GPU — TOPOLOGY / NVLink ==="
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
    log "=== GPU — MATRIXMUL — Compute Throughput (GFlop/s) ==="
    for i in 1 2 3 4 5; do
        log "-- Run $i --"
        "$CUDA_BIN/matrixMul" 2>&1 | tee -a "$OUT"
    done

    log ""
    log "=== GPU — NCCL ALL_REDUCE (inter-GPU NVLink) ==="
    log "--- -b 8 -e 256M -f 2 -g 2 ---"
    "$NCCL_BIN/all_reduce_perf" \
        -b 8 -e 256M -f 2 -g 2 \
        2>&1 | tee -a "$OUT"

    log ""
    log "=== GPU — NCCL ALL_GATHER (inter-GPU NVLink) ==="
    log "--- -b 8 -e 256M -f 2 -g 2 ---"
    "$NCCL_BIN/all_gather_perf" \
        -b 8 -e 256M -f 2 -g 2 \
        2>&1 | tee -a "$OUT"

    log ""
    log "=== GPU — NCCL SENDRECV (point-to-point latency GPU0<->GPU1) ==="
    log "--- -b 8 -e 4M -f 2 -g 2 ---"
    "$NCCL_BIN/sendrecv_perf" \
        -b 8 -e 4M -f 2 -g 2 \
        2>&1 | tee -a "$OUT"

    log ""
    log "=== GPU — POST-BENCHMARK STATE ==="
    nvidia-smi --query-gpu=index,name,temperature.gpu,power.draw,memory.used,memory.total,utilization.gpu \
               --format=csv,noheader | tee -a "$OUT"
}

# =============================================================================
# START — DISABLE SWAP
# =============================================================================
log "=== PRE-RUN: disabling swap ==="
sudo -n swapoff -a 2>&1 | tee -a "$OUT" || log "WARNING: swapoff failed or swap was already disabled."
log "Swap state after swapoff:"
swapon --show 2>/dev/null | tee -a "$OUT" || log "(no active swap device)"

# =============================================================================
# HEADER
# =============================================================================
log "=== BENCHMARK HOST: $HOST — $DATE ==="
log "Environment: Physical host — IBM Power9 ppc64le"
log "CPUs allocated (Slurm): $NCPUS"
log "CPUs pinned (taskset): $CPU_LIST"
log "RAM allocated (Slurm): ${MEM_GB} GB"
log "GPUs visible (CUDA_VISIBLE_DEVICES): ${CUDA_VISIBLE_DEVICES}"
log "GPUs detected: $GPU_AVAILABLE"
separator

# =============================================================================
# SYSTEM INFO
# =============================================================================
log "=== SYSTEM INFO ==="
uname -a                              2>&1 | tee -a "$OUT"
log "CPUs visible (nproc): $(nproc)"
log "CPUs in use (Slurm)  : $NCPUS"
free -h                               2>&1 | tee -a "$OUT"
log "Swap: $(swapon --show 2>/dev/null | tail -n +2 | wc -l) device(s)"
separator

# =============================================================================
# FILESYSTEM INFO
# =============================================================================
log "=== FILESYSTEM INFO ==="
df -Th /tmp                           2>&1 | tee -a "$OUT"
mount | grep -E " / "                 2>&1 | tee -a "$OUT"
separator

# =============================================================================
# PROCESS CGROUP
# =============================================================================
log "=== PROCESS CGROUP ==="
cat /proc/self/cgroup                 2>&1 | tee -a "$OUT"
separator

# =============================================================================
# CPU (with taskset — matches VM pinning)
# =============================================================================
log "=== CPU ==="
log "(taskset -c $CPU_LIST)"
$TASKSET stress-ng --cpu "$NCPUS" \
          --cpu-method all \
          --metrics-brief \
          --timeout 60s 2>&1 | tee -a "$OUT"
separator

# =============================================================================
# MEMORY (with taskset)
# =============================================================================
log "=== MEMORY ==="
log "(taskset -c $CPU_LIST)"
$TASKSET stress-ng --vm "$VM_WORKERS" \
          --vm-bytes "$VM_BYTES" \
          --metrics-brief \
          --timeout 60s 2>&1 | tee -a "$OUT"
separator

# =============================================================================
# CACHE (with taskset)
# =============================================================================
log "=== CACHE ==="
log "(taskset -c $CPU_LIST, --cache-level 1)"
log "L1 buffer size (from /sys/): $(cat /sys/devices/system/cpu/cpu0/cache/index0/size)"
$TASKSET stress-ng --cache "$NCPUS" \
          --cache-level 1 \
          --metrics-brief \
          --timeout 60s 2>&1 | tee -a "$OUT"
separator

# =============================================================================
# STREAM — MEMORY BANDWIDTH (with taskset)
# =============================================================================
log "=== STREAM — MEMORY BANDWIDTH (taskset -c $CPU_LIST) ==="
if [ -x "$STREAM_BIN" ]; then
    export OMP_NUM_THREADS="$NCPUS"
    $TASKSET "$STREAM_BIN" 2>&1 | tee -a "$OUT"
else
    log "WARNING: STREAM binary not found at $STREAM_BIN"
    log "Compile with: gcc -O3 -mcpu=native -fopenmp -DSTREAM_ARRAY_SIZE=80000000 stream.c -o stream_power9"
fi
separator

# =============================================================================
# DISK (fio — no taskset)
# =============================================================================
run_fio_suite "DISK_HOST"
separator

# =============================================================================
# GPU (CUDA / NCCL) — only runs if a GPU is available
# =============================================================================
if [[ $GPU_AVAILABLE -eq 1 ]]; then
    log "=== GPU ==="
    run_gpu_suite
    separator
else
    log "=== GPU ==="
    log "WARNING: no GPU detected — GPU tests skipped."
    separator
fi

# =============================================================================
# END — RE-ENABLE SWAP
# =============================================================================
log ""
log "=== POST-RUN: re-enabling swap ==="
sudo -n swapon -a 2>&1 | tee -a "$OUT" || log "WARNING: swapon failed."
log "Swap state after swapon:"
swapon --show 2>/dev/null | tee -a "$OUT" || log "(no active swap device)"

log ""
log "=== END ==="
log "Results saved to: $OUT"
