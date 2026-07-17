# Os percentuais de variação abaixo (tibbles `disco`, `gpu`, `cpu_mem`,
# `stream`) são transcritos manualmente a partir de
# data/processed/resumo_descritivo.csv / dados_extraidos.csv — este script
# NÃO lê os CSVs automaticamente. Se data/raw/ for atualizado, recalcule e
# atualize os tibbles abaixo manualmente antes de regerar as figuras.
library(ggplot2)
library(dplyr)
library(tidyr)

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
    scale_x_continuous(
        breaks = breaks_seq,
        limits = limites
    )
}

# --- FIGURA 1: Disco ---
disco <- tibble(
    metrica = factor(
        c("Leitura seq.", "Escrita seq.", "IOPS rand. leitura", "IOPS rand. escrita", "Latência IOPS (read)", "Latência média"),
        levels = rev(c("Leitura seq.", "Escrita seq.", "IOPS rand. leitura", "IOPS rand. escrita", "Latência IOPS (read)", "Latência média"))
    ),
    KVM  = c(-3.43, -1.70, -23.12, -22.89, 8.12, -1.24),
    QEMU = c(-2.32, -5.07, -82.17, -82.09, -27.26, -2.39)
) %>%
    pivot_longer(cols = c(KVM, QEMU), names_to = "ambiente", values_to = "variacao") %>%
    mutate(ambiente = factor(ambiente, levels = c("KVM", "QEMU")))

fig_disco <- ggplot(disco, aes(x = variacao, y = metrica, fill = ambiente)) +
    faixa_relevancia() +
    geom_col(position = position_dodge(width = 0.7), width = 0.6) +
    linha_zero() +
    scale_fill_manual(values = c("KVM" = COR_KVM, "QEMU" = COR_QEMU)) +
    escala_x_percentual(seq(-100, 20, by = 20), c(-100, 25)) + 
    rotulos_percentuais() +
    labs(x = "Variação Relativa ao Host (%)", y = NULL) +
    tema_artigo()

# --- FIGURA 2: GPU ---
gpu <- tibble(
    metrica = factor(
        c("matrixMul (GFlop/s)", "NCCL all_reduce", "NCCL all_gather", "NCCL sendrecv"),
        levels = rev(c("matrixMul (GFlop/s)", "NCCL all_reduce", "NCCL all_gather", "NCCL sendrecv"))
    ),
    variacao = c(2.24, 0.49, -0.02, 0.04)
)

fig_gpu <- ggplot(gpu, aes(x = variacao, y = metrica)) +
    faixa_relevancia() +
    geom_segment(aes(x = 0, xend = variacao, y = metrica, yend = metrica), color = "grey70", linewidth = 0.5) +
    geom_point(color = COR_KVM, size = 3) +
    linha_zero() +
    escala_x_percentual(seq(-5, 5, by = 2.5), c(-5, 5)) +
    rotulos_percentuais(is_dodge = FALSE) +
    labs(x = "Variação Relativa ao Host (%)", y = NULL) +
    tema_artigo() +
    theme(legend.position = "none")

# --- FIGURA 3: CPU e Memória ---
cpu_mem <- tibble(
    metrica = factor(
        c("CPU (bogo ops/s)", "Memória (bogo ops/s)"),
        levels = rev(c("CPU (bogo ops/s)", "Memória (bogo ops/s)"))
    ),
    KVM  = c(-0.37, -17.11),
    QEMU = c(-93.52, -92.74)
) %>%
    pivot_longer(cols = c(KVM, QEMU), names_to = "ambiente", values_to = "variacao") %>%
    mutate(ambiente = factor(ambiente, levels = c("KVM", "QEMU")))

fig_cpu_mem <- ggplot(cpu_mem, aes(x = variacao, y = metrica, fill = ambiente)) +
    faixa_relevancia() +
    geom_col(position = position_dodge(width = 0.7), width = 0.6) +
    linha_zero() +
    scale_fill_manual(values = c("KVM" = COR_KVM, "QEMU" = COR_QEMU)) +
    escala_x_percentual(seq(-100, 0, by = 20), c(-105, 10)) +
    rotulos_percentuais() +
    labs(x = "Variação Relativa ao Host (%)", y = NULL) +
    tema_artigo()

# --- FIGURA 4: STREAM ---
stream <- tibble(
    metrica = factor(
        c("Copy", "Scale", "Add", "Triad"),
        levels = rev(c("Copy", "Scale", "Add", "Triad"))
    ),
    variacao = c(-29.10, -32.88, -37.36, -36.07)
)

fig_stream <- ggplot(stream, aes(x = variacao, y = metrica)) +
    faixa_relevancia() +
    geom_segment(aes(x = 0, xend = variacao, y = metrica, yend = metrica), color = "grey70", linewidth = 0.6) +
    geom_point(color = COR_KVM, size = 3) +
    linha_zero() +
    escala_x_percentual(seq(-40, 0, by = 10), c(-45, 5)) +
    rotulos_percentuais(is_dodge = FALSE) +
    labs(x = "Variação KVM Relativa ao Host (%)", y = NULL) +
    tema_artigo() +
    theme(legend.position = "none")

# --- EXPORTAÇÃO ---
# Caminhos ATUALIZADOS na reorganização de 2026-07-15: os PDFs iam para o
# diretório de trabalho (raiz do repo); agora vão explicitamente para
# results/figures/. Caminho relativo -> rode este script com o diretório de
# trabalho == raiz do repo, ex.: `Rscript src/analysis/geracao_graficos.R`
# a partir da raiz (ver docs/REPRODUCE.md).
largura_1col  <- 3.33
largura_2col  <- 6.99
altura_padrao <- 2.5

dir.create("results/figures", showWarnings = FALSE, recursive = TRUE)

ggsave("results/figures/fig1_variacao_disco.pdf",   fig_disco,   width = largura_2col, height = altura_padrao, device = "pdf")
ggsave("results/figures/fig2_variacao_gpu.pdf",     fig_gpu,     width = largura_1col, height = altura_padrao, device = "pdf")
ggsave("results/figures/fig3_variacao_cpu_mem.pdf", fig_cpu_mem, width = largura_1col, height = altura_padrao, device = "pdf")
ggsave("results/figures/fig4_variacao_stream.pdf",  fig_stream,  width = largura_1col, height = altura_padrao, device = "pdf")