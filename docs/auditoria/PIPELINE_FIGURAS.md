# Pipeline de figuras

Este repositório gera figuras por **dois caminhos independentes**, com
propósitos diferentes. Isso não é óbvio olhando só a estrutura de pastas,
por isso está documentado explicitamente aqui.

## 1. `results/figures/graficos/*.png` — exploratório, gerado pelo Python

Gerado automaticamente por `src/analysis/analise_benchmarks.py`
(função `gerar_graficos`), **direto a partir de `data/raw/`**, sem
transcrição manual. Organizado em subpastas por seção do artigo (`cpu/`,
`memoria/`, `cache/`, `disco_seq/`, `disco_iops/`, `gpu/`).

**Um PNG por métrica, com um subplot POR AMBIENTE e escala Y
INDEPENDENTE em cada subplot** (não um único eixo compartilhado entre
host/KVM/QEMU). Isso foi corrigido em 2026-07-16: a versão anterior usava
um eixo Y compartilhado (com log automático quando a razão entre medianas
passava de 8x), mas mesmo com log, quando um ambiente tem variância
absoluta muito menor que outro no mesmo eixo (típico aqui: QEMU é
10-90x mais lento que host/KVM em CPU/memória/cache), a caixa desse
ambiente é comprimida a uma linha reta sem informação nenhuma sobre
IQR/whiskers — um boxplot que não informa nada. Ver a docstring de
`_plot_metrica` no código para o raciocínio completo. Com small
multiples (um Axes por ambiente, escala própria), a caixa de QUALQUER
ambiente fica sempre legível, ao custo de não dar pra comparar altura de
caixa entre painéis a olho (por isso o aviso no título de cada figura) —
mas essa comparação de magnitude relativa já é o papel das figuras finais
do artigo (seção 2 abaixo), não desta figura exploratória.

Regenerar:
```bash
python3 src/analysis/analise_benchmarks.py --logs-dir data/raw --out-dir data/processed
mv data/processed/graficos results/figures/graficos  # ver docs/REPRODUCE.md
```

## 2. `results/figures/fig{1..4}_variacao_*.pdf` — figuras finais do artigo, geradas pelo R

Geradas por `src/analysis/geracao_graficos.R` (ggplot2), que produz os 4
gráficos de barras/pontos de variação percentual relativa ao host,
usados no artigo:

- `fig1_variacao_disco.pdf` — disco (leitura/escrita seq., IOPS aleatório, latência)
- `fig2_variacao_gpu.pdf` — GPU (matrixMul, NCCL)
- `fig3_variacao_cpu_mem.pdf` — CPU e memória
- `fig4_variacao_stream.pdf` — STREAM

**Importante para reprodutibilidade:** este script **NÃO lê os CSVs do
pipeline Python**. Os percentuais de variação usados em cada figura
(tibbles `disco`, `gpu`, `cpu_mem`, `stream`, no topo do script) são
**transcritos manualmente** a partir de `data/processed/resumo_descritivo.csv`
/ `dados_extraidos.csv`, calculados como:

```
overhead(%) = (média_ambiente / média_host - 1) * 100
```

(mesma fórmula da Seção 2.6 do artigo).

### Verificação de sincronia (2026-07-15)

Ao reorganizar o repositório, recalculamos o overhead de CPU e memória
diretamente de `data/processed/dados_extraidos.csv` e comparamos com os
números hardcoded em `geracao_graficos.R`:

| métrica | KVM (script) | KVM (recalculado) | QEMU (script) | QEMU (recalculado) |
|---|---|---|---|---|
| CPU (tempo real) | -0.37% | -0.37% | -93.52% | -93.52% |
| Memória (tempo real) | -17.11% | -17.11% | -92.74% | -92.74% |

Os valores batem exatamente — os números do R estão em sincronia com os
dados atuais em `data/raw/`. **Isso não é garantido automaticamente**: se
`data/raw/` for atualizado (nova coleta, nova réplica), os tibbles em
`geracao_graficos.R` precisam ser recalculados e atualizados manualmente
antes de regerar as figuras finais. Não há, atualmente, verificação
automática dessa sincronia — um comando de conferência sugerido é:

```bash
python3 -c "
import pandas as pd
d = pd.read_csv('data/processed/dados_extraidos.csv')
for m in ['cpu_bogo_ops_s_real', 'mem_bogo_ops_s_real']:
    sub = d[d['metrica'] == m]
    means = sub.groupby('ambiente')['valor'].mean()
    host = means['host']
    print(m, {amb: round((means[amb]/host - 1)*100, 2) for amb in ('kvm', 'qemu') if amb in means})
"
```

e comparar manualmente com os tibbles do topo de `geracao_graficos.R`.

### Correção do matrixMul e reconferência (2026-07-16)

O achado "3a" de `docs/auditoria/AUDITORIA_SCRIPTS_RESUMO.md` (matrixMul
com metodologia divergente entre `analise_benchmarks.py`, que usava só a
1ª de 5 execuções por arquivo, e `resumo_benchmarks_overhead.py`, que
sempre tirou a média das 5) foi corrigido: `parse_file()` em
`analise_benchmarks.py` agora também tira a média das 5 execuções de
matrixMul por arquivo. Recalculando a partir de `resumo_descritivo.csv`
pós-correção:

| métrica | host (média) | kvm (média) | overhead KVM |
|---|---|---|---|
| matrixMul (GFlop/s) | 2867.91 | 2932.27 | +2.24% |

O valor `2.24` já hardcoded no tibble `gpu` de `geracao_graficos.R` não
mudou — ele já tinha sido transcrito de `resumo_benchmarks_overhead.py`
(que sempre fez a média), então a correção alinhou
`analise_benchmarks.py` a esse valor, e não o contrário. `fig2_variacao_gpu.pdf`
foi regerado por precaução, mas o conteúdo é idêntico ao anterior.

Regenerar as figuras finais (rodar a partir da RAIZ do repositório, porque
o script escreve em `results/figures/` com caminho relativo):

```bash
Rscript src/analysis/geracao_graficos.R
```

## 3. `figures/*.png` — cópias p/ `\includegraphics` do artigo

Dois arquivos na RAIZ do repositório (não em `results/figures/`), cópias
manuais das versões CURADAS (1 métrica por domínio, ver
`METRICAS_PRINCIPAIS`) dos heatmaps:

- `figures/heterogeneidade_vms_heatmap.png` ← `heatmap_heterogeneidade_vm.png`
- `figures/significancia_testes_heatmap.png` ← `heatmap_significancia.png`

**Não são geradas automaticamente** — depois de qualquer reexecução de
`analise_benchmarks.py` que mude os dados, repita:

```bash
cp results/figures/heatmaps/heatmap_heterogeneidade_vm.png figures/heterogeneidade_vms_heatmap.png
cp results/figures/heatmaps/heatmap_significancia.png figures/significancia_testes_heatmap.png
```

As duas figuras não têm título nem texto interpretativo embutido (só o
heatmap, rótulos de coluna/grupo e a legenda de cores) — título/legenda
textual em cima da imagem não é usual em artigo, isso vai na caption do
LaTeX. Sugestão de caption (ACM `figure*`, 2 colunas):

```latex
\caption{Testes formais de significância por métrica (host vs. KVM vs.
QEMU). Colunas à esquerda: verificação de pressupostos (Shapiro-Wilk por
ambiente, Levene) — p baixo indica pressuposto violado, motivando o
teste não-paramétrico, não uma diferença real. Colunas centrais: teste
de hipótese (Kruskal-Wallis/ANOVA bruto e p FDR-corrigido via
Benjamini-Hochberg) — p baixo indica diferença estatisticamente
significativa entre ambientes. Coluna à direita: effect size (ε²).}

\caption{Teste de heterogeneidade (Kruskal-Wallis) entre as 3 VMs de
origem de cada tratamento, por métrica. Vermelho indica heterogeneidade
significativa (p<0.05) entre VMs — risco de pseudo-replicação ao tratar
as 15 execuções do tratamento como réplicas i.i.d.}
```

## Por que não unificar os dois pipelines agora

Consolidar tudo em um único pipeline (ex.: o R lendo os CSVs diretamente)
é uma melhoria de reprodutibilidade genuína, mas está fora do escopo
desta reorganização (que só move/documenta/ajusta caminhos, sem alterar a
lógica estatística substantiva do artigo). Fica registrado aqui como
recomendação para uma iteração futura.
