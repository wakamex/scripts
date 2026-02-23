#!/usr/bin/env bash
# Export a numbered list of all commits: date, short hash, first line of message, [co-authors]
git -C "${1:-.}" log --reverse --format='%x00%ad %h %s%x01%b' --date=short | python3 -c "
import sys
G='\033[32m'; Y='\033[33m'; DW='\033[2;37m'; R='\033[0m'
data = sys.stdin.read()
entries = data.split('\x00')
n = 0
for e in entries:
    e = e.strip()
    if not e:
        continue
    parts = e.split('\x01', 1)
    header = parts[0].strip()
    date, hash, *msg = header.split(' ', 2)
    msg = msg[0] if msg else ''
    body = parts[1] if len(parts) > 1 else ''
    coauthors = []
    for line in body.splitlines():
        if 'Co-Authored-By:' in line:
            name = line.split('Co-Authored-By:')[1].split('<')[0].strip()
            if name:
                coauthors.append(name)
    n += 1
    ca = ', '.join(coauthors)
    suffix = f' {DW}{ca}{R}' if coauthors else ''
    print(f'{n:6d}\t{G}{date} {Y}{hash} {R}{msg}{suffix}')
"
