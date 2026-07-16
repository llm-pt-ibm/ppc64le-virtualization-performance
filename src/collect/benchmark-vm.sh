#!/bin/bash
# =============================================================================
# BENCHMARK VM — IBM Power9 (ppc64le) | KVM Guest
# Cobre: CPU, Memória, Cache, Disco (fio/ext4), STREAM e GPU (CUDA/NCCL)
#
# Uso:
#   chmod +x benchmark_vm.sh
#   ./benchmark_vm.sh
#
# Pré-requisitos na VM:
#   - stress-ng     (dnf install -y stress-ng)
#   - fio           (dnf install -y fio)
#   - ~/stream      compilado
#   - CUDA 12.2     em /usr/local/cuda-12.2
#   - NCCL 2.21.5   em /lib64
#   - bandwidthTest e matrixMul em ~/cuda-samples/bin/ppc64le/linux/release/
#   - all_reduce_perf / all_gather_perf / sendrecv_perf em ~/nccl-tests/build/
# =============================================================================

# NOTA: sem -e para evitar abort em falha de teste GPU
set -uo pipefail

# ---------------------------------------------------------------------------
# CAMINHOS E VARIÁVEIS GERAIS
# ---------------------------------------------------------------------------
HOST=$(hostname)
DATE=$(date +%Y%m%d_%H%M%S)
OUTDIR="/root/experimentos"
OUT="${OUTDIR}/benchmark_vm_${HOST}_${DATE}.txt"

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
# PARÂMETROS — GPU
# Sem detecção automática — nvidia-smi só roda dentro de run_gpu_suite()
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

# Roda um comando e loga — nunca aborta o script em caso de falha
run_logged() {
	    local label="$1"
	        shift
		    log ""
		        log "--- $label ---"
			    # O || true impede que -e aborte em falha
			        "$@" 2>&1 | tee -a "$OUT" || {
					        log "[AVISO] '$label' retornou erro — continuando benchmark."
				    }
			    }

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

													# ---------------------------------------------------------------------------
													# FUNÇÃO: suite de testes GPU
													# nvidia-smi só é chamado aqui dentro — nunca no início do script
													# Cada teste tem || true para nunca abortar o benchmark completo
													# ---------------------------------------------------------------------------
													run_gpu_suite() {

														    # Verifica binários antes de qualquer chamada de GPU
														        local missing=0
															    for bin in \
																            "$CUDA_BIN/bandwidthTest" \
																	            "$CUDA_BIN/matrixMul" \
																		            "$NCCL_BIN/all_reduce_perf" \
																			            "$NCCL_BIN/all_gather_perf" \
																				            "$NCCL_BIN/sendrecv_perf"; do
															            if [[ ! -x "$bin" ]]; then
																	                log "AVISO: binário não encontrado: $bin"
																			            missing=1
																				            fi
																					        done

																						    if [[ $missing -eq 1 ]]; then
																							            log "AVISO: um ou mais binários GPU ausentes — abortando suite GPU."
																								            return
																									        fi

																										    # --- Info das GPUs -------------------------------------------------------
																										        log ""
																											    log "=== GPU — INFO (nvidia-smi) ==="
																											        nvidia-smi 2>&1 | tee -a "$OUT" || {
																													        log "[ERRO] nvidia-smi falhou — abortando suite GPU."
																												        return
																													    }

																													        # --- Topologia — nvidia-smi topo pode travar em VFIO, protegido com timeout
																														    log ""
																														        log "=== GPU — TOPOLOGIA / NVLink ==="
																															    timeout 15 nvidia-smi topo -m 2>&1 | tee -a "$OUT" || \
																																            log "[AVISO] nvidia-smi topo -m falhou ou expirou — ignorando."

																															        # --- Bandwidth Test ------------------------------------------------------
																																    # H2D/D2H: ref bare metal V100 SXM2 ~12-16 GB/s | via VFIO ~1-6 GB/s
																																        # D2D HBM2: ref bare metal ~700-800 GB/s | VFIO praticamente sem overhead
																																	    log ""
																																	        log "=== GPU — BANDWIDTH TEST (H2D / D2H / D2D) ==="
																																		    log "--- GPU 0 ---"
																																		        "$CUDA_BIN/bandwidthTest" --device=0 --mode=range \
																																				        --start=1048576 --end=268435456 --increment=33554432 \
																																					        2>&1 | tee -a "$OUT" || log "[AVISO] bandwidthTest GPU 0 falhou."

																																			    log ""
																																			        log "--- GPU 1 ---"
																																				    "$CUDA_BIN/bandwidthTest" --device=1 --mode=range \
																																					            --start=1048576 --end=268435456 --increment=33554432 \
																																						            2>&1 | tee -a "$OUT" || log "[AVISO] bandwidthTest GPU 1 falhou."

																																				        # --- MatrixMul -----------------------------------------------------------
																																					    # 5 execuções para média estatística; cada uma protegida individualmente
																																					        log ""
																																						    log "=== GPU — MATRIXMUL — Throughput de Computação (GFlop/s) ==="
																																						        for i in 1 2 3 4 5; do
																																								        log "-- Execução $i --"
																																									        "$CUDA_BIN/matrixMul" 2>&1 | tee -a "$OUT" || \
																																											            log "[AVISO] matrixMul execução $i falhou."
																																										    done

																																										        # --- NCCL all_reduce -----------------------------------------------------
																																											    # Ref NVLink 2.0 bare metal AC922: pico ~100-130 GB/s (busbw)
																																											        # Via VFIO esperado: ~60-80% do bare metal (~60-66 GB/s medido)
																																												    log ""
																																												        log "=== GPU — NCCL ALL_REDUCE (NVLink inter-GPU) ==="
																																													    log "--- -b 8 -e 256M -f 2 -g 2 ---"
																																													        "$NCCL_BIN/all_reduce_perf" \
																																															        -b 8 -e 256M -f 2 -g 2 \
																																																        2>&1 | tee -a "$OUT" || log "[AVISO] all_reduce_perf falhou."

																																														    # --- NCCL all_gather -----------------------------------------------------
																																														        log ""
																																															    log "=== GPU — NCCL ALL_GATHER (NVLink inter-GPU) ==="
																																															        log "--- -b 8 -e 256M -f 2 -g 2 ---"
																																																    "$NCCL_BIN/all_gather_perf" \
																																																	            -b 8 -e 256M -f 2 -g 2 \
																																																		            2>&1 | tee -a "$OUT" || log "[AVISO] all_gather_perf falhou."

																																																        # --- NCCL sendrecv — latência ponto a ponto ------------------------------
																																																	    # Latência baixa = NVLink ativo; latência alta = fallback PCIe
																																																	        log ""
																																																		    log "=== GPU — NCCL SENDRECV (latência GPU0↔GPU1) ==="
																																																		        log "--- -b 8 -e 4M -f 2 -g 2 ---"
																																																			    "$NCCL_BIN/sendrecv_perf" \
																																																				            -b 8 -e 4M -f 2 -g 2 \
																																																					            2>&1 | tee -a "$OUT" || log "[AVISO] sendrecv_perf falhou."

																																																			        # --- Estado pós-benchmark ------------------------------------------------
																																																				    log ""
																																																				        log "=== GPU — ESTADO PÓS-BENCHMARK ==="
																																																					    nvidia-smi --query-gpu=index,name,temperature.gpu,power.draw,\
																																																						    memory.used,memory.total,utilization.gpu \
																																																						            --format=csv,noheader 2>&1 | tee -a "$OUT" || true
																																																				    }

																																																				    # =============================================================================
																																																				    # CABEÇALHO
																																																				    # =============================================================================
																																																				    log "=== BENCHMARK VM: $HOST — $DATE ==="
																																																				    log "Ambiente: VM KVM — IBM Power9 ppc64le"
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
																																																					    --cache-level 1 \
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
																																																						# GPU (CUDA / NCCL)
																																																						# =============================================================================
																																																						log "=== GPU ==="
																																																						run_gpu_suite
																																																						separator

																																																						# =============================================================================
																																																						# FIM
																																																						# =============================================================================
																																																						log ""
																																																						log "=== FIM ==="
																																																						log "Resultados salvos em: $OUT"
