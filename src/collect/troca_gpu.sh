#!/bin/bash
# =============================================================================
# TROCA DE GPU ENTRE VMs — Power9 ppc64le / KVM + VFIO
# Uso: ./troca_gpu.sh <vm_origem> <vm_destino>
# Pré-requisito: GPUs já estão sob vfio-pci (vfio-pci.conf configurado)
# =============================================================================

VM_ORIGEM=$1
VM_DESTINO=$2

if [[ -z "$VM_ORIGEM" || -z "$VM_DESTINO" ]]; then
    echo "Uso: $0 <vm_origem> <vm_destino>"
    exit 1
fi

# Salva o XML ANTES de qualquer operação de desligamento
echo "=== Salvando XML de $VM_ORIGEM ==="
sudo virsh dumpxml --inactive "$VM_ORIGEM" > /tmp/${VM_ORIGEM}_backup.xml
if [[ $? -ne 0 ]]; then
    echo "ERRO: Não foi possível salvar o XML de $VM_ORIGEM. Abortando."
    exit 1
fi
echo "Backup salvo em /tmp/${VM_ORIGEM}_backup.xml"

echo ""
echo "=== Desligando $VM_ORIGEM ==="
sudo virsh shutdown "$VM_ORIGEM"

echo "Aguardando shut off..."
for i in $(seq 1 30); do
    STATUS=$(sudo virsh domstate "$VM_ORIGEM" 2>/dev/null)
    if [[ "$STATUS" == "shut off" ]]; then
        echo "$VM_ORIGEM desligada."
        break
    fi
    sleep 5
    if [[ $i -eq 30 ]]; then
        echo "AVISO: $VM_ORIGEM não desligou em 150s. Forçando..."
        sudo virsh destroy "$VM_ORIGEM"
        sleep 3
    fi
done

echo ""
echo "=== Removendo hostdevs e backingStore de $VM_ORIGEM ==="
python3 -c "
import re
content = open('/tmp/${VM_ORIGEM}_backup.xml').read()

# Remove hostdevs
clean = re.sub(r'\s*<hostdev[^>]*>.*?</hostdev>', '', content, flags=re.DOTALL)

# Remove backingStore não vazio (mantém apenas <backingStore/>)
clean = re.sub(r'<backingStore>.*?</backingStore>', '<backingStore/>', clean, flags=re.DOTALL)

open('/tmp/${VM_ORIGEM}_sem_gpu.xml', 'w').write(clean)
print('OK')
"
sudo virsh define /tmp/${VM_ORIGEM}_sem_gpu.xml
echo "$VM_ORIGEM redefinida sem GPU."

echo ""
echo "=== Validando hostdevs de $VM_DESTINO ==="
HOSTDEVS=$(sudo virsh dumpxml --inactive "$VM_DESTINO" | grep -c "hostdev")
ENDERECOS=$(sudo virsh dumpxml --inactive "$VM_DESTINO" | grep -c "domain='0x0004'\|domain='0x0006'")

echo "Linhas hostdev: $HOSTDEVS (esperado: 16)"
echo "Endereços PCI corretos: $ENDERECOS (esperado: 8)"

if [[ $HOSTDEVS -ne 16 || $ENDERECOS -ne 8 ]]; then
    echo "ERRO: $VM_DESTINO não tem os hostdevs corretos. Abortando."
    sudo virsh define /tmp/${VM_ORIGEM}_backup.xml
    exit 1
fi

echo ""
echo "=== Limpando backingStore de $VM_DESTINO antes de subir ==="
sudo virsh dumpxml --inactive "$VM_DESTINO" > /tmp/${VM_DESTINO}_pre_start.xml
python3 -c "
import re
content = open('/tmp/${VM_DESTINO}_pre_start.xml').read()
clean = re.sub(r'<backingStore>.*?</backingStore>', '<backingStore/>', content, flags=re.DOTALL)
open('/tmp/${VM_DESTINO}_pre_start.xml', 'w').write(clean)
print('OK')
"
sudo virsh define /tmp/${VM_DESTINO}_pre_start.xml

echo ""
echo "=== Subindo $VM_DESTINO ==="
sudo virsh start "$VM_DESTINO"
if [[ $? -eq 0 ]]; then
    echo "$VM_DESTINO iniciada com sucesso."
else
    echo "ERRO ao iniciar $VM_DESTINO."
    echo "Restaurando XML original de $VM_ORIGEM..."
    sudo virsh define /tmp/${VM_ORIGEM}_backup.xml
    exit 1
fi

echo ""
echo "=== Estado final ==="
sudo virsh list --all | grep -E "$VM_ORIGEM|$VM_DESTINO"
