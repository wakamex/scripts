#!/usr/bin/env bash
# List recent local branches: date, relative time, author, branch name, subject, co-authors
git for-each-ref refs/heads \
  --color=always \
  --sort=-committerdate \
  --count="${1:-12}" \
  --format=' %(color:green)%(committerdate:format-local:%b-%d)%(color:reset) %(color:dim green)%(align:14,right)%(committerdate:relative)%(end)%(color:reset) %(color:red)%(align:17,left)%(authorname)%(end)%(color:reset) %(color:yellow)%(refname:lstrip=2)%(color:reset) %(subject) %(color:dim white)[%(trailers:key=Co-authored-by,key=Signed-off-by,separator=%x2C ,valueonly)]%(color:reset)' \
| sed 's/ <[^>]*>//g; s/\[\]//g'
