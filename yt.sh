#!/bin/zsh
# Download video or audio from YouTube and other sites
# Usage:
#   yt.sh URL                    # video
#   yt.sh -a URL                 # audio only (mp3)
#   yt.sh URL START END          # video, cut from START to END seconds
#   yt.sh -a URL START END       # audio, cut from START to END seconds
# Example: ./yt.sh "https://www.youtube.com/watch?v=xyz" $((4*60+52)) $((4*60+55))

# Check for audio flag
audio_only=false
if [[ "$1" == "-a" ]]; then
    audio_only=true
    shift
fi

url="$1"
start_time="$2"
end_time="$3"

if [[ -z "$url" ]]; then
    echo "Usage: yt.sh [-a] URL [START_SECONDS] [END_SECONDS]"
    exit 1
fi

if $audio_only; then
    # Audio only: best audio, convert to mp3
    yt-dlp --progress -i --remote-components ejs:github \
        -f 'ba/b' -x --audio-format mp3 \
        -o "output.mp3" "$url"
    output="output.mp3"
else
    # Video + audio: try combined, then separate streams, then best available
    yt-dlp --progress -i --remote-components ejs:github \
        -f 'b/bv*+ba/b' -k --no-embed-metadata \
        --merge-output-format mp4 \
        -o "combined.mp4" "$url"
    output="combined.mp4"
fi

# Cut if start time provided
if [[ -n "$start_time" ]]; then
    timeargs=("-ss" "$start_time")
    [[ -n "$end_time" ]] && timeargs+=("-to" "$end_time")

    ext="${output##*.}"
    cut_output="${output%.*}_cut.${ext}"

    ffmpeg -y -hide_banner "${timeargs[@]}" -i "$output" "$cut_output"
    mv "$cut_output" "$output"
fi

# Cleanup temp files (yt-dlp may leave some with -k flag)
if ! $audio_only; then
    mv combined.mp4 _combined.mp4 2>/dev/null || true
    temp_files=(combined*.mp4(N) combined*.webm(N) combined*.mkv(N))
    (( ${#temp_files} )) && rm -f -- "${temp_files[@]}"
    mv _combined.mp4 combined.mp4 2>/dev/null || true
fi

echo "Done: $output"
