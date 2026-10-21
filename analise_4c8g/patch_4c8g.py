# Aplica 2 ajustes ao analyze_benchmarks.py do TCC para o experimento 4c8g (1 VM por tratamento):
#  1) não quebra quando não há teste de heterogeneidade entre VMs (uma VM só);
#  2) descarta métricas do stress-ng de execuções que FALHARAM (ex.: SIGILL sob TCG,
#     "unsuccessful run" sem ser erro de bit, marcador [FALHA-TCG] do kit). O TCC original
#     aproveitava a linha de métrica dessas execuções (por isso o -93% de CPU do QEMU).
#     Execuções marcadas [BIT-ERROR] (RAM sem ECC no x86) continuam válidas.
import sys, re
f = sys.argv[1]; s = open(f, encoding="utf-8").read()
if "_bloco_stressng_falhou" in s:
    print("já aplicado"); sys.exit(0)

old1 = '            het_row = df_heterog[df_heterog["metrica"] == row["metrica"]]\n'
new1 = ('            # uma única VM por tratamento: sem teste de heterogeneidade (df vazio)\n'
        '            het_row = df_heterog[df_heterog["metrica"] == row["metrica"]] if not df_heterog.empty else df_heterog\n')
assert s.count(old1) == 1, "trecho 1 não encontrado"
s = s.replace(old1, new1)

old2 = 'def parse_file(path: str, ambiente: str, vm_id: str = "host",'
helper = '''def _bloco_stressng_falhou(lines: list[str]) -> Optional[str]:
    """Motivo da falha se a execução do stress-ng nesse bloco não foi bem-sucedida
    (a linha de métrica existe, mas o valor não representa o teste completo)."""
    txt = "\\n".join(lines)
    if "[FALHA-TCG]" in txt:
        return "stress-ng falhou sob TCG ([FALHA-TCG])"
    if "unsuccessful run" in txt and "[BIT-ERROR]" not in txt:
        return "stress-ng 'unsuccessful run' (ex.: SIGILL)"
    return None


'''
assert s.count(old2) == 1, "trecho 2 não encontrado"
s = s.replace(old2, helper + old2)

for sec, var, res in [("cpu_block", "cpu_res", 'parse_stressng_block(cpu_block, "cpu")'),
                      ("cache_block", "cache_res", 'parse_stressng_block(cache_block, "cache")')]:
    old = f"    {var} = {res}\n"
    assert s.count(old) == 1, f"trecho {var} não encontrado"
    s = s.replace(old, old + f"    if {var} and _bloco_stressng_falhou({sec}):\n"
                             f"        _falhas.append(({sec!r}.split('_')[0], _bloco_stressng_falhou({sec}))); {var} = None\n")
oldm = "    if mem_res:\n        add(\"mem_bogo_ops_s_real\""
assert s.count(oldm) == 1, "trecho mem não encontrado"
s = s.replace(oldm, "    if mem_res and _bloco_stressng_falhou(mem_block):\n"
                    "        _falhas.append(('mem', _bloco_stressng_falhou(mem_block))); mem_res = None\n" + oldm)
oldr = "    rows = []\n\n    def add(metric, value):"
assert s.count(oldr) == 1, "trecho rows não encontrado"
s = s.replace(oldr, "    rows = []\n    _falhas = []\n\n    def add(metric, value):")
olda = '                    "motivo": "matching section/regex found no valid data in the file",'
assert s.count(olda) == 1, "trecho avisos não encontrado"
s = s.replace(olda, '                    "motivo": next((m for p, m in _falhas if metrica_esperada.startswith(p)),\n'
                    '                                   "matching section/regex found no valid data in the file"),')
open(f, "w", encoding="utf-8").write(s); print("patch aplicado")
