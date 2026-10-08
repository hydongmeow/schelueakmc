"""
baseline.py  —  RTOS Scheduler Inference · Baseline Package Entry Point
========================================================================
Central script for the RTOS period-inference baseline study.
Run any mode directly, or import individual components from this file.

MODES
-----
    eval     K-fold cross-validation, prints MSE(log)/MSE(raw) summary table
    stats    10-fold CV x knowledge ON/OFF → paired tests + per-regime ANOVA CSVs
    predict  Train CA (best model, knowledge=ON) → per-fold prediction CSVs
    profile  Measure param count, serialized size, and per-sample latency

QUICK START  (both load regimes are loaded together: 184 + 56 = 240 samples)
-----------
    python baseline.py --mode eval    --csv_dir log_attacker/,log_attacker_l/ \
                                      --config_path task_configs.json,task_configs_l.json \
                                      --with-ca --use-knowledge
    python baseline.py --mode stats   ... --folds 10 --with-ca
    python baseline.py --mode predict ... --predict_output_dir results/
    python baseline.py --mode profile ... --with-ca --use-knowledge

CROSS-VALIDATION SPLITS  (--split)
---------------------------------
    group   (default) GroupKFold: every fold's validation samples come from
            task sets (period triples) that never appear in that fold's
            training data.  This removes the target leakage that a random
            split has, because the same period triple is simulated under
            10 schedulers and (for some sets) under two load regimes.
    random  Legacy shuffled KFold (kept only to quantify the leakage effect).

TASK DEFINITION
---------------
    INPUT  : attacker/observer execution intervals (start_ts, end_ts per row).
             Truncated/padded to SEQ_LEN rows, flattened  →  R^(SEQ_LENx2).
             With --use-knowledge: 19-dim block appended
               (scheduler one-hot + observer/critical-task static features).
    OUTPUT : 3 critical-task periods (identifiers 1, 2, 3) from the config.
    METRIC : MSE in log-period space.

APPLICABLE METHODS
------------------
    (3) Bayesian    scikit-learn BayesianRidge (evidence maximisation),
                    one head per period via MultiOutputRegressor
    (4) SVM         SVR-RBF        (multi-output via MultiOutputRegressor)
    (5) AIS         Clonal-selection immune-network regressor
    (6) CA          Cellular-automaton reservoir (rule 90) + ridge readout
    (7) DNN         Two-hidden-layer MLP, trained with Adam
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
from sklearn.model_selection import KFold, GroupKFold

from processing import (
    parse_variation_from_config,
    parse_scheduler_from_filename,
    encode_sample_from_csv,
    SCHEDULER_VOCAB,
)

warnings.filterwarnings("ignore")
np.random.seed(42)
torch.manual_seed(42)

DEVICE       = torch.device("cuda" if torch.cuda.is_available() else "cpu")
CRITICAL_IDS = (1, 2, 3)
SCHEDULER_NAMES = {v: k for k, v in SCHEDULER_VOCAB.items()}


# ============================================================================
# DATA LOADING
# ============================================================================
def split_list(s):
    """'a,b' -> ['a', 'b'];  list passes through unchanged."""
    if isinstance(s, (list, tuple)):
        return list(s)
    return [p.strip() for p in str(s).split(",") if p.strip()]


def load_flat_series(csv_path: str, seq_len: int,
                     feature_mode: str = "relative") -> np.ndarray:
    """
    Load one attacker CSV → flat vector of length seq_len*2.
    feature_mode='relative': (offset-since-first, run-duration) per row.
    Sequences longer than seq_len are truncated; shorter are zero-padded.
    """
    df  = pd.read_csv(csv_path)
    arr = df[["start_ts", "end_ts"]].to_numpy(dtype=np.float32)

    if feature_mode == "relative" and len(arr) > 0:
        offset   = arr[:, 0] - arr[0, 0]
        duration = arr[:, 1] - arr[:, 0]
        arr = np.stack([offset, duration], axis=1)

    if len(arr) >= seq_len:
        arr = arr[:seq_len]
    else:
        arr = np.concatenate(
            [arr, np.zeros((seq_len - len(arr), 2), np.float32)], axis=0)
    return arr.reshape(-1)


def build_knowledge_features(csv_path: str, config_path: str) -> np.ndarray:
    """
    19-dim knowledge block:
        scheduler one-hot  (10 dims, SCHEDULER_VOCAB)
        +  observer + critical-task static features  (9 dims, from modality_3)
    No target leakage: block contains WCETs and observer period, never the
    critical-task periods being predicted.
    """
    sched_id = parse_scheduler_from_filename(
        os.path.basename(csv_path), SCHEDULER_VOCAB)
    onehot        = np.zeros(len(SCHEDULER_VOCAB), dtype=np.float32)
    onehot[sched_id] = 1.0
    features, _   = encode_sample_from_csv(config_path, csv_path)
    return np.concatenate([onehot, features.astype(np.float32)])


def build_dataset(csv_dirs, config_paths, seq_len, feature_mode,
                  use_knowledge=False, verbose=True):
    """
    Build (X, Y, meta) for all valid CSVs in one or more directories.

    csv_dirs / config_paths : comma-separated strings or lists.  Directory k is
        parsed with config k (a single config is reused for every directory).
    X    : (N, D)  where D = seq_len*2  (or seq_len*2+19 with knowledge)
    Y    : (N, 3)  critical-task periods
    meta : list of dicts, one per row:
             file, dir, config, scheduler, variation,
             group (period triple, e.g. '5-10-20'), modality_3 (9 floats)
    """
    dirs = split_list(csv_dirs)
    cfgs = split_list(config_paths)
    if len(cfgs) == 1 and len(dirs) > 1:
        cfgs = cfgs * len(dirs)
    if len(cfgs) != len(dirs):
        raise ValueError("--csv_dir and --config_path must have the same number "
                         "of comma-separated entries (or one config for all)")

    X, Y, meta = [], [], []
    for d, cfg in zip(dirs, cfgs):
        csv_files = sorted(glob.glob(os.path.join(d, "*.csv")))
        if not csv_files:
            raise ValueError(f"No CSV files found in {d}")
        for path in csv_files:
            try:
                _, _, out = parse_variation_from_config(cfg, path)
                y  = [out.periods[i] for i in CRITICAL_IDS]
                ts = load_flat_series(path, seq_len, feature_mode)
                m3, _ = encode_sample_from_csv(cfg, path)
                sched_id = parse_scheduler_from_filename(os.path.basename(path),
                                                         SCHEDULER_VOCAB)
                if use_knowledge:
                    ts = np.concatenate([ts, build_knowledge_features(path, cfg)])
            except Exception as e:
                if verbose:
                    print(f"  [skip] {os.path.basename(path)}: {e}")
                continue
            X.append(ts); Y.append(y)
            meta.append({
                "file":       os.path.basename(path),
                "dir":        d,
                "config":     cfg,
                "scheduler":  SCHEDULER_NAMES[sched_id],
                "variation":  int(os.path.basename(path).split("variation_")[1].split("_")[0]),
                "group":      "-".join(str(int(p)) for p in sorted(y)),
                "modality_3": [float(v) for v in m3],
            })

    X = np.asarray(X, dtype=np.float32)
    Y = np.asarray(Y, dtype=np.float32)
    if verbose:
        extra = f" (+{len(SCHEDULER_VOCAB)+9} knowledge)" if use_knowledge else ""
        n_groups = len({m["group"] for m in meta})
        print(f"Loaded {len(X)} samples from {len(dirs)} dir(s) | X {X.shape}{extra} "
              f"| Y {Y.shape} | {n_groups} distinct period triples")
    return X, Y, meta


def make_folds(meta, folds: int, split: str = "group", seed: int = 42):
    """
    Return a list of (train_idx, val_idx) pairs.

    split='group' : GroupKFold on the period triple.  No period triple is
                    shared between the training and validation part of a fold.
    split='random': shuffled KFold (legacy; leaks period triples across folds).
    """
    n = len(meta)
    groups = np.array([m["group"] for m in meta])
    if split == "group":
        n_groups = len(set(groups))
        k = min(folds, n_groups)
        gkf = GroupKFold(n_splits=k)
        return list(gkf.split(np.zeros((n, 1)), groups=groups))
    k = min(folds, n)
    kf = KFold(n_splits=k, shuffle=True, random_state=seed)
    return list(kf.split(np.zeros((n, 1))))


def describe_folds(meta, splits):
    """Print, for every fold, the validation period triples and confirm that
    none of them occurs in the fold's training set (leakage check)."""
    groups = np.array([m["group"] for m in meta])
    leaks = 0
    for k, (tr, te) in enumerate(splits, start=1):
        val_groups = sorted({str(g) for g in groups[te]})
        shared     = {str(g) for g in groups[tr]} & {str(g) for g in groups[te]}
        leaks     += len(shared)
        print(f"  fold {k:>2}: n_val={len(te):>3} | val period triples: "
              f"{val_groups}" + (f"  !! shared with train: {sorted(shared)}" if shared else ""))
    print(f"  period triples shared between train/val across all folds: {leaks}"
          + ("  (no leakage)" if leaks == 0 else "  (LEAKAGE)"))
    return leaks


# ============================================================================
# METRICS
# ============================================================================
def compute_metrics(y_true_raw, y_pred_raw):
    """Return (MSE_raw, MSE_log)."""
    yt = np.asarray(y_true_raw, dtype=np.float64)
    yp = np.clip(np.asarray(y_pred_raw, dtype=np.float64), 1e-6, None)
    return float(np.mean((yp - yt) ** 2)), \
           float(np.mean((np.log(yp) - np.log(yt + 1e-8)) ** 2))


# ============================================================================
# (7) DNN — MLP
# ============================================================================
class MLPRegressor(nn.Module):
    def __init__(self, in_dim, hidden=256, out_dim=3, dropout=0.2):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, hidden), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(hidden, hidden // 2), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(hidden // 2, out_dim),
        )
    def forward(self, x):
        return self.net(x)


def train_dnn(Xtr, Ytr, Xte, epochs=200, lr=1e-3):
    scaler  = StandardScaler().fit(Xtr)
    Xtr_s   = torch.tensor(scaler.transform(Xtr), dtype=torch.float32, device=DEVICE)
    Xte_s   = torch.tensor(scaler.transform(Xte), dtype=torch.float32, device=DEVICE)
    ytr_log = torch.tensor(np.log(Ytr + 1e-8),    dtype=torch.float32, device=DEVICE)
    model   = MLPRegressor(Xtr.shape[1]).to(DEVICE)
    opt     = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    loss_fn = nn.MSELoss()
    model.train()
    for _ in range(epochs):
        opt.zero_grad(); loss_fn(model(Xtr_s), ytr_log).backward(); opt.step()
    model.eval()
    with torch.no_grad():
        return np.exp(model(Xte_s).cpu().numpy())


# ============================================================================
# (4) SVM  and  (3) Bayesian  — sklearn multi-output regressors
# ============================================================================
def train_sklearn(estimator, Xtr, Ytr, Xte):
    scaler = StandardScaler().fit(Xtr)
    reg    = MultiOutputRegressor(estimator)
    reg.fit(scaler.transform(Xtr), np.log(Ytr + 1e-8))
    return np.exp(reg.predict(scaler.transform(Xte)))


# ============================================================================
# (5) AIS — clonal-selection / immune-network regressor
# ============================================================================
class AISRegressor:
    """
    Instance-based AIS regressor.
    fit()     : store (and mutate-clone) training samples as antibodies.
    predict() : affinity-weighted k-NN prediction in log-period space.
    """
    def __init__(self, k=5, clones=2, mutation=0.02):
        self.k, self.clones, self.mutation = k, clones, mutation

    def fit(self, X, Ylog):
        self.scaler = StandardScaler().fit(X)
        Xs          = self.scaler.transform(X)
        abs_X, abs_Y = [Xs], [Ylog]
        for _ in range(self.clones):
            noise = np.random.normal(0, self.mutation, Xs.shape).astype(np.float32)
            abs_X.append(Xs + noise); abs_Y.append(Ylog)
        self.ab_X = np.concatenate(abs_X, 0)
        self.ab_Y = np.concatenate(abs_Y, 0)
        return self

    def predict(self, X):
        Xs = self.scaler.transform(X)
        k  = min(self.k, len(self.ab_X))
        preds = []
        for x in Xs:
            d   = np.linalg.norm(self.ab_X - x, axis=1)
            idx = np.argsort(d)[:k]
            w   = 1.0 / (d[idx] + 1e-6); w /= w.sum()
            preds.append((w[:, None] * self.ab_Y[idx]).sum(0))
        return np.asarray(preds)


def train_ais(Xtr, Ytr, Xte):
    return np.exp(AISRegressor().fit(Xtr, np.log(Ytr + 1e-8)).predict(Xte))


# ============================================================================
# (6) Cellular Automaton reservoir
# ============================================================================
def _ca_step(state, rule=90):
    """One elementary CA update (periodic boundary, batch-wise)."""
    left  = np.roll(state, 1,  axis=1)
    right = np.roll(state, -1, axis=1)
    idx   = (left << 2) | (state << 1) | right
    return np.array([(rule >> i) & 1 for i in range(8)],
                    dtype=np.int64)[idx]


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
# METHOD REGISTRY  &  EVALUATION HARNESS
# ============================================================================
def get_methods(with_ca=False):
    """Return list of (name, train_predict_fn) for all applicable baselines."""
    methods = [
        ("(7) DNN (MLP)",             lambda a, b, c, e: train_dnn(a, b, c, epochs=e)),
        ("(4) SVM (SVR-RBF)",         lambda a, b, c, e: train_sklearn(SVR(kernel="rbf", C=10.0), a, b, c)),
        ("(3) Bayesian (BayesRidge)", lambda a, b, c, e: train_sklearn(BayesianRidge(), a, b, c)),
        ("(5) AIS (clonal-select)",   lambda a, b, c, e: train_ais(a, b, c)),
    ]
    if with_ca:
        methods.append(("(6) CA reservoir",
                        lambda a, b, c, e: train_ca(a, b, c)))
    return methods


def fold_losses(fn, X, Y, splits, epochs):
    """Return (mse_raw_per_fold, mse_log_per_fold) arrays for given splits."""
    raw, log = [], []
    for tr, te in splits:
        r, l = compute_metrics(Y[te], fn(X[tr], Y[tr], X[te], epochs))
        raw.append(r); log.append(l)
    return np.asarray(raw), np.asarray(log)


def cross_validate(name, fn, X, Y, splits, epochs) -> dict:
    raw, log = fold_losses(fn, X, Y, splits, epochs)
    return {"name": name,
            "mse_raw": float(np.mean(raw)), "mse_raw_std": float(np.std(raw)),
            "mse_log": float(np.mean(log)), "mse_log_std": float(np.std(log)),
            "folds":   len(raw)}


# ============================================================================
# MODE: eval
# ============================================================================
def run_eval(args):
    """K-fold CV for all methods; print summary table sorted by MSE(log)."""
    print("=" * 82)
    print(f"Device: {DEVICE} | seq_len={args.seq_len} | "
          f"feature_mode={args.feature_mode} | split={args.split} | "
          f"knowledge={'ON' if args.use_knowledge else 'OFF'}")
    print("=" * 82)

    X, Y, meta = build_dataset(args.csv_dir, args.config_path,
                               args.seq_len, args.feature_mode, args.use_knowledge)
    splits = make_folds(meta, args.folds, args.split)
    describe_folds(meta, splits)

    results = []
    for name, fn in get_methods(args.with_ca):
        print(f"\n>>> {name}")
        r = cross_validate(name, fn, X, Y, splits, args.epochs)
        print(f"    MSE(raw)={r['mse_raw']:.4f} ± {r['mse_raw_std']:.4f} | "
              f"MSE(log)={r['mse_log']:.4f} ± {r['mse_log_std']:.4f} | "
              f"folds={r['folds']}")
        results.append(r)

    print("\n" + "=" * 82)
    print("SUMMARY  (sorted by MSE(log); lower is better)")
    print("=" * 82)
    print(f"{'Method':<28}{'MSE(raw)':>14}{'MSE(log) mean':>16}{'MSE(log) std':>16}")
    print("-" * 82)
    for r in sorted(results, key=lambda d: d["mse_log"]):
        print(f"{r['name']:<28}{r['mse_raw']:>14.4f}"
              f"{r['mse_log']:>16.4f}{r['mse_log_std']:>16.4f}")
    print("=" * 82)


# ============================================================================
# MODE: stats
# ============================================================================
def _cohens_dz(diff):
    diff = np.asarray(diff, dtype=np.float64)
    sd = diff.std(ddof=1)
    return float(diff.mean() / sd) if sd > 0 else float("nan")


def _eta_squared(groups):
    allv = np.concatenate(groups)
    gm   = allv.mean()
    ss_b = sum(len(g) * (g.mean() - gm) ** 2 for g in groups)
    ss_t = float(((allv - gm) ** 2).sum())
    return float(ss_b / ss_t) if ss_t > 0 else float("nan")


def run_statistical_analysis(args):
    """
    10-fold CV x {knowledge OFF, ON} for every method, on IDENTICAL folds.
    Saves timestamped CSVs to --output_dir:
        per_fold_losses, summary, embedding_tests, model_tests
    Prints:
        (1) knowledge-embedding effect  — PAIRED tests (same folds under both
            conditions): paired t-test + Wilcoxon signed-rank, per model and
            pooled over all (model, fold) pairs; effect size = Cohen's d_z.
        (2) model differences — one-way ANOVA (+ eta^2) and Kruskal-Wallis
            across the 5 models, reported SEPARATELY for OFF and ON.
    """
    os.makedirs(args.output_dir, exist_ok=True)
    ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
    folds  = args.folds
    epochs = args.epochs
    method_names = [n for n, _ in get_methods(args.with_ca)]

    print("=" * 82)
    print(f"STATISTICAL ANALYSIS | {folds}-fold CV | split={args.split} | "
          f"metric=MSE(log) | device={DEVICE}")
    print("=" * 82)

    log_data, per_fold_rows, summary_rows = {}, [], []
    splits = None
    for knowledge in (False, True):
        tag = "ON" if knowledge else "OFF"
        print(f"\n[knowledge={tag}] building dataset ...")
        X, Y, meta = build_dataset(args.csv_dir, args.config_path,
                                   args.seq_len, args.feature_mode, knowledge)
        if splits is None:                      # identical folds for OFF and ON
            splits = make_folds(meta, folds, args.split)
            describe_folds(meta, splits)
        for name, fn in get_methods(args.with_ca):
            raw_arr, log_arr = fold_losses(fn, X, Y, splits, epochs)
            log_data[(knowledge, name)] = log_arr
            for i, (r, l) in enumerate(zip(raw_arr, log_arr)):
                per_fold_rows.append({"knowledge": tag, "model": name,
                                      "fold": i+1, "mse_raw": r, "mse_log": l})
            summary_rows.append({"knowledge": tag, "model": name,
                                  "mse_log_mean": float(np.mean(log_arr)),
                                  "mse_log_std":  float(np.std(log_arr)),
                                  "mse_raw_mean": float(np.mean(raw_arr)),
                                  "mse_raw_std":  float(np.std(raw_arr)),
                                  "n_folds": len(log_arr)})
            print(f"    {name:<28} MSE(log)={np.mean(log_arr):.4f} ± {np.std(log_arr):.4f}")

    # (1) paired tests, per model and pooled
    test_rows = []
    def _paired(label, off, on):
        diff = off - on                          # >0 means ON improved
        t_stat, p_t = stats.ttest_rel(off, on)
        try:
            w_stat, p_w = stats.wilcoxon(off, on)
        except ValueError:                       # all differences zero
            w_stat, p_w = float("nan"), float("nan")
        test_rows.append({
            "model": label, "n_pairs": len(diff),
            "mse_log_mean_OFF": float(np.mean(off)),
            "mse_log_mean_ON":  float(np.mean(on)),
            "mean_diff(OFF-ON)": float(diff.mean()),
            "paired_t": float(t_stat), "paired_p": float(p_t),
            "wilcoxon_W": float(w_stat), "wilcoxon_p": float(p_w),
            "cohens_dz": _cohens_dz(diff),
            "significant(p<0.05)": bool(p_t < 0.05),
            "significant_improvement": bool(p_t < 0.05 and diff.mean() > 0),
        })
    for name in method_names:
        off, on = log_data[(False, name)], log_data[(True, name)]
        if len(off) >= 2:
            _paired(name, off, on)
    off_all = np.concatenate([log_data[(False, n)] for n in method_names])
    on_all  = np.concatenate([log_data[(True,  n)] for n in method_names])
    _paired("ALL models pooled", off_all, on_all)

    # (2) ANOVA + Kruskal-Wallis per regime
    modeltest_rows = []
    for knowledge in (False, True):
        tag    = "ON" if knowledge else "OFF"
        groups = [log_data[(knowledge, n)] for n in method_names]
        if min(len(g) for g in groups) < 2:
            continue
        F, p_anova = stats.f_oneway(*groups)
        H, p_kw    = stats.kruskal(*groups)
        modeltest_rows.append({
            "knowledge": tag, "n_models": len(groups),
            "anova_F": float(F), "anova_p": float(p_anova),
            "eta_squared": _eta_squared(groups),
            "anova_significant":   bool(p_anova < 0.05),
            "kruskal_H": float(H), "kruskal_p": float(p_kw),
            "kruskal_significant": bool(p_kw < 0.05),
        })

    # save CSVs
    paths = {}
    for key, rows in [("per_fold_losses", per_fold_rows), ("summary", summary_rows),
                      ("embedding_tests", test_rows),     ("model_tests", modeltest_rows)]:
        path = os.path.join(args.output_dir, f"{key}_{ts}.csv")
        pd.DataFrame(rows).to_csv(path, index=False)
        paths[key] = path

    # console report
    print("\n" + "=" * 82)
    print("(1) EMBEDDING EFFECT — paired t-test / Wilcoxon on MSE(log), OFF vs ON "
          "(same folds)")
    print("=" * 82)
    print(f"{'Model':<28}{'mean OFF':>10}{'mean ON':>10}{'t':>9}{'p':>10}"
          f"{'Wilc. p':>10}{'d_z':>8}  verdict")
    print("-" * 82)
    for r in test_rows:
        verdict = ("improved (sig.)"       if r["significant_improvement"] else
                   "changed (sig., worse)" if r["significant(p<0.05)"]     else
                   "no sig. difference")
        print(f"{r['model']:<28}{r['mse_log_mean_OFF']:>10.4f}"
              f"{r['mse_log_mean_ON']:>10.4f}"
              f"{r['paired_t']:>9.3f}{r['paired_p']:>10.4f}"
              f"{r['wilcoxon_p']:>10.4f}{r['cohens_dz']:>8.3f}  {verdict}")

    print("\n" + "=" * 82)
    print("(2) MODEL DIFFERENCES — ANOVA (+eta^2) and Kruskal-Wallis, per regime")
    print("=" * 82)
    print(f"{'Knowledge':<12}{'ANOVA F':>12}{'ANOVA p':>12}{'eta^2':>9}"
          f"{'Kruskal H':>12}{'Kruskal p':>12}  verdict")
    print("-" * 82)
    for r in modeltest_rows:
        verdict = ("models differ" if (r["anova_significant"] or r["kruskal_significant"])
                   else "no sig. difference")
        print(f"{r['knowledge']:<12}{r['anova_F']:>12.3f}{r['anova_p']:>12.4f}"
              f"{r['eta_squared']:>9.4f}"
              f"{r['kruskal_H']:>12.3f}{r['kruskal_p']:>12.4f}  {verdict}")

    print("\nSaved CSVs:")
    for k, p in paths.items():
        print(f"  {k:<18} -> {p}")
    print("=" * 82)


# ============================================================================
# ARGUMENT PARSER  (unified, all modes share common flags)
# ============================================================================
def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="RTOS Scheduler Inference — Baseline Package",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--mode", default="eval",
                    choices=["eval", "stats", "predict", "profile"],
                    help="eval: CV table | stats: statistical tests | "
                         "predict: CA fold CSVs | profile: size+latency")

    # shared data flags
    ap.add_argument("--csv_dir", default="log_attacker/,log_attacker_l/",
                    help="comma-separated attacker-log directories")
    ap.add_argument("--config_path", default="task_configs.json,task_configs_l.json",
                    help="comma-separated configs, one per --csv_dir entry")
    ap.add_argument("--seq_len",      type=int, default=256, choices=[256, 512])
    ap.add_argument("--feature_mode", default="relative", choices=["relative", "raw"])
    ap.add_argument("--use-knowledge", action="store_true",
                    help="append scheduler one-hot + observer/task static features")
    ap.add_argument("--with-ca",       action="store_true",
                    help="include CA reservoir method")
    ap.add_argument("--split", default="group", choices=["group", "random"],
                    help="group: GroupKFold by period triple (no leakage) | "
                         "random: legacy shuffled KFold")

    # eval / stats
    ap.add_argument("--folds",  type=int, default=10,
                    help="K for K-fold CV")
    ap.add_argument("--epochs", type=int, default=200, help="DNN training epochs")
    ap.add_argument("--output_dir", default="baseline_stats",
                    help="[stats] directory for result CSVs")

    # predict (CA-specific)
    ap.add_argument("--predict_output_dir", default="results/",
                    help="[predict] where to write val_predictions_ca_*.csv")
    ap.add_argument("--predict_model", default="ca",
                    choices=["ca", "mlp", "svr", "bayes", "ais"],
                    help="[predict] predictor written to the fold CSVs")
    ap.add_argument("--rule",  type=int,   default=90,  help="[predict] CA rule")
    ap.add_argument("--steps", type=int,   default=4,   help="[predict] CA steps")
    ap.add_argument("--alpha", type=float, default=1.0, help="[predict] ridge alpha")

    # profile
    ap.add_argument("--repeats",          type=int, default=3)
    ap.add_argument("--max_time_samples", type=int, default=200)

    return ap


# ============================================================================
# ENTRY POINT
# ============================================================================
def main():
    args = build_argparser().parse_args()

    if args.mode == "eval":
        run_eval(args)

    elif args.mode == "stats":
        run_statistical_analysis(args)

    elif args.mode == "predict":
        from prediction import run as run_predict
        pred_ns = argparse.Namespace(
            csv_dir      = args.csv_dir,
            config_path  = args.config_path,
            output_dir   = args.predict_output_dir,
            seq_len      = args.seq_len,
            feature_mode = args.feature_mode,
            folds        = args.folds,
            split        = args.split,
            model        = args.predict_model,
            epochs       = args.epochs,
            rule         = args.rule,
            steps        = args.steps,
            alpha        = args.alpha,
        )
        run_predict(pred_ns)

    elif args.mode == "profile":
        from profiling import run as run_profile
        run_profile(args)


if __name__ == "__main__":
    main()
