#!/bin/zsh
# take in three parameters and run different yt-dlp commands based on them
# $1 is the filename
# $2 is the start time in seconds
# $3 is the end time in seconds
# example: ./yt.sh "https://www.youtube.com/watch?v=Ia-zOj8awiI" $((4*60+52)) $((4*60+55))

# download audio and video separately
# bothargs='-f bestvideo,bestaudio -k --no-embed-metadata -o "temp%(autonumber)s" --cookies-from-browser firefox'
bothargs='-f "bestvideo[vcodec=av01]+bestaudio/bestvideo+bestaudio" -k --no-embed-metadata -o "combined" --merge-output-format mp4'
eval yt-dlp --progress -i \'$1\' $bothargs # using https://github.com/yt-dlp/yt-dlp

# cut if asked for
[ -n "$2" ] && timeargs=("-ss $2")  # add start time
[ -n "$3" ] && timeargs+=("-to $3") # add end time
if [ -n "$2" ]; then                # cut if asked for
    eval ffmpeg -y -hide_banner $timeargs -i combined.mp4 combined_cut.mp4
    mv combined_cut.mp4 combined.mp4
fi

rm temp* 2>/dev/null || true
