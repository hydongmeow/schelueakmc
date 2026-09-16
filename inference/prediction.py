"""

Train the CA-reservoir (best-performing model, knowledge=ON) using
10-fold CV and save one prediction CSV per validation fold, compatible with
assessment.py's expected format and directory layout.

Output layout (one file per fold, saved to --output_dir):
    val_predictions_ca_{fold}.csv
    columns: idx, prediction, true_value, modality_3, mse_error

Usage
-----
    python prediction.py --csv_dir log_attacker/ --config_path task_configs.json
    python predict_ca.py --csv_dir log_attacker/ --output_dir results/ --seq_len 512
"""

import os
import glob
import argparse

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import KFold

# reuse data-loading and CA helpers from baseline.py (no duplication)
from baseline import (
    load_flat_series,
    build_knowledge_features,
    build_dataset,
    _ca_step,
    CRITICAL_IDS,
)
from processing import encode_sample_from_csv

np.random.seed(42)


# ============================================================================
# modality_3 reconstruction
# ============================================================================
def build_modality3(csv_path: str, config_path: str) -> list:
    """
    Return the 9-element modality_3 vector in the exact layout that
    assessment.py's parse_modality3 / process_sample expect:

        [obs_wcet, obs_id, obs_period,
         task1_id, task1_wcet,
         task2_id, task2_wcet,
         task3_id, task3_wcet]

    encode_sample_from_csv (processing.py) already assembles this vector
    identically to how __getitem__ builds modality_3 in MultiModalRTOSDataset,
    so reusing it guarantees consistency with the rest of the pipeline.
    """
    features, _ = encode_sample_from_csv(config_path, csv_path)  # ndarray (9,)
    return [float(v) for v in features]


# ============================================================================
# Per-sample modality_3 list aligned to build_dataset's row order
# ============================================================================
def collect_modality3(csv_dir: str, config_path: str, seq_len: int) -> list:
    """
    Return modality_3 vectors in the same row order as build_dataset's X/Y
    arrays by replicating its exact glob + skip logic.

    A row is included only if ALL three of the following succeed
    (mirroring the try/except in build_dataset):
        1. parse_variation_from_config  (label extraction)
        2. load_flat_series             (time-series feature)
        3. build_knowledge_features     (scheduler + static features)
    If any one fails, the row is skipped — identical to build_dataset.
    """
    csv_files = sorted(glob.glob(os.path.join(csv_dir, "*.csv")))
    m3_list = []
    for path in csv_files:
        try:
            m3 = build_modality3(path, config_path)
            load_flat_series(path, seq_len)
            build_knowledge_features(path, config_path)
        except Exception:
            continue
        m3_list.append(m3)
    return m3_list


# ============================================================================
# CA reservoir (mirrors train_ca in baseline.py, returns fitted objects)
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
    """
    Fit CA reservoir + ridge readout on training data (log-period space).
    Returns (scaler, head) — the two objects needed at inference time.
    """
    scaler = StandardScaler().fit(Xtr)
    head = Ridge(alpha=alpha)
    head.fit(_reservoir(scaler.transform(Xtr), steps, rule),
             np.log(Ytr + 1e-8))
    return scaler, head


def predict_ca(scaler, head, Xte: np.ndarray,
               steps: int = 4, rule: int = 90) -> np.ndarray:
    """Return raw-period predictions (exp of ridge log-period output)."""
    r = _reservoir(scaler.transform(Xte), steps, rule)
    return np.exp(head.predict(r))          # (N, 3)


# ============================================================================
# Main: 10-fold CV prediction loop
# ============================================================================
def run(args):
    os.makedirs(args.output_dir, exist_ok=True)

    print("=" * 70)
    print(f"CA predictor | seq_len={args.seq_len} | knowledge=ON | "
          f"folds={args.folds} | rule={args.rule} | steps={args.steps}")
    print("=" * 70)

    # ── load features and labels (knowledge=ON, best config from eval) ──────
    X, Y, _ = build_dataset(
        args.csv_dir, args.config_path,
        args.seq_len, args.feature_mode,
        use_knowledge=True,
    )
    N = len(X)

    # ── modality_3 in the same row order as X/Y ──────────────────────────────
    m3_list = collect_modality3(args.csv_dir, args.config_path, args.seq_len)
    assert len(m3_list) == N, (
        f"modality_3 count ({len(m3_list)}) != dataset size ({N}). "
        "Both must use the same --csv_dir and --config_path."
    )

    # ── 10-fold CV: train on train folds, predict on val folds ───────────────
    kf = KFold(n_splits=args.folds, shuffle=True, random_state=42)
    all_fold_dfs = []

    for fold, (tr_idx, val_idx) in enumerate(kf.split(X), start=1):
        Xtr, Ytr = X[tr_idx], Y[tr_idx]
        Xte, Yte = X[val_idx], Y[val_idx]

        scaler, head = fit_ca(Xtr, Ytr,
                              steps=args.steps, rule=args.rule, alpha=args.alpha)
        preds = predict_ca(scaler, head, Xte, steps=args.steps, rule=args.rule)

        # round to nearest integer period (mirrors _maybe_round in model.py)
        preds_rounded = np.maximum(np.round(preds), 1.0)

        # ── build fold DataFrame in assessment.py's exact format ─────────────
        rows = []
        for rank, i in enumerate(val_idx):
            pred_list = preds_rounded[rank].tolist()
            true_list = Yte[rank].tolist()
            m3        = m3_list[i]

            # log-MSE: matches compute_log_mse inside assessment.py
            log_mse = float(np.mean(
                (np.log(np.array(pred_list) + 1e-8) -
                 np.log(np.array(true_list) + 1e-8)) ** 2
            ))

            rows.append({
                "idx":        i,
                "prediction": str([float(v) for v in pred_list]),
                "true_value": str([float(v) for v in true_list]),
                "modality_3": str([float(v) for v in m3]),
                "mse_error":  round(log_mse, 6),
            })

        fold_df = pd.DataFrame(rows)
        fname   = os.path.join(args.output_dir, f"val_predictions_ca_{fold}.csv")
        fold_df.to_csv(fname, index=False)

        fold_mse = float(fold_df["mse_error"].mean())
        print(f"  Fold {fold:>2}/{args.folds} | val samples={len(val_idx):>4} "
              f"| mean MSE(log)={fold_mse:.6f} | -> {os.path.basename(fname)}")

        all_fold_dfs.append(fold_df)

    # ── aggregate across all folds ────────────────────────────────────────────
    combined = pd.concat(all_fold_dfs, ignore_index=True)
    agg = combined["mse_error"].agg(["mean", "std", "min", "max"])

    print("\n" + "=" * 70)
    print("10-fold CV summary | MSE(log):")
    print(f"  mean={agg['mean']:.6f}  std={agg['std']:.6f}  "
          f"min={agg['min']:.6f}  max={agg['max']:.6f}")
    print(f"  {args.folds} CSVs saved to: {args.output_dir}/")
    print("=" * 70)


def main():
    ap = argparse.ArgumentParser(
        description="CA baseline: 10-fold CV prediction CSVs for assessment.py")
    ap.add_argument("--csv_dir",      default="log_attacker/")
    ap.add_argument("--config_path",  default="task_configs.json")
    ap.add_argument("--output_dir",   default="results/",
                    help="where to write val_predictions_ca_*.csv")
    ap.add_argument("--seq_len",      type=int,   default=256, choices=[256, 512])
    ap.add_argument("--feature_mode", default="relative", choices=["relative", "raw"])
    ap.add_argument("--folds",        type=int,   default=10)
    ap.add_argument("--rule",         type=int,   default=90,
                    help="elementary CA rule number (default: 90)")
    ap.add_argument("--steps",        type=int,   default=4,
                    help="CA evolution steps (default: 4)")
    ap.add_argument("--alpha",        type=float, default=1.0,
                    help="ridge readout regularisation (default: 1.0)")
    args = ap.parse_args()
    run(args)


if __name__ == "__main__":
    main()