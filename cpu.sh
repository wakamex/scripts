#!/bin/bash
while true; do
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    cpu_usage=$(mpstat 1 1 | awk '/Average:/ {print $3}')
    black_processes=$(pgrep -fc black)
    ruff_processes=$(pgrep -fc ruff)
    pylint_processes=$(pgrep -fc pylint)
    pyright_processes=$(pgrep -fc pyright)
    printf "%s | CPU: %.2f%% | Black: %d | Ruff: %d | Pylint: %d | Pyright: %d\n" \
        "$timestamp" "$cpu_usage" "$black_processes" "$ruff_processes" "$pylint_processes" "$pyright_processes"
    sleep 1
done
