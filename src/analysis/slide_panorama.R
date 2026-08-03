# =============================================================================
# slide_panorama.R — painel consolidado pra slide de apresentação.
#
# Diferença-chave em relação a um bar chart único: usa facet_wrap com
# scales = "free_x", dando a CADA domínio (CPU/Memória, Cache, STREAM,
# Disco, GPU) seu próprio eixo X. Sem isso, o -93% do QEMU em CPU/memória
# obriga um eixo compartilhado enorme, e o +0.6% da GPU vira um traço
# invisível ao lado dele — mesmo princípio já usado nos boxplots de
# analise_benchmarks.py (_plot_metrica), aplicado aqui pro gráfico de barras.
#
# Uso (rodar da raiz do repo, depois de já ter gerado os CSVs — mesmos
# arquivos usados por geracao_graficos.R):
#   Rscript src/analysis/slide_panorama.R
#
# Saída: results/figures/slide_panorama.png
# =============================================================================
library(ggplot2)
library(dplyr)
library(readr)
library(tibble)

COR_KVM  <- "#0072B2"
COR_QEMU <- "#E69F00"

ARQ_KVM  <- "data/processed/resumo_kvm.csv"
ARQ_QEMU <- "data/processed/resumo_qemu.csv"

carrega_csv <- function(arq, ambiente_esperado) {
    if (!file.exists(arq)) stop(sprintf("Arquivo não encontrado: %s", arq))
    df <- read_csv(arq, show_col_types = FALSE)
    df$ambiente <- ambiente_esperado
    df
}

dados_brutos <- bind_rows(
    carrega_csv(ARQ_KVM, "KVM"),
    carrega_csv(ARQ_QEMU, "QEMU")
) %>%
    filter(!is.na(overhead_pct))

# --- Mapeamento metrica_id -> domínio + rótulo curto pra exibição ---
# Nota: usa lat_read_iops (IOPS em QD=1) em vez de lat_clat_avg_us (µs),
# pra manter "negativo = pior" consistente em todo o painel — latência em
# µs tem sinal invertido (positivo = pior) e confundiria a leitura.
mapa <- tribble(
    ~metrica_id,       ~grupo,          ~rotulo,
    "cpu_bogo",        "CPU / Memória", "CPU",
    "mem_bogo",        "CPU / Memória", "Memória",
    "cache_bogo",      "Cache",         "Cache",
    "stream_copy",     "STREAM",        "Copy",
    "stream_scale",    "STREAM",        "Scale",
    "stream_add",      "STREAM",        "Add",
    "stream_triad",    "STREAM",        "Triad",
    "seq_read_bw",     "Disco",         "Leitura seq.",
    "seq_write_bw",    "Disco",         "Escrita seq.",
    "rand_read_iops",  "Disco",         "IOPS aleat. leitura",
    "rand_write_iops", "Disco",         "IOPS aleat. escrita",
    "lat_read_iops",   "Disco",         "IOPS QD=1",
    "matrix_gflops",   "GPU",           "matrixMul",
    "nccl_allreduce",  "GPU",           "NCCL all_reduce",
    "nccl_allgather",  "GPU",           "NCCL all_gather",
    "nccl_sendrecv",   "GPU",           "NCCL sendrecv"
)

dados <- dados_brutos %>%
    inner_join(mapa, by = "metrica_id") %>%
    # STREAM e GPU sob QEMU: STREAM é medição inválida sob TCG (timer sem
    # resolução confiável, sai 0), GPU é fora de escopo (sem passthrough).
    # Sem este filtro, apareceria um "-100%" enganoso pro QEMU nessas linhas.
    filter(!(ambiente == "QEMU" & grupo %in% c("STREAM", "GPU"))) %>%
    mutate(
        grupo    = factor(grupo, levels = c("CPU / Memória", "Cache", "STREAM", "Disco", "GPU")),
        ambiente = factor(ambiente, levels = c("KVM", "QEMU"))
    )

# Ordena cada rótulo pela magnitude média do overhead dentro do seu grupo —
# leitura mais rápida: do maior overhead pro menor, de cima pra baixo.
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
        x = "Variação relativa ao host (%) — host com pinning equalizado",
        y = NULL,
        title = "Panorama consolidado — overhead vs. host"
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
ggsave("results/figures/slide_panorama.png", p, width = 12, height = 6.5, dpi = 220, bg = "white")
message("Salvo em results/figures/slide_panorama.png")
