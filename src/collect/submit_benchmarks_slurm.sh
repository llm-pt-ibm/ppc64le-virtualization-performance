#!/bin/bash

for i in {1..15}; do
    echo "[$(date)] Submitting run $i of 15..."

    JOBID=$(sbatch --parsable benchmark-host.sh)

    echo "Job $JOBID submitted."

    # Wait for the job to finish
    while squeue -h -j "$JOBID" >/dev/null && [ -n "$(squeue -h -j "$JOBID")" ]; do
        sleep 10
    done

    echo "[$(date)] Job $JOBID finished."
done

echo "All 15 runs completed!"
