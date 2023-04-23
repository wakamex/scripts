#!/bin/zsh

# Define the list of URLs
# scraped from https://eth-clients.github.io/checkpoint-sync-endpoints/#goerli on 2023-4-23
goerli_urls=(
    "sync-goerli.beaconcha.in"
    "goerli.beaconstate.info"
    "goerli-sync.invis.tools"
    "checkpoint-sync.goerli.ethpandaops.io"
    "prater-checkpoint-sync.stakely.io"
    "goerli.beaconstate.ethstaker.cc"
    "beaconstate-goerli.chainsafe.io"
    "prater.checkpoint.sigp.io"
)
mainnet_urls=(
    "checkpointz.pietjepuk.net"
    "mainnet-checkpoint-sync.attestant.io"
    "sync.invis.tools"
    "mainnet-checkpoint-sync.stakely.io"
    "beaconstate.ethstaker.cc"
    "beaconstate.info"
    "beaconstate-mainnet.chainsafe.io"
    "mainnet.checkpoint.sigp.io"
    "sync-mainnet.beaconcha.in"
)

# Check the input argument and set the URLs accordingly, maintaining the list type
if [[ "$1" == "mainnet" ]]; then
    urls=("${mainnet_urls[@]}")
else
    urls=("${goerli_urls[@]}")
fi

# Initialize the fastest URL and the lowest average time
fastest_url=""
lowest_avg_time=0

# Iterate over the URLs and ping each of them
for url in $urls; do
    echo "Pinging $url..."

    # Perform the ping and extract the average response time
    avg_time=$(ping -c 10 $url | awk -F '/' '/^rtt/ { print $5 }')

    # Print the average response time
    echo "Average response time: $avg_time ms"

    # Check if the current URL has a faster average time than the current fastest URL
    if [[ -z "$fastest_url" ]] || [[ $(echo "$avg_time < $lowest_avg_time" | bc -l) -eq 1 ]]; then
        fastest_url=$url
        lowest_avg_time=$avg_time
    fi
done

# Print the fastest URL and its average response time
echo "Fastest URL: $fastest_url"
echo "Average response time: $lowest_avg_time ms"
output_file="fastest_${1:-goerli}.txt"
echo "$fastest_url" >$output_file # use $1 or"goerli" if no argument is provided
echo "Saved to $output_file"
