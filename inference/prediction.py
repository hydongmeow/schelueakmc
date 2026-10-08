"""
prediction.py
=============
Train one predictor (default: CA reservoir; --model mlp|svr|bayes|ais) with
knowledge=ON under 10-fold task-set-grouped CV and save one prediction CSV per
validation fold, compatible with assessment.py.

Folds are built with GroupKFold on the period triple (see baseline.make_folds),
so a validation task set never occurs in the training data of the same fold.
Pass --split random to reproduce the legacy leaky split.

Output (one file per fold in --output_dir):
    val_predictions_{model}_{fold}.csv
    columns: idx, fold, source_file, scheduler, group,
             prediction, true_value, modality_3, mse_error

Importable entry point:  run(args)
Standalone:              python prediction.py [--csv_dir ...] [--output_dir ...]
Via baseline.py:         python baseline.py --mode predict [same flags]
"""

import os
import argparse

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

# all CA and data-loading logic lives in baseline.py (single source of truth)
from baseline import (
    build_dataset,
    make_folds,
    describe_folds,
    _ca_step,
    train_dnn, train_sklearn, train_ais,
)
from sklearn.svm import SVR
from sklearn.linear_model import BayesianRidge

MODELS = ("ca", "mlp", "svr", "bayes", "ais")

np.random.seed(42)


# ============================================================================
# CA reservoir  (fitted variant returning objects for reuse at inference)
# ============================================================================
def _reservoir(Xs: np.ndarray, steps: int = 4, rule: int = 90) -> np.ndarray:
    """Binarize -> evolve CA rule -> concatenate all steps."""
    Xb = (Xs > 0).astype(np.int64)
    states, s = [Xb], Xb
    for _ in range(steps):
        s = _ca_step(s, rule)
        states.append(s)
    return np.concatenate(states, axis=1).astype(np.float32)


def fit_ca(Xtr: np.ndarray, Ytr: np.ndarray,
           steps: int = 4, rule: int = 90, alpha: float = 1.0):
    """Fit scaler + ridge readout in log-period space. Returns (scaler, head)."""
    scaler = StandardScaler().fit(Xtr)
    head   = Ridge(alpha=alpha)
    head.fit(_reservoir(scaler.transform(Xtr), steps, rule), np.log(Ytr + 1e-8))
    return scaler, head


def predict_ca(scaler, head, Xte: np.ndarray,
               steps: int = 4, rule: int = 90) -> np.ndarray:
    """Return raw-period predictions."""
    return np.exp(head.predict(_reservoir(scaler.transform(Xte), steps, rule)))


# ============================================================================
# Main prediction loop
# ============================================================================
def run(args):
    os.makedirs(args.output_dir, exist_ok=True)
    split = getattr(args, "split", "group")

    print("=" * 70)
    print(f"{getattr(args, 'model', 'ca').upper()} predictor | seq_len={args.seq_len} | "
          f"knowledge=ON | folds={args.folds} | split={split} | "
          f"rule={args.rule} | steps={args.steps}")
    print("=" * 70)

    X, Y, meta = build_dataset(
        args.csv_dir, args.config_path,
        args.seq_len, args.feature_mode,
        use_knowledge=True,
    )
    splits = make_folds(meta, args.folds, split)
    describe_folds(meta, splits)

    model = getattr(args, "model", "ca")
    all_fold_dfs = []
    for fold, (tr_idx, val_idx) in enumerate(splits, start=1):
        Xtr, Ytr, Xte = X[tr_idx], Y[tr_idx], X[val_idx]
        if model == "ca":
            scaler, head = fit_ca(Xtr, Ytr, steps=args.steps, rule=args.rule, alpha=args.alpha)
            raw = predict_ca(scaler, head, Xte, args.steps, args.rule)
        elif model == "mlp":
            raw = train_dnn(Xtr, Ytr, Xte, epochs=getattr(args, "epochs", 200))
        elif model == "svr":
            raw = train_sklearn(SVR(kernel="rbf", C=10.0), Xtr, Ytr, Xte)
        elif model == "bayes":
            raw = train_sklearn(BayesianRidge(), Xtr, Ytr, Xte)
        elif model == "ais":
            raw = train_ais(Xtr, Ytr, Xte)
        else:
            raise ValueError(f"unknown --model {model}")
        preds = np.maximum(np.round(raw), 1.0)     # periods are integer ms

        rows = []
        for rank, i in enumerate(val_idx):
            pred_list = preds[rank].tolist()
            true_list = Y[i].tolist()
            log_mse = float(np.mean(
                (np.log(np.array(pred_list) + 1e-8) -
                 np.log(np.array(true_list) + 1e-8)) ** 2
            ))
            rows.append({
                "idx":         int(i),
                "fold":        fold,
                "source_file": meta[i]["file"],
                "scheduler":   meta[i]["scheduler"],
                "group":       meta[i]["group"],
                "config":      meta[i]["config"],
                "prediction":  str([float(v) for v in pred_list]),
                "true_value":  str([float(v) for v in true_list]),
                "modality_3":  str(meta[i]["modality_3"]),
                "mse_error":   round(log_mse, 6),
            })

        fold_df = pd.DataFrame(rows)
        fname   = os.path.join(args.output_dir, f"val_predictions_{model}_{fold}.csv")
        fold_df.to_csv(fname, index=False)
        print(f"  Fold {fold:>2}/{len(splits)} | val={len(val_idx):>4} samples "
              f"| MSE(log)={fold_df['mse_error'].mean():.6f} | -> {os.path.basename(fname)}")
        all_fold_dfs.append(fold_df)

    combined = pd.concat(all_fold_dfs, ignore_index=True)
    agg = combined["mse_error"].agg(["mean", "std", "min", "max", "median"])
    exact = float((combined["mse_error"] == 0).mean())
    print("\n" + "=" * 70)
    print(f"{len(splits)}-fold CV summary | MSE(log): "
          f"mean={agg['mean']:.6f}  std={agg['std']:.6f}  median={agg['median']:.6f}  "
          f"min={agg['min']:.6f}  max={agg['max']:.6f}")
    print(f"exact period matches (MSE=0): {int((combined['mse_error']==0).sum())}/"
          f"{len(combined)} = {100*exact:.2f}%")
    print(f"CSVs saved to: {args.output_dir}/")
    print("=" * 70)


def build_argparser(parent=None):
    """Return argparser for the predict mode (shared with baseline.py --mode predict)."""
    ap = parent or argparse.ArgumentParser(
        description="CA grouped 10-fold CV prediction CSVs for assessment.py")
    ap.add_argument("--csv_dir",      default="log_attacker/,log_attacker_l/")
    ap.add_argument("--config_path",  default="task_configs.json,task_configs_l.json")
    ap.add_argument("--output_dir",   default="results/")
    ap.add_argument("--seq_len",      type=int,   default=256, choices=[256, 512])
    ap.add_argument("--feature_mode", default="relative", choices=["relative", "raw"])
    ap.add_argument("--folds",        type=int,   default=10)
    ap.add_argument("--split",        default="group", choices=["group", "random"])
    ap.add_argument("--model",        default="ca", choices=list(MODELS),
                    help="predictor written to the fold CSVs (ca | mlp | svr | bayes | ais)")
    ap.add_argument("--epochs",       type=int,   default=200, help="[mlp] training epochs")
    ap.add_argument("--rule",         type=int,   default=90)
    ap.add_argument("--steps",        type=int,   default=4)
    ap.add_argument("--alpha",        type=float, default=1.0)
    return ap


if __name__ == "__main__":
    run(build_argparser().parse_args())
