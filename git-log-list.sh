#!/usr/bin/env bash
# Export a numbered list of all commits: date, short hash, first line of message
git -C "${1:-.}" log --format='%ad %h %s' --date=short | nl -ba
