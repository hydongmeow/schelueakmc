import pandas as pd
from scipy import stats
import pandas as pd
from scipy.stats import tukey_hsd

# Significance test of the scheduler reconstruction results
df = pd.read_csv('Book1.csv')

df['W_group'] = pd.cut(df['W_true'], bins=[0, 30, 60],
                        labels=['Low (≤30)', 'High (>30)'])

grouped = df.groupby('W_group')[['mse', 'sched_err', 'E', 'W_delta']].agg(['mean'])
print(grouped)

grouped = df.groupby('W_group')[['mse', 'sched_err', 'E', 'W_delta']].agg(['std'])
print(grouped)

groups = [group['mse'].values for name, group in df.groupby('W_group')]

h_stat, p_value = stats.kruskal(*groups)
print(f"Kruskal-Wallis H: {h_stat:.4f}, p-value: {p_value:.6f}")

f_stat, p_value = stats.f_oneway(*groups)
print(f"ANOVA F-statistic: {f_stat:.4f}")
print(f"p-value: {p_value:.6f}")
if p_value < 0.05:
    print("✓ Significant difference between groups")
else:
    print("✗ No significant difference")

result = tukey_hsd(*groups)
print(result)