#!/bin/zsh

# get list of installed packages
apt list --installed | cut -f1 -d '/' | tail -n +2 >installed.txt
apt list --installed | grep python | cut -f1 -d '/' | tail -n +2 >installedpython.txt

# get their installed size
xargs -a installed.txt apt show | grep Installed-Size | cut -f2,3 -d ' ' >size.txt

# combine them
paste -d ':' installed.txt size.txt | column -t -s ':' | sort -k2 -h >combined.txt

# pull out only >10MB
cat combined.txt | grep MB | sort -k2 -h -u >mb.txt

# exclude packages that start with lib
cat mb.txt | grep -v ^lib >nolib.txt

# get rdepends
rm rdepends.txt
cat nolib.txt | cut -f1 -d ' ' | xargs -I {} sh -c "apt-rdepends -r {} | grep Reverse | wc -l >> rdepends.txt"

# things that depend on python
cat installedpython.txt | cut -f1 -d ' ' | xargs -I {} zsh -c "apt-rdepends -r {} | grep Reverse >>rdependspython.txt"
sort rdependspython.txt | uniq -c | sort -bg | column -t | cut -f1 -d ' ' >rdependspythonvalues.txt
sort rdependspython.txt | uniq -c | sort -bg | cut -f2 -d ':' | cut -f2 -d ' ' >rdependspythonnames.txt
paste -d ' ' rdependspythonnames.txt rdependspythonvalues.txt >rdependspythoncombined.txt
# show what i have installed
xargs -I {} -a rdependspythoninstalled.txt grep '{} ' rdependspythoncombined.txt | sort | uniq

# combine and sort
paste -d ':' nolib.txt rdepends.txt | column -t -s ':' | sort -k4 -n >combined2.txt

cat combined2.txt
