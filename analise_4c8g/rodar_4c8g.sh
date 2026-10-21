#!/usr/bin/env bash
# Organiza os dados do experimento 4c8g (ppc64le e x86) dentro do repositório do TCC e roda: pipeline do TCC
# por arquitetura (cópia com patch em analise_4c8g/build/) + comparação entre arquiteturas.
# O src/analysis/analyze_benchmarks.py rastreado nunca é modificado.
# uso (de dentro do repositório, ex.: ~/Documentos/Multi-Arq/experimentos):
#   bash analise_4c8g/rodar_4c8g.sh ~/Documentos/Multi-Arq
set -euo pipefail
BASE="${1:?pasta que contém p9_4c8g/ e x86_4c8g/ (opcionais: p9_4c8g_cpu/, x86_4c8g_cpu/, x86_diag.tar.gz)}"
REPO="$(pwd)"; HERE="$(cd "$(dirname "$0")" && pwd)"
[ -f src/analysis/analyze_benchmarks.py ] || { echo "rode de dentro do repositório do TCC"; exit 1; }
[ -n "${VIRTUAL_ENV:-}" ] || { [ -f venv/bin/activate ] && source venv/bin/activate; }

echo "== 1. dados brutos -> data/raw_4c8g/<arq>/{host,kvm-exp,qemu-exp} (só as 15 válidas; warmup/descartadas ficam de fora)"
for A in p9 x86; do
  S="$BASE/${A}_4c8g"; D="data/raw_4c8g/$A"
  [ -d "$S" ] || { echo "falta $S"; exit 1; }
  rm -rf "$D/host" "$D/kvm-exp" "$D/qemu-exp" "$D/meta"; mkdir -p "$D/host" "$D/kvm-exp" "$D/qemu-exp" "$D/meta"
  cp "$S"/host/benchmark_*.txt "$D/host/"; cp "$S"/kvm/benchmark_*.txt "$D/kvm-exp/"; cp "$S"/qemu/benchmark_*.txt "$D/qemu-exp/"
  cp "$S"/env_host_*.txt "$S"/monitor_*.csv "$S"/iostat_*.txt "$D/meta/" 2>/dev/null || true
  cp "$S"/DESVIOS.md "$S"/auto.log "$S"/release.log "$D/meta/" 2>/dev/null || true
  for amb in host kvm-exp qemu-exp; do
    n=$(ls "$D/$amb"/benchmark_*.txt | wc -l); echo "  $A/$amb: $n"
    [ "$n" -eq 15 ] || { echo "ERRO: esperado 15 em $A/$amb"; exit 1; }
  done
  (cd "$D" && sha256sum */benchmark_*.txt > SHA256SUMS)

  # série de CPU por subconjunto de métodos -> cpu_subset/{host,kvm,qemu}
  SC="$BASE/${A}_4c8g_cpu"
  if [ -d "$SC" ]; then
    C="$D/cpu_subset"; rm -rf "$C"; mkdir -p "$C/meta"
    for amb in host kvm qemu; do
      mkdir -p "$C/$amb"; cp "$SC/${amb}_cpu"/benchmark_cpu_*.txt "$C/$amb/"
      cp "$SC/${amb}_cpu/series.log" "$C/meta/series_${amb}.log"
      n=$(ls "$C/$amb"/benchmark_cpu_*.txt | wc -l); echo "  $A/cpu_subset/$amb: $n"
      [ "$n" -eq 15 ] || { echo "ERRO: esperado 15 em $A/cpu_subset/$amb"; exit 1; }
    done
    cp "$SC"/cpu_methods_subset.txt "$SC"/cpu_probe_tcg.log "$SC"/cpu_noite.log "$SC"/monitor_cpu_*.csv "$C/meta/" 2>/dev/null || true
    (cd "$C" && sha256sum */benchmark_cpu_*.txt > SHA256SUMS)
  else
    echo "  (sem $SC: cpu_subset não atualizado)"
  fi
done
if [ -f "$BASE/x86_diag.tar.gz" ]; then
  rm -rf data/raw_4c8g/x86/diag; tar xzf "$BASE/x86_diag.tar.gz" -C data/raw_4c8g/x86; echo "  x86/diag: ok"
fi

echo "== 2. cópia do analyze_benchmarks.py com patch (1 VM por tratamento + descarta stress-ng que falhou)"
mkdir -p "$HERE/build"; cp src/analysis/analyze_benchmarks.py "$HERE/build/analyze_benchmarks.py"
python3 "$HERE/patch_4c8g.py" "$HERE/build/analyze_benchmarks.py"

echo "== 3. pipeline do TCC por arquitetura"
for A in p9 x86; do
  python3 "$HERE/build/analyze_benchmarks.py" --logs-dir "$REPO/data/raw_4c8g/$A" --out-dir "$REPO/results/4c8g/$A" \
    > "results_4c8g_$A.log" 2>&1 || { echo "ERRO no pipeline $A (veja results_4c8g_$A.log)"; exit 1; }
  mkdir -p "results/4c8g/$A"; mv "results_4c8g_$A.log" "results/4c8g/$A/pipeline.log"
  echo "  $A: ok -> results/4c8g/$A"
done

echo "== 4. comparação entre arquiteturas"
python3 "$HERE/compare_arch_4c8g.py" --p9 results/4c8g/p9/extracted_data.csv --x86 results/4c8g/x86/extracted_data.csv \
  --out-dir results/4c8g/comparacao_mediana --raw-x86 data/raw_4c8g/x86 > /dev/null
tar czf "$BASE/resultados_4c8g_analise.tar.gz" results/4c8g
echo "PRONTO. Resumo: results/4c8g/comparacao_mediana/resumo.txt | pacote: $BASE/resultados_4c8g_analise.tar.gz"
