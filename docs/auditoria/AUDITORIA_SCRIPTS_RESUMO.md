# Auditoria dos scripts de resumo (`resumo_benchmarks_overhead.py` e `resumo_benchmarks_qemu.py`)

**Data:** 2026-07-16
**Escopo:** testar os dois scripts contra os dados reais em `data/raw/` e
decidir se devem ser mantidos, corrigidos, ou movidos para legado.

## Como foram rodados

Nenhum dos dois scripts sabe agregar múltiplas VMs sozinho — cada um
recebe UM `--pattern` (glob). Para reproduzir a agregação "kvm" (3 VMs ×
5 execuções = 15) que `analise_benchmarks.py` faz, usei glob com
wildcard no nome da pasta, que o módulo `glob` do Python suporta:

```bash
python3 src/analysis/resumo_benchmarks_overhead.py \
  --pattern "data/raw/kvm-*/benchmark_vm_*.txt" \
  --baseline "data/raw/host/benchmark_host_*.txt" \
  --baseline-disk-label "DISCO_HOST" --label "KVM"

python3 src/analysis/resumo_benchmarks_overhead.py \
  --pattern "data/raw/qemu-*/benchmark_qemu_*.txt" \
  --baseline "data/raw/host/benchmark_host_*.txt" \
  --baseline-disk-label "DISCO_HOST" --label "QEMU"

python3 src/analysis/resumo_benchmarks_qemu.py \
  --pattern "data/raw/qemu-*/benchmark_qemu_*.txt" \
  --baseline "data/raw/host/benchmark_host_*.txt"
```

## 1. Rodam sem erro?

**Sim, os três invocações rodam do início ao fim sem exceção**, para os
dois scripts. Nenhum crash, nenhum traceback.

## 2. Os caminhos batem com a estrutura atual?

**Não, sem ajuste manual.** Nenhum dos dois tem um `--logs-dir`/`--out-dir`
como `analise_benchmarks.py` — usam `--pattern`/`--baseline` (globs
livres) e imprimem só no console (não escrevem CSV/arquivo).
Especificamente:

- `resumo_benchmarks_qemu.py` tem um `DEFAULT_PATTERN = "benchmark_qemu_*.txt"`
  hardcoded, pensado para ser rodado **de dentro** de uma pasta com os
  `.txt` (como no antigo `kvm-gabrielly/resumo_benchmarks.py`). Rodado da
  raiz do repo sem `--pattern`, ele imprime só
  `"Nenhum arquivo encontrado para o padrão: benchmark_qemu_*.txt"` e sai
  — confirmado nesta auditoria.
- Os exemplos no docstring de ambos os scripts (`--pattern
  "kvm_results/benchmark_vm_*.txt"`, `"host_results/benchmark_host_*.txt"`)
  referenciam uma convenção de nomes (`kvm_results/`, `host_results/`)
  que **nunca existiu** nem na estrutura antiga (`kvm-gabrielly/`, `host/`)
  nem na atual (`data/raw/kvm-gabrielly/`) — já estavam desatualizados
  antes desta reorganização. Precisam de `--pattern
  "data/raw/kvm-*/benchmark_vm_*.txt"` (glob com wildcard na pasta,
  suportado pelo `glob` do Python) pra agregar as 3 VMs de um tratamento
  numa só chamada.
- **Footgun confirmado**: se você comparar KVM/QEMU contra o host e
  esquecer `--baseline-disk-label DISCO_HOST`, o script **não avisa** —
  simplesmente imprime `"sem baseline"` em todas as métricas de disco,
  silenciosamente, porque o rótulo de fase default (`DISCO_EXT4`, correto
  pras VMs) não bate com o que o host usa (`DISCO_HOST`). Reproduzi isso
  nesta auditoria: rodar sem o argumento faz `Leitura seq.`,
  `Escrita seq.`, `IOPS aleatório (leitura/escrita)` e `Latência média`
  aparecerem como `sem baseline` sem nenhum erro ou aviso explicando o
  motivo.

## 3. Os números batem com os de `analise_benchmarks.py`?

**Na maioria das métricas, sim — exatamente.** Comparação lado a lado
(overhead % em relação ao host, `(média_amb / média_host - 1) × 100`):

| métrica | resumo_benchmarks_overhead.py | analise_benchmarks.py (recalculado de `dados_extraidos.csv`) |
|---|---|---|
| CPU (KVM) | -0.37% | -0.37% |
| CPU (QEMU) | -93.52% | -93.52% |
| Memória (KVM) | -17.11% | -17.11% |
| Memória (QEMU) | -92.74% | -92.74% |
| Cache (KVM) | +324.11% | +324.11% |
| Cache (QEMU) | +46.47% | +46.47% |
| Leitura seq. (KVM/QEMU) | -3.43% / -2.32% | -3.43% / -2.32% |
| Escrita seq. (KVM/QEMU) | -1.70% / -5.07% | -1.70% / -5.07% |
| IOPS rand. leitura (KVM/QEMU) | -23.12% / -82.17% | -23.12% / -82.17% |
| IOPS rand. escrita (KVM/QEMU) | -22.89% / -82.09% | -22.89% / -82.09% |
| Latência média (KVM/QEMU) | -1.24% / -2.39% | -1.24% / -2.39% |
| NCCL all_reduce (KVM) | +0.49% | +0.49% |

Essas batidas exatas são uma boa notícia de auditoria cruzada: três
implementações independentes (`resumo_benchmarks_overhead.py`,
`analise_benchmarks.py`, e os números hardcoded em
`geracao_graficos.R`, já verificados em sincronia em
[PIPELINE_FIGURAS.md](PIPELINE_FIGURAS.md)) concordam.

### 3a. Discrepância real encontrada e CORRIGIDA (2026-07-16): `gpu_matrixmul_gflops`

**Os números não batiam**, e a causa era uma diferença real de
metodologia entre os dois scripts, não um bug de arredondamento:

- `resumo_benchmarks_overhead.py`: overhead KVM = **+2.24%**
- `analise_benchmarks.py` (antes da correção): overhead KVM = **+2.37%**

Cada arquivo de log de host/KVM contém **5 execuções seguidas** do
matrixMul (`for i in 1 2 3 4 5: ... matrixMul`, ver
`src/collect/benchmark-host.sh`), gerando 5 linhas `Performance= ...
GFlop/s` por arquivo (confirmei isso contando as ocorrências num arquivo
real: 5 linhas `Performance=` com valores entre 2996.04 e 3002.11
GFlop/s). Os dois scripts tratavam essas 5 execuções de forma diferente:

- `resumo_benchmarks_overhead.py` (`extract_metrics`): usa
  `re.findall(r'Performance=\s*([\d.]+)\s*GFlop/s', txt)` e faz a
  **média das 5** execuções daquele arquivo — um valor agregado por
  arquivo, que depois entra no cálculo de n=15.
- `analise_benchmarks.py` (`parse_file`), ANTES da correção: usava
  `MATRIXMUL_LINE.search(ln)` dentro de um loop `for ln in mm_block: ...
  break` — pegava **só a PRIMEIRA** das 5 execuções e descartava as
  outras 4.

**Correção aplicada**: `parse_file()` agora também tira a média das 5
execuções por arquivo, alinhando-se ao script de resumo (ver diff em
`src/analysis/analise_benchmarks.py`, bloco `# --- GPU (host e KVM
apenas) ---`). `dados_extraidos.csv`, `resultados_estatisticos.csv` e os
heatmaps de significância foram regenerados.

**Isto teve um efeito que vai além do overhead percentual — mudou o
resultado do teste de hipótese formal**: com a correção, o teste global
(Kruskal-Wallis) de `gpu_matrixmul_gflops` deixou de ser significativo:

| | antes (1ª execução) | depois (média das 5) |
|---|---|---|
| overhead KVM | +2.37% | +2.24% |
| teste global (p) | 0.033 | 0.059 |
| p FDR-corrigido | 0.041 | 0.074 |
| effect size (ε²) | 0.157 | 0.123 |

Ou seja: a versão anterior (usando só 1/5 das execuções) reportava
diferença estatisticamente significativa entre host e KVM para matrixMul
(p<0.05); a versão corrigida (usando as 5 execuções) não encontra mais
essa significância (p=0.059, marginal). **Se o texto do artigo cita
significância estatística para o overhead de matrixMul, revise essa
afirmação.**

### 3b. Discrepância real encontrada: STREAM sob QEMU (bug confirmado)

`analise_benchmarks.py` documenta explicitamente (comentário no bloco
STREAM de `parse_file`) que a saída do STREAM sob QEMU/TCG é
frequentemente inválida (`Best Rate = 0.0 MB/s`, `Max time` com overflow
de float ~3.4e38) e por isso **descarta** (`if valor <= 0.0: continue`)
qualquer valor STREAM ≤ 0. É por isso que `resultados_estatisticos.csv`
mostra `qemu_n` vazio (NaN) para as 4 métricas STREAM, e os gráficos
mostram "QEMU excluído".

**Nenhum dos dois scripts de resumo implementa esse descarte.** Rodando
`resumo_benchmarks_overhead.py --pattern "data/raw/qemu-*/..."`, as 4
métricas STREAM aparecem como:

```
STREAM Copy (MB/s)     10    0.00    0.00    0.00    0.00   -100.00%
```

Ou seja: o script **reporta um overhead de -100% como se fosse um dado
real**, quando na verdade é a média de valores `0.0` fisicamente
inválidos (conferido diretamente no log:
`Copy: 0.0 0.040010 340282346638528859811704183484516925440.000000 0.000000`
— o "Best Rate" é sempre 0.0 e o "Max time" estoura em overflow de float
sob TCG). Isso é **enganoso**: um leitor que use só este script (sem
cruzar com `analise_benchmarks.py`) pode reportar "-100% de overhead de
banda de memória sob QEMU" como se fosse uma medição válida, quando na
verdade a medição simplesmente falhou.

Adicionalmente, `n=10` em vez de `n=15` porque as 5 execuções de
`qemu-gabrielly` nem chegam a rodar o STREAM (`AVISO: STREAM não
encontrado em /root/stream` nos logs — o binário não estava compilado
naquela VM/execução); isso é uma lacuna real de coleta, não um bug do
script — mas o script também não avisa sobre isso, só reduz o `n`
silenciosamente.

## 4. Recomendação

**Manter os dois scripts como auxiliares, com um aviso explícito no
próprio cabeçalho/docstring sobre a limitação do STREAM sob QEMU**, em
vez de corrigi-los ou movê-los para `legacy/`. Justificativa:

- Você já indicou anteriormente que quer mantê-los (foram a base das
  primeiras análises do trabalho) — não há motivo para descartar esse
  histórico.
- Fora do caso STREAM/QEMU, os números batem exatamente com o pipeline
  principal em toda métrica comparável — não são scripts quebrados, são
  scripts corretos com uma lacuna específica e conhecida.
- Corrigir os scripts de resumo (replicar o filtro `valor <= 0.0` do
  STREAM) é possível, mas viraria duplicação de lógica de correção em
  dois lugares — se algum dia quiser essa correção, o caminho mais
  barato é o inverso: usar `analise_benchmarks.py --logs-dir <pasta>
  --out-dir <tmp>` e ler `resumo_descritivo.csv`, que já tem essa lógica
  centralizada e testada (ver [AUDITORIA_SCRIPTS.md](AUDITORIA_SCRIPTS.md)
  para o nível de escrutínio que esse pipeline já recebeu). A
  discrepância de matrixMul (achado 3a) já foi resolvida do lado
  oposto — `analise_benchmarks.py` foi corrigido para concordar com
  `resumo_benchmarks_overhead.py`, não o contrário.

**Ação mínima recomendada**: adicionar uma linha ao docstring de
`resumo_benchmarks_overhead.py` e `resumo_benchmarks_qemu.py` alertando
que os valores de STREAM sob QEMU não devem ser usados sem
conferência manual (achado 3b acima, ainda não corrigido em nenhum dos
dois scripts de resumo).
