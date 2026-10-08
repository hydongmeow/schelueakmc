"""
graphs.py
=========
Significance tests on the scheduler-reconstruction results produced by
assessment.py (results/assessment_summary.csv).

Question answered: does the reconstruction error depend on the length of the
true vulnerability window?  Samples are split at the median w_true into
'short' and 'long' windows; the per-sample errors are compared with
Kruskal-Wallis (non-parametric) and one-way ANOVA, and the monotone
association is quantified with Spearman's rho.

Usage:  python graphs.py --summary results/assessment_summary.csv
"""
import argparse
import pandas as pd
from scipy import stats

ap = argparse.ArgumentParser()
ap.add_argument("--summary", default="results/assessment_summary.csv")
args = ap.parse_args()

df = pd.read_csv(args.summary)
metrics = ["mse", "eps_P", "sched_err", "iou", "E_total", "W_delta"]

med = df["w_true"].median()
df["w_group"] = pd.cut(df["w_true"], bins=[-1, med, df["w_true"].max() + 1],
                       labels=[f"short (<= {med:g} ms)", f"long (> {med:g} ms)"])

print("Group means:")
print(df.groupby("w_group", observed=True)[metrics].mean().round(4))
print("\nGroup std:")
print(df.groupby("w_group", observed=True)[metrics].std().round(4))
print("\nGroup sizes:", df["w_group"].value_counts().to_dict())

for mcol in metrics:
    groups = [g[mcol].values for _, g in df.groupby("w_group", observed=True)]
    h, p_kw = stats.kruskal(*groups)
    f, p_an = stats.f_oneway(*groups)
    rho, p_rho = stats.spearmanr(df["w_true"], df[mcol])
    print(f"\n{mcol:>10}: Kruskal-Wallis H={h:.3f} p={p_kw:.4f} | "
          f"ANOVA F={f:.3f} p={p_an:.4f} | Spearman rho(w_true, {mcol})={rho:.3f} p={p_rho:.4f}"
          + ("   <- significant" if min(p_kw, p_an) < 0.05 else ""))
