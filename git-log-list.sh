#!/usr/bin/env bash
# Export a numbered list of all commits: date, short hash, first line of message, [co-authors]
git -C "${1:-.}" log --reverse --format='%x00%ad %h %ae %s%x01%b' --date=short | python3 -c "
import sys, subprocess, re

G='\033[32m'; Y='\033[33m'; C='\033[36m'; DW='\033[2;37m'; R='\033[0m'

data = sys.stdin.read()
entries = data.split('\x00')

# Parse all entries
parsed = []
emails = set()
for e in entries:
    e = e.strip()
    if not e:
        continue
    parts = e.split('\x01', 1)
    header = parts[0].strip()
    date, hash, email, *msg = header.split(' ', 3)
    msg = msg[0] if msg else ''
    body = parts[1] if len(parts) > 1 else ''
    emails.add(email)
    parsed.append((date, hash, email, msg, body))

# Persistent cache: ~/.cache/git-log-list/email-logins
import os, pathlib
cache_dir = pathlib.Path.home() / '.cache' / 'git-log-list'
cache_file = cache_dir / 'email-logins'
login_map = {}
if cache_file.exists():
    for line in cache_file.read_text().splitlines():
        if '=' in line:
            k, v = line.split('=', 1)
            login_map[k] = v

new_entries = []
for email in emails:
    if email in login_map:
        continue
    m = re.match(r'(?:\d+\+)?(.+)@users\.noreply\.github\.com', email)
    if m:
        login_map[email] = m.group(1)
        new_entries.append(f'{email}={m.group(1)}')
        continue
    try:
        r = subprocess.run(['gh', 'api', f'/search/users?q={email}+in:email',
            '-q', '.items[0].login'], capture_output=True, text=True, timeout=5)
        login = r.stdout.strip()
        if login:
            login_map[email] = login
            new_entries.append(f'{email}={login}')
    except Exception:
        pass

if new_entries:
    cache_dir.mkdir(parents=True, exist_ok=True)
    with open(cache_file, 'a') as f:
        f.write('\n'.join(new_entries) + '\n')

authors = [login_map.get(email, email.split('@')[0]) for _, _, email, _, _ in parsed]
w = max((len(a) for a in authors), default=0)

for n, (date, hash, email, msg, body) in enumerate(parsed, 1):
    author = authors[n - 1]
    coauthors = []
    for line in body.splitlines():
        if 'Co-Authored-By:' in line:
            name = line.split('Co-Authored-By:')[1].split('<')[0].strip()
            if name:
                coauthors.append(name)
    ca = ', '.join(coauthors)
    suffix = f' {DW}{ca}{R}' if coauthors else ''
    print(f'{n:6d}  {G}{date} {Y}{hash} {C}{author:<{w}} {R}{msg}{suffix}')
"
