#!/bin/bash
name=$(hostname)
if [ -n "$1" ]; then
    name="$1"
fi
echo "using name $name"
seed=$(echo $name | cksum | cut -f1 -d" ")
echo "using seed $seed" 

r=$(printf "%02x" $(( (seed & 0xFF0000) >> 16 )))
g=$(printf "%02x" $(( (seed & 0x00FF00) >> 8 )))
b=$(printf "%02x" $(( seed & 0x0000FF )))
color="#$r$g$b"
echo "using color $color"

tmux set-option status-bg $color
tmux set-option status-fg black
tmux set-option pane-border-style "fg=$color"
tmux set-option pane-active-border-style "fg=$color"
