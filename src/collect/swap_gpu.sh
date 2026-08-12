#!/bin/bash
# =============================================================================
# GPU SWAP BETWEEN VMs — Power9 ppc64le / KVM + VFIO
# Usage: ./swap_gpu.sh <source_vm> <target_vm>
# Prerequisite: GPUs are already under vfio-pci (vfio-pci.conf configured)
# =============================================================================

VM_ORIGEM=$1
VM_DESTINO=$2

if [[ -z "$VM_ORIGEM" || -z "$VM_DESTINO" ]]; then
    echo "Usage: $0 <source_vm> <target_vm>"
    exit 1
fi

# Saves the XML BEFORE any shutdown operation
echo "=== Saving XML for $VM_ORIGEM ==="
sudo virsh dumpxml --inactive "$VM_ORIGEM" > /tmp/${VM_ORIGEM}_backup.xml
if [[ $? -ne 0 ]]; then
    echo "ERROR: Could not save the XML for $VM_ORIGEM. Aborting."
    exit 1
fi
echo "Backup saved to /tmp/${VM_ORIGEM}_backup.xml"

echo ""
echo "=== Shutting down $VM_ORIGEM ==="
sudo virsh shutdown "$VM_ORIGEM"

echo "Waiting for shut off..."
for i in $(seq 1 30); do
    STATUS=$(sudo virsh domstate "$VM_ORIGEM" 2>/dev/null)
    if [[ "$STATUS" == "shut off" ]]; then
        echo "$VM_ORIGEM shut down."
        break
    fi
    sleep 5
    if [[ $i -eq 30 ]]; then
        echo "WARNING: $VM_ORIGEM did not shut down within 150s. Forcing..."
        sudo virsh destroy "$VM_ORIGEM"
        sleep 3
    fi
done

echo ""
echo "=== Removing hostdevs and backingStore from $VM_ORIGEM ==="
python3 -c "
import re
content = open('/tmp/${VM_ORIGEM}_backup.xml').read()

# Remove hostdevs
clean = re.sub(r'\s*<hostdev[^>]*>.*?</hostdev>', '', content, flags=re.DOTALL)

# Remove non-empty backingStore (keep only <backingStore/>)
clean = re.sub(r'<backingStore>.*?</backingStore>', '<backingStore/>', clean, flags=re.DOTALL)

open('/tmp/${VM_ORIGEM}_no_gpu.xml', 'w').write(clean)
print('OK')
"
sudo virsh define /tmp/${VM_ORIGEM}_no_gpu.xml
echo "$VM_ORIGEM redefined without GPU."

echo ""
echo "=== Validating hostdevs for $VM_DESTINO ==="
HOSTDEVS=$(sudo virsh dumpxml --inactive "$VM_DESTINO" | grep -c "hostdev")
ENDERECOS=$(sudo virsh dumpxml --inactive "$VM_DESTINO" | grep -c "domain='0x0004'\|domain='0x0006'")

echo "Hostdev lines: $HOSTDEVS (expected: 16)"
echo "Correct PCI addresses: $ENDERECOS (expected: 8)"

if [[ $HOSTDEVS -ne 16 || $ENDERECOS -ne 8 ]]; then
    echo "ERROR: $VM_DESTINO doesn't have the correct hostdevs. Aborting."
    sudo virsh define /tmp/${VM_ORIGEM}_backup.xml
    exit 1
fi

echo ""
echo "=== Clearing backingStore for $VM_DESTINO before starting it ==="
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
echo "=== Starting $VM_DESTINO ==="
sudo virsh start "$VM_DESTINO"
if [[ $? -eq 0 ]]; then
    echo "$VM_DESTINO started successfully."
else
    echo "ERROR starting $VM_DESTINO."
    echo "Restoring the original XML for $VM_ORIGEM..."
    sudo virsh define /tmp/${VM_ORIGEM}_backup.xml
    exit 1
fi

echo ""
echo "=== Final state ==="
sudo virsh list --all | grep -E "$VM_ORIGEM|$VM_DESTINO"
