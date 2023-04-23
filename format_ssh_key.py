import sys
import json
from datetime import datetime, timedelta, timezone


def format_ssh_key(file_path, email, expire_duration=3600):
    with open(file_path, "r") as f:
        key_parts = f.read().strip().split()

    key_protocol = key_parts[0]
    key_blob = key_parts[1]

    expire_on = datetime.now(timezone.utc) + timedelta(seconds=expire_duration)
    expire_on_iso = expire_on.strftime("%Y-%m-%dT%H:%M:%S+0000")

    metadata = {"userName": email, "expireOn": expire_on_iso}

    return f"{key_protocol} {key_blob} google-ssh {json.dumps(metadata)}"


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python format_ssh_key.py <key_file> <email> <expire_duration>")
        sys.exit(1)

    key_file = sys.argv[1]
    email = sys.argv[2]
    expire_duration = int(sys.argv[3])

    formatted_key = format_ssh_key(key_file, email, expire_duration)
    print(formatted_key)
