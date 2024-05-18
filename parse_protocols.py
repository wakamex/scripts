# %%
import pandas as pd

# df = pd.read_csv("results.csv")
df = pd.read_parquet("protocols.parquet")

# %%
dates = sorted(df.Month.unique())

# %%
latest = df.loc[df.Month == dates[-1]]
print(f"{len(latest)=}")

previous = df.loc[df.Month == dates[-2]]
print(f"{len(previous)=}")

# %%
recent = df.loc[df.Month == dates[-2]].sort_values(by="Developers", ascending=False).copy()
recent.reset_index(drop=True, inplace=True)
recent.to_csv("recent.csv", index=False)
recent.to_csv("protocols_recent.csv", index=False)

# %%
# de-duplicate, taking the first entry in "ID"
flattened = recent[["Name", "ID", "Developers"]].drop_duplicates(subset='ID', keep='first').reset_index(drop=True)
flattened.index = flattened.index+1
flattened.rename(columns={'Developers': 'Commits'}, inplace=True)
flattened.head(10).style.hide(axis=1,subset="ID")

# %%