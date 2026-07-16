# Overhead de virtualização: KVM vs. QEMU em IBM Power9 (ppc64le)

Repositório de reprodutibilidade do experimento que compara o overhead de
desempenho entre KVM (virtualização com aceleração de hardware) e QEMU puro
(emulação via TCG), em relação a um baseline bare-metal, num servidor IBM
Power9 (ppc64le). As métricas cobertas são CPU, memória, cache, disco
(fio/ext4), largura de banda de memória (STREAM) e GPU (matrixMul/NCCL via
CUDA).

## Estrutura do repositório

```
environment/     dependências Python (requirements.txt) e R (r_packages.txt)
src/
  collect/        scripts de coleta (rodam no servidor Power9 real via Slurm/SSH)
  analysis/       pipeline de parsing + estatística (Python) e geração de figuras (R)
data/
  raw/            logs brutos de benchmark (.txt), um subdiretório por ambiente/VM
  processed/      CSVs gerados pelo pipeline de análise (ver docs/REPRODUCE.md)
results/
  figures/        figuras finais do artigo (PDF) e gráficos exploratórios (PNG)
docs/             metodologia, guia de reprodução, auditoria e cenário do experimento
```

`data/raw/` usa nomes de pasta como `kvm-gabrielly/`, `kvm-lucas/`,
`kvm-ramalho/`, `qemu-lucas/` etc. — `gabrielly`, `lucas` e `ramalho` aqui
são **identificadores das 3 VMs replicadas** do experimento (`vm_id`),
**não** nomes de autores/colaboradores. A lista de autoria está em
[CITATION.cff](CITATION.cff).

## Início rápido

Para reproduzir a análise a partir dos dados brutos já coletados (que já
estão em `data/raw/`), veja o passo a passo completo em
[docs/REPRODUCE.md](docs/REPRODUCE.md). Resumo:

```bash
python -m venv venv && source venv/bin/activate
pip install -r environment/requirements.txt --break-system-packages

python3 src/analysis/analise_benchmarks.py --logs-dir data/raw --out-dir data/processed
```

## Limitação de reprodutibilidade

A reprodutibilidade deste trabalho tem dois níveis:

- **Reprodução da ANÁLISE** a partir dos dados brutos publicados
  (`data/raw/`) — roda em qualquer máquina com Python (nenhum requisito
  de hardware). É o nível garantido pelo kit de artefatos.
- **Reprodução da COLETA** original — os scripts em `src/collect/*.sh`
  requerem um servidor com arquitetura ppc64le, processador IBM POWER9
  e, para os testes de GPU, GPUs NVIDIA com suporte a NVLink2 e VFIO
  para passthrough. **Não precisa ser especificamente o servidor
  original do experimento** — qualquer hardware Power9/ppc64le com
  essas características permite a reexecução da coleta. A reprodução
  EXATA dos valores absolutos depende, no entanto, de hardware
  equivalente; isso é uma limitação de validade externa já documentada
  no artigo, não uma restrição do kit de artefatos em si.

Ver [docs/REPRODUCE.md](docs/REPRODUCE.md) para o passo a passo completo
de cada nível.

## Documentação

- [docs/METHODOLOGY.md](docs/METHODOLOGY.md) — resumo da metodologia experimental
- [docs/REPRODUCE.md](docs/REPRODUCE.md) — passo a passo de reprodução
- [docs/GLOSSARIO_METODOLOGICO.md](docs/GLOSSARIO_METODOLOGICO.md) — conceitos estatísticos do
  desenho experimental (ex.: por que 3 VMs × 5 execuções, e o que significa o aviso de
  "pseudo-replicação"), em linguagem simples
- [docs/snapshot_vm.md](docs/snapshot_vm.md) — procedimento de clonagem das VMs de teste

Investigações de integridade estatística e de sincronia entre pipelines
(auditoria interna, não necessária para reproduzir o experimento) ficam
em `docs/auditoria/` — ver `docs/auditoria/README.md`.

## Licença

Código (`src/`, `environment/`): [MIT](LICENSE).
Dados e figuras (`data/`, `results/`): [CC BY 4.0](LICENSE-DATA).

## Como citar

Ver [CITATION.cff](CITATION.cff).
