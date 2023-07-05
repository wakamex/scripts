# %%
import json
import pandas as pd
import time

import __main__ as main_to_check_for_interactive_mode_only

# make it run in both interactive and non-interactive mode
if hasattr(main_to_check_for_interactive_mode_only, '__file__'):
    from IPython.display import display
    try:
        assert display
    except AssertionError as exc:
        raise ValueError("display failed to import") from exc
    INTERACTIVE = True
else:
    display = print  # If not in interactive mode, display is equivalent to print
    INTERACTIVE = False

# %%
with open('provider_data.json', mode='r', encoding='utf-8') as file:
    json_data = json.load(file)
# flatten stats attribute
for provider in json_data:
    for stat in json_data[provider]['stats']:
        json_data[provider][stat] = json_data[provider]['stats'][stat]
    del json_data[provider]['stats']
df = pd.DataFrame(json_data).T
display(df.head(2))

# %%
calls = [
    "eth_call",
    "eth_getBalance",
    "eth_getBlockByNumber",
    "eth_getCode",
    "eth_getStorageAt",
]
weight_by_request_rate = [
    1,  # requestRate 1
    1,  # requestRate 4
    1,  # requestRate 16
    1,  # requestRate 64
    1,  # requestRate 256
    1,  # requestRate 512
]
weight_by_metric = {
    'successRate': 1,
    'throughput': 1,
    'latencyMean': 0.5,
    'latencyP50': 0.5/4,
    'latencyP90': 0.5/4,
    'latencyP95': 0.5/4,
    'latencyP99': 0.5/4,
}
weight_by_metric_0_throughput = {
    'successRate': 1,
    'throughput': 0,
    'latencyMean': 0.5,
    'latencyP50': 0.5/4,
    'latencyP90': 0.5/4,
    'latencyP95': 0.5/4,
    'latencyP99': 0.5/4,
}
# %%
start_time = time.time()
subtotals_dict = {}
call_df, call_df_0_throughput = pd.DataFrame(), pd.DataFrame()
for call in calls:
    subtotals = pd.DataFrame()
    for provider in df.id.values:
        ec = df.loc[df.id==provider,call].iloc[0]
        ecdf = pd.DataFrame(ec)

        # add subtotal row
        idx = ecdf.index
        data_cols = [c for c in ecdf.columns if c != 'requestRate']
        for col in data_cols:
            # element-wise multiply ecdf[col] with weight_by_request_rate then sum it up
            ecdf.loc[provider, col] = ecdf.loc[idx,col].multiply(weight_by_request_rate).sum()/sum(weight_by_request_rate)
        subtotals = pd.merge(subtotals, ecdf.loc[provider, data_cols].T, how='outer', left_index=True, right_index=True)

    idx = subtotals.index
    for col in subtotals.columns:
        subtotals.loc['aggregate', col] =  subtotals.loc[idx,col].multiply(list(weight_by_metric.values())).sum()/sum(list(weight_by_metric.values()))
        subtotals.loc['aggregate_0_throughput', col] =  subtotals.loc[idx,col].multiply(list(weight_by_metric_0_throughput.values())).sum()/sum(list(weight_by_metric_0_throughput.values()))
    subtotals_dict[call] = subtotals
    call_df = pd.merge(call_df, subtotals.loc['aggregate',:], how='outer', left_index=True, right_index=True)
    call_df.rename(columns={'aggregate': call}, inplace=True)
    call_df_0_throughput = pd.merge(call_df_0_throughput, subtotals.loc['aggregate_0_throughput',:], how='outer', left_index=True, right_index=True)
    call_df_0_throughput.rename(columns={'aggregate_0_throughput': call}, inplace=True)
call_df.loc[:,'average'] = call_df.mean(axis=1)
call_df = call_df.sort_values(by='average', ascending=False)
call_df_0_throughput.loc[:,'average'] = call_df_0_throughput.mean(axis=1)
call_df_0_throughput = call_df_0_throughput.sort_values(by='average', ascending=False)

print(f"--- {time.time() - start_time} seconds ---")

# %%
for call in calls:
    print(f"{call}:")
    display(subtotals_dict[call])

print("all calls:")
display(call_df)

print("all calls except throughput:")
display(call_df_0_throughput)

# %% compare throughput
call = "eth_call"
metric = "throughput"
throughput_df = pd.DataFrame()
for provider in df.id.values:
    ec = df.loc[df.id==provider,call].iloc[0]
    ecdf = pd.DataFrame(ec)
    new_series = ecdf.loc[len(ecdf)-1,metric]
    throughput_df.loc[provider,metric] = new_series
throughput_df = throughput_df.sort_values(by=metric, ascending=False)
display(
    throughput_df.style.format(
        {
            'throughput': '{:.2f}',
        }
    )
    )

# %%
