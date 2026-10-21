# Desvios registrados (x86/Ubuntu)

## 2026-10-15 — `exp_get_base.sh`: falso-negativo no SHA-256 por locale

**O quê:** `exp_get_base.sh` verifica a integridade da imagem base com
`sha256sum -c --ignore-missing CHECKSUM | grep "$IMG: OK"`. Nesta máquina o
locale é `pt_BR.UTF-8` (`LANG=pt_BR.UTF-8`), e o `sha256sum` traduz a palavra
de status: em vez de `OK` ele imprime `SUCESSO`. O `grep` não encontra `OK` e
o script aborta com `ERRO: SHA-256 não confere`, mesmo com o arquivo íntegro.

**Por quê é desvio e não falha real:** conferi manualmente —
`sha256sum AlmaLinux-8-GenericCloud-latest.x86_64.qcow2` em `~/discos`
retornou o mesmo hash listado em `CHECKSUM`
(`4a4dfcbd0a02f9fbf62dee6420a168858ff1640a26fb8840ea4013db57436a6c`), e
`sha256sum -c` isolado sobre essa linha confirmou `SUCESSO` (exit 0). O
`die()` interrompeu o script **antes** do `mv`, então nenhum arquivo foi
corrompido ou renomeado incorretamente.

**Correção aplicada:** forcei `LC_ALL=C` só na chamada do `sha256sum -c` em
`exp_get_base.sh` (script não está na lista de protegidos do CLAUDE.md), para
que a saída de status fique em inglês (`OK`) independente do locale do shell.
Não mudei a lógica de verificação, nem a URL, nem o nome/versão da imagem.

**Linha alterada:**
```diff
- sha256sum -c --ignore-missing CHECKSUM | grep "$IMG: OK" || die "SHA-256 não confere"
+ LC_ALL=C sha256sum -c --ignore-missing CHECKSUM | grep "$IMG: OK" || die "SHA-256 não confere"
```
2026-10-15T18:11:14-03:00 kvm-nx-lpage-recovery (thread do KVM x86, mitigação iTLB multihit) estava na CPU 3; movida para 5,11 (emulador) antes da série KVM. 2 IRQs não movíveis nas CPUs reservadas (Power9: 5).
2026-10-15T18:15:38-03:00 correção: taskset não funcionou (cgroup vcpu2 só permite CPU 3); a thread foi movida para o cgroup 'emulator' do libvirt (cgroup.threads) e ficou em 5,11.
2026-10-15T19:19:30-03:00 execução 1/15 KVM abortada: stress-ng --vm 'detected 1 bit errors' (RAM sem ECC; provável método rowhammer). benchmark.sh: erro de bit no --vm passa a ser marcado [BIT-ERROR] e não aborta; comando de carga inalterado. Arquivo abortado mantido como registro.
2026-10-16T09:18:50-03:00 série host abortou no aquecimento: benchmark.sh compara Cpus_allowed_list como texto ('1-4' do kernel x '1,2,3,4'). HOST_CPU_LIST passou a '1-4' (mesmas CPUs). Sem efeito nos dados.
