#!/usr/bin/env bash
set -euo pipefail

readonly YT_DLP_VERSION="2026.8.19"

usage() {
    cat <<'EOF'
Usage: yt.sh [-a] [--yt-dlp-order] [-o OUTPUT] URL [START_SECONDS [END_SECONDS]]

  -a, --audio-only  Download audio and convert it to MP3.
  --yt-dlp-order     Use yt-dlp's built-in video ordering instead of the
                     default codec-adjusted bitrate selection up to 1080p.
  -o, --output PATH Publish to PATH instead of output.mp3 or combined.mp4.
  -h, --help        Show this help.

Examples:
  yt.sh -a -o interview.mp3 'https://www.youtube.com/watch?v=VIDEO_ID'
  yt.sh 'https://www.youtube.com/watch?v=VIDEO_ID'
  yt.sh --yt-dlp-order 'https://www.youtube.com/watch?v=VIDEO_ID'
  yt.sh -o clip.mp4 'https://www.youtube.com/watch?v=VIDEO_ID' 292 295
EOF
}

fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "$1 is required"
}

audio_only=false
quality_score=true
output=""

while (( $# )); do
    case "$1" in
        -a|--audio-only)
            audio_only=true
            shift
            ;;
        -q|--quality-score)
            quality_score=true
            shift
            ;;
        --yt-dlp-order)
            quality_score=false
            shift
            ;;
        -o|--output)
            (( $# >= 2 )) || fail "$1 requires a path"
            output=$2
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            break
            ;;
        -*)
            fail "unknown option: $1"
            ;;
        *)
            break
            ;;
    esac
done

(( $# >= 1 && $# <= 3 )) || {
    usage >&2
    exit 1
}

url=$1
start_time=${2:-}
end_time=${3:-}

if [[ -n "$end_time" && -z "$start_time" ]]; then
    fail "END_SECONDS requires START_SECONDS"
fi

if $audio_only; then
    output=${output:-output.mp3}
    [[ "${output,,}" == *.mp3 ]] || fail "audio output must end in .mp3"
    expected_stream=audio
else
    output=${output:-combined.mp4}
    [[ "${output,,}" == *.mp4 ]] || fail "video output must end in .mp4"
    expected_stream=video
fi

require_command uvx
require_command ffmpeg
require_command ffprobe
if ! $audio_only && $quality_score; then
    require_command python3
fi

output_name=$(basename -- "$output")
output_parent=$(dirname -- "$output")
[[ "$output_name" != "." && "$output_name" != "/" ]] || fail "output path is invalid"
mkdir -p -- "$output_parent"
output_parent=$(cd -- "$output_parent" && pwd -P)
destination="$output_parent/$output_name"
stage_directory=$(mktemp -d "$output_parent/.yt-download.XXXXXX")

cleanup() {
    rm -rf -- "$stage_directory"
}
trap cleanup EXIT

template="$stage_directory/download.%(ext)s"
yt_dlp=(
    uvx --no-config --from "yt-dlp==$YT_DLP_VERSION" yt-dlp
    --no-playlist
    --progress
    --output "$template"
)

if $audio_only; then
    "${yt_dlp[@]}" --format 'ba/b' --extract-audio --audio-format mp3 "$url"
    downloaded="$stage_directory/download.mp3"
else
    format_selector='bv*+ba/b'
    if $quality_score; then
        script_directory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
        format_selector=$(python3 "$script_directory/yt_quality.py" --format-only "$url")
    fi
    "${yt_dlp[@]}" --format "$format_selector" --merge-output-format mp4 --remux-video mp4 "$url"
    downloaded="$stage_directory/download.mp4"
fi

[[ -f "$downloaded" ]] || fail "yt-dlp did not produce the expected $expected_stream file"

if [[ -n "$start_time" ]]; then
    clipped="$stage_directory/clipped.${downloaded##*.}"
    ffmpeg_args=(-y -hide_banner -loglevel error -ss "$start_time")
    [[ -z "$end_time" ]] || ffmpeg_args+=(-to "$end_time")
    ffmpeg "${ffmpeg_args[@]}" -i "$downloaded" "$clipped"
    downloaded=$clipped
fi

duration=$(ffprobe -v error -show_entries format=duration -of default=nk=1:nw=1 "$downloaded")
awk -v duration="$duration" 'BEGIN { exit !(duration > 0) }' \
    || fail "download has no measurable duration"

stream=$(
    ffprobe -v error -select_streams "${expected_stream:0:1}:0" \
        -show_entries stream=codec_type -of default=nk=1:nw=1 "$downloaded"
)
[[ "$stream" == "$expected_stream" ]] || fail "download has no $expected_stream stream"

chmod 0600 "$downloaded"
mv -f -- "$downloaded" "$destination"
trap - EXIT
cleanup

printf 'Done: %s\n' "$destination"
