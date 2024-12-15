#!/bin/zsh

# take in three parameters and run different yt-dlp commands based on them
# $1 is the filename
# $2 is the start time in seconds
# $3 is the end time in seconds
# example: ./yta.sh "https://www.youtube.com/watch?v=Ia-zOj8awiI" $((4*60+52)) $((4*60+55))

# download audio only
# -f ba: Select the best available audio
# -x : Extract audio only
# --audio-format mp3 : Encode audio as mp3, as requested
# -o output.mp3: Set the output filename
bothargs='-f ba -x --audio-format mp3 -o output.mp3'
eval yt-dlp --progress -i \'$1\' $bothargs

# Cut in ffmpeg
[ -n "$2" ] && timeargs=("-ss $2")  # add start time
[ -n "$3" ] && timeargs+=("-to $3") # add end time
if [ -n "$2" ]; then
  eval ffmpeg -y -hide_banner $timeargs -i output.mp3 output_cut.mp3
  mv output_cut.mp3 output.mp3 # rename the cut version back
fi
