#!/usr/bin/env bash
set -euo pipefail

readonly YT_DLP_VERSION="2026.8.19"

usage() {
    cat <<'EOF'
Usage: yt.sh [-a] [--yt-dlp-order] [AUTH_OPTIONS] [-o OUTPUT] URL [START_SECONDS [END_SECONDS]]

  -a, --audio-only  Download audio and convert it to MP3.
  --yt-dlp-order     Use yt-dlp's built-in video ordering instead of the
                     default codec-adjusted bitrate selection up to 1080p.
  --firefox-ssh HOST Retry YouTube sign-in blocks with Firefox cookies from
                     HOST. Also requires --firefox-profile.
  --firefox-profile PATH
                     Firefox profile path on HOST. Also requires --firefox-ssh.
                     The YT_FIREFOX_SSH_HOST and YT_FIREFOX_PROFILE environment
                     variables provide the same opt-in configuration.
  --cookies PATH     Use an existing Netscape-format cookie file. This cannot
                     be combined with the Firefox SSH options.
  -o, --output PATH Publish to PATH instead of output.mp3 or combined.mp4.
  -h, --help        Show this help.

Examples:
  yt.sh -a -o interview.mp3 'https://www.youtube.com/watch?v=VIDEO_ID'
  yt.sh -a --firefox-ssh windows --firefox-profile 'C:/Users/Name/AppData/Roaming/Mozilla/Firefox/Profiles/PROFILE' 'https://www.youtube.com/watch?v=VIDEO_ID'
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
firefox_ssh=${YT_FIREFOX_SSH_HOST:-}
firefox_profile=${YT_FIREFOX_PROFILE:-}
supplied_cookie_file=""

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
        --firefox-ssh)
            (( $# >= 2 )) || fail "$1 requires a host"
            firefox_ssh=$2
            shift 2
            ;;
        --firefox-profile)
            (( $# >= 2 )) || fail "$1 requires a path"
            firefox_profile=$2
            shift 2
            ;;
        --cookies)
            (( $# >= 2 )) || fail "$1 requires a path"
            supplied_cookie_file=$2
            shift 2
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

if [[ -n "$firefox_ssh" || -n "$firefox_profile" ]]; then
    [[ -n "$firefox_ssh" && -n "$firefox_profile" ]] \
        || fail "--firefox-ssh and --firefox-profile must be used together"
fi
if [[ -n "$supplied_cookie_file" ]]; then
    [[ -z "$firefox_ssh" && -z "$firefox_profile" ]] \
        || fail "--cookies cannot be combined with the Firefox SSH options"
    [[ -f "$supplied_cookie_file" && -r "$supplied_cookie_file" ]] \
        || fail "cookie file is not a readable regular file: $supplied_cookie_file"
    supplied_cookie_file=$(cd -- "$(dirname -- "$supplied_cookie_file")" && pwd -P)/$(basename -- "$supplied_cookie_file")
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
if ! $audio_only && $quality_score && [[ -z "$supplied_cookie_file" ]]; then
    require_command python3
fi
if [[ -n "$firefox_ssh" ]]; then
    require_command ssh
    require_command deno
fi

script_directory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
output_name=$(basename -- "$output")
output_parent=$(dirname -- "$output")
[[ "$output_name" != "." && "$output_name" != "/" ]] || fail "output path is invalid"
mkdir -p -- "$output_parent"
output_parent=$(cd -- "$output_parent" && pwd -P)
destination="$output_parent/$output_name"
stage_directory=$(mktemp -d "$output_parent/.yt-download.XXXXXX")
cookie_directory=""
cookie_file=""

cleanup() {
    rm -rf -- "$stage_directory"
    if [[ -n "$cookie_directory" && -d "$cookie_directory" ]]; then
        if [[ -f "$cookie_directory/cookies.txt" ]]; then
            chmod 0600 "$cookie_directory/cookies.txt"
            shred -u -- "$cookie_directory/cookies.txt" 2>/dev/null \
                || rm -f -- "$cookie_directory/cookies.txt"
        fi
        rmdir -- "$cookie_directory" 2>/dev/null || true
    fi
}
trap cleanup EXIT

template="$stage_directory/download.%(ext)s"
yt_dlp=(
    uvx --no-config --from "yt-dlp==$YT_DLP_VERSION" yt-dlp
    --ignore-config
    --no-playlist
    --progress
    --output "$template"
)

if command -v deno >/dev/null 2>&1; then
    yt_dlp+=(
        --no-js-runtimes
        --js-runtimes "deno:$(command -v deno)"
        --remote-components ejs:github
    )
fi

download_error="$stage_directory/yt-dlp.stderr"

run_yt_dlp() {
    local cookie_file=$1
    shift
    local -a command=("${yt_dlp[@]}")
    if [[ -n "$cookie_file" ]]; then
        command+=(--cookies "$cookie_file")
    fi
    command+=("$@")
    "${command[@]}" 2> >(tee "$download_error" >&2)
}

export_firefox_cookies() {
    local runtime_parent=${XDG_RUNTIME_DIR:-/dev/shm}
    [[ -d "$runtime_parent" && -w "$runtime_parent" ]] \
        || fail "no private writable runtime directory is available for Firefox cookies"
    cookie_directory=$(mktemp -d "$runtime_parent/yt-cookies.XXXXXX")
    chmod 0700 "$cookie_directory"
    cookie_file="$cookie_directory/cookies.txt"
    umask 077
    ssh "$firefox_ssh" "py -3 - '$firefox_profile'" \
        < "$script_directory/firefox_youtube_cookies.py" > "$cookie_file"
    chmod 0600 "$cookie_file"
}

download_with_retry() {
    local status
    if run_yt_dlp "$supplied_cookie_file" "$@"; then
        return 0
    else
        status=$?
    fi
    if [[ -n "$supplied_cookie_file" || -z "$firefox_ssh" ]] \
        || ! grep -Fq "Sign in to confirm" "$download_error"; then
        return "$status"
    fi
    printf 'YouTube requested sign-in; retrying with the configured Firefox session.\n' >&2
    export_firefox_cookies
    run_yt_dlp "$cookie_file" "$@"
}

if $audio_only; then
    # Some YouTube livestream archives expose a higher-ranked Opus track that is
    # silent while the AAC/M4A track contains the program audio. Prefer M4A and
    # retain the generic best-audio fallback for videos without one.
    download_with_retry --format 'ba[ext=m4a]/ba/b' --extract-audio --audio-format mp3 "$url"
    downloaded="$stage_directory/download.mp3"
else
    format_selector='bv*+ba/b'
    if $quality_score && [[ -z "$supplied_cookie_file" ]]; then
        if ! format_selector=$(python3 "$script_directory/yt_quality.py" --format-only "$url"); then
            [[ -n "$firefox_ssh" ]] || exit 1
            printf 'Anonymous quality selection failed; using yt-dlp ordering for the authenticated retry.\n' >&2
            format_selector='bv*+ba/b'
        fi
    fi
    download_with_retry --format "$format_selector" --merge-output-format mp4 --remux-video mp4 "$url"
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
