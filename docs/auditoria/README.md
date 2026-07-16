# docs/auditoria/

Esta pasta documenta investigações de integridade estatística e de
sincronia entre pipelines, conduzidas durante a preparação deste
artefato. Não é necessária para reproduzir o experimento (ver
[REPRODUCE.md](../REPRODUCE.md) na raiz de `docs/`), mas serve como
evidência de rigor metodológico e pode ser consultada por revisores.

- `AUDITORIA_SCRIPTS.md` — investigação da coincidência de estatística
  idêntica entre métricas de cache e memória (e STREAM); conclusão:
  benigna, propriedade matemática do Kruskal-Wallis, não bug.
- `diagnostico_duplicacao.py` — script de diagnóstico usado nessa
  investigação, reexecutável a qualquer momento.
- `AUDITORIA_SCRIPTS_RESUMO.md` — teste dos scripts auxiliares
  `resumo_benchmarks_overhead.py`/`resumo_benchmarks_qemu.py` contra o
  pipeline principal.
- `PIPELINE_FIGURAS.md` — documenta os dois caminhos independentes de
  geração de figuras (Python automático vs. R com números transcritos
  manualmente) e a verificação de sincronia entre eles.
