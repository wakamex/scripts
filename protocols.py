# %%
import csv
import os
import pathlib
import time

import pandas as pd
import requests

# %%
api_endpoint = "https://api.llama.fi/lite/protocols2?b=2"
data = requests.get(api_endpoint).json()
protocols = data["protocols"]
eth_protocols = list(filter(lambda x: "Ethereum" in x["chains"], protocols))

# %%
results = []
for pnum, protocol in enumerate(eth_protocols):
    name = protocol["name"]
    id_ = protocol["parentProtocol"].replace("#", "/") if "parentProtocol" in protocol else protocol["defillamaId"]
    endpoint = f"https://defillama-datasets.llama.fi/dev-metrics/github/{id_}.json"
    response = requests.get(endpoint)

    data = None
    try:
        data = response.json()
    except:
        print(f"failed to get json for {name}, response: {response}")
        print("endpoint = ", endpoint)
        continue  # skip to next protocol

    value = timestamp = monthly_devs = None
    try:
        monthly_devs = data["report"]["monthly_devs"]
        latest = monthly_devs[-1]
        timestamp = latest["k"]
        value = latest["cc"]
        print(f"    {pnum+1:4}/{len(eth_protocols):4}: {name} = {value}")
    except:
        print(f"not found: {name}")

    # add new record to results
    results.append((name, id_, monthly_devs))
    time.sleep(0.1)

# %%
pd.DataFrame(
    [
        (name, id_, record["k"], record["cc"])
        for name, id_, monthly_devs in results
        for record in monthly_devs
    ], 
    columns=["Name", "ID", "Month", "Developers"]  # type: ignore
).to_parquet("protocols.parquet", index=False)

# %%