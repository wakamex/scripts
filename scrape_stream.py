# %%
from typing import Union

import requests
import subprocess
import datetime
import time
import dotenv
import dateutil.parser
import pandas as pd

# %%
denv = dotenv.dotenv_values(".env")

STREAM_URL: str = str(denv["STREAM_URL"])


# %%
def record_track(stream_url, output_filename):
    command = ["ffmpeg", "-i", stream_url, "-vn", "-acodec", "copy", output_filename]
    return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


# %%
def get_currently_playing(channel: Union[str, int], channels):
    if isinstance(channel, str):
        channel = int(channels[channels["key"] == channel].iloc[0]["id"])
    channel_id: int = channel

    url = "https://api.audioaddict.com/v1/di/currently_playing"
    response = requests.get(url)

    if response.status_code != 200:
        print(f"Unable to fetch currently playing track info: {response.status_code}")
        return None

    currently_playing_stations = response.json()

    return next(
        (cp for cp in currently_playing_stations if cp["channel_id"] == channel_id),
        None,
    )


# %%
def get_channels():
    url = "https://api.audioaddict.com/v1/di/channels"
    response = requests.get(url)

    if response.status_code != 200:
        print(f"Unable to fetch channels: {response.status_code}")
        return None

    records = [{"id": item["id"], "key": item["key"], "name": item["name"]} for item in response.json()]
    return pd.DataFrame.from_records(records)
    # display(channels.style.hide(axis=0))
    # id	key         name
    # 1	    trance	    Trance
    # 2	    vocaltrance	Vocal Trance


# %%
def main():
    channels = get_channels()
    print("Starting stream ripper...")
    last_song = None
    p = None
    while True:
        try:
            current_track = get_currently_playing("hardstyle", channels)
            if current_track is not None:
                name = f"{current_track['track']['display_artist']} - {current_track['track']['display_title']}"
                print(f"{current_track['channel_key']}: {name}")
                if current_track["track"]["display_title"] != last_song:
                    print(f"New track detected: {name}")
                    if p:
                        print("Stopping current recording...")
                        p.terminate()
                    # timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
                    # output_filename = f"{name}_{timestamp}.mp3"
                    output_filename = f"/data/mp3s/{name}.mp3"
                    print(f"Starting new recording: {output_filename}")
                    p = record_track(STREAM_URL, output_filename)
                    last_song = current_track["track"]["display_title"]

                    # parse start_time into a datetime object
                    start_time = dateutil.parser.parse(current_track["track"]["start_time"])
                    # calculate the difference between now and the start_time
                    time_passed = (datetime.datetime.now(datetime.timezone.utc) - start_time).total_seconds()
                    # subtract the time_passed from the duration to get the remaining sleep duration
                    sleep_duration = max(0, current_track["track"]["duration"] - time_passed)
                    print(f"Sleeping for {sleep_duration} seconds...")
                    time.sleep(sleep_duration)  # sleep for the remaining duration of the track
                else:
                    print(f"Still playing: {current_track['track']['display_title']}")
        except Exception as e:
            print(f"Error: {e}")
        time.sleep(1)  # wait 1 second before checking again, to handle errors


if __name__ == "__main__":
    main()
main()

# %%

# %%
channels = get_channels()
current = get_currently_playing(60, channels)
print(f"{current=}")
current = get_currently_playing("hardstyle", channels)
print(f"{current=}")

schema = {
    "channel_id": 60,
    "channel_key": "hardstyle",
    "track": {
        "id": 3046583,
        "display_artist": "DJ Inzane",
        "display_title": "Hardstyle Classics #1",
        "start_time": "2023-05-13T22:38:10-04:00",
        "duration": 6569.0,
    },
}
