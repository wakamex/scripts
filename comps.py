# %%
import pandas as pd

from stepwise import stepwise_selection

# %%
comps = pd.read_csv("~/defi/houses.csv")
for col_with_comma in ["Lot","Sold"]:
    if comps[col_with_comma].dtype == "object":
        comps[col_with_comma] = comps[col_with_comma].str.strip().str.replace(",","")
        comps[col_with_comma] = comps[col_with_comma].apply(pd.to_numeric, errors='coerce')
sold = comps.loc[comps.Sold.notna(),:]
sold

# %%
exclude_cols = ["House"]
# result, model = stepwise_selection(data=sold[[c for c in sold.columns if c not in exclude_cols]], dep="Sold",threshold_in=0.0001, threshold_out=0.0001)
result, model = stepwise_selection(
    data=sold,
    dep="Sold",
    threshold_in=0.0001,
    threshold_out=0.0001,
    initial_list=["Reno"],
)
result_df = pd.DataFrame({"feature":['Intercept']+result, "coef":model.params}).set_index("feature")
display(result_df)
print(f"model rsq: {model.rsquared_adj}")

# %%
comps["Exp"] = model.predict(comps)
comps["Diff"] = comps["Sold"] - comps["Exp"]

# %%
comps

# %%
comps.to_csv("~/defi/comps.csv", index=False)

# %%