#!/usr/bin/env python3
"""
diagnostico_duplicacao.py

Investiga o achado de que os testes de Kruskal-Wallis globais para
cache_bogo_ops_s_cpu, cache_bogo_ops_s_real, mem_bogo_ops_s_cpu e
mem_bogo_ops_s_real produziram estatística IDÊNTICA até a 4a casa decimal
(H=39.1304, p=3.184e-09, epsilon^2=0.8893) em resultados_estatisticos.csv,
apesar de cache e memória serem subsistemas medidos por stressors
diferentes do stress-ng, em escalas numéricas completamente distintas.

Ver docs/auditoria/AUDITORIA_SCRIPTS.md, seção "Duplicação de estatística
cache/memória", para a conclusão final e a justificativa matemática.

USO:
    python3 docs/diagnostico_duplicacao.py \
        [--csv data/processed/dados_extraidos.csv]
"""

import argparse

import numpy as np
import pandas as pd
from scipy import stats


def carregar_arrays_por_ambiente(df, metrica):
    """Para uma métrica, retorna dict ambiente -> array de valores,
    ordenado por (vm_id, timestamp) para garantir correspondência estável
    entre chamadas (mesma ordem para cache e memória)."""
    sub = df[df["metrica"] == metrica].sort_values(["vm_id", "timestamp"])
    return {amb: g["valor"].values for amb, g in sub.groupby("ambiente", sort=True)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/processed/dados_extraidos.csv")
    args = ap.parse_args()

    df = pd.read_csv(args.csv)

    print("=" * 78)
    print("DIAGNÓSTICO: duplicação de estatística cache vs. memória")
    print("=" * 78)

    metricas_pares = [
        ("cache_bogo_ops_s_real", "mem_bogo_ops_s_real"),
        ("cache_bogo_ops_s_cpu", "mem_bogo_ops_s_cpu"),
    ]

    for metrica_cache, metrica_mem in metricas_pares:
        print(f"\n--- Par: {metrica_cache}  x  {metrica_mem} ---")

        arr_cache_por_amb = carregar_arrays_por_ambiente(df, metrica_cache)
        arr_mem_por_amb = carregar_arrays_por_ambiente(df, metrica_mem)

        ambientes = sorted(set(arr_cache_por_amb) & set(arr_mem_por_amb))

        todos_cache = []
        todos_mem = []
        for amb in ambientes:
            vc = arr_cache_por_amb[amb]
            vm = arr_mem_por_amb[amb]
            print(f"\n  ambiente={amb}  (n_cache={len(vc)}, n_mem={len(vm)})")
            print(f"    {metrica_cache:<25s}: {np.array2string(vc, precision=2)}")
            print(f"    {metrica_mem:<25s}: {np.array2string(vm, precision=2)}")

            if len(vc) == len(vm) and len(vc) >= 2:
                rho, p_rho = stats.spearmanr(vc, vm)
                print(f"    Spearman(cache, mem) dentro do ambiente '{amb}': "
                      f"rho={rho:.4f}, p={p_rho:.4g}")
            else:
                print(f"    [aviso] tamanhos diferentes (n_cache={len(vc)} "
                      f"vs n_mem={len(vm)}) — pulando Spearman por ambiente")

            todos_cache.append(vc)
            todos_mem.append(vm)

        todos_cache = np.concatenate(todos_cache)
        todos_mem = np.concatenate(todos_mem)
        n_total = len(todos_cache)

        print(f"\n  Total combinado (todos os ambientes, N={n_total}):")
        if len(todos_cache) == len(todos_mem):
            rho_total, p_total = stats.spearmanr(todos_cache, todos_mem)
            print(f"    Spearman(cache, mem) global: rho={rho_total:.4f}, p={p_total:.4g}")
        else:
            print(f"    [aviso] N difere entre cache ({len(todos_cache)}) e "
                  f"mem ({len(todos_mem)}) — Spearman global não calculado")

        # --- Ranks dos 45 valores combinados (juntando os 3 ambientes) ---
        ranks_cache = stats.rankdata(todos_cache)
        ranks_mem = stats.rankdata(todos_mem)
        ranks_identicos = np.array_equal(ranks_cache, ranks_mem)
        print(f"\n  Ranks (rankdata) dos {n_total} valores combinados são "
              f"IDÊNTICOS entre cache e memória? -> {ranks_identicos}")
        if not ranks_identicos:
            n_diff = np.sum(ranks_cache != ranks_mem)
            print(f"    {n_diff}/{n_total} posições têm rank diferente "
                  f"entre cache e memória (arrays de rank NÃO são iguais).")

        # --- Kruskal-Wallis recalculado aqui, independente do pipeline ---
        grupos_cache = [arr_cache_por_amb[a] for a in ambientes]
        grupos_mem = [arr_mem_por_amb[a] for a in ambientes]
        h_cache, p_cache = stats.kruskal(*grupos_cache)
        h_mem, p_mem = stats.kruskal(*grupos_mem)
        print(f"\n  Kruskal-Wallis recalculado agora (fora do pipeline):")
        print(f"    {metrica_cache}: H={h_cache:.6f}, p={p_cache:.6e}")
        print(f"    {metrica_mem}:   H={h_mem:.6f}, p={p_mem:.6e}")
        stats_batem = np.isclose(h_cache, h_mem) and np.isclose(p_cache, p_mem)
        print(f"    Estatísticas batem (confirma o achado)? -> {stats_batem}")

        # --- Checagem de separação completa entre grupos (explicação
        # matemática candidata: se os grupos não se sobrepõem e têm o
        # mesmo tamanho, o valor de H do Kruskal-Wallis só depende de QUAL
        # BLOCO de ranks consecutivos (1..n1, n1+1..n1+n2, ...) cada grupo
        # ocupa, não de quais valores brutos geraram aquele bloco. Dois
        # conjuntos de grupos com tamanhos iguais e ambos com separação
        # completa produzem H idêntico mesmo com valores brutos
        # completamente diferentes e ranks por-valor diferentes.) ---
        def tem_separacao_completa(grupos):
            faixas = [(g.min(), g.max()) for g in grupos]
            faixas_ordenadas = sorted(faixas, key=lambda t: t[0])
            for (lo1, hi1), (lo2, hi2) in zip(faixas_ordenadas, faixas_ordenadas[1:]):
                if hi1 >= lo2:
                    return False
            return True

        tam_iguais = len(set(len(g) for g in grupos_cache)) == 1 and len(set(len(g) for g in grupos_mem)) == 1
        sep_cache = tem_separacao_completa(grupos_cache)
        sep_mem = tem_separacao_completa(grupos_mem)
        print(f"\n  Grupos (por ambiente) têm todos o mesmo tamanho? -> {tam_iguais}")
        print(f"  '{metrica_cache}' tem separação COMPLETA entre ambientes (sem overlap)? -> {sep_cache}")
        print(f"  '{metrica_mem}' tem separação COMPLETA entre ambientes (sem overlap)? -> {sep_mem}")

        if ranks_identicos:
            print("\n  CONCLUSÃO deste par: ranks combinados idênticos -> "
                  "coincidência estruturalmente inevitável dado os dados brutos "
                  "(explicação benigna).")
        elif stats_batem and tam_iguais and sep_cache and sep_mem:
            print("\n  CONCLUSÃO deste par: ranks por-valor DIFEREM, mas a "
                  "estatística de Kruskal-Wallis bate mesmo assim. Isso é "
                  "explicado por uma propriedade matemática do teste, não por "
                  "bug de reuso de array: com grupos de MESMO TAMANHO e "
                  "SEPARAÇÃO COMPLETA (nenhum overlap de valores entre "
                  "ambientes) em ambas as métricas, H de Kruskal-Wallis "
                  "depende apenas de quais blocos de ranks consecutivos "
                  "(1..15, 16..30, 31..45) cada ambiente ocupa — não de quais "
                  "valores brutos geraram esse bloco. Como os 3 ambientes têm "
                  "n=15 em ambas as métricas e ambas têm separação completa, "
                  "H (e portanto p e epsilon^2) saem idênticos por construção "
                  "matemática, mesmo com arrays de entrada e ranks por-valor "
                  "totalmente diferentes. Ver docs/auditoria/AUDITORIA_SCRIPTS.md.")
        elif stats_batem:
            print("\n  CONCLUSÃO deste par: ranks DIFEREM e a explicação de "
                  "separação completa NÃO se confirmou plenamente — "
                  "necessário inspecionar analisar_metrica() com "
                  "instrumentação de id() dos arrays para descartar reuso "
                  "acidental de array antes de aceitar como coincidência.")
        else:
            print("\n  CONCLUSÃO deste par: estatísticas NÃO batem nesta "
                  "execução — o achado original pode ter sido específico de "
                  "uma versão anterior dos dados/pipeline. Registrar como "
                  "não reproduzido nesta auditoria.")


if __name__ == "__main__":
    main()
