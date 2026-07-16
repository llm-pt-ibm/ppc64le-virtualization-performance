# Glossário metodológico: pseudo-replicação

Este documento explica, em linguagem simples, o que significa o aviso
`(cuidado: pseudo-replicação)` que aparece em `data/processed/relatorio.txt`
para algumas métricas, com um exemplo numérico real tirado deste próprio
experimento.

## O desenho experimental: por que 3 VMs, não 15 amostras independentes

Para os tratamentos KVM e QEMU, este experimento não coletou "15 execuções
independentes" — coletou **3 VMs diferentes** (identificadas por `vm_id`:
`gabrielly`, `lucas`, `ramalho`), cada uma rodando o benchmark **5 vezes**.
`3 × 5 = 15`, o mesmo `n` usado nos testes host vs. KVM vs. QEMU — mas a
estrutura interna desses 15 números não é a mesma que teríamos se tivéssemos
provisionado 15 VMs diferentes e rodado o benchmark 1 vez em cada uma.

Isso é chamado de **desenho aninhado** (nested design, ou desenho em
blocos): "execução" está aninhada dentro de "VM", que por sua vez está
aninhada dentro de "tratamento" (KVM/QEMU). É uma prática **padrão e
correta** em avaliação de desempenho de sistemas — não um atalho nem um
erro de coleta. A alternativa (provisionar 15 VMs diferentes) custaria
5x mais tempo de máquina para o mesmo `n`, sem necessariamente adicionar
informação nova, já que o que varia entre execuções DENTRO da mesma VM
(ruído de medição, jitter do agendador, etc.) tende a ser bem menor do
que o que pode variar ENTRE VMs (pequenas diferenças de alocação de
NUMA/vCPU, estado de cache, ordem de inicialização, etc.).

## O que é pseudo-replicação

**Pseudo-replicação** é o erro de tratar réplicas que NÃO são
estatisticamente independentes (ex.: 5 execuções da mesma VM) como se
fossem `n` amostras independentes de um "tratamento KVM" genérico. O
risco concreto: se a VM `gabrielly` tiver, por acaso, um desempenho
sistematicamente diferente das outras duas (não por causa do KVM, e sim
por alguma particularidade daquela VM específica — um vizinho barulhento
na época da coleta, uma diferença de alocação de NUMA, etc.), e você
simplesmente empilhar as 15 execuções como se fossem 15 amostras i.i.d.
de "o KVM", o teste estatístico pode:

- **subestimar a variância real do tratamento** (porque a variação
  *entre VMs* — que é uma fonte real de incerteza sobre "o que é típico
  do KVM" — fica escondida dentro de uma variância inflada só de
  "execução"), e
- como consequência, **superestimar a confiança na diferença observada**
  entre KVM e host/QEMU (p-valor artificialmente pequeno, IC
  artificialmente estreito) — reportando um resultado como "mais
  significativo" do que os dados realmente sustentam.

O termo (nesse sentido estatístico geral) é discutido em profundidade em
Hurlbert, S. H. (1984), *"Pseudoreplication and the Design of Ecological
Field Experiments"*, e a forma específica como isso se aplica a
benchmarking de sistemas — múltiplas execuções na mesma máquina/VM não
sendo amostras independentes do "tratamento" — é tratada no capítulo de
desenho de experimentos de Jain, R. (1991), *The Art of Computer Systems
Performance Analysis* (Wiley), especialmente nas seções sobre variância
entre réplicas e a necessidade de identificar corretamente a unidade
experimental antes de aplicar um teste de hipótese.

## Exemplo numérico real: `mem_bogo_ops_s_real` sob KVM

Em `data/processed/dados_extraidos.csv`, as 15 execuções de
`mem_bogo_ops_s_real` sob KVM se dividem assim por VM de origem:

| VM | n | média (bogo ops/s) | desvio padrão |
|---|---|---|---|
| gabrielly | 5 | 437 331 | 13 623 |
| lucas | 5 | 462 739 | 11 371 |
| ramalho | 5 | 459 983 | 13 246 |

A VM `gabrielly` roda, em média, ~5-6% mais devagar que `lucas` e
`ramalho` nesta métrica. Isso é exatamente o tipo de diferença
sistemática **entre VMs** (não entre execuções) que a pseudo-replicação
esconderia se você só olhasse a média das 15 execuções combinadas.

`checar_heterogeneidade_vms()` (em `src/analysis/analise_benchmarks.py`)
testa isso diretamente: roda um Kruskal-Wallis comparando `gabrielly` vs.
`lucas` vs. `ramalho` (só entre si, dentro do tratamento KVM) para cada
métrica. Para `mem_bogo_ops_s_real` sob KVM
(`data/processed/heterogeneidade_entre_vms.csv`):

```
metrica=mem_bogo_ops_s_real, ambiente=kvm, n_vms=3, kruskal_h=6.02, kruskal_p=0.0493, heterogeneo=True
```

`p=0.0493 < 0.05` → as 3 VMs **diferem entre si** de forma
estatisticamente detectável nesta métrica, sob KVM. É por isso que
`relatorio.txt` imprime, para esta métrica/ambiente:

```
Heterogeneidade entre VMs (kvm): H=6.020, p=0.0493 -> diferem entre si? SIM (cuidado: pseudo-replicação)
```

Para comparação, a mesma checagem sob **QEMU**, para a mesma métrica, dá
`kruskal_h=0.98, kruskal_p=0.613, heterogeneo=False` — as 3 VMs QEMU
**não** diferem entre si de forma detectável nesta métrica, então ali a
suposição de "15 execuções ~ i.i.d." é mais defensável.

## O que o pipeline já faz a respeito (não precisa fazer nada extra)

Isto **não é um problema não resolvido** — é uma limitação conhecida do
desenho aninhado, e o pipeline já tem duas respostas para ela:

1. **IC bootstrap estratificado por VM**
   (`bootstrap_ci_estratificado_por_vm` em `analise_benchmarks.py`): em
   vez de reamostrar as 15 execuções como um pool único, o bootstrap
   sorteia primeiro VMs (com reposição), depois réplicas dentro de cada
   VM sorteada (com reposição) — um bootstrap de 2 níveis que respeita a
   estrutura aninhada e produz um IC mais largo (mais honesto) quando há
   heterogeneidade real entre VMs, em vez de um IC artificialmente
   estreito.
2. **O próprio aviso de heterogeneidade**: sempre que
   `checar_heterogeneidade_vms` detecta `p<0.05` para uma métrica ×
   tratamento, isso fica registrado em
   `data/processed/heterogeneidade_entre_vms.csv`,
   [heatmap_heterogeneidade_vm.png](../results/figures/heatmaps/heatmap_heterogeneidade_vm.png),
   e como o aviso "(cuidado: pseudo-replicação)" em `relatorio.txt` — para
   que qualquer leitor saiba exatamente em quais métricas/tratamentos a
   suposição de réplicas i.i.d. é mais frágil, em vez de descobrir isso
   só lendo o código.

Ou seja: a pseudo-replicação em si é uma característica esperada e
metodologicamente correta do desenho aninhado (3 VMs × 5 execuções); o
que o pipeline garante é que, quando ela realmente se manifesta como
heterogeneidade detectável entre VMs, isso fica visível e é tratado com
o método estatístico apropriado (bootstrap estratificado), em vez de
ignorado.
