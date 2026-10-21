# =============================================================================
# generate_plots_4c8g.R — overhead figures for the 4c8g experiment
# (ppc64le x x86_64, 4 vCPU / 8 GiB), in the paper's visual format.
#
# The theme helpers below are copied from generate_plots.R (not sourced:
# sourcing it would regenerate the paper figures). Differences: one facet
# per environment with both architectures side by side (free x per panel),
# thin 95% CI error bars, labels at the CI end, captions.
#
# Run from the repo root, after the summary CSVs exist:
#   python3 src/analysis/summary_4c8g.py
#   Rscript src/analysis/generate_plots_4c8g.R
#
# Output: results/figures/4c8g/
#   fig1_disk_variation_4c8g.pdf, fig3_cpu_mem_cache_variation_4c8g.pdf,
#   fig4_stream_variation_4c8g.pdf, fig6_arch_comparison_4c8g.pdf
# =============================================================================
library(ggplot2)
library(dplyr)
library(tidyr)
library(readr)

DIR_CSV <- "data/processed/4c8g"
DIR_FIG <- "results/figures/4c8g"
ARQS    <- c(p9 = "ppc64le", x86 = "x86_64")

# --- Visual configuration (same palette as generate_plots.R) ---
COR_KVM  <- "#0072B2"
COR_QEMU <- "#E69F00"
COR_ARQ  <- c("ppc64le" = COR_KVM, "x86_64" = COR_QEMU)

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
            plot.margin         = margin(5, 12, 5, 5),
            plot.caption        = element_text(size = 7, color = "grey30", hjust = 0),
            plot.caption.position = "plot"
        )
}

faixa_relevancia <- function() {
    annotate("rect", xmin = -5, xmax = 5, ymin = -Inf, ymax = Inf, fill = "grey90", alpha = 0.5)
}

linha_zero <- function() {
    geom_vline(xintercept = 0, color = "black", linewidth = 0.5)
}

pct <- function(x) sub(".", ",", sprintf("%+.2f%%", x), fixed = TRUE)

# Labels sit past the CI end so they don't overlap the error bar;
# folga: for lollipops, where the point can be wider than a narrow CI.
rotulos_percentuais <- function(dodge_width = 0.7, folga = 0) {
    geom_text(
        aes(x = ifelse(variacao >= 0, pmax(ic95_sup, variacao + folga), pmin(ic95_inf, variacao - folga)),
            label = pct(variacao), hjust = ifelse(variacao >= 0, -0.15, 1.15)),
        position = position_dodge(width = dodge_width), size = 2.5, fontface = "bold"
    )
}

barras_ic <- function(dodge_width = 0.7) {
    geom_errorbar(aes(xmin = ic95_inf, xmax = ic95_sup), position = position_dodge(width = dodge_width),
                  width = 0.25, linewidth = 0.3, color = "grey20", orientation = "y")
}

legenda <- function(...) paste(strwrap(paste(...), width = 145), collapse = "\n")

LEGENDA_BASE <- "Média; IC95% bootstrap."
NOTA_FREE_X  <- "Escala do eixo x própria de cada painel."

# --- Loads the KVM/QEMU summaries of both architectures ---
carrega_csv <- function(arq, amb) {
    f <- file.path(DIR_CSV, arq, sprintf("%s_summary.csv", tolower(amb)))
    if (!file.exists(f)) stop(sprintf("File not found: %s (run summary_4c8g.py first)", f))
    df <- read_csv(f, show_col_types = FALSE)
    df$ambiente <- amb
    df$arquitetura <- ARQS[[arq]]
    df
}

dados <- bind_rows(lapply(names(ARQS), function(a) bind_rows(carrega_csv(a, "KVM"), carrega_csv(a, "QEMU")))) %>%
    filter(!is.na(overhead_pct)) %>%
    mutate(variacao = as.numeric(overhead_pct),
           arquitetura = factor(arquitetura, levels = unname(ARQS)),
           ambiente = factor(ifelse(ambiente == "QEMU", "QEMU (TCG)", ambiente), levels = c("KVM", "QEMU (TCG)")))

# One facet per environment, bars (or lollipops) = architecture.
fig_por_amb <- function(ids_e_rotulos, legenda, ambientes = c("KVM", "QEMU (TCG)"), lollipop = FALSE,
                        rotulo_x = "Variação em relação ao host (%)") {
    df <- dados %>%
        filter(metrica_id %in% names(ids_e_rotulos), ambiente %in% ambientes) %>%
        mutate(metrica = factor(ids_e_rotulos[metrica_id], levels = rev(unname(ids_e_rotulos))),
               ambiente = factor(ambiente, levels = ambientes))
    sem_dado <- expand_grid(metrica = factor(unname(ids_e_rotulos), levels = levels(df$metrica)),
                            ambiente = factor(ambientes, levels = ambientes)) %>%
        anti_join(distinct(df, metrica, ambiente), by = c("metrica", "ambiente"))
    dodge <- if (lollipop) 0.6 else 0.7

    p <- ggplot(df, aes(x = variacao, y = metrica, group = arquitetura)) + faixa_relevancia()
    if (lollipop) {
        p <- p +
            geom_linerange(aes(xmin = 0, xmax = variacao), position = position_dodge(width = dodge),
                           color = "grey70", linewidth = 0.6, orientation = "y") +
            barras_ic(dodge) +
            geom_point(aes(color = arquitetura), position = position_dodge(width = dodge), size = 2.5) +
            scale_color_manual(values = COR_ARQ)
    } else {
        p <- p +
            geom_col(aes(fill = arquitetura), position = position_dodge(width = dodge), width = 0.6) +
            barras_ic(dodge) +
            scale_fill_manual(values = COR_ARQ)
    }
    p +
        linha_zero() +
        geom_text(data = sem_dado, aes(x = 0, y = metrica, label = "sem execução válida"),
                  inherit.aes = FALSE, hjust = 1.05, size = 2.3, color = "grey40", fontface = "italic") +
        facet_wrap(~ambiente, nrow = 1, scales = "free_x", drop = FALSE) +
        scale_x_continuous(expand = expansion(mult = 0.3),
                           labels = function(x) sub(".", ",", format(x, trim = TRUE), fixed = TRUE)) +
        rotulos_percentuais(dodge, folga = if (lollipop) 0.25 else 0) +
        labs(x = rotulo_x, y = NULL, caption = legenda) +
        tema_artigo() +
        theme(panel.spacing.x = unit(1.5, "lines"))  # free x: keeps tick labels of adjacent panels apart
}

# --- FIGURE 1: Disk (IOPS QD=1 instead of latency in µs; latency in the table) ---
ids_disco <- c(
    seq_read_bw      = "Leitura seq.",
    seq_write_bw     = "Escrita seq.",
    rand_read_iops   = "IOPS aleatório (leitura)",
    rand_write_iops  = "IOPS aleatório (escrita)",
    lat_read_iops    = "IOPS QD=1"
)
fig_disco <- fig_por_amb(ids_disco, legenda(LEGENDA_BASE,
    "QD=1 no ppc64le sensível à sessão do host (ver ameaças à validade).",
    "No x86_64, o arquivo de teste do host fica em ext4 e o do convidado em XFS (mesmo SSD); valores positivos",
    "do KVM/QEMU em disco no x86 refletem essa diferença, não ganho de virtualização.", NOTA_FREE_X))

# --- FIGURE 3: CPU (cpu_subset series), Memory and Cache ---
ids_cpu_mem_cache <- c(
    cpu_subset80 = "CPU (80 métodos comuns)",
    cpu_subset76 = "CPU (sem métodos de libm)",
    mem_bogo     = "Memória",
    cache_bogo   = "Cache"
)
fig_cpu_mem_cache <- fig_por_amb(ids_cpu_mem_cache, legenda(LEGENDA_BASE,
    "CPU medida em série separada (subconjunto de métodos do stress-ng executáveis sob TCG nas duas arquiteturas;",
    "1 aquecimento + 15 execuções por ambiente): média geométrica por execução dos 80 métodos comuns;",
    "\"sem métodos de libm\" exclui explog, zeta, pi e factorial.",
    "Cache: distribuição bimodal no KVM ppc64le; ver mediana na tabela.", NOTA_FREE_X))

# --- FIGURE 4: STREAM (KVM only) ---
ids_stream <- c(
    stream_copy  = "Copy",
    stream_scale = "Scale",
    stream_add   = "Add",
    stream_triad = "Triad"
)
fig_stream <- fig_por_amb(ids_stream, ambientes = "KVM", lollipop = TRUE,
    rotulo_x = "Variação do KVM em relação ao host (%)",
    legenda = legenda(LEGENDA_BASE, "QEMU (TCG): STREAM sem execução válida (instruções vetoriais não emuladas)."))

# --- FIGURE 6: cross-architecture summary ---
ids_resumo <- c(
    cpu_subset80    = "CPU (80 métodos comuns)",
    mem_bogo        = "Memória",
    cache_bogo      = "Cache",
    stream_triad    = "STREAM Triad",
    seq_read_bw     = "Leitura seq.",
    seq_write_bw    = "Escrita seq.",
    rand_read_iops  = "IOPS aleatório (leitura)",
    lat_read_iops   = "IOPS QD=1"
)
fig_resumo <- fig_por_amb(ids_resumo, legenda(LEGENDA_BASE, NOTA_FREE_X))

# --- EXPORT ---
largura_2col <- 6.99

dir.create(DIR_FIG, showWarnings = FALSE, recursive = TRUE)
salva <- function(nome, fig, altura) {
    ggsave(file.path(DIR_FIG, nome), fig, width = largura_2col, height = altura, device = "pdf")
}

salva("fig1_disk_variation_4c8g.pdf",          fig_disco,         3.4)
salva("fig3_cpu_mem_cache_variation_4c8g.pdf", fig_cpu_mem_cache, 3.3)
salva("fig4_stream_variation_4c8g.pdf",        fig_stream,        2.4)
salva("fig6_arch_comparison_4c8g.pdf",         fig_resumo,        3.4)

message(sprintf("Figures saved to %s/", DIR_FIG))
