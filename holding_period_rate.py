# %%
starting_price = 0.9
print(f"with starting price {starting_price}")
rate_total = (1-starting_price)/starting_price
print(f"rate_total is {rate_total:.2%}")

term_elapsed = 0.5
intermediate_price = 0.975
print(f"when intermediate price is {intermediate_price}")
holding_period_rate_1 = (intermediate_price-starting_price)/starting_price
print(f"holding period rate 1 is {holding_period_rate_1:.2%}")
annualized_rate_1 = holding_period_rate_1*(1/term_elapsed)
print(f"annualized rate 1 is {annualized_rate_1:.2%}")

# %% solve for rate_2
term_remaining = 1 - term_elapsed

print("=== using price ===")
holding_period_rate_2 = (1-intermediate_price)/intermediate_price
print(f" holding period rate 2 is {holding_period_rate_2:.2%}")
annualized_rate_2 = holding_period_rate_2*(1/term_remaining)
print(f" annualized rate 2 is {annualized_rate_2:.2%}")

print("=== using holding period rates ===")
# rate_total = ( 1 + holding_period_rate_1 ) * ( 1+ holding_period_rate_2 ) - 1
holding_period_rate_2 = ( 1 + rate_total ) / ( 1 + holding_period_rate_1 ) - 1
print(f" holding period rate 2 is {holding_period_rate_2:.2%}")
annualized_rate_2 = holding_period_rate_2*(1/term_remaining)
print(f" annualized rate 2 is {annualized_rate_2:.2%}")

print("=== using annualized rates ===")
# rate_total = ( 1 + annualized_rate_1 * term_elapsed ) * ( 1 + annualized_rate_2 * term_remaining ) - 1
annualized_rate_2 = ( ( 1 + rate_total ) / ( 1 + annualized_rate_1 * term_elapsed ) - 1 ) / term_remaining
holding_period_rate_2 = annualized_rate_2 * term_remaining
print(f" holding period rate 2 is {holding_period_rate_2:.2%}")
print(f" annualized rate 2 is {annualized_rate_2:.2%}")
