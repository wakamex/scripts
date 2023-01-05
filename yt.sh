#!/bin/zsh
# take in three parameters and run different yt-dlp commands based on them
# $1 is the filename
# $2 is the start time in seconds
# $3 is the end time in seconds
# example: ./yt.sh "https://www.youtube.com/watch?v=Ia-zOj8awiI" $((4*60+52)) $((4*60+55))

# download audio and video separately
bothargs='-f bestvideo,bestaudio -k --no-embed-metadata -o "temp%(autonumber)s.%(ext)s"'
eval yt-dlp -q --progress -i \'$1\' $bothargs # using https://github.com/yt-dlp/yt-dlp

# combine in ffmpeg
[ -n "$2" ] && timeargs=("-ss $2")  # add start time
[ -n "$3" ] && timeargs+=("-to $3") # add end time
if [ -n "$2" ]; then                # cut if asked for
    eval ffmpeg -y -hide_banner -loglevel warning $timeargs -i temp00001.webm tempvideo_cut.mp4
    eval ffmpeg -y -hide_banner -loglevel warning $timeargs -i temp00002.webm tempaudio_cut.m4a
    eval ffmpeg -y -hide_banner -loglevel warning -i tempvideo_cut.mp4 -i tempaudio_cut.m4a combined.mp4
else
    eval ffmpeg -y -hide_banner -loglevel warning -i temp00001.webm -i temp00002.webm combined.mp4
fi
