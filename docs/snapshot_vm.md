# vm_lucas
sudo virsh dumpxml vm_gabrielly > /tmp/vm_lucas.xml
python3 -c "
import uuid
c = open('/tmp/vm_lucas.xml').read()
c = c.replace('vm_gabrielly', 'vm_lucas')
c = c.replace('90a9f135-6caf-4c02-b168-e914ca47c31f', str(uuid.uuid4()))
c = c.replace('vm_gabrielly.qcow2', 'vm_lucas.qcow2')
open('/tmp/vm_lucas.xml', 'w').write(c)
print('OK')
"
sudo virsh undefine vm_lucas
sudo virsh define /tmp/vm_lucas.xml

# vm_ramalho
sudo virsh dumpxml vm_gabrielly > /tmp/vm_ramalho.xml
python3 -c "
import uuid
c = open('/tmp/vm_ramalho.xml').read()
c = c.replace('vm_gabrielly', 'vm_ramalho')
c = c.replace('90a9f135-6caf-4c02-b168-e914ca47c31f', str(uuid.uuid4()))
c = c.replace('vm_gabrielly.qcow2', 'vm_ramalho.qcow2')
open('/tmp/vm_ramalho.xml', 'w').write(c)
print('OK')
"
sudo virsh undefine vm_ramalho
sudo virsh define /tmp/vm_ramalho.xml
