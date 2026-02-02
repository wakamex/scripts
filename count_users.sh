#!/bin/bash
count=$(who | wc -l)
users=$(who | cut -d' ' -f1 | sort | uniq | tr '\n' ' ' | sed 's/ /, /g' | sed 's/, $//')
echo "Users: $count [$users]"
