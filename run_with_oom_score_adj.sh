#!/bin/bash

# Usage: ./run_with_oom_score_adj.sh <OOM_SCORE_ADJ_VALUE> <COMMAND> <ARG1> <ARG2> ...

oom_score_adj_value="$1"
shift  # Remove the OOM_SCORE_ADJ_VALUE from the arguments
command_to_run="$1"
shift  # Remove the COMMAND from the arguments

"$command_to_run" "$@" &  # Run the command with the remaining arguments in the background
pid=$!  # Get the PID of the last background process

# Set the OOM score for the newly created process
echo "$oom_score_adj_value" > /proc/$pid/oom_score_adj

# Wait for the process to finish
wait $pid
