#!/usr/bin/env python3
"""
summary_4c8g.py — summary CSVs and overhead table for the 4c8g experiment
(ppc64le x x86_64, 1 VM per environment, 15 runs each).

Same per-file extraction as benchmark_summary_overhead.py and the same
overhead convention as the paper figures:
    overhead(%) = (environment_mean / host_mean - 1) * 100
plus, per (architecture, environment, metric):
  - 95% CI via the delta* bootstrap of analyze_benchmarks.py
    (_ci_delta_star_a_partir_de, B=10000, seed 42), applied to the
    overhead itself: host and environment resampled independently;
  - overhead by median (robustness check, table only).

Differences from the paper's summary scripts:
  - stress-ng blocks that failed ([FALHA-TCG], or "unsuccessful run"
    without [BIT-ERROR]) are dropped, as are STREAM values <= 0 or
    blocks marked [FALHA-TCG]; [BIT-ERROR] runs stay;
  - CPU comes from the cpu_subset series: per-run geometric mean of the
    80 methods common to both architectures, and a 76-method variant
    without explog, zeta, pi and factorial. The main benchmark's
    "--cpu-method all" (cpu_bogo) is kept in the CSV only.

Usage (from the repo root):
  python3 src/analysis/summary_4c8g.py \
      --raw-dir data/raw_4c8g --out-dir data/processed/4c8g \
      --table-dir results/tables/4c8g
"""
import argparse
import csv
import glob
import os
import re
import statistics as st
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from benchmark_summary_overhead import METRIC_LABELS, LOWER_IS_BETTER, extract_metrics  # noqa: E402
from analyze_benchmarks import _ci_delta_star_a_partir_de  # noqa: E402

ARQUITETURAS = {"p9": "ppc64le", "x86": "x86_64"}
# (environment, main-benchmark folder, cpu_subset folder, disk label)
AMBIENTES = [
    ("Host", "host", "host", "DISK_HOST"),
    ("KVM", "kvm-exp", "kvm", "DISK_EXT4"),
    ("QEMU", "qemu-exp", "qemu", "DISK_EXT4"),
]
LIBM = {"explog", "zeta", "pi", "factorial"}
METRICAS = {k: v for k, v in METRIC_LABELS.items()
            if not k.startswith(("matrix", "nccl"))}
METRICAS["cpu_subset80"] = "CPU subset, 80 common methods (geomean bogo ops/s)"
METRICAS["cpu_subset76"] = "CPU subset, 76 methods w/o explog/zeta/pi/factorial (geomean bogo ops/s)"
STRESSNG = {"cpu_bogo": "CPU", "mem_bogo": "MEMORY", "cache_bogo": "CACHE"}
STREAM = ("stream_copy", "stream_scale", "stream_add", "stream_triad")
B, SEED = 10000, 42


def bloco(txt, nome):
    m = re.search(rf"^=== {nome}\b.*?(?=^=== |\Z)", txt, re.S | re.M)
    return m.group(0) if m else ""


def stressng_falhou(b):
    return "[FALHA-TCG]" in b or ("unsuccessful run" in b and "[BIT-ERROR]" not in b)


def metricas_validas(txt, disk_label):
    r = extract_metrics(txt, disk_label=disk_label)
    for k, sec in STRESSNG.items():
        if k in r and stressng_falhou(bloco(txt, sec)):
            del r[k]
    falha_stream = "[FALHA-TCG]" in bloco(txt, "STREAM")
    for k in STREAM:
        if k in r and (falha_stream or r[k] <= 0):
            del r[k]
    return r


def geomeans_cpu(txt, comuns):
    d = {m: float(v) for m, v in re.findall(r"^METHOD (\S+) ([\d.]+)", txt, re.M)}
    faltam = [m for m in comuns if d.get(m, 0) <= 0]
    if faltam:
        raise ValueError(f"cpu_subset without valid value for: {faltam}")
    g = lambda ms: float(np.exp(np.mean([np.log(d[m]) for m in ms])))
    return {"cpu_subset80": g(comuns), "cpu_subset76": g([m for m in comuns if m not in LIBM])}


def carrega(raw_dir, arq, comuns):
    """{environment: {metric: [values]}} for one architecture."""
    dados = {}
    for amb, pasta, pasta_cpu, disk in AMBIENTES:
        vals = {k: [] for k in METRICAS}
        for f in sorted(glob.glob(os.path.join(raw_dir, arq, pasta, "benchmark_*.txt"))):
            for k, v in metricas_validas(open(f, errors="ignore").read(), disk).items():
                if k in vals:
                    vals[k].append(v)
        for f in sorted(glob.glob(os.path.join(raw_dir, arq, "cpu_subset", pasta_cpu, "benchmark_cpu_*.txt"))):
            for k, v in geomeans_cpu(open(f, errors="ignore").read(), comuns).items():
                vals[k].append(v)
        dados[amb] = vals
    return dados


def ic_overhead(h, v):
    """delta* bootstrap CI (analyze_benchmarks.py) of the mean-based overhead."""
    h, v = np.asarray(h, float), np.asarray(v, float)
    rng = np.random.default_rng(SEED)
    boot = np.empty(B)
    for i in range(B):
        boot[i] = (rng.choice(v, size=len(v), replace=True).mean()
                   / rng.choice(h, size=len(h), replace=True).mean() - 1) * 100
    _, lo, hi = _ci_delta_star_a_partir_de((v.mean() / h.mean() - 1) * 100, boot, 0.05)
    return lo, hi


def resume(vals):
    n = len(vals)
    q = st.quantiles(vals, n=4, method="inclusive") if n >= 4 else [float("nan")] * 3
    return {"n": n, "media": st.mean(vals), "mediana": st.median(vals),
            "desvio": st.stdev(vals) if n > 1 else 0.0, "iqr": q[2] - q[0]}


def r4(x):
    return "" if x is None or x != x else round(x, 4)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw-dir", default="data/raw_4c8g")
    ap.add_argument("--out-dir", default="data/processed/4c8g")
    ap.add_argument("--table-dir", default="results/tables/4c8g")
    a = ap.parse_args()

    listas = {arq: open(os.path.join(a.raw_dir, arq, "cpu_subset", "meta", "cpu_methods_subset.txt")).read().split()
              for arq in ARQUITETURAS}
    comuns = [m for m in listas["p9"] if m in set(listas["x86"])]
    print(f"cpu_subset: {len(comuns)} common methods "
          f"(only x86: {sorted(set(listas['x86']) - set(comuns))}, only p9: {sorted(set(listas['p9']) - set(comuns))})")

    tabela = []
    campos = ["arquitetura", "ambiente", "metrica_id", "metrica", "n", "media", "mediana", "desvio", "iqr",
              "overhead_pct", "ic95_inf", "ic95_sup", "overhead_mediana_pct", "lower_is_better"]
    for arq, nome_arq in ARQUITETURAS.items():
        dados = carrega(a.raw_dir, arq, comuns)
        os.makedirs(os.path.join(a.out_dir, arq), exist_ok=True)
        host = dados["Host"]
        for amb, *_ in AMBIENTES:
            linhas = []
            for k, rot in METRICAS.items():
                vals = dados[amb][k]
                if not vals:
                    continue
                s = resume(vals)
                ov = lo = hi = ovm = None
                if amb != "Host" and len(host[k]) >= 2 and len(vals) >= 2:
                    hs = resume(host[k])
                    ov = (s["media"] / hs["media"] - 1) * 100
                    ovm = (s["mediana"] / hs["mediana"] - 1) * 100
                    lo, hi = ic_overhead(host[k], vals)
                    tabela.append({"arquitetura": nome_arq, "ambiente": amb, "metrica_id": k,
                                   "n_host": hs["n"], "media_host": hs["media"], "mediana_host": hs["mediana"],
                                   "n": s["n"], "overhead_media_pct": ov, "ic95_inf": lo, "ic95_sup": hi,
                                   "overhead_mediana_pct": ovm})
                linhas.append({"arquitetura": nome_arq, "ambiente": amb, "metrica_id": k, "metrica": rot,
                               "n": s["n"], "media": r4(s["media"]), "mediana": r4(s["mediana"]),
                               "desvio": r4(s["desvio"]), "iqr": r4(s["iqr"]), "overhead_pct": r4(ov),
                               "ic95_inf": r4(lo), "ic95_sup": r4(hi), "overhead_mediana_pct": r4(ovm),
                               "lower_is_better": "1" if k in LOWER_IS_BETTER else "0"})
            arq_csv = os.path.join(a.out_dir, arq, f"{amb.lower()}_summary.csv")
            with open(arq_csv, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=campos)
                w.writeheader()
                w.writerows(linhas)
            print(f"{arq_csv}: {len(linhas)} metrics")

    escreve_tabela(tabela, a.table_dir)


# --- table (CSV + LaTeX) --------------------------------------------------
TABELA_METRICAS = [  # metrica_id, LaTeX label, unit of the host mean
    ("cpu_subset80", "CPU (80 métodos comuns)", "bogo ops/s"),
    ("cpu_subset76", "CPU (76 métodos, sem libm)\\tnote{a}", "bogo ops/s"),
    ("mem_bogo", "Memória (stress-ng vm)", "bogo ops/s"),
    ("cache_bogo", "Cache (stress-ng)", "bogo ops/s"),
    ("stream_copy", "STREAM Copy", "MB/s"),
    ("stream_scale", "STREAM Scale", "MB/s"),
    ("stream_add", "STREAM Add", "MB/s"),
    ("stream_triad", "STREAM Triad", "MB/s"),
    ("seq_read_bw", "Leitura sequencial", "MiB/s"),
    ("seq_write_bw", "Escrita sequencial", "MiB/s"),
    ("rand_read_iops", "IOPS aleatório (leitura)", "IOPS"),
    ("rand_write_iops", "IOPS aleatório (escrita)", "IOPS"),
    ("lat_read_iops", "IOPS QD=1\\tnote{c}", "IOPS"),
    ("lat_clat_avg_us", "Latência média QD=1\\tnote{d}", "$\\mu$s"),
]
NOTAS = {
    ("ppc64le", "KVM", "cache_bogo"): "distribuição bimodal no KVM ppc64le; ver mediana na tabela",
    ("ppc64le", "KVM", "lat_read_iops"): "QD=1 no ppc64le sensível à sessão do host (ver ameaças à validade)",
    ("ppc64le", "QEMU", "lat_read_iops"): "QD=1 no ppc64le sensível à sessão do host (ver ameaças à validade)",
}
SEM_DADO = "sem execução válida sob TCG (STREAM: 0,0 MB/s no ppc64le, SIGILL no x86_64)"


def br(x, casas=2, sinal=True):
    """pt-BR number for LaTeX: decimal comma, dot thousands, math-mode sign."""
    s = (f"{x:+.{casas}f}" if sinal else f"{x:,.{casas}f}").replace(",", "X").replace(".", ",").replace("X", ".")
    return s.replace("-", "$-$").replace("+", "$+$")


def escreve_tabela(tabela, table_dir):
    os.makedirs(table_dir, exist_ok=True)
    idx = {(t["arquitetura"], t["ambiente"], t["metrica_id"]): t for t in tabela}
    campos = ["arquitetura", "metrica_id", "unidade", "n_host", "media_host", "mediana_host", "ambiente", "n",
              "overhead_media_pct", "ic95_inf", "ic95_sup", "overhead_mediana_pct", "nota"]
    linhas_csv, tex = [], []
    for nome_arq in ARQUITETURAS.values():
        tex.append(f"\\midrule\n\\multicolumn{{6}}{{l}}{{\\textbf{{{nome_arq.replace('_', chr(92) + '_')}}}}} \\\\")
        for k, rot, un in TABELA_METRICAS:
            hostref = next((idx[(nome_arq, amb, k)] for amb in ("KVM", "QEMU") if (nome_arq, amb, k) in idx), None)
            if hostref is None:
                continue
            celulas = []
            for amb in ("KVM", "QEMU"):
                t = idx.get((nome_arq, amb, k))
                nota = NOTAS.get((nome_arq, amb, k), "")
                if t is None:
                    linhas_csv.append({"arquitetura": nome_arq, "metrica_id": k, "unidade": un.replace("$\\mu$", "u"),
                                       "n_host": hostref["n_host"], "media_host": r4(hostref["media_host"]),
                                       "mediana_host": r4(hostref["mediana_host"]), "ambiente": amb, "n": 0,
                                       "nota": SEM_DADO})
                    celulas += ["\\multicolumn{1}{c}{---\\tnote{e}}", "\\multicolumn{1}{c}{---}"]
                    continue
                linhas_csv.append({"arquitetura": nome_arq, "metrica_id": k, "unidade": un.replace("$\\mu$", "u"),
                                   "n_host": t["n_host"], "media_host": r4(t["media_host"]),
                                   "mediana_host": r4(t["mediana_host"]), "ambiente": amb, "n": t["n"],
                                   "overhead_media_pct": r4(t["overhead_media_pct"]), "ic95_inf": r4(t["ic95_inf"]),
                                   "ic95_sup": r4(t["ic95_sup"]), "overhead_mediana_pct": r4(t["overhead_mediana_pct"]),
                                   "nota": nota})
                marca = "\\tnote{b}" if k == "cache_bogo" and nota else ""
                celulas += [f"{br(t['overhead_media_pct'])}{marca} [{br(t['ic95_inf'])}; {br(t['ic95_sup'])}]",
                            br(t["overhead_mediana_pct"])]
            if nome_arq != "ppc64le":  # note c applies to ppc64le only
                rot = rot.replace("\\tnote{c}", "")
            host = f"{br(hostref['media_host'], 0 if hostref['media_host'] >= 100 else 1, sinal=False)} {un}"
            tex.append(f"{rot} & {host} & " + " & ".join(celulas) + " \\\\")

    with open(os.path.join(table_dir, "overhead_4c8g.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(linhas_csv)

    corpo = "\n".join(tex)
    with open(os.path.join(table_dir, "overhead_4c8g.tex"), "w") as f:
        f.write(f"""% Gerado por src/analysis/summary_4c8g.py. Requer \\usepackage{{booktabs,threeparttable,amsmath}}.
\\begin{{table*}}[htbp]
\\centering
\\small
\\begin{{threeparttable}}
\\caption{{Overhead de virtualização em relação ao host (\\%), 4 vCPU / 8 GiB, 15 execuções por ambiente: média com IC95\\% bootstrap e, como verificação de robustez, overhead pela mediana.}}
\\label{{tab:overhead-4c8g}}
\\begin{{tabular}}{{lrrrrr}}
\\toprule
 & & \\multicolumn{{2}}{{c}}{{KVM}} & \\multicolumn{{2}}{{c}}{{QEMU (TCG)}} \\\\
\\cmidrule(lr){{3-4}} \\cmidrule(lr){{5-6}}
Métrica & Host (média) & Média [IC95\\%] & Mediana & Média [IC95\\%] & Mediana \\\\
{corpo}
\\bottomrule
\\end{{tabular}}
\\begin{{tablenotes}}\\footnotesize
\\item Overhead $= (\\bar{{x}}_{{\\text{{ambiente}}}} / \\bar{{x}}_{{\\text{{host}}}} - 1) \\times 100$; IC95\\% bootstrap delta$^*$ ($B = 10\\,000$). Vazão: negativo = pior que o host; latência: positivo = pior.
\\item[a] Sem explog, zeta, pi e factorial (dependentes da libm; glibc 2.35 no host x86\\_64 e 2.28 no convidado).
\\item[b] Distribuição bimodal no KVM ppc64le; ver mediana na tabela.
\\item[c] QD=1 no ppc64le sensível à sessão do host (ver ameaças à validade).
\\item[d] Somente na tabela; nas figuras de disco usa-se IOPS QD=1.
\\item[e] Sem execução válida sob TCG (ppc64le: STREAM reporta 0,0 MB/s; x86\\_64: STREAM termina com SIGILL).
\\end{{tablenotes}}
\\end{{threeparttable}}
\\end{{table*}}
""")
    print(f"table: {table_dir}/overhead_4c8g.{{csv,tex}}")


if __name__ == "__main__":
    main()
