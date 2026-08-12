# =============================================================================
# generate_plots.R — reads the summary CSVs and generates the overhead figures.
#
# Expected flow (run from the repo root):
#
#   1. Generate the CSVs with Python (the existing data/raw/ logs still use
#      the Portuguese disk labels DISCO_HOST/DISCO_EXT4, since raw logs are
#      never rewritten after collection — new logs from the current, English
#      collection scripts would use DISK_HOST/DISK_EXT4 instead):
#      python3 benchmark_summary_overhead.py \
#          --pattern "data/raw/kvm-*/benchmark_vm_localhost.localdomain_*" \
#          --baseline "data/raw/host/benchmark_host_ibm-power9_20260724_0*" \
#          --disk-label "DISCO_EXT4" \
#          --baseline-disk-label "DISCO_HOST" \
#          --label "KVM" \
#          --output-csv data/processed/kvm_summary.csv
#
#      python3 benchmark_summary_qemu.py \
#          --pattern "data/raw/qemu-*/benchmark_qemu_*" \
#          --baseline "data/raw/host/benchmark_host_ibm-power9_20260724_0*" \
#          --disk-label "DISCO_EXT4" \
#          --baseline-disk-label "DISCO_HOST" \
#          --output-csv data/processed/qemu_summary.csv
#
#   2. Run this script:
#      Rscript src/analysis/generate_plots.R
#
# The CSVs must be in data/processed/. If they're elsewhere, adjust
# ARQ_KVM / ARQ_QEMU below.
# =============================================================================
library(ggplot2)
library(dplyr)
library(tidyr)
library(readr)

# --- CSV paths ---
ARQ_KVM  <- "data/processed/kvm_summary.csv"
ARQ_QEMU <- "data/processed/qemu_summary.csv"

# --- Visual configuration ---
COR_KVM  <- "#0072B2"
COR_QEMU <- "#E69F00"

tema_artigo <- function() {
    theme_classic(base_size = 10) +
        theme(
            axis.title          = element_text(size = 9),
            axis.text           = element_text(size = 8, color = "black"),
            legend.title        = element_blank(),
            legend.text         = element_text(size = 8),
            legend.position     = "bottom",
            panel.grid.major.x  = element_line(color = "grey95", linewidth = 0.3),
            panel.grid.major.y  = element_blank(),
            strip.background    = element_blank(),
            plot.margin         = margin(5, 12, 5, 5)
        )
}

faixa_relevancia <- function() {
    annotate("rect", xmin = -5, xmax = 5, ymin = -Inf, ymax = Inf, fill = "grey90", alpha = 0.5)
}

linha_zero <- function() {
    geom_vline(xintercept = 0, color = "black", linewidth = 0.5)
}

rotulos_percentuais <- function(is_dodge = TRUE, dodge_width = 0.7) {
    if (is_dodge) {
        geom_text(
            aes(label = sprintf("%+.2f%%", variacao),
                hjust = ifelse(variacao >= 0, -0.15, 1.15)),
            position = position_dodge(width = dodge_width),
            size = 2.5, fontface = "bold"
        )
    } else {
        geom_text(
            aes(label = sprintf("%+.2f%%", variacao),
                hjust = ifelse(variacao >= 0, -0.3, 1.3)),
            size = 2.5, fontface = "bold"
        )
    }
}

escala_x_percentual <- function(breaks_seq, limites) {
    scale_x_continuous(breaks = breaks_seq, limits = limites)
}

# --- Loads and combines the two CSVs ---
carrega_csv <- function(arq, ambiente_esperado) {
    if (!file.exists(arq)) {
        warning(sprintf("File not found: %s. Skipping %s.", arq, ambiente_esperado))
        return(NULL)
    }
    df <- read_csv(arq, show_col_types = FALSE)
    df$ambiente <- ambiente_esperado  # ensures label consistency
    df
}

dados <- bind_rows(
    carrega_csv(ARQ_KVM,  "KVM"),
    carrega_csv(ARQ_QEMU, "QEMU")
) %>%
    filter(!is.na(overhead_pct)) %>%
    mutate(variacao = as.numeric(overhead_pct))

if (nrow(dados) == 0) {
    stop("No data loaded. Check that the CSVs exist and have an overhead_pct column.")
}

# ---------------------------------------------------------------------------
# Utility: builds a tibble in the format each figure expects, from a list
# of metrica_id + display label, preserving order.
# ---------------------------------------------------------------------------
monta_dados <- function(ids_e_rotulos, ambientes = c("KVM", "QEMU")) {
    dados %>%
        filter(metrica_id %in% names(ids_e_rotulos), ambiente %in% ambientes) %>%
        mutate(
            metrica = factor(
                ids_e_rotulos[metrica_id],
                levels = rev(unname(ids_e_rotulos))
            ),
            ambiente = factor(ambiente, levels = ambientes)
        ) %>%
        select(metrica, ambiente, variacao)
}

# --- FIGURE 1: Disk ---
ids_disco <- c(
    seq_read_bw      = "Seq. read",
    seq_write_bw     = "Seq. write",
    rand_read_iops   = "Random read IOPS",
    rand_write_iops  = "Random write IOPS",
    lat_read_iops    = "Latency IOPS (read)",
    lat_clat_avg_us  = "Average latency"
)
disco <- monta_dados(ids_disco)

fig_disco <- ggplot(disco, aes(x = variacao, y = metrica, fill = ambiente)) +
    faixa_relevancia() +
    geom_col(position = position_dodge(width = 0.7), width = 0.6) +
    linha_zero() +
    scale_fill_manual(values = c("KVM" = COR_KVM, "QEMU" = COR_QEMU)) +
    escala_x_percentual(seq(-100, 20, by = 20), c(-100, 25)) +
    rotulos_percentuais() +
    labs(x = "Variation Relative to Host (%)", y = NULL) +
    tema_artigo()

# --- FIGURE 2: GPU (KVM only — QEMU has no GPU) ---
ids_gpu <- c(
    matrix_gflops   = "matrixMul (GFlop/s)",
    nccl_allreduce  = "NCCL all_reduce",
    nccl_allgather  = "NCCL all_gather",
    nccl_sendrecv   = "NCCL sendrecv"
)
gpu <- monta_dados(ids_gpu, ambientes = "KVM")

fig_gpu <- ggplot(gpu, aes(x = variacao, y = metrica)) +
    faixa_relevancia() +
    geom_segment(aes(x = 0, xend = variacao, y = metrica, yend = metrica), color = "grey70", linewidth = 0.5) +
    geom_point(color = COR_KVM, size = 3) +
    linha_zero() +
    escala_x_percentual(seq(-5, 5, by = 2.5), c(-5, 5)) +
    rotulos_percentuais(is_dodge = FALSE) +
    labs(x = "Variation Relative to Host (%)", y = NULL) +
    tema_artigo() +
    theme(legend.position = "none")

# --- FIGURE 3: CPU and Memory ---
ids_cpu_mem <- c(
    cpu_bogo = "CPU (bogo ops/s)",
    mem_bogo = "Memory (bogo ops/s)"
)
cpu_mem <- monta_dados(ids_cpu_mem)

fig_cpu_mem <- ggplot(cpu_mem, aes(x = variacao, y = metrica, fill = ambiente)) +
    faixa_relevancia() +
    geom_col(position = position_dodge(width = 0.7), width = 0.6) +
    linha_zero() +
    scale_fill_manual(values = c("KVM" = COR_KVM, "QEMU" = COR_QEMU)) +
    escala_x_percentual(seq(-100, 0, by = 20), c(-105, 10)) +
    rotulos_percentuais() +
    labs(x = "Variation Relative to Host (%)", y = NULL) +
    tema_artigo()

# --- FIGURE 4: STREAM (KVM only — QEMU/TCG doesn't run STREAM reliably) ---
ids_stream <- c(
    stream_copy  = "Copy",
    stream_scale = "Scale",
    stream_add   = "Add",
    stream_triad = "Triad"
)
stream <- monta_dados(ids_stream, ambientes = "KVM")

fig_stream <- ggplot(stream, aes(x = variacao, y = metrica)) +
    faixa_relevancia() +
    geom_segment(aes(x = 0, xend = variacao, y = metrica, yend = metrica), color = "grey70", linewidth = 0.6) +
    geom_point(color = COR_KVM, size = 3) +
    linha_zero() +
    escala_x_percentual(seq(-40, 0, by = 10), c(-45, 5)) +
    rotulos_percentuais(is_dodge = FALSE) +
    labs(x = "KVM Variation Relative to Host (%)", y = NULL) +
    tema_artigo() +
    theme(legend.position = "none")

# --- FIGURE 5: Cache ---
ids_cache <- c(cache_bogo = "Cache (bogo ops/s)")
cache_df <- monta_dados(ids_cache)

fig_cache <- ggplot(cache_df, aes(x = variacao, y = metrica, fill = ambiente)) +
    faixa_relevancia() +
    geom_col(position = position_dodge(width = 0.7), width = 0.6) +
    linha_zero() +
    scale_fill_manual(values = c("KVM" = COR_KVM, "QEMU" = COR_QEMU)) +
    escala_x_percentual(seq(-100, 0, by = 20), c(-105, 5)) +
    rotulos_percentuais() +
    labs(x = "Variation Relative to Host (%)", y = NULL) +
    tema_artigo()

# --- EXPORT ---
largura_1col  <- 3.33
largura_2col  <- 6.99
altura_padrao <- 2.5

dir.create("results/figures", showWarnings = FALSE, recursive = TRUE)

ggsave("results/figures/fig1_disk_variation.pdf",    fig_disco,   width = largura_2col, height = altura_padrao, device = "pdf")
ggsave("results/figures/fig2_gpu_variation.pdf",     fig_gpu,     width = largura_1col, height = altura_padrao, device = "pdf")
ggsave("results/figures/fig3_cpu_mem_variation.pdf", fig_cpu_mem, width = largura_1col, height = altura_padrao, device = "pdf")
ggsave("results/figures/fig4_stream_variation.pdf",  fig_stream,  width = largura_1col, height = altura_padrao, device = "pdf")
ggsave("results/figures/fig5_cache_variation.pdf",   fig_cache,   width = largura_1col, height = altura_padrao, device = "pdf")

message("Figures saved to results/figures/")
