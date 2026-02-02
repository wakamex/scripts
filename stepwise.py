import pandas as pd
import statsmodels.formula.api as smf

def calc(data, dep, kinds, included, included_additional, excluding = None):
    excluding = excluding or []
    _formula = f'{dep} ~ '
    if len(included) == 0:
        return _formula, None, 0
    _formula += ' + '.join([var if kinds[var] in 'biufc' else f'C({var})' for var in included])
    _formula += ' + ' + ' + '.join([i for i in included_additional if i not in excluding]) if len(included_additional) > 0 else ''
    _model = smf.ols(formula=_formula, data=data).fit()
    _rsquared_adj = _model.rsquared_adj
    return _formula, _model, _rsquared_adj

def stepwise_selection(data, dep, additional_cols_to_check=[],
                       initial_list=[], 
                       threshold_in=0.01, 
                       threshold_out = 0.01, 
                       verbose=True):
    included = list(initial_list)
    included_additional = []
    numeric_cols = data._get_numeric_data().columns
    print(f"{numeric_cols=}")
    data.loc[:,numeric_cols] = data.loc[:,numeric_cols].fillna(data.loc[:,numeric_cols].mean())  # fill with mean
    kinds = {col: dtype.kind for col, dtype in data.dtypes.items()}
    while True:
        changed=False
        # numerical forward step
        excluded = list(set(data.columns)-set(included) - {dep})
        new_stats = []
        base_formula, base_model, base_rsquared_adj = calc(data, dep, kinds, included, included_additional)
        for new_column in excluded:
            if data[new_column].notna().all() and (data[new_column].unique().size > 1 or kinds[new_column] in 'biufc'):
                new_formula, new_model, new_rsquared_adj = calc(data, dep, kinds, included + [new_column], included_additional)
                matching_columns = [key for key in new_model.pvalues.keys() if new_column in key]
                new_stats.append({"feature": new_column, "pval": new_model.pvalues[matching_columns[0]], "rsq": new_model.rsquared_adj})
        new_stats = pd.DataFrame(new_stats).set_index("feature")
        best_rsq = new_stats.rsq.max()
        best_feature = new_stats.rsq.idxmax()
        if best_rsq - base_rsquared_adj >= threshold_in:
            included.append(best_feature)
            changed=True
            if verbose:
                print(f"Add {best_feature:30} with rsq-adj increase {best_rsq - base_rsquared_adj:.6} vs. {threshold_in:.6}")
        elif verbose:
            for new_feature in [f for f in new_stats.rsq.index if f not in included]:
                print(f" not adding {new_feature:30} with rsq-adj increase {new_stats.rsq[new_feature]-base_rsquared_adj:.6} vs. {threshold_in:.6}")
        
        # numerical backward step
        if changed:
            base_formula, base_model, base_rsquared_adj = calc(data, dep, kinds, included, included_additional)
        best_drop_feature = None
        best_drop_rsq_change = 0
        for drop_column in included.copy():
            new_formula, new_model, new_rsquared_adj = calc(data, dep, kinds, list(set(included) - {drop_column}), included_additional)
            new_rsq_change = new_rsquared_adj - base_rsquared_adj
            if new_rsq_change >= threshold_out and new_rsq_change > best_drop_rsq_change:
                best_drop_feature = drop_column
                best_drop_rsq_change = new_rsq_change
            elif verbose:
                print(f" not dropping {drop_column:30} with rsq-adj increase {new_rsq_change:.6} vs. {threshold_out:.6}")
        if best_drop_feature is not None:
            included.remove(best_drop_feature)
            changed=True
            if verbose:
                print(f"Drop {best_drop_feature:30} with rsq-adj increase {best_drop_rsq_change:.6} vs. {threshold_out:.6}")
        
        # categorical forward step
        if changed:
            base_formula, base_model, base_rsquared_adj = calc(data, dep, kinds, included, included_additional)
        best_new_feature = None
        best_new_rsq_change = 0
        for new_column in [col for col in additional_cols_to_check if col not in included_additional]:
            new_formula, new_model, new_rsquared_adj = calc(data, dep, kinds, included, included_additional + [new_column])
            new_rsq_change = new_rsquared_adj - base_rsquared_adj
            if new_rsq_change >= threshold_in and new_rsq_change > best_new_rsq_change:
                best_new_feature = new_column
                best_new_rsq_change = new_rsq_change
            elif verbose:
                print(f" not adding {new_column:34} with rsq-adj increase {new_rsq_change:.6} vs. {threshold_in:.6}")
        if best_new_feature is not None:
            included_additional.append(best_new_feature)
            changed=True
            if verbose:
                print(f"Add  {best_new_feature:30} with rsq-adj increase {best_new_rsq_change:.6} vs. {threshold_in:.6}")

        # categorical backward step
        if changed:
            base_formula, base_model, base_rsquared_adj = calc(data, dep, kinds, included, included_additional)
        best_drop_feature = None
        best_drop_rsq_change = 0
        for drop_column in included_additional.copy():
            new_formula, new_model, new_rsquared_adj = calc(data, dep, kinds, included, list(set(included_additional)-{drop_column}))
            new_rsq_change = new_rsquared_adj - base_rsquared_adj
            if new_rsq_change >= threshold_out and new_rsq_change > best_drop_rsq_change:
                best_drop_feature = drop_column
                best_drop_rsq_change = new_rsq_change
            elif verbose:
                print(f" not dropping {drop_column:34} with rsq-adj increase {new_rsq_change:.6} vs. {threshold_out:.6}")
        if best_drop_feature is not None:
            included_additional.remove(best_drop_feature)
            changed=True
            if verbose:
                print(f"Drop {best_drop_feature:30} with rsq-adj increase {best_drop_rsq_change:.6} vs. {threshold_out:.6}")

        if not changed:
            display("resulting model")
            display(base_model.summary())
            break
    return included+included_additional, base_model