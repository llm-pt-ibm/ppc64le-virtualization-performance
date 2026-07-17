#!/usr/bin/env python3
"""
analise_benchmarks.py

Parser + pipeline estatístico para os logs de benchmark
(host / KVM / QEMU puro) do experimento de virtualização ppc64le.

USO:
    python3 analise_benchmarks.py --logs-dir /caminho/para/pasta_com_host_kvm_qemu \
                                   --out-dir /caminho/para/saida

Espera encontrar, dentro de --logs-dir, três subpastas:
    host/           -> benchmark_host_*.txt
    kvm-results/     -> benchmark_vm_*.txt
    qemu-results/    -> benchmark_qemu_*.txt

DEPENDÊNCIAS (instalar com pip):
    pip install pandas numpy scipy scikit-posthocs matplotlib seaborn statsmodels --break-system-packages

SAÍDAS (em --out-dir):
    dados_extraidos.csv       -> uma linha por (arquivo, métrica), formato "tidy"
    resumo_descritivo.csv     -> média, mediana, desvio, IQR por métrica x ambiente
    resultados_estatisticos.csv -> normalidade, homogeneidade, teste global (+ p_fdr_corrigido
                                    via Benjamini-Hochberg entre todas as métricas), post-hoc,
                                    IC bootstrap
    avisos_parsing.csv          -> arquivos onde uma métrica esperada não foi encontrada
    alerta_estatisticas_duplicadas.csv -> pares de métricas com teste_global_estat/p/epsilon²
                                           EXATAMENTE idênticos (ver checar_estatisticas_duplicadas())
    metadata.json                -> versão/commit git, timestamp e seed da execução
    relatorio.txt              -> resumo legível em português (avisos de heterogeneidade entre
                                    VMs referenciam "pseudo-replicação" — ver
                                    docs/GLOSSARIO_METODOLOGICO.md para o que isso significa)
    graficos/*.png              -> boxplot/stripplot por métrica (small multiples, 1 subplot por
                                    ambiente, escala Y independente por painel)
    heatmaps/*.png              -> resumo compacto dos testes formais e da heterogeneidade entre
                                    VMs, em par curada/completa: heatmap_significancia.png e
                                    heatmap_heterogeneidade_vm.png (1 métrica por domínio
                                    experimental, ver METRICAS_PRINCIPAIS) + as versões
                                    "_completo" com as ~19 métricas (auditoria/apêndice)

NOTA METODOLÓGICA — pseudo-replicação: os tratamentos KVM/QEMU usam 3 VMs
× 5 execuções (não 15 VMs independentes). Isso é um desenho aninhado
correto e esperado (ver docs/GLOSSARIO_METODOLOGICO.md, com exemplo
numérico real), não um erro de coleta — o pipeline já trata isso via
bootstrap estratificado por VM (bootstrap_ci_estratificado_por_vm) e via
checar_heterogeneidade_vms, que sinaliza explicitamente quando as VMs de
um tratamento divergem entre si o suficiente pra merecer cautela.
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import warnings
from datetime import datetime, timezone
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch
from scipy import stats
from statsmodels.stats.multitest import multipletests

warnings.filterwarnings("ignore")

# --------------------------------------------------------------------------
# Métricas esperadas por ambiente, usadas por `avisos_parsing` para detectar
# quando um arquivo deveria ter produzido uma métrica e não produziu (ex.:
# arquivo de host sem "Avg bus bandwidth" para NCCL). GPU e STREAM só são
# esperados em host/kvm: QEMU puro (TCG) não faz passthrough de GPU, e a
# saída STREAM sob QEMU é descartada de propósito por ser numericamente
# inválida (ver comentário no bloco STREAM de parse_file) — portanto a
# ausência dessas métricas em arquivos QEMU não é um aviso de parsing.
METRICAS_ESPERADAS_COMUNS = [
    "cpu_bogo_ops_s_real", "cpu_bogo_ops_s_cpu",
    "mem_bogo_ops_s_real", "mem_bogo_ops_s_cpu",
    "cache_bogo_ops_s_real", "cache_bogo_ops_s_cpu",
    "disk_seqread_MiBs", "disk_seqwrite_MiBs",
    "disk_iops_randread", "disk_iops_randwrite",
    "disk_iops_qd1", "disk_lat_avg_usec",
]
METRICAS_ESPERADAS_HOST_KVM = [
    "stream_copy_mbs", "stream_scale_mbs", "stream_add_mbs", "stream_triad_mbs",
    "gpu_matrixmul_gflops",
    "gpu_nccl_allreduce_busbw_gbs", "gpu_nccl_allgather_busbw_gbs", "gpu_nccl_sendrecv_busbw_gbs",
]

# --------------------------------------------------------------------------
# 1. REGEX DE EXTRAÇÃO
# --------------------------------------------------------------------------
# stress-ng linha de métricas, ex:
# stress-ng: info:  [8864] cpu             123456     60.01    409.02     10.27     16463.20        2356.34
STRESSNG_LINE = re.compile(
    r"^stress-ng:\s*info:\s*\[\d+\]\s+(?P<stressor>\S+)\s+"
    r"(?P<bogo_ops>\d+)\s+(?P<real_time>[\d.]+)\s+(?P<usr_time>[\d.]+)\s+"
    r"(?P<sys_time>[\d.]+)\s+(?P<bogo_ops_s_real>[\d.]+)\s+(?P<bogo_ops_s_cpu>[\d.]+)\s*$"
)

# fio: linhas de IOPS e latência (formato "read:" / "write:" seguido de bw=...)
FIO_IOPS_LINE = re.compile(
    r"^\s*(read|write):\s+IOPS=([\d.]+k?),\s+BW=([\d.]+)(KiB|MiB)/s"
)
FIO_LAT_LINE = re.compile(
    r"^\s*(?:clat|lat)\s*\(usec\):\s*min=\d+,\s*max=\d+,\s*avg=\s*([\d.]+)"
)
FIO_LAT_LINE_MSEC = re.compile(
    r"^\s*(?:clat|lat)\s*\(msec\):\s*min=\d+,\s*max=\d+,\s*avg=\s*([\d.]+)"
)

# STREAM: Copy:  117565.93 ...   (ajuste conforme formato real do seu log)
STREAM_LINE = re.compile(
    r"^(Copy|Scale|Add|Triad):\s+([\d.]+)"
)

# CUDA matrixMul: procura por algo como "GFlop/s" na linha
MATRIXMUL_LINE = re.compile(r"([\d.]+)\s*GFlop/s", re.IGNORECASE)

# NCCL busbw: CORRIGIDO — em vez de indexar colunas da linha de dados (frágil
# por causa do campo #wrong, que alterna entre "0" e "N/A" e desloca a
# posição), lê a linha de resumo que o próprio nccl-tests já calcula:
#   "# Avg bus bandwidth    : 19.4013"
NCCL_AVG_LINE = re.compile(r"Avg bus bandwidth\s*:\s*([\d.]+)")


def parse_stressng_block(lines: list[str], stressor_name: str) -> Optional[dict]:
    """Procura a linha de métricas do stress-ng para um stressor específico
    (cpu, memória/vm, cache) dentro de uma lista de linhas."""
    for ln in lines:
        m = STRESSNG_LINE.match(ln.strip())
        if m and stressor_name in m.group("stressor"):
            return {
                "bogo_ops": float(m.group("bogo_ops")),
                "real_time_s": float(m.group("real_time")),
                "bogo_ops_s_real": float(m.group("bogo_ops_s_real")),
                "bogo_ops_s_cpu": float(m.group("bogo_ops_s_cpu")),
            }
    return None


def parse_fio_block(lines: list[str]) -> dict:
    """Extrai IOPS (read/write), BW e latência média (usec) de um bloco de
    saída do fio. Retorna dict com o que encontrar."""
    out = {}
    for ln in lines:
        m = FIO_IOPS_LINE.match(ln)
        if m:
            direction, iops_raw, bw, bw_unit = m.groups()
            iops = float(iops_raw.replace("k", "")) * (1000 if "k" in iops_raw else 1)
            bw_val = float(bw) * (1024 if bw_unit == "MiB" else 1)  # normaliza p/ KiB/s
            out[f"iops_{direction}"] = iops
            out[f"bw_kibs_{direction}"] = bw_val
        m2 = FIO_LAT_LINE.match(ln)
        if m2:
            out.setdefault("lat_avg_usec", float(m2.group(1)))
        m3 = FIO_LAT_LINE_MSEC.match(ln)
        if m3:
            out.setdefault("lat_avg_usec", float(m3.group(1)) * 1000)
    return out


def extract_section(text: str, header_regex: str, next_header_regex: str = r"^={10,}\s*$") -> list[str]:
    """Retorna as linhas entre um cabeçalho '=== ... ===' que bate com
    header_regex e o próximo separador '===...===' OU o próximo cabeçalho
    '=== [...] ===' / '=== ... ===' de qualquer tipo (importante: nos logs,
    subseções de disco consecutivas -- ex. IOPS ALEATÓRIO seguida de
    LATÊNCIA -- não têm um separador '========' entre si, só entre blocos
    maiores; sem essa checagem extra o parser vaza de uma subseção pra
    outra)."""
    lines = text.splitlines()
    collecting = False
    block = []
    for ln in lines:
        is_any_header = bool(re.match(r"^===.*===\s*$", ln.strip()))
        if re.search(header_regex, ln):
            collecting = True
            block = []
            continue
        if collecting and (re.match(next_header_regex, ln) or is_any_header):
            break
        if collecting:
            block.append(ln)
    return block


def parse_file(path: str, ambiente: str, vm_id: str = "host",
                avisos: Optional[list[dict]] = None) -> list[dict]:
    """Faz o parsing de um arquivo de log completo e retorna uma lista de
    dicts no formato tidy: {arquivo, ambiente, vm_id, metrica, valor}.

    Se `avisos` (lista) for passado, qualquer métrica esperada para este
    ambiente que não aparecer nas linhas extraídas deste arquivo é
    registrada nele (ver METRICAS_ESPERADAS_COMUNS/_HOST_KVM), para virar
    data/processed/avisos_parsing.csv em main()."""
    with open(path, "r", errors="ignore") as f:
        text = f.read()

    fname = os.path.basename(path)
    m_ts = re.search(r"(\d{8}_\d{6})", fname)
    timestamp = m_ts.group(1) if m_ts else fname

    rows = []

    def add(metric, value):
        if value is not None:
            rows.append({"arquivo": fname, "ambiente": ambiente, "vm_id": vm_id,
                          "timestamp": timestamp, "metrica": metric, "valor": value})

    # --- CPU ---
    cpu_block = extract_section(text, r"^=== CPU ===")
    cpu_res = parse_stressng_block(cpu_block, "cpu")
    if cpu_res:
        add("cpu_bogo_ops_s_real", cpu_res["bogo_ops_s_real"])
        add("cpu_bogo_ops_s_cpu", cpu_res["bogo_ops_s_cpu"])

    # --- MEMÓRIA ---
    mem_block = extract_section(text, r"^=== MEMÓRIA ===")
    mem_res = None
    for stressor_guess in ("vm", "memhotplug", "malloc", "mem"):
        mem_res = parse_stressng_block(mem_block, stressor_guess)
        if mem_res:
            break
    if mem_res:
        add("mem_bogo_ops_s_real", mem_res["bogo_ops_s_real"])
        add("mem_bogo_ops_s_cpu", mem_res["bogo_ops_s_cpu"])

    # --- CACHE ---
    cache_block = extract_section(text, r"^=== CACHE ===")
    cache_res = parse_stressng_block(cache_block, "cache")
    if cache_res:
        add("cache_bogo_ops_s_real", cache_res["bogo_ops_s_real"])
        add("cache_bogo_ops_s_cpu", cache_res["bogo_ops_s_cpu"])

    # --- STREAM ---
    stream_block = extract_section(text, r"^=== STREAM")
    for ln in stream_block:
        m = STREAM_LINE.match(ln.strip())
        if m:
            valor = float(m.group(2))
            # STREAM sob QEMU/TCG frequentemente "roda" mas produz saída
            # numericamente inválida (Best Rate = 0.0 MB/s, Max time com
            # overflow de float ~3.4e38) devido à falta de resolução
            # confiável do timer sob emulação TCG. Isso NÃO é um dado real
            # de bandwidth — é uma falha de medição e deve ser descartado,
            # conforme já documentado na Seção 6.1 do artigo.
            if valor <= 0.0:
                continue
            add(f"stream_{m.group(1).lower()}_mbs", valor)

    # --- DISCO (host usa [DISCO_HOST], VMs usam [DISCO_EXT4]) ---
    disk_prefix = "DISCO_HOST" if ambiente == "host" else "DISCO_EXT4"

    seq_read = extract_section(text, rf"^=== \[{disk_prefix}\] LEITURA SEQUENCIAL")
    fr = parse_fio_block(seq_read)
    if "bw_kibs_read" in fr:
        add("disk_seqread_MiBs", fr["bw_kibs_read"] / 1024)

    seq_write = extract_section(text, rf"^=== \[{disk_prefix}\] ESCRITA SEQUENCIAL")
    fw = parse_fio_block(seq_write)
    if "bw_kibs_write" in fw:
        add("disk_seqwrite_MiBs", fw["bw_kibs_write"] / 1024)

    randrw = extract_section(text, rf"^=== \[{disk_prefix}\] IOPS ALEAT")
    frw = parse_fio_block(randrw)
    if "iops_read" in frw:
        add("disk_iops_randread", frw["iops_read"])
    if "iops_write" in frw:
        add("disk_iops_randwrite", frw["iops_write"])

    latblk = extract_section(text, rf"^=== \[{disk_prefix}\] LAT[ÊE]NCIA")
    flat = parse_fio_block(latblk)
    if "iops_read" in flat:
        add("disk_iops_qd1", flat["iops_read"])
    if "lat_avg_usec" in flat:
        add("disk_lat_avg_usec", flat["lat_avg_usec"])

    # --- GPU (host e KVM apenas) ---
    if ambiente in ("host", "kvm"):
        mm_block = extract_section(text, r"^=== GPU — MATRIXMUL")
        # Cada arquivo contém 5 "-- Execução N --" do matrixMul. CORRIGIDO:
        # antes só a 1ª "Performance=... GFlop/s" era usada (`break` após o
        # 1º match), descartando as outras 4 — inconsistente com
        # resumo_benchmarks_overhead.py, que já faz média das 5. Agora
        # também tira a média das 5, pelo mesmo motivo: uma única execução
        # de GPU é mais suscetível a ruído/jitter do que a média de 5.
        valores_mm = [float(m.group(1)) for ln in mm_block
                      for m in [MATRIXMUL_LINE.search(ln)] if m]
        if valores_mm:
            add("gpu_matrixmul_gflops", sum(valores_mm) / len(valores_mm))

        for nccl_name, header in [
            ("allreduce", r"^=== GPU — NCCL ALL_REDUCE"),
            ("allgather", r"^=== GPU — NCCL ALL_GATHER"),
            ("sendrecv", r"^=== GPU — NCCL SENDRECV"),
        ]:
            block = extract_section(text, header)
            # CORRIGIDO: a abordagem antiga (nums[-1] na última linha de dados)
            # pegava, dependendo do log, o campo #wrong (frequentemente "0",
            # explicando os zeros de all_reduce/all_gather) ou o busbw de uma
            # mensagem pequena no início da varredura (explicando o valor
            # inflado de sendrecv), porque o campo #wrong às vezes é "N/A"
            # (sem dígitos) e desloca a indexação de nums[-1]. A linha de
            # resumo "Avg bus bandwidth" que o próprio nccl-tests já calcula
            # é a fonte robusta e é o que bate com os valores manuscritos
            # originais do artigo.
            for ln in block:
                m = NCCL_AVG_LINE.search(ln)
                if m:
                    add(f"gpu_nccl_{nccl_name}_busbw_gbs", float(m.group(1)))
                    break

    if avisos is not None:
        metricas_encontradas = {r["metrica"] for r in rows}
        esperadas = list(METRICAS_ESPERADAS_COMUNS)
        if ambiente in ("host", "kvm"):
            esperadas += METRICAS_ESPERADAS_HOST_KVM
        for metrica_esperada in esperadas:
            if metrica_esperada not in metricas_encontradas:
                avisos.append({
                    "arquivo": fname, "ambiente": ambiente, "vm_id": vm_id,
                    "metrica_esperada": metrica_esperada,
                    "motivo": "seção/regex correspondente não encontrou dado válido no arquivo",
                })

    return rows


# --------------------------------------------------------------------------
# 2. PIPELINE ESTATÍSTICO
# --------------------------------------------------------------------------
def _ci_delta_star_a_partir_de(M: float, boot_means: np.ndarray, alpha: float) -> tuple[float, float, float]:
    """Núcleo comum às duas funções de bootstrap abaixo: dado o vetor de
    médias reamostradas e a média observada M, aplica o método delta*
    (M - u*) para obter (M, ci_lower, ci_upper). Extraído para não repetir
    a mesma conta em bootstrap_ci_delta_star e
    bootstrap_ci_estratificado_por_vm."""
    if np.allclose(boot_means, boot_means[0]):
        return M, M, M
    delta_star = boot_means - M
    lo = np.quantile(delta_star, alpha / 2)
    hi = np.quantile(delta_star, 1 - alpha / 2)
    return M, M - hi, M - lo


def bootstrap_ci_delta_star(
    sample: np.ndarray, n_boot: int = 10000, alpha: float = 0.05, seed: int = 42
) -> tuple[float, float, float]:
    """IC via bootstrap, método delta* (M - u*), conforme material do
    professor (slide 'Calculando Intervalos de Confiança').
    Retorna (M, ci_lower, ci_upper)."""
    rng = np.random.default_rng(seed)
    sample = np.asarray(sample, dtype=float)
    M = sample.mean()
    n = len(sample)
    if n < 2 or np.allclose(sample, sample[0]):
        # amostra degenerada (n<2 ou todos os valores idênticos) -> IC colapsa no próprio valor
        return M, M, M
    boot_means = np.empty(n_boot)
    for i in range(n_boot):
        boot_sample = rng.choice(sample, size=n, replace=True)
        boot_means[i] = boot_sample.mean()
    return _ci_delta_star_a_partir_de(M, boot_means, alpha)


def bootstrap_ci_estratificado_por_vm(
    df_metric: pd.DataFrame, n_boot: int = 10000, alpha: float = 0.05, seed: int = 42
) -> tuple[float, float, float]:
    """IC via bootstrap RESPEITANDO a estrutura aninhada (3 VMs x 5 réplicas):
    a cada iteração, sorteia VMs COM reposição (nível 1), depois sorteia
    réplicas COM reposição dentro de cada VM sorteada (nível 2). Isso evita
    tratar as 15 execuções como i.i.d. quando há heterogeneidade real entre
    VMs (ver checar_heterogeneidade_vms). Espera df_metric com colunas
    ['vm_id', 'valor']. Se houver só 1 VM (ex.: host), cai no bootstrap
    simples de bootstrap_ci_delta_star."""
    vms = df_metric["vm_id"].unique()
    if len(vms) < 2:
        return bootstrap_ci_delta_star(df_metric["valor"].values, n_boot, alpha, seed)

    rng = np.random.default_rng(seed)
    valores_por_vm = {vm: df_metric[df_metric["vm_id"] == vm]["valor"].values for vm in vms}
    M = df_metric["valor"].mean()
    n_vms = len(vms)

    boot_means = np.empty(n_boot)
    for i in range(n_boot):
        vms_sorteadas = rng.choice(vms, size=n_vms, replace=True)
        amostra_iter = []
        for vm in vms_sorteadas:
            vals = valores_por_vm[vm]
            amostra_iter.extend(rng.choice(vals, size=len(vals), replace=True))
        boot_means[i] = np.mean(amostra_iter)

    return _ci_delta_star_a_partir_de(M, boot_means, alpha)


def analisar_metrica(df_metric: pd.DataFrame, alpha: float = 0.05,
                      metrica_nome: str = "?") -> dict:
    """Recebe um DataFrame com colunas ['ambiente', 'vm_id', 'valor'] para
    UMA métrica, e roda o pipeline: descritiva, Shapiro-Wilk, Levene,
    ANOVA ou Kruskal-Wallis, post-hoc de Dunn (se aplicável), IC bootstrap
    por ambiente (estratificado por VM quando aplicável).

    `metrica_nome` só é usado para identificar a métrica em warnings de
    console quando Shapiro-Wilk/Levene falham (ex.: amostra degenerada) —
    antes essas falhas eram silenciosas (fallback direto para "não
    normal"/"variância não homogênea" sem aviso nenhum)."""
    resultado = {}
    grupos = {amb: g["valor"].values for amb, g in df_metric.groupby("ambiente")}

    # Descritiva + IC bootstrap por ambiente (estratificado por VM se houver >1 VM)
    for amb, g in df_metric.groupby("ambiente"):
        vals = g["valor"].values
        if len(vals) < 2:
            continue
        if g["vm_id"].nunique() > 1:
            M, lo, hi = bootstrap_ci_estratificado_por_vm(g[["vm_id", "valor"]])
            resultado[f"{amb}_ic_metodo"] = "estratificado_por_vm"
        else:
            M, lo, hi = bootstrap_ci_delta_star(vals)
            resultado[f"{amb}_ic_metodo"] = "simples"
        resultado[f"{amb}_n"] = len(vals)
        resultado[f"{amb}_media"] = np.mean(vals)
        resultado[f"{amb}_mediana"] = np.median(vals)
        resultado[f"{amb}_desvio"] = np.std(vals, ddof=1)
        resultado[f"{amb}_iqr"] = np.percentile(vals, 75) - np.percentile(vals, 25)
        resultado[f"{amb}_ic95_lo"] = lo
        resultado[f"{amb}_ic95_hi"] = hi

    # Normalidade (Shapiro-Wilk) por grupo
    normal_flags = []
    for amb, vals in grupos.items():
        if len(vals) >= 3:
            try:
                w, p = stats.shapiro(vals)
                resultado[f"{amb}_shapiro_p"] = p
                normal_flags.append(p > alpha)
            except Exception as exc:
                print(f"[aviso] Shapiro-Wilk falhou em '{metrica_nome}'/{amb} "
                      f"(assumindo não-normal): {exc}")
                normal_flags.append(False)

    todos_normais = all(normal_flags) if normal_flags else False

    # Homogeneidade de variância (Levene)
    vals_list = [v for v in grupos.values() if len(v) >= 2]
    if len(vals_list) >= 2:
        try:
            lev_stat, lev_p = stats.levene(*vals_list)
            resultado["levene_p"] = lev_p
            variancias_homogeneas = lev_p > alpha
        except Exception as exc:
            print(f"[aviso] Levene falhou em '{metrica_nome}' "
                  f"(assumindo variâncias não homogêneas): {exc}")
            resultado["levene_p"] = np.nan
            variancias_homogeneas = False
    else:
        variancias_homogeneas = False

    # Teste global: ANOVA se normal + homogêneo, senão Kruskal-Wallis
    if len(vals_list) >= 2:
        if todos_normais and variancias_homogeneas:
            f_stat, p_val = stats.f_oneway(*vals_list)
            resultado["teste_global"] = "ANOVA"
            resultado["teste_global_estat"] = f_stat
            resultado["teste_global_p"] = p_val
        else:
            h_stat, p_val = stats.kruskal(*vals_list)
            resultado["teste_global"] = "Kruskal-Wallis"
            resultado["teste_global_estat"] = h_stat
            resultado["teste_global_p"] = p_val

        # Post-hoc de Dunn + Bonferroni, só se o teste global rejeitar H0
        if p_val < alpha and len(vals_list) > 2:
            try:
                import scikit_posthocs as sp
                dunn = sp.posthoc_dunn(df_metric, val_col="valor", group_col="ambiente",
                                        p_adjust="bonferroni")
                resultado["posthoc_dunn"] = dunn.to_dict()
            except ImportError:
                resultado["posthoc_dunn"] = "scikit-posthocs não instalado (pip install scikit-posthocs --break-system-packages)"

        # Effect size (epsilon-squared para Kruskal-Wallis)
        if resultado["teste_global"] == "Kruskal-Wallis":
            n_total = sum(len(v) for v in vals_list)
            resultado["effect_size_epsilon2"] = h_stat / (n_total - 1) if n_total > 1 else np.nan

    return resultado


def checar_heterogeneidade_vms(df: pd.DataFrame, alpha: float = 0.05) -> pd.DataFrame:
    """Para cada métrica e cada tratamento (kvm/qemu), testa se as 3 VMs
    de origem diferem entre si (Kruskal-Wallis vm_A vs vm_B vs vm_C).
    Retorna um DataFrame com o resultado por métrica x tratamento."""
    linhas = []
    for (metrica, ambiente), sub in df[df["ambiente"] != "host"].groupby(["metrica", "ambiente"]):
        vms = sub["vm_id"].unique()
        if len(vms) < 2:
            continue
        grupos = [sub[sub["vm_id"] == vm]["valor"].values for vm in vms]
        grupos = [g for g in grupos if len(g) >= 2]
        if len(grupos) < 2:
            continue
        todos_valores = np.concatenate(grupos)
        if np.allclose(todos_valores, todos_valores[0]):
            # todos os valores idênticos entre todas as VMs -> sem evidência de diferença
            h, p = 0.0, 1.0
        else:
            try:
                h, p = stats.kruskal(*grupos)
            except Exception:
                h, p = np.nan, np.nan
        linhas.append({
            "metrica": metrica, "ambiente": ambiente,
            "n_vms": len(grupos), "kruskal_h": h, "kruskal_p": p,
            "heterogeneo": (p < alpha) if pd.notna(p) else None,
        })
    return pd.DataFrame(linhas)


def aplicar_correcao_fdr(df_stats: pd.DataFrame, alpha: float = 0.05) -> pd.DataFrame:
    """Aplica correção de Benjamini-Hochberg (FDR) sobre teste_global_p
    através de TODAS as métricas simultaneamente (distinto do post-hoc de
    Dunn, que já usa Bonferroni mas só DENTRO de uma métrica). Adiciona a
    coluna p_fdr_corrigido a df_stats (NaN nas linhas sem teste global)."""
    df_stats = df_stats.copy()
    df_stats["p_fdr_corrigido"] = np.nan
    mask = df_stats["teste_global_p"].notna() if "teste_global_p" in df_stats else pd.Series(dtype=bool)
    if mask.sum() > 0:
        _, p_corrigido, _, _ = multipletests(
            df_stats.loc[mask, "teste_global_p"].values, alpha=alpha, method="fdr_bh"
        )
        df_stats.loc[mask, "p_fdr_corrigido"] = p_corrigido
    return df_stats


def checar_estatisticas_duplicadas(df_stats: pd.DataFrame) -> pd.DataFrame:
    """Sanidade: compara teste_global_estat, teste_global_p e
    effect_size_epsilon2 entre TODOS os pares de métricas diferentes. Se
    algum par tiver os três valores EXATAMENTE idênticos, imprime um
    WARNING (estatísticas idênticas entre métricas conceitualmente
    distintas são um forte indício de bug de reuso de array, mas também
    podem ser benignas: propriedade matemática do Kruskal-Wallis sob
    separação completa entre grupos de mesmo tamanho — como ocorre entre
    cache e memória neste experimento — produz H idêntico
    independentemente dos valores brutos). Retorna um DataFrame (uma linha
    por par colidente) que também é salvo em
    data/processed/alerta_estatisticas_duplicadas.csv."""
    cols = ["teste_global_estat", "teste_global_p", "effect_size_epsilon2"]
    if not all(c in df_stats.columns for c in cols):
        return pd.DataFrame(columns=["metrica_a", "metrica_b"] + cols)

    sub = df_stats.dropna(subset=cols)[["metrica"] + cols].reset_index(drop=True)
    linhas = []
    for i in range(len(sub)):
        for j in range(i + 1, len(sub)):
            a, b = sub.iloc[i], sub.iloc[j]
            if (a[cols] == b[cols]).all():
                linhas.append({
                    "metrica_a": a["metrica"], "metrica_b": b["metrica"],
                    "teste_global_estat": a["teste_global_estat"],
                    "teste_global_p": a["teste_global_p"],
                    "effect_size_epsilon2": a["effect_size_epsilon2"],
                })

    df_alerta = pd.DataFrame(linhas, columns=["metrica_a", "metrica_b"] + cols)
    if not df_alerta.empty:
        print(f"\n[AVISO] {len(df_alerta)} par(es) de métricas com estatística de teste global "
              f"IDÊNTICA (teste_global_estat, teste_global_p e effect_size_epsilon2 batendo "
              f"exatamente) — ver data/processed/alerta_estatisticas_duplicadas.csv e revisar "
              f"manualmente antes de assumir que é coincidência benigna:")
        for _, row in df_alerta.iterrows():
            print(f"    {row['metrica_a']}  ==  {row['metrica_b']}  "
                  f"(H={row['teste_global_estat']:.4f}, p={row['teste_global_p']:.4g})")
    return df_alerta


def obter_metadata_execucao(args: argparse.Namespace, seed_bootstrap: int = 42) -> dict:
    """Coleta metadados da execução (commit git do script, timestamp UTC,
    seed usada na randomização) para metadata.json ao lado das saídas —
    permite reproduzir/auditar exatamente qual versão do código e dos
    dados gerou um dado resultados_estatisticos.csv."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=script_dir, stderr=subprocess.DEVNULL
        ).decode().strip()
        dirty = bool(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=script_dir, stderr=subprocess.DEVNULL
        ).decode().strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        commit, dirty = None, None

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": commit,
        "git_dirty": dirty,
        "git_commit_nota": (
            "null porque o diretório não era um repositório git no momento da execução"
            if commit is None else None
        ),
        "seed_bootstrap": seed_bootstrap,
        "logs_dir": os.path.abspath(args.logs_dir),
        "out_dir": os.path.abspath(args.out_dir),
    }


# Estilo enxuto, no espírito de figura de artigo científico: pouco ruído
# visual, contraste adequado, fontes/tamanhos consistentes entre todas as
# figuras. Aplicado uma única vez, no import — não repetido por gráfico.
plt.rcParams.update({
    "font.size": 10,
    "axes.titlesize": 11,
    "axes.titleweight": "normal",
    "axes.labelsize": 10,
    "axes.edgecolor": "#4d4d4d",
    "axes.linewidth": 0.8,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "xtick.color": "#333333",
    "ytick.color": "#333333",
    "legend.frameon": False,
    "legend.fontsize": 8,
    "figure.facecolor": "white",
    "savefig.facecolor": "white",
})

# ---------------------------------------------------------------------------
# GRÁFICOS — um arquivo por métrica, cada um um painel de pequenos
# múltiplos com um subplot POR AMBIENTE, e escala Y independente em cada
# subplot (ver docstring de _plot_metrica). Isso substitui uma versão
# anterior com um único Axes por métrica e eixo compartilhado entre
# ambientes: quando QEMU é 10-90x mais lento que host/KVM (CPU, memória,
# cache), forçar os três num mesmo eixo (mesmo com escala log) reduzia o(s)
# ambiente(s) de menor variância absoluta a uma linha reta sem caixa
# visível — um boxplot que não informava nada sobre aquele grupo.
# ---------------------------------------------------------------------------

# Mapa: metrica interna -> (rótulo legível, unidade)
ROTULOS = {
    "cpu_bogo_ops_s_real":  ("CPU — tempo real",        "bogo ops/s"),
    "cpu_bogo_ops_s_cpu":   ("CPU — tempo de CPU",       "bogo ops/s"),
    "mem_bogo_ops_s_real":  ("Memória — tempo real",     "bogo ops/s"),
    "mem_bogo_ops_s_cpu":   ("Memória — tempo de CPU",   "bogo ops/s"),
    "stream_copy_mbs":      ("STREAM — Copy",            "MB/s"),
    "stream_scale_mbs":     ("STREAM — Scale",           "MB/s"),
    "stream_add_mbs":       ("STREAM — Add",             "MB/s"),
    "stream_triad_mbs":     ("STREAM — Triad",           "MB/s"),
    "cache_bogo_ops_s_real":("Cache — tempo real",       "bogo ops/s"),
    "cache_bogo_ops_s_cpu": ("Cache — tempo de CPU",     "bogo ops/s"),
    "disk_seqread_MiBs":    ("Leitura sequencial",       "MiB/s"),
    "disk_seqwrite_MiBs":   ("Escrita sequencial",       "MiB/s"),
    "disk_iops_qd1":        ("IOPS QD=1 (leitura)",      "IOPS"),
    "disk_lat_avg_usec":    ("Latência média",           "µs"),
    "disk_iops_randread":   ("IOPS aleatório — leitura", "IOPS"),
    "disk_iops_randwrite":  ("IOPS aleatório — escrita", "IOPS"),
    "gpu_matrixmul_gflops": ("matrixMul",                "GFlop/s"),
    "gpu_nccl_allreduce_busbw_gbs": ("NCCL all_reduce",  "GB/s (busbw)"),
    "gpu_nccl_allgather_busbw_gbs": ("NCCL all_gather",  "GB/s (busbw)"),
    "gpu_nccl_sendrecv_busbw_gbs":  ("NCCL sendrecv",    "GB/s (busbw)"),
}

# metrica -> subpasta (só organiza a saída em disco; cada figura continua
# sendo 1 arquivo independente, com título já indicando a seção do artigo)
SECAO = {
    "cpu_bogo_ops_s_real": ("cpu", "Seção 5.2"), "cpu_bogo_ops_s_cpu": ("cpu", "Seção 5.2"),
    "mem_bogo_ops_s_real": ("memoria", "Seção 5.3"), "mem_bogo_ops_s_cpu": ("memoria", "Seção 5.3"),
    "stream_copy_mbs": ("memoria", "Seção 5.3"), "stream_scale_mbs": ("memoria", "Seção 5.3"),
    "stream_add_mbs": ("memoria", "Seção 5.3"), "stream_triad_mbs": ("memoria", "Seção 5.3"),
    "cache_bogo_ops_s_real": ("cache", "Seção 5.4"), "cache_bogo_ops_s_cpu": ("cache", "Seção 5.4"),
    "disk_seqread_MiBs": ("disco_seq", "Seção 5.5"), "disk_seqwrite_MiBs": ("disco_seq", "Seção 5.5"),
    "disk_iops_qd1": ("disco_seq", "Seção 5.5"), "disk_lat_avg_usec": ("disco_seq", "Seção 5.5"),
    "disk_iops_randread": ("disco_iops", "Seção 5.6"), "disk_iops_randwrite": ("disco_iops", "Seção 5.6"),
    "gpu_matrixmul_gflops": ("gpu", "Seção 5.7"),
    "gpu_nccl_allreduce_busbw_gbs": ("gpu", "Seção 5.7"),
    "gpu_nccl_allgather_busbw_gbs": ("gpu", "Seção 5.7"),
    "gpu_nccl_sendrecv_busbw_gbs": ("gpu", "Seção 5.7"),
}

# Motivo da ausência de QEMU numa métrica — NÃO é o mesmo motivo pra STREAM
# e pra GPU, então usamos um dict em vez de uma string fixa condicional:
#   - STREAM: falha real de build/execução sob TCG (métrica é coletada, mas
#     sai numericamente inválida — Best Rate=0.0, Max time com overflow de
#     float — e é descartada em parse_file; ver comentário no bloco STREAM).
#   - GPU (matrixMul/NCCL): QEMU puro nunca foi testado aqui por DESENHO
#     experimental (RQ4 restringe GPU a host/KVM), e também não teria como
#     funcionar sem VFIO/passthrough (TCG não expõe driver NVIDIA à VM) —
#     isso é uma limitação de escopo conhecida, não uma falha de execução.
MOTIVO_EXCLUSAO_QEMU = {
    "stream_copy_mbs": "QEMU excluído — falha de build/execução sob TCG (SIMD/VSX não implementado)",
    "stream_scale_mbs": "QEMU excluído — falha de build/execução sob TCG (SIMD/VSX não implementado)",
    "stream_add_mbs": "QEMU excluído — falha de build/execução sob TCG (SIMD/VSX não implementado)",
    "stream_triad_mbs": "QEMU excluído — falha de build/execução sob TCG (SIMD/VSX não implementado)",
    "gpu_matrixmul_gflops": "QEMU fora de escopo (RQ4) — driver NVIDIA não suportado sem VFIO/passthrough",
    "gpu_nccl_allreduce_busbw_gbs": "QEMU fora de escopo (RQ4) — driver NVIDIA não suportado sem VFIO/passthrough",
    "gpu_nccl_allgather_busbw_gbs": "QEMU fora de escopo (RQ4) — driver NVIDIA não suportado sem VFIO/passthrough",
    "gpu_nccl_sendrecv_busbw_gbs": "QEMU fora de escopo (RQ4) — driver NVIDIA não suportado sem VFIO/passthrough",
}

# Cor por AMBIENTE — consistente com o resto do artigo (não por VM)
COR_AMBIENTE = {"host": "#4d4d4d", "kvm": "#1f77b4", "qemu": "#d97706"}
ORDEM_AMBIENTES = ["host", "kvm", "qemu"]


def _cor_vm_map(sub: pd.DataFrame) -> dict:
    """Mapa VM -> cor, consistente entre chamadas (mesma VM = mesma cor
    em qualquer figura que a coloriza)."""
    paleta = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"]
    vms = sorted(v for v in sub["vm_id"].unique() if v != "host")
    return {vm: paleta[i % len(paleta)] for i, vm in enumerate(vms)}


# jitter horizontal discreto — só o suficiente pra separar pontos
# sobrepostos, sem espalhar a nuvem a ponto de competir com o boxplot
_JITTER_STD = 0.035

# Dentro de UM ambiente (não mais entre ambientes — ver nota em
# _plot_metrica), acima dessa razão entre o maior e o menor valor
# positivo, a escala linear comprime demais a própria distribuição desse
# ambiente e passamos pra log.
RAZAO_LOG = 8.0


def _pontos_do_ambiente(ax, vals_por_vm: dict, ambiente_heterogeneo: bool,
                         vm_color_map: dict, rng: np.random.Generator, legend_handles: dict) -> None:
    """Desenha os pontos individuais de UM ambiente, centrados em x=1 (cada
    ambiente agora tem seu próprio Axes — ver _plot_metrica). Cor neutra
    (cinza) por padrão; cor por VM só quando esse ambiente tem
    heterogeneidade significativa entre VMs (`ambiente_heterogeneo`).
    Pontos ficam em zorder baixo — servem só para mostrar dispersão, o
    boxplot é desenhado por cima e é o elemento visual dominante."""
    if ambiente_heterogeneo:
        for vm, vvals in vals_por_vm.items():
            jitter = rng.normal(0, _JITTER_STD, size=len(vvals))
            pontos = ax.scatter(np.full(len(vvals), 1) + jitter, vvals,
                                 color=vm_color_map[vm], s=16, alpha=0.5,
                                 zorder=2, linewidths=0)
            legend_handles[vm] = pontos
    else:
        vals = np.concatenate(list(vals_por_vm.values()))
        jitter = rng.normal(0, _JITTER_STD, size=len(vals))
        ax.scatter(np.full(len(vals), 1) + jitter, vals,
                   color="#404040", s=16, alpha=0.4, zorder=2, linewidths=0)


def _plot_ambiente(ax, sub_amb: pd.DataFrame, amb: str, cor: str, unidade: str,
                    ambiente_heterogeneo: bool, vm_color_map: dict,
                    rng: np.random.Generator, legend_handles: dict) -> None:
    """Desenha o boxplot + pontos de UM ambiente num Axes só dele, com
    escala Y dimensionada apenas pelos dados deste ambiente (ver
    _plot_metrica para o porquê disso ser essencial aqui).

    `legend_handles` é um dict COMPARTILHADO entre todos os ambientes da
    mesma figura (populado aqui, mas a legenda em si é desenhada uma vez
    só, fora deste Axes — ver _plot_metrica). Isso evita que a legenda de
    um ambiente no meio da linha (ex.: KVM, quando há 3 painéis) fique
    presa às coordenadas daquele Axes especificamente e acabe encostando
    ou sobrepondo o painel vizinho (ex.: QEMU) — na prática a maioria dos
    casos de heterogeneidade entre VMs deste experimento ocorre sob KVM,
    que fica no painel do meio quando os 3 ambientes estão presentes."""
    vals = sub_amb["valor"].values
    vals_por_vm = {vm: g["valor"].values for vm, g in sub_amb.groupby("vm_id")}

    _pontos_do_ambiente(ax, vals_por_vm, ambiente_heterogeneo, vm_color_map, rng, legend_handles)

    bp = ax.boxplot([vals], tick_labels=["Host" if amb == "host" else amb.upper()],
                     showfliers=False, widths=0.5, patch_artist=True, zorder=3,
                     boxprops=dict(linewidth=1.6, edgecolor="#333333"),
                     whiskerprops=dict(linewidth=1.6, color="#333333"),
                     capprops=dict(linewidth=1.6, color="#333333"),
                     medianprops=dict(linewidth=2.2, color="black"))
    bp["boxes"][0].set_facecolor(cor)
    bp["boxes"][0].set_alpha(0.6)

    # Escala log só quando OS PRÓPRIOS valores deste ambiente variam muito
    # entre si (decisão local — cada ambiente tem seu eixo independente).
    positivos = vals[vals > 0]
    if len(positivos) >= 2 and (positivos.max() / positivos.min()) > RAZAO_LOG:
        ax.set_yscale("log")

    ax.set_ylabel(unidade)
    ax.grid(axis="y", linestyle="--", linewidth=0.5, alpha=0.35, zorder=0)
    # Cada painel tem sua própria escala Y (ver _plot_metrica) — o número de
    # ordem de grandeza plotado varia entre painéis, então cravamos margem
    # extra pra caixa/whiskers nunca encostarem na borda do painel.
    ax.margins(y=0.08)


def _plot_metrica(df: pd.DataFrame, metrica: str, df_heterog: Optional[pd.DataFrame] = None):
    """Gera a Figure de UMA métrica como um painel de pequenos múltiplos:
    UM subplot por ambiente presente, cada um com escala Y INDEPENDENTE.

    Por quê: um único eixo compartilhado entre host/KVM/QEMU só funciona
    quando os três têm magnitude parecida. Neste experimento isso é a
    exceção, não a regra — QEMU costuma ser 10-90x mais lento que
    host/KVM em CPU/memória/cache. Com um eixo linear compartilhado, o(s)
    grupo(s) de menor variância absoluta (normalmente host) fica(m)
    reduzido(s) a uma linha reta sem caixa visível — um boxplot que não
    informa nada sobre aquele grupo. Trocar só a escala (linear -> log)
    no eixo compartilhado não resolve: o problema não é a escala em si, é
    que a AMPLITUDE de cada grupo precisa do seu próprio referencial pra
    ficar legível, e amplitude entre grupos difere tanto quanto a
    magnitude. A solução padrão pra esse cenário (grupos com
    localização/escala muito diferentes) é small multiples: cada ambiente
    ganha seu próprio Axes, dimensionado só para os seus dados — dessa
    forma a caixa, os whiskers e os pontos de QUALQUER ambiente, grande ou
    pequeno, ficam sempre legíveis.

    A comparação de MAGNITUDE relativa entre ambientes (o "quantos % mais
    lento") já é coberta pelas figuras finais do artigo
    (results/figures/fig1..4, geradas por geracao_graficos.R). Esta figura
    aqui é deliberadamente sobre a
    FORMA de cada distribuição (IQR, caudas, outliers, heterogeneidade
    entre VMs), não sobre comparar alturas de caixa entre painéis — por
    isso a nota no título abaixo avisando que os eixos são independentes.
    """
    sub = df[df["metrica"] == metrica]
    rotulo, unidade = ROTULOS.get(metrica, (metrica, ""))

    ambientes_presentes = [a for a in ORDEM_AMBIENTES if len(sub[sub["ambiente"] == a]) > 0]
    if not ambientes_presentes:
        return None

    ambientes_heterog = set()
    if df_heterog is not None and not df_heterog.empty:
        hrows = df_heterog[(df_heterog["metrica"] == metrica) & (df_heterog["heterogeneo"] == True)]
        ambientes_heterog = set(hrows["ambiente"])

    n = len(ambientes_presentes)
    fig, axes = plt.subplots(1, n, figsize=(2.8 * n + 1.0, 4.5))
    axes = [axes] if n == 1 else list(axes)

    rng = np.random.default_rng(0)
    vm_color_map = _cor_vm_map(sub)

    # Compartilhado entre todos os ambientes desta figura — a legenda é
    # desenhada UMA vez, fora de qualquer Axes específico (ver docstring
    # de _plot_ambiente e o fig.legend mais abaixo).
    legend_handles = {}
    for ax, amb in zip(axes, ambientes_presentes):
        sub_amb = sub[sub["ambiente"] == amb]
        _plot_ambiente(ax, sub_amb, amb, COR_AMBIENTE[amb], unidade,
                       amb in ambientes_heterog, vm_color_map, rng, legend_handles)

    titulo_fig = rotulo
    secao = SECAO.get(metrica, (None, None))[1]
    if secao:
        titulo_fig += f" ({secao})"
    if "qemu" not in ambientes_presentes and "kvm" in ambientes_presentes:
        titulo_fig += f"\n{MOTIVO_EXCLUSAO_QEMU.get(metrica, 'QEMU excluído — motivo não catalogado')}"
    titulo_fig += "\nescala Y independente por painel — não compare alturas de caixa entre ambientes"
    fig.suptitle(titulo_fig, fontsize=10.5)

    # Legenda de VM fora da área de plotagem (à direita da figura inteira),
    # em vez de dentro de um Axes específico — evita sobrepor a caixa/os
    # pontos de dados, e evita colidir com o painel vizinho quando o
    # ambiente heterogêneo é o do meio (tipicamente KVM, quando os 3
    # ambientes estão presentes). bbox_inches="tight" no savefig (ver
    # gerar_graficos) garante que a legenda não seja cortada ao salvar.
    if legend_handles:
        fig.legend(legend_handles.values(), legend_handles.keys(),
                   loc="upper left", bbox_to_anchor=(1.0, 0.95), bbox_transform=fig.transFigure,
                   title="VM", title_fontsize=8, fontsize=8, borderaxespad=0.5)

    return fig


def gerar_graficos(df: pd.DataFrame, out_dir: str, df_heterog: Optional[pd.DataFrame] = None) -> None:
    """Gera 1 figura por métrica (painel de pequenos múltiplos, um subplot
    por ambiente — ver _plot_metrica), organizadas em subpastas por seção
    do artigo: out_dir/graficos/<secao>/<metrica>.png"""
    graf_dir_base = os.path.join(out_dir, "graficos")
    os.makedirs(graf_dir_base, exist_ok=True)

    for metrica in df["metrica"].unique():
        if metrica not in ROTULOS:
            continue  # métrica sem rótulo mapeado (ex.: nova métrica futura) — pula
        pasta, _ = SECAO.get(metrica, ("outros", None))
        graf_dir = os.path.join(graf_dir_base, pasta)
        os.makedirs(graf_dir, exist_ok=True)

        fig = _plot_metrica(df, metrica, df_heterog=df_heterog)
        if fig is None:
            continue
        fig.tight_layout()
        fig.savefig(os.path.join(graf_dir, f"{metrica}.png"),
                    dpi=200, bbox_inches="tight")
        plt.close(fig)
        print(f"  salvo: {pasta}/{metrica}.png")


# ---------------------------------------------------------------------------
# HEATMAPS — resumo compacto dos testes formais das ~19 métricas de uma vez
# só (hoje isso só existe espalhado em resultados_estatisticos.csv/
# relatorio.txt). Estes heatmaps COMPLEMENTAM os boxplots de gerar_graficos
# (que mostram a distribuição dos dados brutos por ambiente) — não os
# substituem.
# ---------------------------------------------------------------------------

# Faixas de significância usadas nos dois heatmaps abaixo. Código 0 é
# reservado a "sem dado" (NaN — ex.: teste não aplicável) e fica cinza,
# nunca verde/vermelho, pra não ser confundido com "não significativo".
_CORES_P = ["#d9d9d9", "#2ca25f", "#fee08b", "#fc8d59", "#d73027"]
_LEGENDA_P = ["sem dado", "n.s. (p≥0.05)", "p<0.05", "p<0.01", "p<0.001"]


def _bin_p_valor(p: float) -> int:
    """Categoriza um p-valor nas faixas de _CORES_P/_LEGENDA_P."""
    if pd.isna(p):
        return 0
    if p < 0.001:
        return 4
    if p < 0.01:
        return 3
    if p < 0.05:
        return 2
    return 1


def _formatar_p(p: float) -> str:
    if pd.isna(p):
        return "—"
    if p < 0.001:
        return "<0.001"
    return f"{p:.3f}"


def _ordem_metricas_presentes(metricas_disponiveis) -> list[str]:
    """Ordena métricas seguindo a mesma ordem de seção do artigo usada em
    ROTULOS/SECAO (não a ordem alfabética do CSV), com qualquer métrica
    não catalogada em ROTULOS anexada ao final."""
    disponiveis = set(metricas_disponiveis)
    ordenadas = [m for m in ROTULOS if m in disponiveis]
    restantes = sorted(disponiveis - set(ordenadas))
    return ordenadas + restantes


def _rotulo_curto(metrica: str) -> str:
    return ROTULOS.get(metrica, (metrica, ""))[0]


# Uma métrica por domínio experimental, para a figura "principal" (corpo do
# artigo) — a versão "_completo" com as ~19 métricas continua sendo gerada
# à parte, para auditoria/apêndice; nada é escondido, só reordenado por
# relevância editorial. Critério de escolha (registrar no texto, não só
# aqui, porque é uma decisão de conteúdo, não só de código):
#   - cpu/mem: tempo REAL (não tempo de CPU) — é o tempo de parede que capta
#     overhead de escalonamento do hipervisor; tempo de CPU tende a subestimar
#     esse efeito.
#   - cache: liga à anomalia "Cache (RESOLVIDA)" já documentada em
#     docs/METHODOLOGY.md (topologia de cache incompleta exposta ao guest).
#   - stream: Triad é a métrica-resumo mais citada na literatura do STREAM
#     (McCalpin) — combina leitura+escrita+multiplicação num único número.
#   - disco: latência QD=1 liga à anomalia "EM ABERTO" também documentada em
#     docs/METHODOLOGY.md (latência menor sob virtualização — contraintuitivo).
#   - gpu: matrixMul é o benchmark de computação pura; os 3 NCCL medem
#     interconexão entre GPUs, uma pergunta distinta (fica no "_completo").
METRICAS_PRINCIPAIS = [
    "cpu_bogo_ops_s_real",
    "mem_bogo_ops_s_real",
    "cache_bogo_ops_s_real",
    "stream_triad_mbs",
    "disk_lat_avg_usec",
    "gpu_matrixmul_gflops",
]


def _heatmap_significancia(df_stats: pd.DataFrame, heat_dir: str,
                            metricas_alvo: Optional[list] = None,
                            sufixo: str = "") -> None:
    """heatmap_significancia{sufixo}.png — métricas (linha) x testes formais
    (coluna). As colunas NÃO formam um único eixo homogêneo — são dois
    grupos conceitualmente diferentes, separados por uma linha vertical e
    rótulos de grupo:

      1) "Verificação de pressupostos" (Shapiro host/kvm/qemu, Levene): aqui
         p BAIXO (vermelho) significa pressuposto VIOLADO (não-normalidade
         ou heterocedasticidade) — é o que decide ANOVA vs. Kruskal-Wallis,
         não é uma "diferença real" entre ambientes.
      2) "Teste de hipótese" (teste global bruto e p_fdr_corrigido): aqui
         p BAIXO (vermelho) significa diferença estatisticamente
         significativa detectada entre host/KVM/QEMU — é o resultado
         substantivo. p_fdr_corrigido (Benjamini-Hochberg entre todas as
         métricas) é o valor que deve orientar conclusões ao comparar
         várias métricas simultaneamente.

    Misturar os dois grupos sob um único rótulo de eixo x (ex.: "p-valor")
    seria enganoso, porque teria o leitor lendo "muito vermelho" como
    "muitas diferenças reais", quando parte do vermelho pode ser só
    "dados não são normais" — daí a separação visual explícita.

    Effect size (ε²) fica num terceiro subplot à parte, com colormap
    contínuo próprio (não é p-valor, é magnitude)."""
    disponiveis = _ordem_metricas_presentes(df_stats["metrica"])
    metricas = ([m for m in disponiveis if m in set(metricas_alvo)]
                if metricas_alvo is not None else disponiveis)
    idx = df_stats.set_index("metrica").reindex(metricas)

    colunas_p = {
        "Shapiro\nHost": "host_shapiro_p",
        "Shapiro\nKVM": "kvm_shapiro_p",
        "Shapiro\nQEMU": "qemu_shapiro_p",
        "Levene": "levene_p",
        "Teste\nglobal (p)": "teste_global_p",
        "p FDR-\ncorrigido": "p_fdr_corrigido",
    }
    n_pressupostos = 4  # as 4 primeiras colunas de colunas_p acima
    codes = pd.DataFrame({col: idx[src].apply(_bin_p_valor) for col, src in colunas_p.items()},
                         index=metricas)
    textos = pd.DataFrame({col: idx[src].apply(_formatar_p) for col, src in colunas_p.items()},
                          index=metricas)
    effect = idx[["effect_size_epsilon2"]].rename(columns={"effect_size_epsilon2": "Effect size\n(ε²)"})

    rotulos_linha = [_rotulo_curto(m) for m in metricas]
    n = len(metricas)
    # Sem título/legenda-texto embutidos na imagem: em artigo (caption via
    # LaTeX), título e texto interpretativo duplicado dentro da figura são
    # redundantes/não-usuais.
    # Fonte grande (11-12pt) porque a figura tende a ser incluída em largura
    # de coluna/página menor que o tamanho nativo (ex.: 2 colunas ACM).
    fig = plt.figure(figsize=(8.5, 0.40 * n + 1.5))
    gs = fig.add_gridspec(1, 2, width_ratios=[6, 1.1], wspace=0.3)
    ax_p = fig.add_subplot(gs[0, 0])
    # Sem sharey de propósito: os dois `sns.heatmap` abaixo já recebem o
    # mesmo número de linhas (n) e a mesma altura de figura, então as
    # linhas ficam alinhadas de qualquer forma — compartilhar o eixo Y
    # aqui faz o segundo `sns.heatmap` (chamado com yticklabels=False)
    # sobrescrever/ocultar os rótulos de métrica desenhados pelo primeiro.
    ax_e = fig.add_subplot(gs[0, 1])

    cmap_p = ListedColormap(_CORES_P)
    norm_p = BoundaryNorm([-0.5, 0.5, 1.5, 2.5, 3.5, 4.5], len(_CORES_P))
    sns.heatmap(codes, annot=textos, fmt="", cmap=cmap_p, norm=norm_p, cbar=False,
                linewidths=0.6, linecolor="white", ax=ax_p,
                yticklabels=rotulos_linha, annot_kws={"fontsize": 11})
    ax_p.set_ylabel("")
    ax_p.tick_params(axis="x", rotation=0, labelsize=11)
    ax_p.tick_params(axis="y", labelsize=11)

    # Divisor visual + rótulos de grupo entre "pressupostos" e "teste de
    # hipótese" (ver docstring acima — são naturezas de p-valor diferentes).
    n_cols_p = len(colunas_p)
    ax_p.axvline(x=n_pressupostos, color="#333333", linewidth=1.8, clip_on=False)
    ax_p.text(n_pressupostos / 2, -0.55, "Verificação de pressupostos",
              ha="center", va="bottom", fontsize=11.5, fontweight="bold", clip_on=False)
    ax_p.text(n_pressupostos + (n_cols_p - n_pressupostos) / 2, -0.55, "Teste de hipótese",
              ha="center", va="bottom", fontsize=11.5, fontweight="bold", clip_on=False)

    sns.heatmap(effect, annot=True, fmt=".3f", cmap="Blues", vmin=0,
                vmax=max(0.1, effect.iloc[:, 0].max(skipna=True)),
                mask=effect.isna(), cbar=True, cbar_kws={"label": "ε² (Kruskal-Wallis)"},
                linewidths=0.6, linecolor="white", ax=ax_e,
                yticklabels=False, annot_kws={"fontsize": 11})
    ax_e.tick_params(axis="x", rotation=0, labelsize=11)
    ax_e.text(0.5, -0.55, "Magnitude",
              ha="center", va="bottom", fontsize=11.5, fontweight="bold", clip_on=False)

    legend_elems = [Patch(facecolor=c, edgecolor="#999999", label=l)
                    for c, l in zip(_CORES_P, _LEGENDA_P)]
    fig.legend(handles=legend_elems, loc="lower center", ncol=3,
               bbox_to_anchor=(0.5, -0.14), frameon=False, fontsize=10.5)

    nome_arquivo = f"heatmap_significancia{sufixo}.png"
    fig.savefig(os.path.join(heat_dir, nome_arquivo), dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  salvo: heatmaps/{nome_arquivo}")


def _heatmap_heterogeneidade(df_heterog: Optional[pd.DataFrame], heat_dir: str,
                              metricas_alvo: Optional[list] = None,
                              sufixo: str = "") -> None:
    """heatmap_heterogeneidade_vm{sufixo}.png — métricas (linha) x [Het. KVM,
    Het. QEMU] (coluna): testa se as 3 VMs de origem de cada tratamento
    diferem entre si (ver checar_heterogeneidade_vms). Complementa
    heatmap_significancia{sufixo}.png, que é sobre host vs. KVM vs. QEMU, não
    sobre VM vs. VM dentro do mesmo tratamento. Aqui as duas colunas (KVM,
    QEMU) são da mesma natureza — o mesmo teste aplicado a dois tratamentos
    — então, ao contrário de heatmap_significancia, não há necessidade de
    separar em grupos: um único rótulo de eixo x já é suficiente."""
    if df_heterog is None or df_heterog.empty:
        print(f"  [aviso] heterogeneidade_entre_vms vazio — heatmap_heterogeneidade_vm{sufixo}.png não gerado")
        return

    pivot_p = df_heterog.pivot(index="metrica", columns="ambiente", values="kruskal_p")
    pivot_het = df_heterog.pivot(index="metrica", columns="ambiente", values="heterogeneo")

    disponiveis = _ordem_metricas_presentes(pivot_p.index)
    metricas = ([m for m in disponiveis if m in set(metricas_alvo)]
                if metricas_alvo is not None else disponiveis)
    colunas = [c for c in ("kvm", "qemu") if c in pivot_p.columns]
    pivot_p = pivot_p.reindex(index=metricas, columns=colunas)
    pivot_het = pivot_het.reindex(index=metricas, columns=colunas)

    cores = ["#d9d9d9", "#2ca25f", "#d73027"]
    legenda = ["sem dado (<2 VMs válidas)", "homogêneo (p≥0.05)",
               "heterogêneo (p<0.05)"]

    def _bin_het(v):
        if pd.isna(v):
            return 0
        return 2 if bool(v) else 1

    codes = pivot_het.map(_bin_het)
    textos = pivot_p.map(lambda p: "—" if pd.isna(p) else _formatar_p(p))
    rotulos_linha = [_rotulo_curto(m) for m in metricas]
    rotulos_coluna = ["Het. KVM" if c == "kvm" else "Het. QEMU" for c in colunas]

    n = len(metricas)
    # Sem título embutido (caption via LaTeX no artigo). Fonte grande (13pt)
    # pelo mesmo motivo do heatmap de significância: a imagem costuma ser
    # incluída menor que o tamanho nativo.
    fig, ax = plt.subplots(figsize=(5.2, 0.45 * n + 1.3))
    cmap = ListedColormap(cores)
    norm = BoundaryNorm([-0.5, 0.5, 1.5, 2.5], len(cores))
    sns.heatmap(codes, annot=textos, fmt="", cmap=cmap, norm=norm, cbar=False,
                linewidths=0.6, linecolor="white", ax=ax,
                yticklabels=rotulos_linha, xticklabels=rotulos_coluna,
                annot_kws={"fontsize": 13})
    ax.set_ylabel("")
    ax.set_xlabel("")
    ax.tick_params(axis="x", rotation=0, labelsize=13)
    ax.tick_params(axis="y", labelsize=13)

    legend_elems = [Patch(facecolor=c, edgecolor="#999999", label=l) for c, l in zip(cores, legenda)]
    fig.legend(handles=legend_elems, loc="upper left", bbox_to_anchor=(1.0, 0.9),
               bbox_transform=fig.transFigure, frameon=False, fontsize=11)

    nome_arquivo = f"heatmap_heterogeneidade_vm{sufixo}.png"
    fig.savefig(os.path.join(heat_dir, nome_arquivo), dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"  salvo: heatmaps/{nome_arquivo}")


def gerar_heatmap_resumo(df_stats: pd.DataFrame, df_heterog: Optional[pd.DataFrame], out_dir: str) -> None:
    """Gera QUATRO figuras de heatmap em out_dir/heatmaps/ (mesma
    convenção de gerar_graficos: relocar depois para
    results/figures/heatmaps/ — ver docs/REPRODUCE.md):

      - heatmap_significancia.png / heatmap_heterogeneidade_vm.png:
        versão CURADA (METRICAS_PRINCIPAIS, 1 métrica por domínio
        experimental) — pensada para o corpo do artigo, onde ~19 linhas
        por figura seriam ilegíveis/pouco informativas.
      - heatmap_significancia_completo.png / heatmap_heterogeneidade_vm_completo.png:
        versão com TODAS as métricas — nada é descartado, só reordenado
        por relevância editorial; fica para auditoria/apêndice.

    Estas figuras COMPLEMENTAM os boxplots — resumem de forma compacta o
    resultado dos testes formais, o que hoje só existe como tabela de
    texto (relatorio.txt) ou coluna a coluna (resultados_estatisticos.csv)."""
    heat_dir = os.path.join(out_dir, "heatmaps")
    os.makedirs(heat_dir, exist_ok=True)
    _heatmap_significancia(df_stats, heat_dir, metricas_alvo=None, sufixo="_completo")
    _heatmap_heterogeneidade(df_heterog, heat_dir, metricas_alvo=None, sufixo="_completo")
    _heatmap_significancia(df_stats, heat_dir, metricas_alvo=METRICAS_PRINCIPAIS, sufixo="")
    _heatmap_heterogeneidade(df_heterog, heat_dir, metricas_alvo=METRICAS_PRINCIPAIS, sufixo="")



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs-dir", required=True,
                     help="Pasta contendo host/, kvm-<vm>/, qemu-<vm>/ (ex.: kvm-gabrielly, kvm-lucas, kvm-ramalho, qemu-gabrielly, ...)")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    os.makedirs(os.path.join(args.out_dir, "graficos"), exist_ok=True)

    all_rows = []
    avisos_parsing = []
    subpastas = sorted(d for d in os.listdir(args.logs_dir)
                        if os.path.isdir(os.path.join(args.logs_dir, d)))

    for pasta in subpastas:
        full_pasta = os.path.join(args.logs_dir, pasta)
        arquivos = glob.glob(os.path.join(full_pasta, "*.txt"))
        if not arquivos:
            continue

        if pasta == "host":
            ambiente, vm_id = "host", "host"
        elif pasta.startswith("kvm-"):
            ambiente, vm_id = "kvm", pasta[len("kvm-"):]
        elif pasta.startswith("qemu-"):
            ambiente, vm_id = "qemu", pasta[len("qemu-"):]
        else:
            print(f"[aviso] pasta '{pasta}' não reconhecida (esperado: host, kvm-<vm>, qemu-<vm>) — ignorando")
            continue

        print(f"[{ambiente}/{vm_id}] {len(arquivos)} arquivos em {full_pasta}")
        for f in arquivos:
            all_rows.extend(parse_file(f, ambiente, vm_id, avisos=avisos_parsing))

    df = pd.DataFrame(all_rows)
    if df.empty:
        print("ERRO: nenhum dado extraído. Confira os caminhos e os regex de parsing.")
        sys.exit(1)

    df_avisos = pd.DataFrame(
        avisos_parsing, columns=["arquivo", "ambiente", "vm_id", "metrica_esperada", "motivo"]
    )
    df_avisos.to_csv(os.path.join(args.out_dir, "avisos_parsing.csv"), index=False)
    if not df_avisos.empty:
        print(f"\n[AVISO] {len(df_avisos)} caso(s) de métrica esperada não encontrada em algum "
              f"arquivo — ver {os.path.join(args.out_dir, 'avisos_parsing.csv')} "
              f"(explica eventuais diferenças de n entre métricas/ambientes, ex.: n=14 vs n=15).")

    df.to_csv(os.path.join(args.out_dir, "dados_extraidos.csv"), index=False)
    print(f"\nExtraídos {len(df)} valores de {df['arquivo'].nunique()} arquivos.")
    print(df.groupby(["ambiente", "vm_id"])["arquivo"].nunique().to_string())

    # Heterogeneidade entre VMs — calculada ANTES dos gráficos, porque a
    # decisão de colorir pontos por VM em cada subplot depende deste
    # resultado (só usamos cor por VM onde a diferença entre VMs é real).
    df_heterog = checar_heterogeneidade_vms(df)
    df_heterog.to_csv(os.path.join(args.out_dir, "heterogeneidade_entre_vms.csv"), index=False)
    n_heterog = df_heterog["heterogeneo"].sum() if not df_heterog.empty else 0
    print(f"\nChecagem de heterogeneidade entre VMs: {n_heterog}/{len(df_heterog)} "
          f"combinações métrica x tratamento mostram diferença significativa entre VMs (p<0.05)")

    gerar_graficos(df, args.out_dir, df_heterog=df_heterog)
    print(f"Gráficos salvos em {os.path.join(args.out_dir, 'graficos')}/")

    # Descritiva
    desc = df.groupby(["metrica", "ambiente"])["valor"].agg(
        n="count", media="mean", mediana="median", desvio="std",
        q1=lambda s: s.quantile(0.25), q3=lambda s: s.quantile(0.75)
    )
    desc["iqr"] = desc["q3"] - desc["q1"]
    desc.to_csv(os.path.join(args.out_dir, "resumo_descritivo.csv"))

    # Pipeline estatístico por métrica (ambiente vs ambiente, agrupando as 3 VMs)
    linhas_stats = []
    for metrica, sub in df.groupby("metrica"):
        if sub["ambiente"].nunique() < 2:
            continue
        res = analisar_metrica(sub[["ambiente", "vm_id", "valor"]], metrica_nome=metrica)
        res["metrica"] = metrica
        linhas_stats.append(res)

    df_stats = pd.DataFrame(linhas_stats)

    # FDR (Benjamini-Hochberg) através de TODAS as métricas simultaneamente
    # — distinto do post-hoc de Dunn (Bonferroni), que corrige comparações
    # dentro de uma métrica, não entre métricas.
    df_stats = aplicar_correcao_fdr(df_stats)

    # Sanidade: métricas conceitualmente distintas com estatística de teste
    # global EXATAMENTE idêntica.
    df_alerta_dup = checar_estatisticas_duplicadas(df_stats)
    df_alerta_dup.to_csv(os.path.join(args.out_dir, "alerta_estatisticas_duplicadas.csv"), index=False)

    df_stats.to_csv(os.path.join(args.out_dir, "resultados_estatisticos.csv"), index=False)

    # Heatmaps-resumo dos testes formais (complementam os boxplots de
    # gerar_graficos — ver docstring de gerar_heatmap_resumo).
    gerar_heatmap_resumo(df_stats, df_heterog, args.out_dir)

    # Metadata da execução (commit git, timestamp, seed) para auditoria/reprodutibilidade
    metadata = obter_metadata_execucao(args)
    with open(os.path.join(args.out_dir, "metadata.json"), "w") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    # Relatório legível
    with open(os.path.join(args.out_dir, "relatorio.txt"), "w") as f:
        f.write("RELATÓRIO DE ANÁLISE ESTATÍSTICA — BENCHMARKS ppc64le\n")
        f.write("=" * 70 + "\n\n")
        f.write(
            "NOTA sobre alerta_estatisticas_duplicadas.csv: este arquivo lista pares de\n"
            "métricas cujo teste estatístico global produziu resultado exatamente idêntico.\n"
            "Isso pode ser benigno (grupos de mesmo tamanho com separação completa entre\n"
            "ambientes produzem o mesmo H de Kruskal-Wallis por construção matemática,\n"
            "independentemente dos valores brutos) ou indicar um problema de cálculo —\n"
            "revise manualmente antes de aceitar qualquer novo caso que apareça aqui.\n"
            "É gerado automaticamente em toda execução deste pipeline, não é uma\n"
            "checagem pontual/manual.\n\n"
            "NOTA sobre \"(cuidado: pseudo-replicação)\" abaixo: ver\n"
            "docs/GLOSSARIO_METODOLOGICO.md para o que esse aviso significa e por que é uma\n"
            "consequência esperada do desenho aninhado (3 VMs x 5 execuções), não um erro.\n"
        )
        f.write("-" * 70 + "\n\n")
        for _, row in df_stats.iterrows():
            f.write(f"### {row['metrica']} ###\n")
            for amb in ("host", "kvm", "qemu"):
                if f"{amb}_media" in row and pd.notna(row.get(f"{amb}_media")):
                    f.write(
                        f"  {amb}: n={int(row.get(f'{amb}_n', 0))}, "
                        f"média={row.get(f'{amb}_media'):.3f}, "
                        f"mediana={row.get(f'{amb}_mediana'):.3f}, "
                        f"IC95%=[{row.get(f'{amb}_ic95_lo'):.3f}, {row.get(f'{amb}_ic95_hi'):.3f}], "
                        f"Shapiro p={row.get(f'{amb}_shapiro_p', float('nan')):.4f}\n"
                    )
            f.write(f"  Levene p={row.get('levene_p', float('nan')):.4f}\n")
            f.write(f"  Teste global: {row.get('teste_global', '-')}, "
                    f"estatística={row.get('teste_global_estat', float('nan')):.4f}, "
                    f"p={row.get('teste_global_p', float('nan')):.4g}\n")
            if pd.notna(row.get("p_fdr_corrigido")):
                f.write(f"  p corrigido (FDR/Benjamini-Hochberg, entre todas as métricas): "
                        f"{row.get('p_fdr_corrigido'):.4g}\n")
            if pd.notna(row.get("effect_size_epsilon2")):
                f.write(f"  Effect size (epsilon^2): {row.get('effect_size_epsilon2'):.4f}\n")
            het_row = df_heterog[df_heterog["metrica"] == row["metrica"]]
            for _, hr in het_row.iterrows():
                flag = "SIM (cuidado: pseudo-replicação)" if hr["heterogeneo"] else "não"
                f.write(f"  Heterogeneidade entre VMs ({hr['ambiente']}): "
                        f"H={hr['kruskal_h']:.3f}, p={hr['kruskal_p']:.4f} -> diferem entre si? {flag}\n")
            f.write("\n")

    print(f"\nConcluído. Saídas em: {args.out_dir}")
    print("  - dados_extraidos.csv")
    print("  - resumo_descritivo.csv")
    print("  - resultados_estatisticos.csv (com p_fdr_corrigido)")
    print("  - heterogeneidade_entre_vms.csv")
    print("  - avisos_parsing.csv")
    print("  - alerta_estatisticas_duplicadas.csv")
    print("  - metadata.json")
    print("  - relatorio.txt")


if __name__ == "__main__":
    main()