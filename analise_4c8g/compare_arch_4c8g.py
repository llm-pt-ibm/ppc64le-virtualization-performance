#!/usr/bin/env python3
"""Comparação entre arquiteturas do overhead de virtualização (experimento 4 vCPU / 8 GiB).

Entrada: o extracted_data.csv que o analyze_benchmarks.py gerou para cada arquitetura.
Saída (em --out-dir):
  overhead_por_arquitetura.csv  overhead de KVM e QEMU vs host, por arquitetura
                                (mediana, IC95% bootstrap, Mann-Whitney + BH, delta de Cliff)
  diferenca_entre_arquiteturas.csv  Δ = overhead_x86 − overhead_p9 (pontos percentuais), IC95% bootstrap
  sensibilidade_bit_error.csv   (se --raw-x86) memória no x86 com e sem as execuções [BIT-ERROR]
  overhead_comparado.png        gráfico de barras com IC95%
  resumo.txt

Convenção do TCC: overhead = (mediana_ambiente / mediana_host − 1) × 100.
  Métricas de vazão: negativo = pior que o host. Latência: positivo = pior.

uso:
  python compare_arch_4c8g.py --p9 results/4c8g/p9/extracted_data.csv \
      --x86 results/4c8g/x86/extracted_data.csv --out-dir results/4c8g/comparacao_mediana \
      [--raw-x86 data/raw_4c8g/x86]
"""
import argparse, glob, os, re
import numpy as np, pandas as pd
from scipy import stats
from statsmodels.stats.multitest import multipletests

METRICAS = [  # (coluna, rótulo)
    ("cpu_bogo_ops_s_real", "CPU (stress-ng, bogo ops/s)"),
    ("mem_bogo_ops_s_real", "Memória (stress-ng vm, bogo ops/s)"),
    ("cache_bogo_ops_s_real", "Cache L1 (stress-ng, bogo ops/s)"),
    ("stream_copy_mbs", "STREAM Copy (MB/s)"),
    ("stream_scale_mbs", "STREAM Scale (MB/s)"),
    ("stream_add_mbs", "STREAM Add (MB/s)"),
    ("stream_triad_mbs", "STREAM Triad (MB/s)"),
    ("disk_seqread_MiBs", "Leitura sequencial (MiB/s)"),
    ("disk_seqwrite_MiBs", "Escrita sequencial (MiB/s)"),
    ("disk_iops_randread", "IOPS aleatório leitura"),
    ("disk_iops_randwrite", "IOPS aleatório escrita"),
    ("disk_iops_qd1", "IOPS QD=1"),
    ("disk_lat_avg_usec", "Latência média QD=1 (µs)"),
]
B = 10000
LIMIAR_PRATICO = 5.0  # pontos percentuais
rng = np.random.default_rng(42)


def boot_overhead(h, v, n=B):
    """Distribuição bootstrap do overhead % (medianas reamostradas independentemente)."""
    ih = rng.integers(0, len(h), (n, len(h))); iv = rng.integers(0, len(v), (n, len(v)))
    return (np.median(v[iv], axis=1) / np.median(h[ih], axis=1) - 1) * 100


def cliff(a, b):
    a, b = np.asarray(a), np.asarray(b)
    return ((b[:, None] > a[None, :]).sum() - (b[:, None] < a[None, :]).sum()) / (len(a) * len(b))


def carregar(path, arq):
    d = pd.read_csv(path)
    d = d[d.metrica.isin([m for m, _ in METRICAS])].copy()
    d["arq"] = arq
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--p9", required=True); ap.add_argument("--x86", required=True)
    ap.add_argument("--out-dir", required=True); ap.add_argument("--raw-x86")
    a = ap.parse_args(); os.makedirs(a.out_dir, exist_ok=True)
    df = pd.concat([carregar(a.p9, "ppc64le"), carregar(a.x86, "x86_64")])

    # ---- overhead por arquitetura -------------------------------------------
    linhas, boots = [], {}
    for arq in ("ppc64le", "x86_64"):
        for met, rot in METRICAS:
            h = df[(df.arq == arq) & (df.ambiente == "host") & (df.metrica == met)].valor.values
            for amb in ("kvm", "qemu"):
                v = df[(df.arq == arq) & (df.ambiente == amb) & (df.metrica == met)].valor.values
                r = {"arquitetura": arq, "ambiente": amb, "metrica": met, "rotulo": rot,
                     "n_host": len(h), "n_amb": len(v)}
                if len(h) < 3 or len(v) < 3:
                    r["obs"] = "sem dados válidos (ex.: falha sob TCG)"; linhas.append(r); continue
                bo = boot_overhead(h, v); boots[(arq, amb, met)] = bo
                r.update(mediana_host=np.median(h), mediana_amb=np.median(v),
                         cv_host_pct=100 * h.std(ddof=1) / h.mean(), cv_amb_pct=100 * v.std(ddof=1) / v.mean(),
                         overhead_pct=(np.median(v) / np.median(h) - 1) * 100,
                         ic95_inf=np.percentile(bo, 2.5), ic95_sup=np.percentile(bo, 97.5),
                         p_mw=stats.mannwhitneyu(h, v, alternative="two-sided").pvalue,
                         cliff_delta=cliff(h, v))
                linhas.append(r)
    ov = pd.DataFrame(linhas)
    m = ov.p_mw.notna()
    ov.loc[m, "p_fdr"] = multipletests(ov.loc[m, "p_mw"], method="fdr_bh")[1]
    ov["relevante"] = ov.overhead_pct.abs() >= LIMIAR_PRATICO
    ov.to_csv(os.path.join(a.out_dir, "overhead_por_arquitetura.csv"), index=False)

    # ---- diferença entre arquiteturas -------------------------------------------
    dl = []
    for met, rot in METRICAS:
        for amb in ("kvm", "qemu"):
            kp, kx = ("ppc64le", amb, met), ("x86_64", amb, met)
            if kp not in boots or kx not in boots:
                dl.append({"ambiente": amb, "metrica": met, "rotulo": rot,
                           "obs": "faltam dados válidos em uma das arquiteturas"}); continue
            op = ov[(ov.arquitetura == "ppc64le") & (ov.ambiente == amb) & (ov.metrica == met)].overhead_pct.iloc[0]
            ox = ov[(ov.arquitetura == "x86_64") & (ov.ambiente == amb) & (ov.metrica == met)].overhead_pct.iloc[0]
            dd = boots[kx] - boots[kp]  # reamostragens independentes
            lo, hi = np.percentile(dd, [2.5, 97.5])
            dl.append({"ambiente": amb, "metrica": met, "rotulo": rot, "overhead_p9_pct": op,
                       "overhead_x86_pct": ox, "delta_pp": ox - op, "ic95_inf": lo, "ic95_sup": hi,
                       "ic_exclui_zero": (lo > 0) or (hi < 0),
                       "conclusao": ("diferença relevante" if ((lo > 0) or (hi < 0)) and abs(ox - op) >= LIMIAR_PRATICO
                                     else "diferença pequena (< 5 p.p.)" if (lo > 0) or (hi < 0)
                                     else "sem diferença detectável")})
    dif = pd.DataFrame(dl)
    dif.to_csv(os.path.join(a.out_dir, "diferenca_entre_arquiteturas.csv"), index=False)

    # ---- sensibilidade às execuções [BIT-ERROR] (x86) ------------------------------
    sens = None
    if a.raw_x86:
        flag = {}
        for f in glob.glob(os.path.join(a.raw_x86, "*", "*.txt")):
            flag[os.path.basename(f)] = "[BIT-ERROR]" in open(f, errors="ignore").read()
        x = df[(df.arq == "x86_64") & (df.metrica == "mem_bogo_ops_s_real")].copy()
        x["bit_error"] = x.arquivo.map(flag).fillna(False)
        h = x[x.ambiente == "host"].valor.values
        sl = []
        for amb in ("kvm", "qemu"):
            v = x[x.ambiente == amb]
            for lbl, sub in (("todas", v), ("sem BIT-ERROR", v[~v.bit_error]), ("só BIT-ERROR", v[v.bit_error])):
                vv = sub.valor.values
                row = {"ambiente": amb, "subconjunto": lbl, "n": len(vv)}
                if len(vv) >= 3 and len(h) >= 3:
                    bo = boot_overhead(h, vv)
                    row.update(overhead_pct=(np.median(vv) / np.median(h) - 1) * 100,
                               ic95_inf=np.percentile(bo, 2.5), ic95_sup=np.percentile(bo, 97.5))
                sl.append(row)
            a_, b_ = v[v.bit_error].valor.values, v[~v.bit_error].valor.values
            if len(a_) >= 3 and len(b_) >= 3:
                sl.append({"ambiente": amb, "subconjunto": "MW BIT-ERROR × sem",
                           "n": len(a_) + len(b_), "p_mw": stats.mannwhitneyu(a_, b_).pvalue})
        sens = pd.DataFrame(sl)
        sens.to_csv(os.path.join(a.out_dir, "sensibilidade_bit_error.csv"), index=False)

    # ---- gráfico -------------------------------------------------------------
    try:
        import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
        fig, axes = plt.subplots(1, 2, figsize=(14, 7), sharey=True)
        for ax, amb in zip(axes, ("kvm", "qemu")):
            sub = ov[(ov.ambiente == amb) & ov.overhead_pct.notna()]
            rots = [r for _, r in METRICAS if r in set(sub.rotulo)]
            y = np.arange(len(rots))
            for k, (arq, cor) in enumerate((("ppc64le", "#3a6ea5"), ("x86_64", "#c0504d"))):
                s = sub[sub.arquitetura == arq].set_index("rotulo").reindex(rots)
                ax.barh(y + (k - 0.5) * 0.38, s.overhead_pct, 0.38, color=cor, label=arq,
                        xerr=[s.overhead_pct - s.ic95_inf, s.ic95_sup - s.overhead_pct], capsize=2)
            ax.axvline(0, color="k", lw=0.8)
            for t in (-LIMIAR_PRATICO, LIMIAR_PRATICO): ax.axvline(t, color="grey", ls=":", lw=0.8)
            ax.set_yticks(y); ax.set_yticklabels(rots); ax.invert_yaxis()
            ax.set_title(f"{amb.upper()} vs host"); ax.set_xlabel("overhead (%) — negativo = pior (latência: positivo = pior)")
            ax.set_xscale("symlog", linthresh=10)
        axes[0].legend(loc="lower left")
        fig.suptitle("Overhead de virtualização, 4 vCPU / 8 GiB: ppc64le (POWER9) × x86_64 (i7-8700)")
        fig.tight_layout(); fig.savefig(os.path.join(a.out_dir, "overhead_comparado.png"), dpi=150)
    except Exception as e:
        print("gráfico não gerado:", e)

    # ---- resumo ---------------------------------------------------------------
    with open(os.path.join(a.out_dir, "resumo.txt"), "w") as f:
        f.write("Overhead vs host (mediana, IC95% bootstrap, p FDR-BH)\n")
        for _, r in ov.iterrows():
            if pd.isna(r.get("overhead_pct")):
                f.write(f"  {r.arquitetura:8s} {r.ambiente:4s} {r.rotulo:38s} {r.obs}\n"); continue
            f.write(f"  {r.arquitetura:8s} {r.ambiente:4s} {r.rotulo:38s} {r.overhead_pct:+8.2f}% "
                    f"[{r.ic95_inf:+.2f}; {r.ic95_sup:+.2f}] p_fdr={r.p_fdr:.3g} cliff={r.cliff_delta:+.2f}\n")
        f.write("\nDiferença x86 − ppc64le (pontos percentuais)\n")
        for _, r in dif.iterrows():
            if pd.isna(r.get("delta_pp")):
                f.write(f"  {r.ambiente:4s} {r.rotulo:38s} {r.obs}\n"); continue
            f.write(f"  {r.ambiente:4s} {r.rotulo:38s} p9 {r.overhead_p9_pct:+8.2f}  x86 {r.overhead_x86_pct:+8.2f}  "
                    f"Δ {r.delta_pp:+8.2f} [{r.ic95_inf:+.2f}; {r.ic95_sup:+.2f}]  {r.conclusao}\n")
        if sens is not None:
            f.write("\nSensibilidade: memória x86 com/sem execuções [BIT-ERROR]\n" + sens.to_string(index=False) + "\n")
    print(open(os.path.join(a.out_dir, "resumo.txt")).read())


if __name__ == "__main__":
    main()
