"""
profiling.py
============
Model-size and per-sample inference-latency profiler for all baselines.

Reports per model:
    param_count  - learned quantities (family-specific definition, see below)
    size_kb      - serialized footprint (torch.save for DNN, pickle for others)
    latency      - single-sample inference time mean/std/median (ms), with warm-up

Parameter count definitions:
    DNN         -> trainable tensor elements (weights + biases)
    SVR         -> support vectors + dual coefficients + intercepts (all 3 heads)
    Bayesian    -> regression coefficients + intercepts  = (D+1) x 3
    AIS         -> stored antibody floats (repertoire is the model)
    CA          -> linear readout coefficients only (CA rule is a fixed integer)

Standalone:    python profiling.py [flags]
Via baseline:  python baseline.py --mode profile [flags]
"""

import io
import time
import pickle
import argparse

import numpy as np
import torch
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVR
from sklearn.linear_model import BayesianRidge, Ridge
from sklearn.multioutput import MultiOutputRegressor

from baseline import (
    build_dataset,
    MLPRegressor, AISRegressor,
    train_dnn, train_sklearn, train_ais, train_ca,
    _ca_step,
    DEVICE,
)


# ============================================================================
# Uniform fitted-model wrapper
# ============================================================================
class ProfiledModel:
    """Wraps a fitted model with a uniform single-sample predict() interface."""

    def __init__(self, name, scaler, core, predict_log_fn,
                 param_count: int, size_bytes: int):
        self.name        = name
        self.scaler      = scaler
        self.core        = core
        self._predict_log = predict_log_fn
        self.param_count = param_count
        self.size_bytes  = size_bytes

    def predict(self, X_raw: np.ndarray) -> np.ndarray:
        """End-to-end: standardize -> predict -> exp."""
        Xs = self.scaler.transform(X_raw)
        return np.exp(self._predict_log(Xs))


def _pickle_bytes(obj) -> int:
    return len(pickle.dumps(obj))

def _torch_bytes(model) -> int:
    buf = io.BytesIO()
    torch.save(model.state_dict(), buf)
    return buf.tell()


# ============================================================================
# Fit -> ProfiledModel  (train once, then wrap for profiling)
# ============================================================================
def fit_profiled_dnn(X, Y, epochs=200, lr=1e-3) -> ProfiledModel:
    scaler  = StandardScaler().fit(X)
    Xs      = torch.tensor(scaler.transform(X), dtype=torch.float32, device=DEVICE)
    ylog    = torch.tensor(np.log(Y + 1e-8),    dtype=torch.float32, device=DEVICE)
    model   = MLPRegressor(X.shape[1]).to(DEVICE)
    opt     = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    loss_fn = torch.nn.MSELoss()
    model.train()
    for _ in range(epochs):
        opt.zero_grad(); loss_fn(model(Xs), ylog).backward(); opt.step()
    model.eval()

    def predict_log(Xs_np):
        with torch.no_grad():
            return model(torch.tensor(Xs_np, dtype=torch.float32,
                                      device=DEVICE)).cpu().numpy()

    return ProfiledModel(
        "(7) DNN (MLP)", scaler, model, predict_log,
        sum(p.numel() for p in model.parameters()), _torch_bytes(model),
    )


def fit_profiled_svr(X, Y) -> ProfiledModel:
    scaler = StandardScaler().fit(X)
    reg    = MultiOutputRegressor(SVR(kernel="rbf", C=10.0))
    reg.fit(scaler.transform(X), np.log(Y + 1e-8))
    params = sum(
        e.support_vectors_.size + e.dual_coef_.size + e.intercept_.size
        for e in reg.estimators_
    )
    return ProfiledModel(
        "(4) SVM (SVR-RBF)", scaler, reg,
        lambda Xs: reg.predict(Xs), int(params), _pickle_bytes(reg),
    )


def fit_profiled_bayes(X, Y) -> ProfiledModel:
    scaler = StandardScaler().fit(X)
    reg    = MultiOutputRegressor(BayesianRidge())
    reg.fit(scaler.transform(X), np.log(Y + 1e-8))
    params = sum(e.coef_.size + np.size(e.intercept_) for e in reg.estimators_)
    return ProfiledModel(
        "(3) Bayesian (BayesRidge)", scaler, reg,
        lambda Xs: reg.predict(Xs), int(params), _pickle_bytes(reg),
    )


def fit_profiled_ais(X, Y) -> ProfiledModel:
    ais    = AISRegressor().fit(X, np.log(Y + 1e-8))
    scaler = ais.scaler

    def predict_log(Xs_np):
        return ais.predict(scaler.inverse_transform(Xs_np))

    return ProfiledModel(
        "(5) AIS (clonal-select)", scaler, ais, predict_log,
        int(ais.ab_X.size + ais.ab_Y.size), _pickle_bytes(ais),
    )


def fit_profiled_ca(X, Y, rule=90, steps=4) -> ProfiledModel:
    scaler = StandardScaler().fit(X)

    def reservoir(Xs):
        Xb = (Xs > 0).astype(np.int64)
        states, s = [Xb], Xb
        for _ in range(steps):
            s = _ca_step(s, rule); states.append(s)
        return np.concatenate(states, 1).astype(np.float32)

    head = Ridge(alpha=1.0).fit(reservoir(scaler.transform(X)), np.log(Y + 1e-8))
    return ProfiledModel(
        "(6) CA reservoir", scaler, head,
        lambda Xs: head.predict(reservoir(Xs)),
        int(head.coef_.size + np.size(head.intercept_)), _pickle_bytes(head),
    )


# ============================================================================
# Latency measurement
# ============================================================================
def time_inference(model: ProfiledModel, X: np.ndarray,
                   repeats: int, max_samples: int):
    n  = min(len(X), max_samples)
    Xs = X[:n]
    for i in range(min(5, n)):                      # warm-up
        model.predict(Xs[i:i+1])
    times = []
    for _ in range(repeats):
        for i in range(n):
            t0 = time.perf_counter()
            model.predict(Xs[i:i+1])
            times.append(time.perf_counter() - t0)
    t = np.asarray(times) * 1e3                     # ms
    return float(t.mean()), float(t.std()), float(np.median(t)), n


# ============================================================================
# Standalone runner (also called from baseline.py --mode profile)
# ============================================================================
def run(args):
    print("=" * 88)
    print(f"PROFILING | device={DEVICE} | seq_len={args.seq_len} | "
          f"knowledge={'ON' if args.use_knowledge else 'OFF'}")
    print("=" * 88)

    X, Y, _ = build_dataset(args.csv_dir, args.config_path, args.seq_len,
                             args.feature_mode, args.use_knowledge)

    fitters = [
        lambda: fit_profiled_dnn(X, Y, epochs=args.epochs),
        lambda: fit_profiled_svr(X, Y),
        lambda: fit_profiled_bayes(X, Y),
        lambda: fit_profiled_ais(X, Y),
    ]
    if args.with_ca:
        fitters.append(lambda: fit_profiled_ca(X, Y))

    rows = []
    for fit_fn in fitters:
        m = fit_fn()
        mean_ms, std_ms, med_ms, n = time_inference(
            m, X, args.repeats, args.max_time_samples)
        rows.append((m.name, m.param_count, m.size_bytes / 1024.0,
                     mean_ms, std_ms, med_ms))
        print(f"  {m.name:<28} profiled over {n} samples x {args.repeats} passes")

    print("\n" + "=" * 88)
    print(f"INPUT DIM = {X.shape[1]}   (latency includes preprocessing)")
    print("=" * 88)
    print(f"{'Method':<28}{'Params':>12}{'Size (KB)':>12}"
          f"{'Lat mean(ms)':>14}{'Lat std':>10}{'Lat med':>10}")
    print("-" * 88)
    for name, params, size_kb, mean_ms, std_ms, med_ms in rows:
        print(f"{name:<28}{params:>12,}{size_kb:>12.1f}"
              f"{mean_ms:>14.4f}{std_ms:>10.4f}{med_ms:>10.4f}")
    print("=" * 88)


def build_argparser(parent=None):
    ap = parent or argparse.ArgumentParser(
        description="Size & latency profiler for all baselines.")
    ap.add_argument("--csv_dir",          default="log_attacker/,log_attacker_l/")
    ap.add_argument("--config_path",      default="task_configs.json,task_configs_l.json")
    ap.add_argument("--seq_len",          type=int, default=256, choices=[256, 512])
    ap.add_argument("--feature_mode",     default="relative", choices=["relative", "raw"])
    ap.add_argument("--epochs",           type=int, default=200)
    ap.add_argument("--repeats",          type=int, default=3)
    ap.add_argument("--max_time_samples", type=int, default=200)
    ap.add_argument("--use-knowledge",    action="store_true")
    ap.add_argument("--with-ca",          action="store_true")
    return ap


if __name__ == "__main__":
    run(build_argparser().parse_args())
