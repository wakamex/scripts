#!/bin/zsh

# Set the default port to 8547 or use the provided argument
port="${1:-8547}"

# Send a request to the Ethereum node to get the latest block information
curl -s -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","method":"eth_getBlockByNumber","params":["latest",false],"id":"0"}' http://localhost:$port |

    # Extract the block number and timestamp from the JSON response
    jq -r '.result | (.number, .timestamp)' |

    # Read the extracted block number and timestamp
    {
        read -r number
        read -r timestamp

        # Convert and print the block number with thousands separator
        printf "Block     : %'d\n" $((16#${number:2}))

        # Convert and print the timestamp in human-readable format
        printf "Timestamp : "
        printf "%d\n" $((16#${timestamp:2})) | xargs -I % date -u -d @%

        # Calculate the time difference in seconds between the current time and the block timestamp
        time_diff=$(($(date +%s) - $((16#${timestamp:2}))))

        # Print the time difference in a human-readable format
        hours=$((time_diff / 3600))
        minutes=$(((time_diff % 3600) / 60))
        seconds=$((time_diff % 60))

        printf "Age       : %dh:%dm:%ds\n" $hours $minutes $seconds
    }
