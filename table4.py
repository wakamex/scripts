# %%
# Import necessary libraries
import pandas as pd
import numpy as np

# Load the data
df = pd.read_excel("table4.ods", engine="odf")

# Standardize column names: replace spaces with underscores and convert to lowercase
df.columns = df.columns.str.replace(" ", "_").str.lower()

# Convert the 'year' and 'month' columns to a datetime 'date' column
df["date"] = pd.to_datetime(df[["year", "month"]].apply(lambda x: "-".join(x.astype(str)), axis=1), format="%Y-%B")

# Convert columns to appropriate types
convert_cols = ["age-standardised_mortality_rate_/_100,000_person-years", "count_of_deaths"]
convert_cols += ["lower_confidence_limit", "upper_confidence_limit"]
for col in convert_cols:
    df[col] = pd.to_numeric(df[col], errors="coerce")

# %%
# pick out stuff we care about
first_dose = ["First dose, less than 21 days ago", "First dose, at least 21 days ago"]
second_dose = ["Second dose, less than 21 days ago", "Second dose, at least 21 days ago"]
vac_status = ["Unvaccinated"] + first_dose + second_dose
cause_of_death = "All causes"  # "All causes" or "Deaths involving COVID-19"
# cause_of_death = "Deaths involving COVID-19"  # "All causes" or "Deaths involving COVID-19"
metric = "age-standardised_mortality_rate_/_100,000_person-years"
age_group = "80-89"
sex = "Female"
breakeven_days = pd.DataFrame(index=["80-89", "70-79"], data={"Male": [61], "Female": [61]})

# Filter data for stuff we care about
query = (
    # 'noted_as_unreliable != "u"'
    f'cause_of_death == "{cause_of_death}" and age_group == "{age_group}" and sex == "{sex}"'
    " and vaccination_status in " + '["' + '","'.join(vac_status).strip() + '"]'
)
filtered_data = df.query(query)
display(filtered_data.vaccination_status.value_counts())

pivot_metric = filtered_data.pivot_table(index="date", columns="vaccination_status", values=metric)
display(pivot_metric)

# add column for difference in days
pivot_metric["diff_days"] = pivot_metric.index.to_series().diff().shift(-1)

# %%
# make assumptions about when we get first and second doses
doses = {"First": 0, "Second": 1}  # months in which we get the first and second doses

# assign default value
pivot_metric["mortality"] = pivot_metric["Second dose, at least 21 days ago"]
# calculate weighted averages using list comprehensions and np.average
for dose, idx in doses.items():
    month = pivot_metric.index.to_series().iloc[idx]
    weights = [21, pivot_metric.loc[month, "diff_days"].days - 21]
    values = pivot_metric.loc[month, [f"{dose} dose, less than 21 days ago", f"{dose} dose, at least 21 days ago"]]
    pivot_metric.loc[month, "mortality"] = np.average(values, weights=weights)

# update mortality column for previous months
if doses["First"] > 0:
    pivot_metric.loc[:doses["First"], "mortality"] = pivot_metric.loc[:doses["First"], "Unvaccinated"]
display(pivot_metric)

last_month = pivot_metric.index.to_series().iloc[-1]

# %%
def calculate_survival_rate(df, columns):
    year_fraction = df["diff_days"].dt.days / 365.25
    for col in columns:
        df[col + "_survival_rate"] = 1 - df[col] / 100_000 * year_fraction
    return df

def calculate_cumulative_survival(df, columns):
    for col in columns:
        df[col + "_cumulative_survival"] = df[col + "_survival_rate"].cumprod()
    return df

def find_crossing_spot(df, compare_cols):
    skip_first_months = 1
    survival_diffs = df[compare_cols].diff(axis=1).iloc[skip_first_months:, -1]
    crossing_spot = np.where(np.sign(survival_diffs).diff().shift(-1) != 0)[0][0] + skip_first_months
    interp_on = survival_diffs.iloc[crossing_spot-1:crossing_spot+1].to_frame()
    interp_on['date_numeric'] = interp_on.index.to_series()
    # display(interp_on)
    # interp_on.date = pd.to_datetime(interp_on.date)
    interp_on.loc[len(interp_on),"mortality_cumulative_survival"] = 0
    interp_on = interp_on.sort_values(by="mortality_cumulative_survival")
    interp_on = interp_on.interpolate(method="linear").sort_values(by="date_numeric")
    # display(interp_on)
    crossing_date = pd.to_datetime(interp_on["date_numeric"].iloc[1])
    
    label = f"Break even: {(crossing_date - survival_diffs.index[0]).days} days"
    return crossing_date, label

columns = ["Unvaccinated", "mortality"]

pivot_metric = calculate_survival_rate(pivot_metric, columns)
pivot_metric = pivot_metric.loc[:last_month]
pivot_metric = calculate_cumulative_survival(pivot_metric, columns)

compare_cols = [col + "_cumulative_survival" for col in columns]
crossing_date, label = find_crossing_spot(pivot_metric, compare_cols)
pivot_metric.loc[crossing_date, :] = np.nan
pivot_metric = pivot_metric.sort_values(by="date")
pivot_metric = pivot_metric.interpolate(method="linear")
print(f"{pivot_metric=}")

ax = pivot_metric.plot(y=compare_cols, title=f"{sex} {age_group} Survival rate")
ax.axvline(x=crossing_date, color="red", linestyle="--", label=label)
# ax.axvline(x=pd.to_datetime("2021-11-01"), color="black", linestyle="--", linewidth=1)
# ax.axvline(x=pd.to_datetime("2021-12-01"), color="black", linestyle="--", linewidth=1)
ax.legend();

# %%
