#!/bin/bash

for i in {1..15}; do
    echo "[$(date)] Submetendo execução $i de 15..."

    JOBID=$(sbatch --parsable benchmark-host.sh)

    echo "Job $JOBID submetido."

    # Espera o job terminar
    while squeue -h -j "$JOBID" >/dev/null && [ -n "$(squeue -h -j "$JOBID")" ]; do
        sleep 10
    done

    echo "[$(date)] Job $JOBID finalizado."
done

echo "Todas as 15 execuções foram concluídas!"
