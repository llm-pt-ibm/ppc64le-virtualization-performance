# Auditoria dos scripts de análise

Este documento registra verificações de auditoria feitas sobre o pipeline
`src/analysis/analise_benchmarks.py`, para constar como evidência de
reprodutibilidade/integridade estatística no artigo.

## Duplicação de estatística cache/memória

**Data da verificação:** 2026-07-15
**Execução:** `python3 analise_benchmarks.py --logs-dir . --out-dir <saída>`
rodado do zero sobre os logs brutos atuais em `data/raw/` (na época da
verificação, ainda na raiz do repo, antes da reorganização de pastas).

### O achado original

Um relatório anterior deste pipeline mostrava que os testes de
Kruskal-Wallis globais para `cache_bogo_ops_s_cpu`, `cache_bogo_ops_s_real`,
`mem_bogo_ops_s_cpu` e `mem_bogo_ops_s_real` tinham estatística **idêntica**
até a 4ª casa decimal:

```
H = 39.130435,  p = 3.183714e-09,  epsilon² = 0.889328
```

nas quatro métricas — apesar de cache e memória serem subsistemas medidos
por stressors diferentes do stress-ng (`cache` vs. `vm`/`malloc`/`mem`), em
escalas numéricas completamente distintas (cache: centenas/milhares de
bogo ops/s; memória: dezenas/centenas de milhares de bogo ops/s).

### Passo (b) — reprodução nesta execução

Recalculando `resultados_estatisticos.csv` do zero a partir dos dados
brutos atuais, **as quatro linhas continuam com estatística idêntica**:

| métrica | teste_global_estat | teste_global_p | effect_size_epsilon2 |
|---|---|---|---|
| cache_bogo_ops_s_cpu | 39.130435 | 3.183714e-09 | 0.889328 |
| cache_bogo_ops_s_real | 39.130435 | 3.183714e-09 | 0.889328 |
| mem_bogo_ops_s_cpu | 39.130435 | 3.183714e-09 | 0.889328 |
| mem_bogo_ops_s_real | 39.130435 | 3.183714e-09 | 0.889328 |

O achado é reproduzido nesta verificação — **não desapareceu** em relação à
análise anterior.

**Achado adicional relevante:** ao verificar se algum OUTRO grupo de
métricas do mesmo `resultados_estatisticos.csv` também colide, encontramos
que as 4 métricas STREAM (`stream_copy_mbs`, `stream_scale_mbs`,
`stream_add_mbs`, `stream_triad_mbs`) **também** têm estatística idêntica
entre si (H=21.774194, p=3.066978e-06), só que com apenas 2 grupos
(host n=15, kvm n=15 — QEMU é excluído dessas 4 métricas por gerar saída
STREAM numericamente inválida sob TCG, ver comentário em
`parse_file()`/bloco STREAM). Essa segunda coincidência, envolvendo
métricas de subsistemas ainda mais distantes entre si (throughput de banda
de memória via STREAM vs. nada relacionado a cache/CPU), foi o indício
decisivo de que a causa não é específica do par cache/memória — é
estrutural.

### Passo (c) — diagnóstico com `docs/auditoria/diagnostico_duplicacao.py`

O script carrega `data/processed/dados_extraidos.csv`, isola
`cache_bogo_ops_s_real`/`mem_bogo_ops_s_real` e
`cache_bogo_ops_s_cpu`/`mem_bogo_ops_s_cpu`, ordena por `(vm_id,
timestamp)` para garantir correspondência estável entre chamadas, e
calcula:

- **Correlação de Spearman** entre cache e memória, por ambiente e
  globalmente: valores baixos e inconsistentes em sinal (ex.: rho=0.618
  em host, rho=-0.468 em kvm, rho=-0.196 em qemu, para
  `*_real`). Isso já indica que cache e memória **não são a mesma
  série de valores** nem uma transformação monotônica simples uma da
  outra.
- **Ranks combinados (45 valores, 3 ambientes × 15)** via
  `scipy.stats.rankdata`: os ranks de cache e memória são **DIFERENTES em
  todas as 45 posições** (`ranks_identicos = False`), tanto para
  `*_real` quanto para `*_cpu`.
- **Kruskal-Wallis recalculado de forma independente** dentro do próprio
  script de diagnóstico (não reaproveitando nenhum estado do pipeline
  principal): reproduz exatamente H=39.130435, p=3.183714e-09 para as
  quatro métricas — confirmando que a coincidência não é um artefato de
  como o CSV foi lido, é reproduzível recalculando do zero.

Ou seja: caímos exatamente no caso "ranks DIFERENTES, mas
`teste_global_p` idêntico", que a hipótese inicial da investigação
descrevia como confirmação de bug. **Por isso o próximo passo (instrumentação
de `id()`) foi obrigatório antes de aceitar essa conclusão.**

### Instrumentação de `id()` dentro de `analisar_metrica()`

Chamamos `analisar_metrica()` diretamente (fora do `main()`) para as
4 métricas, com `scipy.stats.kruskal` monkey-patchado para imprimir
`id()`, tamanho, soma e os 3 primeiros valores de cada array de grupo
imediatamente antes da chamada real:

```
--- cache_bogo_ops_s_real ---
    grupo[0] (host): id=...376, len=15, sum=74843.07,   first3=[4986.32 4994.89 5043.38]
    grupo[1] (kvm):  id=...896, len=15, sum=317417.45,  first3=[29724.5  31137.94 27719.01]
    grupo[2] (qemu): id=...048, len=15, sum=109620.52,  first3=[6590.11 5593.74 8320.77]
--- mem_bogo_ops_s_real ---
    grupo[0] (host): id=...072, len=15, sum=8204002.50, first3=[548013.7  547618.12 549058.33]
    grupo[1] (kvm):  id=...896, len=15, sum=6800261.52, first3=[423496.74 448241.68 431805.76]
    grupo[2] (qemu): id=...440, len=15, sum=595870.79,  first3=[39755.05 39716.54 39393.81]
```

Um `id()` (`...896`) chegou a se repetir entre a chamada de cache e a
chamada de memória — mas **com soma e valores completamente diferentes**
(317417.45 vs. 6800261.52). Isso não é reuso de array: é o comportamento
normal do CPython de reciclar o mesmo endereço de memória para um objeto
novo depois que o array anterior (da chamada de cache, já fora de escopo)
foi coletado pelo garbage collector. `id()` só garante unicidade entre
objetos com tempo de vida sobreposto — comparar só o `id()` seria
enganoso aqui; por isso o diagnóstico também comparou soma e conteúdo, que
provam que são dados distintos. **Não há nenhum ponto no código onde o
array de uma métrica é passado para o `stats.kruskal()` de outra.**

### Explicação real: propriedade matemática do Kruskal-Wallis com separação completa

A causa da coincidência não é um bug — é uma consequência matemática de
como o teste de Kruskal-Wallis é calculado, combinada com uma
característica genuína (e substantiva) destes dados:

Em **todas** as 4 métricas (cache e memória, `_real` e `_cpu`), os três
ambientes (host, kvm, qemu) têm **exatamente o mesmo tamanho de amostra
(n=15 cada)** e, mais importante, os valores de **cada ambiente não se
sobrepõem aos valores dos outros dois ambientes** (ex.: em
`cache_bogo_ops_s_real`, o maior valor de host, 5043.38, é menor que o
menor valor de qemu, 5251.16, que por sua vez é menor que o menor valor de
kvm, 9750.36 — nenhum overlap entre os três grupos; o mesmo vale, com uma
ordem de blocos diferente, para `mem_bogo_ops_s_real`, onde é qemu < kvm <
host sem overlap).

A estatística de Kruskal-Wallis é:

```
H = 12/(N(N+1)) * Σ(R_i² / n_i) - 3(N+1)
```

onde `R_i` é a soma dos ranks (1..N) do grupo `i` e `n_i` seu tamanho.
Quando os `n_i` são **iguais entre os grupos** (aqui, 15/15/15) e há
**separação completa** entre grupos (nenhum valor de um grupo se
intercala com valores de outro), os ranks combinados formam sempre os
mesmos três blocos consecutivos: `{1..15}`, `{16..30}`, `{31..45}` — a
soma de ranks de cada bloco é sempre a mesma (120, 345, 570,
respectivamente, para blocos de 15), **independentemente de qual valor
bruto caiu em qual bloco**, e independentemente de **qual ambiente**
ocupa qual bloco. Como `Σ(R_i²/n_i)` é uma soma sobre os grupos com todos
os `n_i` iguais, ela não depende de qual grupo específico está em qual
bloco — só de que os blocos existem e têm esse tamanho. Logo, `H` (e por
consequência `p` e `epsilon² = H/(N-1)`) saem **exatamente iguais** para
qualquer par de métricas que (a) tenha os mesmos tamanhos de grupo e (b)
tenha separação completa entre grupos — mesmo que os ranks por-posição
sejam totalmente diferentes (em memória é qemu que fica no bloco mais
baixo; em cache é host).

Essa é exatamente a mesma razão pela qual as 4 métricas STREAM (host
n=15, kvm n=15, separação completa, sem overlap) também colidem entre si
com H=21.774194 idêntico — um segundo caso independente, envolvendo
métricas de subsistemas totalmente diferentes de cache/memória, que serve
de confirmação cruzada da explicação.

Essa separação completa entre host/kvm/qemu não é uma coincidência
suspeita: é o resultado central do experimento — a virtualização (kvm) e
sobretudo a emulação (qemu/TCG) impõem overhead grande o suficiente sobre
CPU/memória/cache para que as distribuições dos três ambientes não se
sobreponham em quase nenhuma métrica de computação pura. Ou seja, a
"coincidência" nasce do próprio efeito que o experimento mede, não de um
defeito no pipeline.

### Conclusão (passo d)

**Ranks diferentes + p idêntico nesta execução**, mas confirmado como
**explicável por uma propriedade matemática do teste sob separação
completa com grupos de tamanho igual**, e não por bug de reuso de array
— a instrumentação de `id()`/conteúdo descarta reuso de array, e a
recorrência do mesmo padrão nas 4 métricas STREAM (subsistema não
relacionado) descarta que seja um artefato específico do parsing de
cache/memória.

**Nenhuma correção de código foi necessária** no pipeline de extração ou
no cálculo de Kruskal-Wallis. Nenhum bug foi encontrado.

Recomendação para o artigo: mencionar explicitamente essa propriedade do
Kruskal-Wallis (H idêntico sob separação completa e grupos de tamanho
igual) na metodologia ou em nota de rodapé, para que revisores não
levantem a mesma suspeita de duplicação de array ao ver estatísticas
idênticas entre métricas de subsistemas diferentes — e para
substituir/complementar `epsilon²` por uma medida menos sensível a esse
efeito de teto (ex.: reportar diretamente a magnitude da diferença
mediana, já presente em `resumo_descritivo.csv`) quando quiser
diferenciar o tamanho do efeito entre métricas que colidem em H.

Como salvaguarda permanente contra reincidência (inclusive um bug real
futuro que produza a mesma assinatura), o item 3c deste trabalho de
reorganização adiciona ao pipeline principal uma checagem de sanidade
automática (`data/processed/alerta_estatisticas_duplicadas.csv`) que
sinaliza qualquer par de métricas com `teste_global_estat`,
`teste_global_p` e `effect_size_epsilon2` idênticos, para que este tipo de
coincidência seja sempre visível e revisado manualmente em execuções
futuras — mesmo já sabendo, por esta auditoria, que o caso atual é benigno.

**Script de diagnóstico:** [`docs/auditoria/diagnostico_duplicacao.py`](diagnostico_duplicacao.py)
(reexecutável a qualquer momento sobre `data/processed/dados_extraidos.csv`).

**Ambiente da verificação:** este diretório ainda não era um repositório
git no momento da verificação (`git init` proposto no item 5 da
reorganização); portanto não há hash de commit para registrar aqui. Caso
o repositório seja inicializado posteriormente, recomenda-se re-rodar
`docs/auditoria/diagnostico_duplicacao.py` uma vez após o primeiro commit e anexar o
hash a este documento, para fins de auditoria.
