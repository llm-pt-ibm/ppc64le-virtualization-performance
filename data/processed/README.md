# data/processed/

Saídas geradas por `python3 src/analysis/analise_benchmarks.py --logs-dir
data/raw --out-dir data/processed` (ver [docs/REPRODUCE.md](../../docs/REPRODUCE.md)).
Este arquivo não é sobrescrito pelo pipeline — só os CSVs/JSON abaixo são.

- `dados_extraidos.csv` — uma linha por (arquivo, métrica), formato tidy.
- `resumo_descritivo.csv` — média, mediana, desvio, IQR por métrica × ambiente.
- `resultados_estatisticos.csv` — normalidade (Shapiro-Wilk), homogeneidade
  (Levene), teste global (ANOVA/Kruskal-Wallis) com `p_fdr_corrigido`
  (Benjamini-Hochberg entre todas as métricas), post-hoc de Dunn, effect
  size, IC bootstrap.
- `heterogeneidade_entre_vms.csv` — testa se as 3 VMs de origem de cada
  tratamento (KVM/QEMU) diferem entre si por métrica (ver
  [docs/GLOSSARIO_METODOLOGICO.md](../../docs/GLOSSARIO_METODOLOGICO.md)
  para o que isso significa).
- `avisos_parsing.csv` — casos em que uma métrica esperada não foi
  encontrada em algum arquivo de log (ajuda a explicar diferenças de `n`
  entre métricas/ambientes).
- **`alerta_estatisticas_duplicadas.csv`** — lista pares de métricas cujo
  teste estatístico global produziu resultado exatamente idêntico. Isso
  pode ser benigno (grupos de mesmo tamanho com separação completa entre
  ambientes produzem o mesmo H de Kruskal-Wallis por construção
  matemática, independentemente dos valores brutos) ou indicar um problema
  de cálculo — **revise manualmente antes de aceitar qualquer novo caso que
  apareça aqui**. Gerado automaticamente em toda execução do pipeline
  (não é uma checagem pontual/manual).
- `metadata.json` — commit git, timestamp UTC e seed da execução que
  gerou os arquivos acima.
- `relatorio.txt` — resumo legível em português de tudo isso, por métrica.
- `graficos/`, `heatmaps/` — gerados na mesma execução; movidos para
  `results/figures/` (ver docs/REPRODUCE.md), não ficam aqui no final.
