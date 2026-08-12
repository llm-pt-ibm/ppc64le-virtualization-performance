# =============================================================================
# slide_overview.R — consolidated panel for a presentation slide.
#
# Key difference from a single bar chart: uses facet_wrap with
# scales = "free_x", giving EACH domain (CPU/Memory, Cache, STREAM,
# Disk, GPU) its own X axis. Without this, QEMU's -93% on CPU/memory
# forces a huge shared axis, and GPU's +0.6% becomes an invisible mark
# next to it — the same principle already used in the boxplots of
# analyze_benchmarks.py (_plot_metrica), applied here to the bar chart.
#
# Usage (run from the repo root, after the CSVs have already been
# generated — same files used by generate_plots.R):
#   Rscript src/analysis/slide_overview.R
#
# Output: results/figures/slide_overview.png
# =============================================================================
library(ggplot2)
library(dplyr)
library(readr)
library(tibble)

COR_KVM  <- "#0072B2"
COR_QEMU <- "#E69F00"

ARQ_KVM  <- "data/processed/kvm_summary.csv"
ARQ_QEMU <- "data/processed/qemu_summary.csv"

carrega_csv <- function(arq, ambiente_esperado) {
    if (!file.exists(arq)) stop(sprintf("File not found: %s", arq))
    df <- read_csv(arq, show_col_types = FALSE)
    df$ambiente <- ambiente_esperado
    df
}

dados_brutos <- bind_rows(
    carrega_csv(ARQ_KVM, "KVM"),
    carrega_csv(ARQ_QEMU, "QEMU")
) %>%
    filter(!is.na(overhead_pct))

# --- metrica_id -> domain + short display label mapping ---
# Note: uses lat_read_iops (IOPS at QD=1) instead of lat_clat_avg_us (µs),
# to keep "negative = worse" consistent across the whole panel — latency
# in µs has an inverted sign (positive = worse) and would confuse the reading.
mapa <- tribble(
    ~metrica_id,       ~grupo,        ~rotulo,
    "cpu_bogo",        "CPU / Memory", "CPU",
    "mem_bogo",        "CPU / Memory", "Memory",
    "cache_bogo",      "Cache",        "Cache",
    "stream_copy",     "STREAM",       "Copy",
    "stream_scale",    "STREAM",       "Scale",
    "stream_add",      "STREAM",       "Add",
    "stream_triad",    "STREAM",       "Triad",
    "seq_read_bw",     "Disk",         "Seq. read",
    "seq_write_bw",    "Disk",         "Seq. write",
    "rand_read_iops",  "Disk",         "Random IOPS read",
    "rand_write_iops", "Disk",         "Random IOPS write",
    "lat_read_iops",   "Disk",         "IOPS QD=1",
    "matrix_gflops",   "GPU",          "matrixMul",
    "nccl_allreduce",  "GPU",          "NCCL all_reduce",
    "nccl_allgather",  "GPU",          "NCCL all_gather",
    "nccl_sendrecv",   "GPU",          "NCCL sendrecv"
)

dados <- dados_brutos %>%
    inner_join(mapa, by = "metrica_id") %>%
    # STREAM and GPU under QEMU: STREAM is an invalid measurement under
    # TCG (timer without reliable resolution, comes out 0), GPU is out of
    # scope (no passthrough). Without this filter, QEMU would show a
    # misleading "-100%" on these rows.
    filter(!(ambiente == "QEMU" & grupo %in% c("STREAM", "GPU"))) %>%
    mutate(
        grupo    = factor(grupo, levels = c("CPU / Memory", "Cache", "STREAM", "Disk", "GPU")),
        ambiente = factor(ambiente, levels = c("KVM", "QEMU"))
    )

# Orders each label by the mean overhead magnitude within its group —
# faster to read: from largest overhead to smallest, top to bottom.
ordem_rotulos <- dados %>%
    group_by(grupo, rotulo) %>%
    summarise(ord = mean(overhead_pct), .groups = "drop") %>%
    arrange(grupo, ord) %>%
    pull(rotulo)
dados <- dados %>% mutate(rotulo = factor(rotulo, levels = unique(ordem_rotulos)))

p <- ggplot(dados, aes(x = overhead_pct, y = rotulo, fill = ambiente)) +
    geom_vline(xintercept = 0, color = "black", linewidth = 0.4) +
    geom_col(position = position_dodge(width = 0.7), width = 0.6) +
    geom_text(
        aes(label = sprintf("%+.1f%%", overhead_pct),
            hjust = ifelse(overhead_pct >= 0, -0.15, 1.15)),
        position = position_dodge(width = 0.7), size = 3.0, fontface = "bold"
    ) +
    facet_wrap(~grupo, scales = "free", ncol = 3) +
    scale_fill_manual(values = c(KVM = COR_KVM, QEMU = COR_QEMU)) +
    scale_x_continuous(expand = expansion(mult = 0.28)) +
    labs(
        x = "Variation relative to host (%) — host with matched pinning",
        y = NULL,
        title = "Consolidated overview — overhead vs. host"
    ) +
    theme_classic(base_size = 11) +
    theme(
        strip.background   = element_rect(fill = "grey92", color = NA),
        strip.text         = element_text(face = "bold", size = 10.5),
        legend.position    = "bottom",
        legend.title       = element_blank(),
        legend.text        = element_text(size = 10),
        panel.grid.major.x = element_line(color = "grey90", linewidth = 0.3),
        panel.grid.major.y = element_blank(),
        plot.title         = element_text(size = 13, face = "bold"),
        axis.text.y        = element_text(size = 9.5)
    )

dir.create("results/figures", showWarnings = FALSE, recursive = TRUE)
ggsave("results/figures/slide_overview.png", p, width = 12, height = 6.5, dpi = 220, bg = "white")
message("Saved to results/figures/slide_overview.png")
