"""
baseline.py
===========
Baseline evaluation for the scheduler-inference (period-recovery) task.

TASK
----
    INPUT  : the attacker/observer task's own execution intervals
             (columns `start_ts`, `end_ts`). Every sequence is truncated/padded
             to SEQ_LEN rows (256 or 512) and flattened  ->  vector SEQ_LEN * 2.
    OUTPUT : the 3 critical-task periods (identifiers 1,2,3) from task_configs.json,
             e.g. variation_20 -> [6, 18, 36].

    So each sample is  x in R^(SEQ_LEN*2)  ->  y in R^3.

OPTIONAL KNOWLEDGE EMBEDDING  (--use-knowledge)
-----------------------------------------------
Mirrors the extra modalities exposed by MultiModalRTOSDataset.__getitem__:
    * modality_2  (scheduler type)              -> one-hot over SCHEDULER_VOCAB (10)
    * modality_3  (observer + critical features)-> [wcet,id,period] + [id,wcet]*3 (9)
When enabled, this 19-dim "knowledge" block is concatenated to the flattened
time series and standardized alongside it.

  NB on the DNN: applying the first nn.Linear to the one-hot scheduler block is
  mathematically equivalent to a learned nn.Embedding lookup, so the DNN option
  realises a genuine *learned* scheduler embedding rather than a fixed encoding.
  No target leakage: modality_3 holds task wcets / observer period, never the
  critical-task periods being predicted.

APPLICABILITY OF THE 7 REQUESTED METHODS  (unchanged)
-----------------------------------------------------
(1) Busy-interval decomposition + combinatorial search   -> NOT trainable (analytic).
(2) Directed timing-inference via state-space exploration -> NOT trainable (search).
(3) Bayesian inference        -> APPLICABLE (BayesianRidge, multi-output).
(4) Support Vector Machines   -> APPLICABLE (SVR-RBF, multi-output).
(5) Artificial Immune Systems -> APPLICABLE (clonal-selection regressor).
(6) Cellular Automaton        -> WEAK FIT / experimental (CA reservoir, --with-ca).
(7) DNN                       -> APPLICABLE (MLP, main baseline).

METRICS (K-fold CV): MSE(raw) mean±std and MSE(log) mean±std.
MSE(log) matches the LogScaleL2Loss used by the full model and is the primary
metric for comparison; the table is sorted by MSE(log).

Usage
-----
    python baseline.py --csv_dir log_attacker/ --config_path task_configs.json
    python baseline.py --csv_dir log_attacker/ --use-knowledge --with-ca
"""

import os
import glob
import argparse
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
from scipy import stats

import torch
import torch.nn as nn

from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVR
from sklearn.linear_model import BayesianRidge, Ridge
from sklearn.multioutput import MultiOutputRegressor
from sklearn.model_selection import KFold

# reused from the provided processing.py
from processing import (
    parse_variation_from_config,
    parse_scheduler_from_filename,
    encode_sample_from_csv,
    SCHEDULER_VOCAB,
)

warnings.filterwarnings("ignore")
np.random.seed(42)
torch.manual_seed(42)

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
CRITICAL_IDS = (1, 2, 3)


# ============================================================================
# DATA LOADING
# ============================================================================
def load_flat_series(csv_path: str, seq_len: int, feature_mode: str = "relative") -> np.ndarray:
    """Flat vector of length seq_len*2 from one attacker CSV (truncate/zero-pad)."""
    df = pd.read_csv(csv_path)
    arr = df[["start_ts", "end_ts"]].to_numpy(dtype=np.float32)

    if feature_mode == "relative" and len(arr) > 0:
        first_start = arr[0, 0]
        offset = arr[:, 0] - first_start          # time since first observation
        duration = arr[:, 1] - arr[:, 0]          # how long the observer ran
        arr = np.stack([offset, duration], axis=1)

    if len(arr) >= seq_len:
        arr = arr[:seq_len]
    else:
        arr = np.concatenate([arr, np.zeros((seq_len - len(arr), 2), np.float32)], axis=0)
    return arr.reshape(-1)


def build_knowledge_features(csv_path: str, config_path: str) -> np.ndarray:
    """
    Knowledge block mirroring modality_2 + modality_3 of __getitem__:
        scheduler one-hot (len vocab)  ++  observer+critical numeric features (9).
    """
    sched_id = parse_scheduler_from_filename(os.path.basename(csv_path), SCHEDULER_VOCAB)
    onehot = np.zeros(len(SCHEDULER_VOCAB), dtype=np.float32)
    onehot[sched_id] = 1.0
    features, _ = encode_sample_from_csv(config_path, csv_path)   # modality_3 (9,)
    return np.concatenate([onehot, features.astype(np.float32)])


def build_dataset(csv_dir, config_path, seq_len, feature_mode, use_knowledge=False):
    """Return X (N, D), Y (N, 3), filenames. D = seq_len*2 (+19 if use_knowledge)."""
    csv_files = sorted(glob.glob(os.path.join(csv_dir, "*.csv")))
    if not csv_files:
        raise ValueError(f"No CSV files found in {csv_dir}")

    X, Y, used = [], [], []
    for path in csv_files:
        try:
            _, _, out = parse_variation_from_config(config_path, path)
            y = [out.periods[i] for i in CRITICAL_IDS]
            ts = load_flat_series(path, seq_len, feature_mode)
            if use_knowledge:
                ts = np.concatenate([ts, build_knowledge_features(path, config_path)])
        except Exception as e:
            print(f"  [skip] {os.path.basename(path)}: {e}")
            continue
        X.append(ts); Y.append(y); used.append(os.path.basename(path))

    X = np.asarray(X, dtype=np.float32)
    Y = np.asarray(Y, dtype=np.float32)
    extra = f" (+{len(SCHEDULER_VOCAB)+9} knowledge)" if use_knowledge else ""
    print(f"Loaded {len(X)} samples | X shape {X.shape}{extra} | Y shape {Y.shape}")
    return X, Y, used


# ============================================================================
# METRICS
# ============================================================================
def compute_metrics(y_true_raw, y_pred_raw):
    """Return MSE(raw), MSE(log)."""
    y_true_raw = np.asarray(y_true_raw, dtype=np.float64)
    y_pred_raw = np.clip(np.asarray(y_pred_raw, dtype=np.float64), 1e-6, None)
    mse_raw = np.mean((y_pred_raw - y_true_raw) ** 2)
    mse_log = np.mean((np.log(y_pred_raw) - np.log(y_true_raw + 1e-8)) ** 2)
    return mse_raw, mse_log


# ============================================================================
# (7) DNN
# ============================================================================
class MLPRegressor(nn.Module):
    def __init__(self, in_dim, hidden=256, out_dim=3, dropout=0.2):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, hidden), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(hidden, hidden // 2), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(hidden // 2, out_dim),          # predicts log-period
        )

    def forward(self, x):
        return self.net(x)


def train_dnn(Xtr, Ytr, Xte, epochs=200, lr=1e-3):
    scaler = StandardScaler().fit(Xtr)
    Xtr_s = torch.tensor(scaler.transform(Xtr), dtype=torch.float32, device=DEVICE)
    Xte_s = torch.tensor(scaler.transform(Xte), dtype=torch.float32, device=DEVICE)
    ytr_log = torch.tensor(np.log(Ytr + 1e-8), dtype=torch.float32, device=DEVICE)

    model = MLPRegressor(Xtr.shape[1]).to(DEVICE)
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    loss_fn = nn.MSELoss()

    model.train()
    for _ in range(epochs):
        opt.zero_grad()
        loss_fn(model(Xtr_s), ytr_log).backward()
        opt.step()

    model.eval()
    with torch.no_grad():
        pred_log = model(Xte_s).cpu().numpy()
    return np.exp(pred_log)


# ============================================================================
# (4) SVM  and  (3) Bayesian
# ============================================================================
def train_sklearn(estimator, Xtr, Ytr, Xte):
    scaler = StandardScaler().fit(Xtr)
    reg = MultiOutputRegressor(estimator)
    reg.fit(scaler.transform(Xtr), np.log(Ytr + 1e-8))
    return np.exp(reg.predict(scaler.transform(Xte)))


# ============================================================================
# (5) AIS  -- clonal-selection / immune-network regressor
# ============================================================================
class AISRegressor:
    def __init__(self, k=5, clones=2, mutation=0.02):
        self.k, self.clones, self.mutation = k, clones, mutation

    def fit(self, X, Ylog):
        self.scaler = StandardScaler().fit(X)
        Xs = self.scaler.transform(X)
        abs_X, abs_Y = [Xs], [Ylog]
        for _ in range(self.clones):
            noise = np.random.normal(0, self.mutation, Xs.shape).astype(np.float32)
            abs_X.append(Xs + noise); abs_Y.append(Ylog)
        self.ab_X = np.concatenate(abs_X, 0)
        self.ab_Y = np.concatenate(abs_Y, 0)
        return self

    def predict(self, X):
        Xs = self.scaler.transform(X)
        k = min(self.k, len(self.ab_X))
        preds = []
        for x in Xs:
            d = np.linalg.norm(self.ab_X - x, axis=1)
            idx = np.argsort(d)[:k]
            w = (1.0 / (d[idx] + 1e-6)); w /= w.sum()
            preds.append((w[:, None] * self.ab_Y[idx]).sum(0))
        return np.asarray(preds)


def train_ais(Xtr, Ytr, Xte):
    ais = AISRegressor().fit(Xtr, np.log(Ytr + 1e-8))
    return np.exp(ais.predict(Xte))


# ============================================================================
# (6) Cellular Automaton reservoir -- EXPERIMENTAL
# ============================================================================
def _ca_step(state, rule=90):
    left = np.roll(state, 1, axis=1); right = np.roll(state, -1, axis=1)
    idx = (left << 2) | (state << 1) | right
    rule_bits = np.array([(rule >> i) & 1 for i in range(8)], dtype=np.int64)
    return rule_bits[idx]


def train_ca(Xtr, Ytr, Xte, rule=90, steps=4):
    scaler = StandardScaler().fit(Xtr)

    def reservoir(X):
        Xb = (scaler.transform(X) > 0).astype(np.int64)
        states, s = [Xb], Xb
        for _ in range(steps):
            s = _ca_step(s, rule); states.append(s)
        return np.concatenate(states, 1).astype(np.float32)

    head = Ridge(alpha=1.0).fit(reservoir(Xtr), np.log(Ytr + 1e-8))
    return np.exp(head.predict(reservoir(Xte)))


# ============================================================================
# EVALUATION HARNESS
# ============================================================================
def get_methods(with_ca=False):
    """Registry of (name, train_predict_fn) for all applicable baselines."""
    methods = [
        ("(7) DNN (MLP)",             lambda a, b, c, e: train_dnn(a, b, c, epochs=e)),
        ("(4) SVM (SVR-RBF)",         lambda a, b, c, e: train_sklearn(SVR(kernel="rbf", C=10.0), a, b, c)),
        ("(3) Bayesian (BayesRidge)", lambda a, b, c, e: train_sklearn(BayesianRidge(), a, b, c)),
        ("(5) AIS (clonal-select)",   lambda a, b, c, e: train_ais(a, b, c)),
    ]
    if with_ca:
        methods.append(("(6) CA reservoir [exp]", lambda a, b, c, e: train_ca(a, b, c)))
    return methods


def fold_losses(fn, X, Y, folds, epochs):
    """Return per-fold (mse_raw_array, mse_log_array) using fixed 10-fold splits."""
    n = len(X)
    n_splits = min(folds, n)
    if n_splits < 2:
        mse_raw, mse_log = compute_metrics(Y, fn(X, Y, X, epochs))
        return np.array([mse_raw]), np.array([mse_log])

    kf = KFold(n_splits=n_splits, shuffle=True, random_state=42)
    raw, log = [], []
    for tr, te in kf.split(X):
        mse_raw, mse_log = compute_metrics(Y[te], fn(X[tr], Y[tr], X[te], epochs))
        raw.append(mse_raw); log.append(mse_log)
    return np.asarray(raw), np.asarray(log)


def cross_validate(name, fn, X, Y, folds, epochs):
    raw, log = fold_losses(fn, X, Y, folds, epochs)
    return {"name": name,
            "mse_raw": float(np.mean(raw)), "mse_raw_std": float(np.std(raw)),
            "mse_log": float(np.mean(log)), "mse_log_std": float(np.std(log)),
            "folds": len(raw)}


def run_eval(args):
    """Single-configuration K-fold evaluation (prints the summary table)."""
    print("=" * 82)
    print(f"Device: {DEVICE} | seq_len={args.seq_len} | feature_mode={args.feature_mode} "
          f"| knowledge={'ON' if args.use_knowledge else 'OFF'}")
    print("=" * 82)

    X, Y, _ = build_dataset(args.csv_dir, args.config_path, args.seq_len,
                            args.feature_mode, args.use_knowledge)
    results = []
    for name, fn in get_methods(args.with_ca):
        print(f"\n>>> {name}")
        r = cross_validate(name, fn, X, Y, args.folds, args.epochs)
        print(f"    MSE(raw)={r['mse_raw']:.4f} ± {r['mse_raw_std']:.4f} | "
              f"MSE(log)={r['mse_log']:.4f} ± {r['mse_log_std']:.4f} | folds={r['folds']}")
        results.append(r)

    print("\n" + "=" * 82)
    print("BASELINE SUMMARY  (sorted by MSE(log) — matches LogScaleL2Loss; lower is better)")
    print("=" * 82)
    print(f"{'Method':<28}{'MSE(raw)':>14}{'MSE(log) mean':>16}{'MSE(log) std':>16}")
    print("-" * 82)
    for r in sorted(results, key=lambda d: d["mse_log"]):
        print(f"{r['name']:<28}{r['mse_raw']:>14.4f}{r['mse_log']:>16.4f}{r['mse_log_std']:>16.4f}")
    print("=" * 82)


# ============================================================================
# STATISTICAL ANALYSIS  (independent method — run with --mode stats)
# ============================================================================
def run_statistical_analysis(args):
    """
    10-fold CV for every model under knowledge=OFF and knowledge=ON, then:

      (1) Embedding effect  -> independent (Welch by default) t-test per model,
          comparing the 10 per-fold MSE(log) values OFF vs ON.
      (2) Model differences -> one-way ANOVA (parametric) AND Kruskal-Wallis
          (non-parametric) across the 5 models' fold-loss distributions,
          run separately for the OFF and ON regimes.

    All per-fold losses, summary stats, and test results are saved as CSVs.
    Tests use MSE(log) (matches LogScaleL2Loss); per-fold MSE(raw) is also saved.

    NOTE: OFF/ON share the same fold splits (random_state=42), so a *paired*
    t-test is also defensible; the independent t-test was requested and is used.
    """
    os.makedirs(args.output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    folds, epochs = args.folds, args.epochs
    method_names = [n for n, _ in get_methods(args.with_ca)]

    print("=" * 82)
    print(f"STATISTICAL ANALYSIS | {folds}-fold CV | metric=MSE(log) | device={DEVICE}")
    print("=" * 82)

    # ---- collect per-fold losses for both knowledge settings --------------
    log_data = {}   # (knowledge_bool, model_name) -> log-loss array
    per_fold_rows, summary_rows = [], []
    for knowledge in (False, True):
        tag = "ON" if knowledge else "OFF"
        print(f"\n[knowledge={tag}] building dataset ...")
        X, Y, _ = build_dataset(args.csv_dir, args.config_path, args.seq_len,
                                args.feature_mode, knowledge)
        for name, fn in get_methods(args.with_ca):
            raw_arr, log_arr = fold_losses(fn, X, Y, folds, epochs)
            log_data[(knowledge, name)] = log_arr
            for i, (r, l) in enumerate(zip(raw_arr, log_arr)):
                per_fold_rows.append({"knowledge": tag, "model": name,
                                      "fold": i + 1, "mse_raw": r, "mse_log": l})
            summary_rows.append({"knowledge": tag, "model": name,
                                 "mse_log_mean": float(np.mean(log_arr)),
                                 "mse_log_std": float(np.std(log_arr)),
                                 "mse_raw_mean": float(np.mean(raw_arr)),
                                 "mse_raw_std": float(np.std(raw_arr)),
                                 "n_folds": len(log_arr)})
            print(f"    {name:<28} MSE(log)={np.mean(log_arr):.4f} ± {np.std(log_arr):.4f}")

    # ---- (1) embedding effect: independent t-test per model ---------------
    equal_var = not args.welch          # --welch -> Welch; default Student's
    ttest_rows = []
    for name in method_names:
        off, on = log_data[(False, name)], log_data[(True, name)]
        if len(off) < 2 or len(on) < 2:
            continue
        t_stat, p_val = stats.ttest_ind(off, on, equal_var=equal_var)
        improved = np.mean(on) < np.mean(off)
        ttest_rows.append({
            "model": name,
            "mse_log_mean_OFF": float(np.mean(off)),
            "mse_log_mean_ON": float(np.mean(on)),
            "delta(ON-OFF)": float(np.mean(on) - np.mean(off)),
            "t_stat": float(t_stat), "p_value": float(p_val),
            "significant(p<0.05)": bool(p_val < 0.05),
            "significant_improvement": bool(p_val < 0.05 and improved),
        })

    # ---- (2) model differences: ANOVA + Kruskal-Wallis per regime ---------
    modeltest_rows = []
    for knowledge in (False, True):
        tag = "ON" if knowledge else "OFF"
        groups = [log_data[(knowledge, n)] for n in method_names]
        if min(len(g) for g in groups) < 2 or len(groups) < 2:
            continue
        F, p_anova = stats.f_oneway(*groups)
        H, p_kw = stats.kruskal(*groups)
        modeltest_rows.append({
            "knowledge": tag, "n_models": len(groups),
            "anova_F": float(F), "anova_p": float(p_anova),
            "anova_significant": bool(p_anova < 0.05),
            "kruskal_H": float(H), "kruskal_p": float(p_kw),
            "kruskal_significant": bool(p_kw < 0.05),
        })

    # ---- save CSVs --------------------------------------------------------
    paths = {}
    for key, rows in [("per_fold_losses", per_fold_rows), ("summary", summary_rows),
                      ("embedding_ttest", ttest_rows), ("model_tests", modeltest_rows)]:
        path = os.path.join(args.output_dir, f"{key}_{ts}.csv")
        pd.DataFrame(rows).to_csv(path, index=False)
        paths[key] = path

    # ---- console report ---------------------------------------------------
    ttype = "Welch" if equal_var is False else "Student's"
    print("\n" + "=" * 82)
    print(f"(1) EMBEDDING EFFECT — independent {ttype} t-test on MSE(log), OFF vs ON")
    print("=" * 82)
    print(f"{'Model':<28}{'mean OFF':>10}{'mean ON':>10}{'t':>9}{'p':>10}  verdict")
    print("-" * 82)
    for r in ttest_rows:
        if r["significant_improvement"]:
            verdict = "improved (sig.)"
        elif r["significant(p<0.05)"]:
            verdict = "changed (sig., worse)"
        else:
            verdict = "no sig. difference"
        print(f"{r['model']:<28}{r['mse_log_mean_OFF']:>10.4f}{r['mse_log_mean_ON']:>10.4f}"
              f"{r['t_stat']:>9.3f}{r['p_value']:>10.4f}  {verdict}")

    print("\n" + "=" * 82)
    print("(2) MODEL DIFFERENCES — ANOVA + Kruskal-Wallis across models, per regime")
    print("=" * 82)
    print(f"{'Knowledge':<12}{'ANOVA F':>12}{'ANOVA p':>12}{'Kruskal H':>12}{'Kruskal p':>12}  verdict")
    print("-" * 82)
    for r in modeltest_rows:
        verdict = "models differ" if (r["anova_significant"] or r["kruskal_significant"]) \
            else "no sig. difference"
        print(f"{r['knowledge']:<12}{r['anova_F']:>12.3f}{r['anova_p']:>12.4f}"
              f"{r['kruskal_H']:>12.3f}{r['kruskal_p']:>12.4f}  {verdict}")

    print("\nSaved CSVs:")
    for k, p in paths.items():
        print(f"  {k:<18} -> {p}")
    print("=" * 82)


def build_argparser():
    ap = argparse.ArgumentParser(description="Baseline evaluation & statistics for scheduler period inference.")
    ap.add_argument("--mode", choices=["eval", "stats"], default="eval",
                    help="'eval' = single-config CV table; 'stats' = full statistical analysis")
    ap.add_argument("--csv_dir", default="log_attacker/")
    ap.add_argument("--config_path", default="task_configs.json")
    ap.add_argument("--seq_len", type=int, default=256, choices=[256, 512])
    ap.add_argument("--feature_mode", default="relative", choices=["relative", "raw"])
    ap.add_argument("--folds", type=int, default=5,
                    help="K for K-fold CV (use 10 for --mode stats)")
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--use-knowledge", action="store_true",
                    help="[eval mode] append scheduler one-hot + observer/critical features")
    ap.add_argument("--with-ca", action="store_true", help="include experimental CA reservoir")
    ap.add_argument("--welch", action="store_true",
                    help="[stats mode] use Welch's t-test instead of Student's independent t-test")
    ap.add_argument("--output_dir", default="baseline_stats",
                    help="[stats mode] directory for result CSVs")
    return ap


def main():
    args = build_argparser().parse_args()
    if args.mode == "stats":
        run_statistical_analysis(args)
    else:
        run_eval(args)


if __name__ == "__main__":
    main()