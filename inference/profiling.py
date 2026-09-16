"""
profiling.py
============
Model-size and per-sample inference-latency profiler for the baselines defined
in baseline.py. This is a *cost* profiler, not an accuracy evaluation — for MSE
use baseline.py.

For each applicable method it reports:
  * param_count : learned quantities, defined per model family
        DNN         -> number of trainable tensor elements (weights+biases)
        SVM (SVR)   -> support vectors + dual coeffs + intercepts (summed over 3 heads)
        Bayesian    -> regression coefficients + intercepts ((D+1) * 3)
        AIS         -> stored antibody floats (repertoire is the "model")
        CA reservoir-> linear-readout coefficients (the CA rule itself is fixed)
  * size_kb     : uniform serialized footprint (torch.save for the DNN,
                  pickle for the rest) — comparable across families
  * latency     : single-sample inference time, mean/std/median in ms,
                  measured end-to-end (scaler.transform + predict), with warm-up

Usage
-----
    python profiling.py --csv_dir log_synth/ --config_path task_configs.json
    python profiling.py --csv_dir log_attacker/ --use-knowledge --with-ca --repeats 5
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

# reuse model code / data loading from baseline.py (no duplication)
from baseline import build_dataset, MLPRegressor, AISRegressor, _ca_step, DEVICE


# ============================================================================
# Uniform profiled-model wrapper
# ============================================================================
class ProfiledModel:
    """Holds a fitted core + scaler and a per-method log-space predict closure."""

    def __init__(self, name, scaler, core, predict_log, param_count, size_bytes, is_torch=False):
        self.name = name
        self.scaler = scaler
        self.core = core
        self._predict_log = predict_log      # fn(Xs) -> log-period array
        self.param_count = param_count
        self.size_bytes = size_bytes
        self.is_torch = is_torch

    def predict(self, X_raw):
        Xs = self.scaler.transform(X_raw)
        return np.exp(self._predict_log(Xs))


def _pickle_bytes(obj) -> int:
    return len(pickle.dumps(obj))


def _torch_bytes(model) -> int:
    buf = io.BytesIO()
    torch.save(model.state_dict(), buf)
    return buf.tell()


# ============================================================================
# Fit functions -> ProfiledModel  (mirror baseline.py training)
# ============================================================================
def fit_dnn(X, Y, epochs=200, lr=1e-3):
    scaler = StandardScaler().fit(X)
    Xs = torch.tensor(scaler.transform(X), dtype=torch.float32, device=DEVICE)
    ylog = torch.tensor(np.log(Y + 1e-8), dtype=torch.float32, device=DEVICE)
    model = MLPRegressor(X.shape[1]).to(DEVICE)
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    loss_fn = torch.nn.MSELoss()
    model.train()
    for _ in range(epochs):
        opt.zero_grad(); loss_fn(model(Xs), ylog).backward(); opt.step()
    model.eval()

    def predict_log(Xs_np):
        with torch.no_grad():
            t = torch.tensor(Xs_np, dtype=torch.float32, device=DEVICE)
            return model(t).cpu().numpy()

    params = sum(p.numel() for p in model.parameters())
    return ProfiledModel("(7) DNN (MLP)", scaler, model, predict_log,
                         params, _torch_bytes(model), is_torch=True)


def _fit_sklearn(name, estimator, X, Y):
    scaler = StandardScaler().fit(X)
    reg = MultiOutputRegressor(estimator).fit(scaler.transform(X), np.log(Y + 1e-8))

    # param count per family
    params = 0
    for est in reg.estimators_:
        if hasattr(est, "support_vectors_"):                     # SVR
            params += est.support_vectors_.size + est.dual_coef_.size + est.intercept_.size
        elif hasattr(est, "coef_"):                              # BayesianRidge
            params += est.coef_.size + np.size(est.intercept_)
    return ProfiledModel(name, scaler, reg,
                         lambda Xs: reg.predict(Xs),
                         int(params), _pickle_bytes(reg))


def fit_svr(X, Y):
    return _fit_sklearn("(4) SVM (SVR-RBF)", SVR(kernel="rbf", C=10.0), X, Y)


def fit_bayes(X, Y):
    return _fit_sklearn("(3) Bayesian (BayesRidge)", BayesianRidge(), X, Y)


def fit_ais(X, Y):
    ais = AISRegressor().fit(X, np.log(Y + 1e-8))
    # the fitted scaler lives inside ais; wrap so predict() interface is uniform
    scaler = ais.scaler

    def predict_log(Xs_np):
        # ais.predict expects RAW X (it scales internally); invert the outer scale
        X_raw = scaler.inverse_transform(Xs_np)
        return ais.predict(X_raw)

    params = ais.ab_X.size + ais.ab_Y.size                       # stored repertoire
    return ProfiledModel("(5) AIS (clonal-select)", scaler, ais, predict_log,
                         int(params), _pickle_bytes(ais))


def fit_ca(X, Y, rule=90, steps=4):
    scaler = StandardScaler().fit(X)

    def reservoir(Xs):
        Xb = (Xs > 0).astype(np.int64)
        states, s = [Xb], Xb
        for _ in range(steps):
            s = _ca_step(s, rule); states.append(s)
        return np.concatenate(states, 1).astype(np.float32)

    head = Ridge(alpha=1.0).fit(reservoir(scaler.transform(X)), np.log(Y + 1e-8))
    params = head.coef_.size + np.size(head.intercept_)          # readout only
    return ProfiledModel("(6) CA reservoir [exp]", scaler, head,
                         lambda Xs: head.predict(reservoir(Xs)),
                         int(params), _pickle_bytes(head))


# ============================================================================
# Latency measurement
# ============================================================================
def time_inference(model, X, repeats, max_samples):
    n = min(len(X), max_samples)
    Xs = X[:n]
    for i in range(min(5, n)):                # warm-up
        model.predict(Xs[i:i + 1])
    times = []
    for _ in range(repeats):
        for i in range(n):
            t0 = time.perf_counter()
            model.predict(Xs[i:i + 1])
            times.append(time.perf_counter() - t0)
    t = np.asarray(times) * 1e3               # ms
    return t.mean(), t.std(), np.median(t), n


def main():
    ap = argparse.ArgumentParser(description="Size & per-sample latency profiler for baselines.")
    ap.add_argument("--csv_dir", default="log_attacker/")
    ap.add_argument("--config_path", default="task_configs.json")
    ap.add_argument("--seq_len", type=int, default=256, choices=[256, 512])
    ap.add_argument("--feature_mode", default="relative", choices=["relative", "raw"])
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--repeats", type=int, default=3, help="timing passes over the sample set")
    ap.add_argument("--max_time_samples", type=int, default=200,
                    help="cap on #samples timed per pass (for speed)")
    ap.add_argument("--use-knowledge", action="store_true")
    ap.add_argument("--with-ca", action="store_true")
    args = ap.parse_args()

    print("=" * 88)
    print(f"PROFILING | device={DEVICE} | seq_len={args.seq_len} | "
          f"knowledge={'ON' if args.use_knowledge else 'OFF'}")
    print("=" * 88)

    X, Y, _ = build_dataset(args.csv_dir, args.config_path, args.seq_len,
                            args.feature_mode, args.use_knowledge)

    fitters = [
        lambda: fit_dnn(X, Y, epochs=args.epochs),
        lambda: fit_svr(X, Y),
        lambda: fit_bayes(X, Y),
        lambda: fit_ais(X, Y),
    ]
    if args.with_ca:
        fitters.append(lambda: fit_ca(X, Y))

    rows = []
    for fit in fitters:
        model = fit()
        mean_ms, std_ms, med_ms, n = time_inference(
            model, X, args.repeats, args.max_time_samples)
        rows.append((model.name, model.param_count, model.size_bytes / 1024.0,
                     mean_ms, std_ms, med_ms))
        print(f">>> {model.name:<26} profiled over {n} samples x {args.repeats} passes")

    print("\n" + "=" * 88)
    print(f"INPUT DIM = {X.shape[1]}   (per-sample latency includes preprocessing)")
    print("=" * 88)
    print(f"{'Method':<26}{'Params':>12}{'Size (KB)':>12}"
          f"{'Lat mean(ms)':>14}{'Lat std':>10}{'Lat med':>10}")
    print("-" * 88)
    for name, params, size_kb, mean_ms, std_ms, med_ms in rows:
        print(f"{name:<26}{params:>12,}{size_kb:>12.1f}"
              f"{mean_ms:>14.4f}{std_ms:>10.4f}{med_ms:>10.4f}")
    print("=" * 88)


if __name__ == "__main__":
    main()