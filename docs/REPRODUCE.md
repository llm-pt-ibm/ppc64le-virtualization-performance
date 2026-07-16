# Como reproduzir

A reprodutibilidade deste trabalho tem dois níveis, com graus de exigência
de hardware bem diferentes:

1. **Reprodução da ANÁLISE** a partir dos dados brutos já publicados
   (`data/raw/`) — roda em **qualquer máquina** com Python 3.13 (nenhum
   requisito de arquitetura/hardware). Este é o nível **garantido** pelo
   kit de artefatos.
2. **Reprodução da COLETA** original — requer hardware Power9/ppc64le
   específico (ver seção 2 abaixo para os requisitos exatos). Documentado
   aqui por transparência metodológica; é uma limitação de validade
   externa já registrada no artigo, não uma restrição deste kit em si.

## 1. Criar o ambiente

### Python

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r environment/requirements.txt --break-system-packages
```

(`--break-system-packages` só é necessário se seu Python for gerenciado
pela distro/PEP 668; dentro de uma venv ativada normalmente não é preciso,
mas foi assim que este ambiente foi originalmente montado neste projeto.)

### R (só necessário se for regerar as figuras finais do artigo)

```bash
Rscript -e 'install.packages(c("ggplot2", "dplyr", "tidyr"))'
```

Versões usadas na verificação desta reorganização (2026-07-15): R 4.6.0,
ggplot2 4.0.3, dplyr 1.2.1, tidyr 1.3.2 — ver `environment/r_packages.txt`.

## 2. Coleta dos benchmarks brutos

Os scripts em `src/collect/` foram usados para coletar os logs que estão
em `data/raw/`:

- `benchmark-host.sh` — roda no servidor Power9 físico via Slurm
  (`sbatch benchmark-host.sh`), gera `data/raw/host/benchmark_host_*.txt`.
- `benchmark-vm.sh` — roda dentro de cada VM KVM (`./benchmark-vm.sh`),
  grava em `/root/experimentos/` na própria VM guest; os arquivos
  resultantes precisam ser copiados manualmente para
  `data/raw/kvm-<vm>/` (`kvm-gabrielly`, `kvm-lucas`, `kvm-ramalho`) — ver
  a seção "Etapa manual obrigatória" logo abaixo, **esse passo não é
  automático**.
- `benchmark-qemu.sh` — idem, para QEMU puro (TCG); copiado manualmente
  para `data/raw/qemu-<vm>/`.
- `submeter_benchmarks_slurm.sh` — laça `sbatch benchmark-host.sh` 15
  vezes em sequência, esperando cada job terminar antes de submeter o
  próximo.
- `troca_gpu.sh` — realoca a GPU (VFIO/hostdev) de uma VM para outra no
  libvirt; utilitário operacional, não gera dados de benchmark.

**Requisitos de hardware:** estes scripts de coleta requerem um servidor
com arquitetura **ppc64le**, processador **IBM POWER9**, e (para os
testes de GPU) **GPUs NVIDIA com suporte a NVLink2 e VFIO** para
passthrough — além de `stress-ng`/`fio`/`STREAM`/CUDA Samples/nccl-tests
compilados nos caminhos esperados pelos scripts (ex.:
`/usr/local/cuda-12.2`, `~/cuda-samples/...`, `~/nccl-tests/build/...`),
Slurm configurado (para `benchmark-host.sh`), e VMs KVM/QEMU já
provisionadas (ver [snapshot_vm.md](snapshot_vm.md) para o procedimento
de clonagem manual via `virsh`, usado neste experimento; para
provisionamento e gerenciamento de VMs KVM via Ansible em vez de manual,
ver https://github.com/llm-pt-ibm/kvm-power9-ansible.git). **Não é
necessário ser especificamente o
servidor original do experimento** — qualquer hardware Power9/ppc64le com
essas características permite a reexecução. A reprodução EXATA dos
valores absolutos depende, no entanto, de hardware equivalente; a
reprodução da ANÁLISE estatística a partir dos dados brutos já coletados
(`data/raw/`) não tem essa restrição e roda em qualquer máquina com
Python 3.13 (seção 3 abaixo).

### Etapa manual obrigatória: copiar logs da VM para o host

`benchmark-vm.sh` e `benchmark-qemu.sh` rodam **dentro do convidado**
(guest) e escrevem em `OUTDIR=/root/experimentos` — um diretório **dentro
da VM**, sem nenhuma correspondência automática com `data/raw/` no
checkout deste repositório (que vive no host, ou na sua máquina local).
**Sem esse passo manual, `data/raw/` fica incompleto e
`analise_benchmarks.py` simplesmente não encontrará esses logs** (o
pipeline não sabe que a VM existe — ele só lê o que já estiver em
`data/raw/`).

Depois de rodar a coleta dentro da VM, copie os `.txt` gerados para o
host/checkout do repositório, ajustando `vm_id` e ambiente conforme o
caso:

```bash
# rodando de dentro da VM já finalizada a coleta (exemplo: KVM, VM "gabrielly"):
scp -r root@<ip-da-vm>:/root/experimentos/*.txt \
    ./data/raw/kvm-gabrielly/

# exemplo: QEMU puro, VM "lucas":
scp -r root@<ip-da-vm>:/root/experimentos/*.txt \
    ./data/raw/qemu-lucas/
```

O mesmo vale para as VMs `lucas` e `ramalho` (ambas em KVM e em QEMU) —
repita para cada uma das 6 combinações VM × ambiente antes de rodar a
análise na seção 3.

## 3. Rodar a análise a partir dos dados brutos já coletados

Com o ambiente Python ativado e a partir da raiz do repositório:

```bash
python3 src/analysis/analise_benchmarks.py --logs-dir data/raw --out-dir data/processed
```

Gera em `data/processed/`:

- `dados_extraidos.csv` — formato tidy, uma linha por (arquivo, métrica)
- `resumo_descritivo.csv` — média/mediana/desvio/IQR por métrica × ambiente
- `resultados_estatisticos.csv` — normalidade, homogeneidade, teste global
  (ANOVA/Kruskal-Wallis), post-hoc de Dunn, effect size, IC bootstrap e
  `p_fdr_corrigido` (Benjamini-Hochberg entre todas as métricas)
- `heterogeneidade_entre_vms.csv` — heterogeneidade entre as 3 VMs por
  métrica × tratamento
- `avisos_parsing.csv` — casos em que uma métrica esperada não foi
  encontrada em algum arquivo (ex.: explica diferenças de n entre
  métricas/ambientes)
- `alerta_estatisticas_duplicadas.csv` — pares de métricas cujo teste
  estatístico global produziu resultado exatamente idêntico. Isso pode
  ser benigno (ver
  [docs/auditoria/AUDITORIA_SCRIPTS.md](auditoria/AUDITORIA_SCRIPTS.md)
  para um caso já investigado e explicado) ou indicar um problema de
  cálculo — revise manualmente antes de aceitar qualquer novo caso que
  apareça aqui. Gerado automaticamente em TODA execução do pipeline
  (não é uma checagem pontual/manual).
- `metadata.json` — commit git, timestamp e seed da execução
- `relatorio.txt` — resumo legível em português
- `graficos/*.png` — boxplots exploratórios por métrica (small multiples,
  1 subplot por ambiente, escala Y independente por painel)
- `heatmaps/*.png` — 4 arquivos, em dois pares curada/completa:
  - `heatmap_significancia.png` / `heatmap_heterogeneidade_vm.png` — versão
    CURADA, com 1 métrica por domínio experimental (CPU, memória, cache,
    STREAM, disco, GPU — ver `METRICAS_PRINCIPAIS` em
    `analise_benchmarks.py`), pensada para o corpo do artigo.
  - `heatmap_significancia_completo.png` / `heatmap_heterogeneidade_vm_completo.png`
    — mesma figura com as ~19 métricas, para auditoria/apêndice.

  Em `heatmap_significancia*.png`, as colunas são agrupadas visualmente em
  "Verificação de pressupostos" (Shapiro host/kvm/qemu, Levene) e "Teste de
  hipótese" (teste global bruto e `p_fdr_corrigido`) — são naturezas de
  p-valor diferentes (p baixo em pressupostos motiva o teste não-paramétrico;
  p baixo no teste de hipótese indica diferença real entre ambientes), por
  isso não compartilham um único rótulo de eixo x. Complementam os
  boxplots, não os substituem.

```bash
rm -rf results/figures/graficos results/figures/heatmaps
mv data/processed/graficos results/figures/graficos
mv data/processed/heatmaps results/figures/heatmaps
```

### Investigações de auditoria (opcional, não necessário para reproduzir)

Ver também [docs/auditoria/](auditoria/) — documenta investigações de
integridade estatística e de sincronia entre pipelines conduzidas durante
a preparação deste artefato (não é necessário para reproduzir o
experimento, mas serve como evidência de rigor metodológico). Em
particular, para reexecutar a auditoria de integridade estatística do
achado cache/memória:

```bash
python3 docs/auditoria/diagnostico_duplicacao.py --csv data/processed/dados_extraidos.csv
```

Ver [docs/auditoria/AUDITORIA_SCRIPTS.md](auditoria/AUDITORIA_SCRIPTS.md)
para o que este script verifica e por que a coincidência de estatística
idêntica entre cache/memória (e entre as métricas STREAM) é benigna, não
um bug.

## 4. Regenerar as figuras finais do artigo

As figuras finais (`results/figures/fig1..4_variacao_*.pdf`) são geradas
pelo **R**, não pelo Python — e usam números transcritos manualmente a
partir de `resumo_descritivo.csv`/`dados_extraidos.csv`, não lidos
automaticamente do CSV. Ver
[docs/auditoria/PIPELINE_FIGURAS.md](auditoria/PIPELINE_FIGURAS.md)
para o procedimento completo de conferência antes de regerar.

A partir da RAIZ do repositório (o script usa caminhos relativos
`results/figures/...`):

```bash
Rscript src/analysis/geracao_graficos.R
```

Os gráficos exploratórios em `results/figures/graficos/*.png` (um PNG por
métrica) são gerados pelo **Python**, no passo 3 acima — esses sim são
computados automaticamente a partir dos dados, sem transcrição manual.
