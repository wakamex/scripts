# calculate glyph counts
fc-list :lang=en --format="%{file}\n" | sort -r | uniq | xargs -n1 bash countglyphs.sh >> glyphcounts.txt

# get font details
fc-list :lang=en --format="%{file}:%{family}\n" | sort -r | uniq >> fcdetails.txt

# combine and sort
paste fcdetails.txt glyphcounts.txt | column -s $'\t' -t | awk '{ print $NF,$0 }' | sort -k1,1 -n | cut -f2- -d' ' >> glyphtally.txt
