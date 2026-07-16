# Metodologia (resumo)

Este documento resume a metodologia experimental para orientar quem for
reproduzir ou auditar este repositório. Para a redação completa e
formal, ver a Seção de Metodologia do artigo (link/DOI a preencher).

## Contexto

Projeto de pesquisa em virtualização na arquitetura IBM Power9 (ppc64le),
conduzido no Laboratório de Sistemas Distribuídos (LSD/UFCG) em parceria
com a IBM (projeto IBM-MULTIARQUITETURAS), usando um servidor IBM Power9
AC922. Também serve como entregável de duas disciplinas de pós-graduação
(Sistemas Distribuídos/Virtualização e Inferência Estatística).

## Objetivo

Caracterizar o overhead de desempenho introduzido por dois mecanismos de
virtualização — **KVM** (virtualização com aceleração de hardware) e
**QEMU puro** (emulação via TCG, sem aceleração) — em relação a um
baseline **bare-metal (host físico)**, sobre cargas representativas de
HPC: CPU, memória, cache, disco e GPU.

## Ambientes comparados

| Ambiente | Descrição | Réplicas |
|---|---|---|
| `host` | Execução direta no servidor Power9 físico | 15 execuções |
| `kvm` | 3 VMs distintas (`gabrielly`, `lucas`, `ramalho`) sob KVM | 5 execuções × 3 VMs = 15 |
| `qemu` | As mesmas 3 VMs sob QEMU puro (TCG, sem KVM) | 5 execuções × 3 VMs = 15 |

> **identificadores de VM** (`vm_id`), usados nos nomes de pasta
> `kvm-lucas/`, `kvm-ramalho/`, `qemu-lucas/` etc. em `data/raw/`. 
> A lista de autoria completa está em [`CITATION.cff`](../CITATION.cff);
> "lucas" e "ramalho" não são autores nem colaboradores.

A estrutura aninhada (3 VMs × 5 réplicas por tratamento) é tratada
explicitamente no pipeline estatístico — ver `bootstrap_ci_estratificado_por_vm`
e `checar_heterogeneidade_vms` em `src/analysis/analise_benchmarks.py` — para
não tratar as 15 execuções de KVM/QEMU como i.i.d. quando há heterogeneidade
real entre VMs de origem (pseudo-replicação).

## Ferramentas de benchmark e o que cada métrica mede

- **stress-ng** (`--cpu`, `--vm`, `--cache`): CPU, memória e cache, via
  contagem de "bogo operations" por segundo (tempo real e tempo de CPU).
  Consultar `man stress-ng` / documentação oficial para a definição exata
  de cada stressor.
- **STREAM**: largura de banda de memória (Copy/Scale/Add/Triad, MB/s).
  Sob QEMU/TCG, a saída é numericamente inválida (falta de resolução
  confiável de timer sob emulação) e é descartada por design — ver
  comentário no bloco STREAM de `parse_file()` em `analise_benchmarks.py`.
- **fio**: disco (`ext4` nas VMs, dispositivo físico no host) — leitura/escrita
  sequencial, IOPS aleatório (mix 70/30 leitura/escrita) e latência (fila
  unitária, QD=1, `ioengine=libaio`, `iodepth=1`, `numjobs=1` — **não** é
  `ioengine=sync`; mesmo em QD=1 passa por `io_submit()`/`io_getevents()`).
- **CUDA Samples (matrixMul)** e **NCCL (nccl-tests)**: GPU — GFlop/s e
  largura de banda de bus (`Avg bus bandwidth`) para `all_reduce`,
  `all_gather` e `sendrecv`. Só coletado em `host` e `kvm` (QEMU/TCG puro
  não faz passthrough de PCIe/GPU).

## Pipeline estatístico (`src/analysis/analise_benchmarks.py`)

Por métrica, comparando os 3 ambientes:

1. Estatística descritiva + IC 95% via bootstrap (estratificado por VM
   quando há mais de uma VM no tratamento).
2. Teste de normalidade por grupo (Shapiro-Wilk) e de homogeneidade de
   variância entre grupos (Levene).
3. Teste global: ANOVA se todos os grupos forem normais e homocedásticos;
   caso contrário, **Kruskal-Wallis**.
4. Post-hoc de Dunn (Bonferroni) quando o teste global rejeita H0, com
   mais de 2 grupos.
5. Effect size (epsilon²) para Kruskal-Wallis.
6. **Correção de Benjamini-Hochberg (FDR)** sobre `teste_global_p` de
   TODAS as métricas simultaneamente (coluna `p_fdr_corrigido`) — controla
   a taxa de falsos positivos ao testar múltiplas métricas independentes,
   adicionada na reorganização de 2026-07-15.
7. Checagem de heterogeneidade entre VMs de origem (Kruskal-Wallis
   VM A vs. B vs. C), para sinalizar quando a "diferença entre
   ambientes" pode estar confundida com diferença entre VMs específicas.

Ver [docs/auditoria/AUDITORIA_SCRIPTS.md](auditoria/AUDITORIA_SCRIPTS.md) para um caso específico
de auditoria de integridade estatística (coincidência de estatística
idêntica entre métricas de cache e memória — explicada e não é bug).

## Anomalias investigadas

### Cache (RESOLVIDA)

`stress-ng --cache` inicialmente reportou ganhos fisicamente implausíveis
sob virtualização (KVM ~+324%, QEMU ~+46% em relação ao host). A hipótese
de PMU (Performance Monitoring Unit) não exposto ao guest foi testada e
não explicava o efeito sozinha. A causa raiz identificada foi
**topologia de cache incompleta exposta ao guest**: o host Power9 expõe
4 níveis de cache (L1i, L1d, L2, L3), mas a VM só enxerga L1i/L1d.
Forçar `stress-ng --cache-level 1` (já incorporado em
`src/collect/benchmark-host.sh` e `benchmark-vm.sh`) reduziu o efeito de
+324% para ~+132% — o resíduo remanescente ainda não tem causa raiz
100% fechada, mas a maior parte da discrepância foi explicada por uma
limitação de topologia de cache em ambientes PAPR/POWER9, não
documentada nas referências consultadas até o momento.

### Latência de disco QD=1 (EM ABERTO)

No teste de fila unitária (leitura aleatória síncrona, QD=1), tanto KVM
quanto QEMU mostraram latência média **menor** e IOPS **maior** que o
host físico — contraintuitivo, já que a virtualização deveria adicionar
overhead, não removê-lo. Hipóteses formuladas (nenhuma delas é resultado
de literatura citada — são hipóteses operacionais a confirmar
experimentalmente):

- **Jitter/baixa concorrência**: o teste QD=1 pode ser mais sensível a
  ruído transitório do sistema do que ao custo estrutural da
  virtualização.
- **Contenção assimétrica via Slurm**: o benchmark do host roda via
  `sbatch` sem `--exclusive` (não garante exclusividade do nó), enquanto
  o benchmark de VM roda como script manual — se outros jobs Slurm
  competirem pelo disco físico durante a rodada de host, isso infla
  artificialmente a latência do host, sem que exista vantagem real de
  virtualização.
- **Ausência de pinning de vCPU**: confirmado via XML da VM
  (`virsh dumpxml`) — sem `<cputune><vcpupin.../></cputune>`, o
  escalonador do host é livre para mover vCPUs entre cores físicos.
  Não descarta nem confirma, mas é uma variável de confusão não
  controlada.
- **Indireção de snapshot/backing file** no disco da VM (qcow2 com
  backing store) adiciona uma camada de indireção ausente no host — mas
  esse efeito tenderia a *piorar*, não melhorar, a latência da VM, então
  não explica sozinho a anomalia.

Esta investigação permanece **em aberto** e é registrada como ameaça à
validade não conclusiva.
