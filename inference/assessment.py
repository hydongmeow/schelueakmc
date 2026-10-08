"""
assessment.py
=============
Dual-core schedule reconstruction and vulnerability-window evaluation.

The benchmark traces come from a 2-core globally scheduled RTOS (SimSo), so
the reconstruction is a 2-core global simulation as well.  The attacker
reconstructs the schedule of the K=3 critical tasks from (i) the predicted
periods, (ii) the publicly known WCETs and (iii) the known scheduler type.
The observer/attacker task itself is not simulated: every slot in which fewer
than m cores are occupied by critical tasks is a slot in which the attacker's
task can execute without being preempted, i.e. a vulnerability window.

Scheduler families used for the reconstruction (slot-based, preemptive,
work-conserving, migration allowed):
    EDF, RUN, WC-RUN        -> global EDF ordering (absolute deadline)
    EDZL, EDCL              -> EDF with zero-laxity override (EDCL's critical-
                               laxity promotion coincides with the zero-laxity
                               override at slot granularity)
    G-FL                    -> priority point  r + D - (m-1)/m * C
    G-FL-ZL                 -> G-FL with zero-laxity override
    LLF, NVNLF              -> global least-laxity ordering
    MLLF                    -> least laxity, running job kept on laxity ties

Metrics per sample (all bounded, see paper):
    L_mse      : log-period MSE carried over from the predictor
    eps_P      : symmetric mean absolute percentage error of the periods, in [0,1]
    W          : unified observation window = min(max(H_pred, H_true), W_max)
    w_true/w_pred : longest interval with a free core (vulnerability window), ms
    dW         : |w_pred - w_true|
    sched_err  : fraction of slots whose set of running tasks differs, in [0,1]
    IoU        : intersection-over-union of the free-core masks, in [0,1]
    E_total    : 0.5 * eps_P + 0.5 * (1 - IoU), in [0,1]

Exploitability (needs --budget_ms, the end-to-end attack latency budget):
    truly exploitable : w_true >= budget
    predicted expl.   : w_pred >= budget
    strict TP         : predicted exploitable AND launching the payload at the
                        start of the predicted window is not preempted in the
                        true schedule for at least the budget
    lenient TP        : predicted exploitable AND truly exploitable

Ground truth: by default S_true is the same simulator driven by the true
periods (Algorithm 2 in the paper).  With --full_log_dirs the free-core mask
is additionally derived from the SimSo full-system log and compared with the
simulated one (fidelity check), and --truth log uses it as S_true instead.

Standalone usage:
    python assessment.py --results_dir results/ --budget_ms 0.25 \
        --full_log_dirs log_full/,log_full_l/ --plot_dir figs/
"""

import os
import re
import ast
import csv
import glob
import json
import math
import time
import argparse
from functools import reduce

import sys
import numpy as np
import pandas as pd

try:                                     # Windows consoles default to cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DT_MS    = 0.1      # slot resolution [ms]
M_CORES  = 2
W_MAX_MS = 100.0    # hard cap on the observation window [ms]
CYCLES_PER_MS = 1_000_000.0   # SimSo default

SCHED_FAMILY = {
    "EDF": "edf", "RUN": "edf", "WCRUN": "edf",
    "EDZL": "edzl", "EDCL": "edzl",
    "GFL": "gfl", "GFLZL": "gflzl",
    "LLF": "llf", "NVNLF": "llf",
    "MLLF": "mllf",
}


# ============================================================================
# 1. Data pre-processing
# ============================================================================
def _parse_list(x):
    return list(ast.literal_eval(x)) if isinstance(x, str) else list(x)


def process_list(df: pd.DataFrame) -> pd.DataFrame:
    """Parse list columns and (re)compute log-MSE per row."""
    df = df.copy()
    for col in ("prediction", "true_value", "modality_3"):
        df[col] = df[col].apply(_parse_list)

    def _log_mse(row):
        pred = np.array(row["prediction"], dtype=np.float64)
        true = np.array(row["true_value"],  dtype=np.float64)
        return float(np.mean((np.log(pred + 1e-8) - np.log(true + 1e-8)) ** 2))

    df["mse_error"] = df.apply(_log_mse, axis=1)
    return df


def load_results(results_dir: str) -> pd.DataFrame:
    files = sorted(glob.glob(os.path.join(results_dir, "val_predictions_*.csv")))
    if not files:
        raise FileNotFoundError(f"No val_predictions_*.csv in {results_dir}")
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    for col, default in (("source_file", ""), ("scheduler", "EDF"),
                         ("group", ""), ("config", ""), ("fold", -1)):
        if col not in df.columns:
            df[col] = default
    # a mixed results/ dir (e.g. old val_predictions_ca_*.csv without these
    # columns next to new files) leaves NaN in string columns -> coerce so
    # downstream .replace()/.get() never see a float
    for col, default in (("source_file", ""), ("scheduler", "EDF"),
                         ("group", ""), ("config", "")):
        df[col] = df[col].fillna(default).astype(str)
    n_no_src = int((df["source_file"] == "").sum())
    if n_no_src:
        print(f"  [warn] {n_no_src} row(s) lack a source_file (likely stale CSVs "
              f"from an older run); their scheduler/fidelity default and no full "
              f"log is matched. Clear results/ of old predictions to avoid this.")
    return process_list(df)


# ============================================================================
# 2. Observation window
# ============================================================================
def _lcm(a, b):
    return a * b // math.gcd(a, b)


def hyperperiod(periods) -> int:
    return reduce(_lcm, [max(1, int(round(p))) for p in periods])


def observation_window(pred_periods, true_periods, w_max: float = W_MAX_MS) -> float:
    """W = min(max(H_pred, H_true), W_max)   (Algorithm 2)."""
    return float(min(max(hyperperiod(pred_periods), hyperperiod(true_periods)), w_max))


# ============================================================================
# 3. Dual-core global schedule simulator
# ============================================================================
def simulate_global(periods, wcets, window_ms, scheduler="EDF",
                    m: int = M_CORES, dt: float = DT_MS) -> dict:
    """
    Slot-based global preemptive simulation of implicit-deadline periodic
    tasks on m identical cores, all released at t = 0.

    Returns dict:
        cores : (m, W) int16 array, task index (0-based) per core/slot or -1
        busy  : (W,)   number of busy cores per slot
        W, dt, m
    """
    fam = SCHED_FAMILY.get(str(scheduler).upper(), "edf")
    n   = len(periods)
    P   = [max(1, int(round(p / dt))) for p in periods]
    C   = [max(0, int(round(c / dt))) for c in wcets]
    W   = int(round(window_ms / dt))

    rem, rel, dl = [0] * n, [0] * n, [0] * n
    core_of = [-1] * n
    cores   = np.full((m, W), -1, dtype=np.int16)
    busy    = np.zeros(W, dtype=np.int8)
    running = set()

    def prio(i, t):
        lax = dl[i] - t - rem[i]
        if fam == "edf":
            return (dl[i], i)
        if fam == "edzl":
            return (0 if lax <= 0 else 1, dl[i], i)
        if fam == "gfl":
            return (rel[i] + P[i] - (m - 1) / m * C[i], i)
        if fam == "gflzl":
            return (0 if lax <= 0 else 1, rel[i] + P[i] - (m - 1) / m * C[i], i)
        if fam == "llf":
            return (lax, dl[i], i)
        if fam == "mllf":
            return (lax, 0 if i in running else 1, dl[i], i)
        return (dl[i], i)

    for t in range(W):
        for i in range(n):
            if t % P[i] == 0:                      # job release
                rem[i], rel[i], dl[i] = C[i], t, t + P[i]
        ready  = [i for i in range(n) if rem[i] > 0]
        chosen = sorted(ready, key=lambda i: prio(i, t))[:m]
        chosen_set = set(chosen)
        for i in running - chosen_set:             # preempted or finished
            core_of[i] = -1
        occupied = {core_of[i] for i in chosen if core_of[i] >= 0}
        free     = [c for c in range(m) if c not in occupied]
        for i in chosen:
            if core_of[i] < 0:                     # (re)dispatch, may migrate
                core_of[i] = free.pop(0)
            cores[core_of[i], t] = i
            rem[i] -= 1
        running  = chosen_set
        busy[t]  = len(chosen)

    return {"cores": cores, "busy": busy, "W": W, "dt": dt, "m": m}


# ============================================================================
# 4. Metrics
# ============================================================================
def free_mask(sim) -> np.ndarray:
    """True where at least one core is not running a critical task."""
    return sim["busy"] < sim["m"]


def longest_run(mask: np.ndarray):
    """(length, start) of the longest run of True in mask (slots)."""
    best_len = best_start = 0
    cur_len = cur_start = 0
    for t, v in enumerate(mask):
        if v:
            if cur_len == 0:
                cur_start = t
            cur_len += 1
            if cur_len > best_len:
                best_len, best_start = cur_len, cur_start
        else:
            cur_len = 0
    return best_len, best_start


def run_from(mask: np.ndarray, start: int) -> int:
    """Number of consecutive True slots starting at `start`."""
    n = 0
    for v in mask[start:]:
        if not v:
            break
        n += 1
    return n


def sched_error(sim_a, sim_b) -> float:
    """Fraction of slots whose set of running tasks differs (core-agnostic)."""
    a = np.sort(sim_a["cores"], axis=0)
    b = np.sort(sim_b["cores"], axis=0)
    return float(np.mean(np.any(a != b, axis=0)))


def iou(mask_a: np.ndarray, mask_b: np.ndarray) -> float:
    union = np.logical_or(mask_a, mask_b).sum()
    if union == 0:
        return 1.0
    return float(np.logical_and(mask_a, mask_b).sum() / union)


def smape(pred, true) -> float:
    p = np.asarray(pred, dtype=np.float64); t = np.asarray(true, dtype=np.float64)
    return float(np.mean(np.abs(p - t) / (np.abs(p) + np.abs(t) + 1e-12)))


# ============================================================================
# 5. Ground truth from the SimSo full-system log (optional)
# ============================================================================
_LOG_RE = re.compile(r"\('(?P<name>.+?) (?P<ev>Executing on Core (?P<core>\d+)|Terminated\.|Preempted!.*?)'")


def _task_names(config_path: str, variation: int):
    with open(config_path) as fh:
        v = json.load(fh)["variations"][f"variation_{variation}"]
    name_to_id = {t["name"]: int(t["identifier"]) for t in v["tasks"]}
    return name_to_id, v["observer_task"]["name"]


def timeline_from_log(full_log_path: str, config_path: str, variation: int,
                      window_ms: float, m: int = M_CORES, dt: float = DT_MS) -> dict:
    """Free-core ground truth derived from the SimSo full-system log."""
    name_to_id, obs_name = _task_names(config_path, variation)
    W      = int(round(window_ms / dt))
    cores  = np.full((m, W), -1, dtype=np.int16)
    active = {}
    with open(full_log_path, newline="") as fh:
        for row in csv.DictReader(fh):
            t_ms = int(row["Timestamp_ms"]) / CYCLES_PER_MS
            mm   = _LOG_RE.match(row["Task_Log"])
            if mm is None:
                continue
            inst = mm.group("name")
            base = inst.rsplit("_", 1)[0]          # strip job number
            if base == obs_name or base not in name_to_id:
                continue
            if mm.group("core") is not None:
                active[inst] = (t_ms, int(mm.group("core")) - 1)
            elif inst in active:
                s_ms, core = active.pop(inst)
                s = int(round(s_ms / dt)); e = min(W, int(round(t_ms / dt)))
                if s < W:
                    cores[core, s:e] = name_to_id[base] - 1
            if t_ms / dt > W + 1 and not active:
                break
    busy = (cores >= 0).sum(axis=0).astype(np.int8)
    return {"cores": cores, "busy": busy, "W": W, "dt": dt, "m": m}


_RENAMED = {"GFLZL": "G_FL_ZL", "GFL": "G_FL", "WCRUN": "WC_RUN"}


def find_full_log(source_file: str, full_log_dirs) -> str:
    """Locate the SimSo full-system log that produced an attacker CSV.
    clean_data.fix_scheduler_names renames attacker_G_FL_ZL_* to attacker_GFLZL_*
    etc., so both spellings are tried."""
    base = source_file.replace("attacker_", "", 1)
    candidates = [base]
    for short, long_ in _RENAMED.items():
        if base.startswith(short + "_variation"):
            candidates.append(long_ + base[len(short):])
    for d in full_log_dirs:
        for c in candidates:
            p = os.path.join(d, "full_system_" + c)
            if os.path.exists(p):
                return p
    return ""


# ============================================================================
# 6. Per-sample processing
# ============================================================================
def process_sample(row, budget_ms=None, w_max=W_MAX_MS, full_log_dirs=(),
                   truth="sim", m=M_CORES, dt=DT_MS) -> dict:
    pred_periods = [float(v) for v in row["prediction"]]
    true_periods = [float(v) for v in row["true_value"]]
    m3           = [float(v) for v in row["modality_3"]]
    wcets        = [m3[4], m3[6], m3[8]]
    scheduler    = str(row.get("scheduler", "EDF"))
    mse          = float(row["mse_error"])

    # --- attacker-side reconstruction (timed: this is L_scheduler-recon) ---
    t0 = time.perf_counter()
    W  = observation_window(pred_periods, true_periods, w_max)
    sim_pred  = simulate_global(pred_periods, wcets, W, scheduler, m, dt)
    mask_pred = free_mask(sim_pred)
    Lp, sp    = longest_run(mask_pred)
    recon_ms  = (time.perf_counter() - t0) * 1e3

    # --- ground truth ------------------------------------------------------
    sim_true  = simulate_global(true_periods, wcets, W, scheduler, m, dt)
    fidelity_busy = fidelity_iou = np.nan
    sim_log = None
    if full_log_dirs and row.get("source_file", ""):
        path = find_full_log(row["source_file"], full_log_dirs)
        cfg  = row.get("config", "") or ""
        var  = int(re.search(r"variation_(\d+)", row["source_file"]).group(1))
        if path and cfg and os.path.exists(cfg):
            sim_log = timeline_from_log(path, cfg, var, W, m, dt)
            fidelity_busy = float(np.mean(sim_log["busy"] == sim_true["busy"]))
            fidelity_iou  = iou(free_mask(sim_log), free_mask(sim_true))
    if truth == "log" and sim_log is not None:
        sim_true = sim_log
    mask_true = free_mask(sim_true)
    Lt, st    = longest_run(mask_true)

    # --- metrics -----------------------------------------------------------
    eps_p  = smape(pred_periods, true_periods)
    s_err  = sched_error(sim_pred, sim_true)
    j      = iou(mask_pred, mask_true)
    E      = 0.5 * eps_p + 0.5 * (1.0 - j)
    w_true, w_pred = Lt * dt, Lp * dt

    out = {
        "idx": row.get("idx", -1), "fold": row.get("fold", -1),
        "source_file": row.get("source_file", ""), "scheduler": scheduler,
        "group": row.get("group", ""),
        "pred_periods": pred_periods, "true_periods": true_periods, "wcets": wcets,
        "W": W, "mse": mse, "eps_P": eps_p,
        "w_true": w_true, "w_pred": w_pred, "W_delta": abs(w_pred - w_true),
        "t_pred_start": sp * dt, "t_true_start": st * dt,
        "sched_err": s_err, "iou": j, "E_total": E,
        "preempt_true": bool((sim_true["busy"] >= m).any()),
        "recon_ms": recon_ms,
        "fidelity_busy": fidelity_busy, "fidelity_iou": fidelity_iou,
        "_sim_pred": sim_pred, "_sim_true": sim_true,
    }

    if budget_ms is not None:
        b = max(1, int(math.ceil(budget_ms / dt)))
        truly = Lt >= b
        pred  = Lp >= b
        launch_ok = bool(pred and run_from(mask_true, sp) >= b)
        out.update({"budget_ms": budget_ms, "truly_exploitable": bool(truly),
                    "pred_exploitable": bool(pred), "launch_ok": launch_ok})
    return out


# ============================================================================
# 7. Plotting (2-core Gantt, inferred vs true)
# ============================================================================
def _segments(track):
    """Yield (task, start, end) runs from a 1-D track of task ids / -1."""
    start, cur = 0, track[0] if len(track) else -1
    for t in range(1, len(track) + 1):
        v = track[t] if t < len(track) else None
        if v != cur:
            if cur >= 0:
                yield int(cur), start, t
            start, cur = t, v


def plot_example(res: dict, out_path: str, budget_ms=None, title_extra=""):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    sim_p, sim_t = res["_sim_pred"], res["_sim_true"]
    dt, m = sim_p["dt"], sim_p["m"]
    colors = ["#1f77b4", "#ff7f0e", "#2ca02c"]
    fig, axes = plt.subplots(2, 1, sharex=True, figsize=(8.5, 4.6))
    for ax, sim, name in ((axes[0], sim_p, "Inferred"), (axes[1], sim_t, "True")):
        fm = free_mask(sim)
        for _, s, e in _segments(fm.astype(np.int16) - 1 + 1 * fm):   # runs of free slots
            pass
        # shade vulnerability windows
        cur, start = False, 0
        for t in range(sim["W"] + 1):
            v = fm[t] if t < sim["W"] else None
            if v != cur:
                if cur:
                    ax.axvspan(start * dt, t * dt, color="#d9f2d9", zorder=0)
                cur, start = v, t
        for c in range(m):
            for task, s, e in _segments(sim["cores"][c]):
                ax.barh(c, (e - s) * dt, left=s * dt, height=0.6,
                        color=colors[task % 3], edgecolor="black", linewidth=0.3)
        ax.set_yticks(range(m)); ax.set_yticklabels([f"Core {c+1}" for c in range(m)])
        ax.set_ylim(-0.6, m - 0.4); ax.invert_yaxis()
        w = res["w_pred"] if name == "Inferred" else res["w_true"]
        ax.set_title(f"{name} schedule ({res['scheduler']}, periods "
                     f"{[int(p) for p in (res['pred_periods'] if name == 'Inferred' else res['true_periods'])]} ms, "
                     f"longest free-core window = {w:.1f} ms)", fontsize=9)
    axes[1].set_xlabel("time (ms)")
    handles = [Patch(color=colors[i], label=f"Task {i+1} (C={res['wcets'][i]:g} ms)") for i in range(3)]
    handles.append(Patch(color="#d9f2d9", label="free core (vulnerability window)"))
    axes[0].legend(handles=handles, fontsize=7, ncol=4, loc="upper right",
                   bbox_to_anchor=(1.0, 1.42), frameon=False)
    sup = (f"L_mse={res['mse']:.3f}  IoU={res['iou']:.2f}  sched_err={res['sched_err']:.2f}"
           f"  E_total={res['E_total']:.3f}{title_extra}")
    fig.suptitle(sup, fontsize=9, y=1.02)
    fig.tight_layout()
    fig.savefig(out_path, dpi=200, bbox_inches="tight")
    plt.close(fig)


def plot_window_distribution(summary: pd.DataFrame, out_path: str):
    """Predicted vs. true vulnerability-window length (replaces errors.png)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(8.5, 3.2))
    bins = np.arange(0, summary[["w_true", "w_pred"]].max().max() + 2, 1.0)
    axes[0].hist(summary["w_true"], bins=bins, alpha=0.6, label="true $w_{vul}$")
    axes[0].hist(summary["w_pred"], bins=bins, alpha=0.6, label="predicted $w_{vul}$")
    axes[0].set_xlabel("longest free-core window (ms)"); axes[0].set_ylabel("samples")
    axes[0].legend(fontsize=8)
    lim = bins[-1]
    axes[1].scatter(summary["w_true"], summary["w_pred"], s=12, alpha=0.5,
                    c=summary["mse"], cmap="viridis")
    axes[1].plot([0, lim], [0, lim], "k--", lw=0.8)
    axes[1].set_xlabel("true $w_{vul}$ (ms)"); axes[1].set_ylabel("predicted $w_{vul}$ (ms)")
    axes[1].set_title("colour = $L_{mse}$", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    plt.close(fig)


# ============================================================================
# 8. Standalone runner
# ============================================================================
def _agg_table(df, cols):
    agg = df[cols].agg(["mean", "median", "std", "min", "max"])
    return agg.round(4).to_string()


def _prf(tp, fp, fn, tn):
    prec = tp / (tp + fp) if tp + fp else float("nan")
    rec  = tp / (tp + fn) if tp + fn else float("nan")
    f1   = (2 * prec * rec / (prec + rec)) if (tp + fp and tp + fn and prec + rec) else float("nan")
    acc  = (tp + tn) / max(1, tp + fp + fn + tn)
    return prec, rec, f1, acc


def exploitability_table(results, budgets, dt=DT_MS):
    """Confusion matrices (strict and lenient) for several latency budgets.
    Only the free-core masks are needed, so the schedules are not re-simulated."""
    rows = []
    for b_ms in budgets:
        b = max(1, int(math.ceil(b_ms / dt)))
        TP = FP = FN = TN = TPl = FPl = truly_n = 0
        for r in results:
            mt, mp = free_mask(r["_sim_true"]), free_mask(r["_sim_pred"])
            Lt, _  = longest_run(mt)
            Lp, sp = longest_run(mp)
            truly, pred = Lt >= b, Lp >= b
            ok = bool(pred and run_from(mt, sp) >= b)
            truly_n += truly
            TP += pred and ok;            FP += pred and not ok
            FN += (not pred) and truly;   TN += (not pred) and not truly
            TPl += pred and truly;        FPl += pred and not truly
        ps, rs, f1s, accs = _prf(TP, FP, FN, TN)
        pl, rl, f1l, accl = _prf(TPl, FPl, FN, TN)
        rows.append({"budget_ms": b_ms, "truly_exploitable": truly_n,
                     "TP": TP, "FP": FP, "FN": FN, "TN": TN,
                     "precision": ps, "recall": rs, "F1": f1s, "accuracy": accs,
                     "TP_lenient": TPl, "FP_lenient": FPl,
                     "precision_lenient": pl, "recall_lenient": rl, "F1_lenient": f1l})
    return pd.DataFrame(rows)


def run_assessment(results_dir="results/", budget_ms=None, w_max=W_MAX_MS,
                   full_log_dirs=(), truth="sim", plot_dir=None, plot_idx=None,
                   summary_csv=None, budget_sweep=()):
    df = load_results(results_dir)
    print(f"Loaded {len(df)} prediction rows from {results_dir}")

    results = [process_sample(row, budget_ms, w_max, full_log_dirs, truth)
               for _, row in df.iterrows()]
    summary = pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")}
                            for r in results])

    # -------- headline metrics --------------------------------------------
    print("\n-- Reconstruction metrics (dual-core, dt = %.1f ms, W_max = %g ms) --" % (DT_MS, w_max))
    print(_agg_table(summary, ["mse", "eps_P", "W_delta", "sched_err", "iou", "E_total"]))
    n_exact = int((summary["mse"] == 0).sum())
    print(f"\nexact period matches (L_mse = 0): {n_exact}/{len(summary)} "
          f"= {100*n_exact/len(summary):.2f}%")
    print(f"samples whose true schedule contains a full-busy (preempting) interval: "
          f"{int(summary['preempt_true'].sum())}/{len(summary)}")
    print(f"observation window W: mean={summary['W'].mean():.1f} ms, "
          f"min={summary['W'].min():.0f}, max={summary['W'].max():.0f}")
    print(f"true vulnerability window w_true: mean={summary['w_true'].mean():.2f} ms, "
          f"median={summary['w_true'].median():.2f}, max={summary['w_true'].max():.2f}")

    # -------- reconstruction latency --------------------------------------
    r = summary["recon_ms"]
    print(f"\nL_scheduler-recon (attacker-side reconstruction per sample): "
          f"mean={r.mean():.4f} ms, std={r.std():.4f}, p95={r.quantile(0.95):.4f}, "
          f"max={r.max():.4f} ms")

    # -------- fidelity against SimSo full log ------------------------------
    if summary["fidelity_busy"].notna().any():
        f = summary.dropna(subset=["fidelity_busy"])
        print(f"\nFidelity of simulated S_true vs SimSo full log ({len(f)} samples found): "
              f"busy-count agreement mean={f['fidelity_busy'].mean():.4f} "
              f"(min {f['fidelity_busy'].min():.4f}); "
              f"free-mask IoU mean={f['fidelity_iou'].mean():.4f} "
              f"(min {f['fidelity_iou'].min():.4f}); truth used for metrics = '{truth}'")

    # -------- exploitability ----------------------------------------------
    if budget_ms is not None:
        s = summary
        TP = int((s.pred_exploitable & s.launch_ok).sum())
        FP = int((s.pred_exploitable & ~s.launch_ok).sum())
        FN = int((~s.pred_exploitable & s.truly_exploitable).sum())
        TN = int((~s.pred_exploitable & ~s.truly_exploitable).sum())
        TPl = int((s.pred_exploitable & s.truly_exploitable).sum())
        FPl = int((s.pred_exploitable & ~s.truly_exploitable).sum())
        def _pr(tp, fp, fn, tn):
            prec = tp / (tp + fp) if tp + fp else float("nan")
            rec  = tp / (tp + fn) if tp + fn else float("nan")
            f1   = 2 * prec * rec / (prec + rec) if prec + rec else float("nan")
            acc  = (tp + tn) / (tp + fp + fn + tn)
            return prec, rec, f1, acc
        print(f"\n-- Exploitability (budget = {budget_ms} ms) --")
        print(f"truly exploitable: {int(s.truly_exploitable.sum())}/{len(s)} | "
              f"predicted exploitable: {int(s.pred_exploitable.sum())}/{len(s)}")
        for label, (tp, fp, fn, tn) in (("strict (launch at predicted window must survive)", (TP, FP, FN, TN)),
                                        ("lenient (w_pred >= budget AND w_true >= budget)", (TPl, FPl, FN, TN))):
            prec, rec, f1, acc = _pr(tp, fp, fn, tn)
            print(f"  {label}:\n    TP={tp} FP={fp} FN={fn} TN={tn} | "
                  f"precision={100*prec:.2f}% recall={100*rec:.2f}% F1={100*f1:.2f}% "
                  f"accuracy={100*acc:.2f}%")

    if budget_sweep:
        tab = exploitability_table(results, budget_sweep)
        print("\n-- Exploitability vs. required uninterrupted execution time (strict TP: "
              "payload launched at the predicted window survives in the true schedule) --")
        with pd.option_context("display.width", 200, "display.max_columns", 30):
            print(tab.round(4).to_string(index=False))
        tab.to_csv(os.path.join(results_dir, "exploitability_sweep.csv"), index=False)
        print(f"saved -> {os.path.join(results_dir, 'exploitability_sweep.csv')}")

    # -------- plots ------------------------------------------------------------
    if plot_dir:
        os.makedirs(plot_dir, exist_ok=True)
        picks = {}
        if plot_idx is not None:
            picks[f"plot_idx{plot_idx}"] = next(r for r in results if int(r["idx"]) == plot_idx)
        else:
            def first(cond):
                return next((r for r in results if cond(r)), None)
            picks["plot_exact"] = first(lambda r: r["mse"] == 0 and r["preempt_true"])
            if budget_ms is not None:
                # imperfect periods, but the predicted window is still safe to use
                picks["plot_hit"]   = first(lambda r: r["preempt_true"] and r["mse"] > 0
                                            and r["pred_exploitable"] and r["launch_ok"])
                # the predicted window is actually occupied -> attacker gets preempted
                picks["plot_false"] = first(lambda r: r["preempt_true"] and r["pred_exploitable"]
                                            and not r["launch_ok"])
                # a true window exists but the attacker predicts none long enough
                picks["plot_miss"]  = first(lambda r: r["preempt_true"] and r["truly_exploitable"]
                                            and not r["pred_exploitable"])
            else:
                picks["plot_miss"]  = first(lambda r: r["preempt_true"] and r["iou"] < 0.5)
        for name, res in picks.items():
            if res is None:
                print(f"  [plot] no sample matches '{name}'"); continue
            path = os.path.join(plot_dir, f"{name}.png")
            plot_example(res, path, budget_ms,
                         title_extra=f"  ({res['source_file'] or 'idx ' + str(res['idx'])})")
            print(f"  [plot] {name}: idx={res['idx']} {res['source_file']} -> {path}")

    if plot_dir:
        plot_window_distribution(summary, os.path.join(plot_dir, "errors.png"))
        print(f"  [plot] window distribution -> {os.path.join(plot_dir, 'errors.png')}")

    # -------- save ----------------------------------------------------------
    summary_csv = summary_csv or os.path.join(results_dir, "assessment_summary.csv")
    out = summary.copy()
    for c in ("pred_periods", "true_periods", "wcets"):
        out[c] = out[c].apply(str)
    out.to_csv(summary_csv, index=False)
    print(f"\nPer-sample summary saved to: {summary_csv}")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Dual-core schedule reconstruction assessment.")
    ap.add_argument("--results_dir", default="results/")
    ap.add_argument("--budget_ms", type=float, default=None,
                    help="end-to-end attack latency budget (ms) for exploitability")
    ap.add_argument("--w_max", type=float, default=W_MAX_MS)
    ap.add_argument("--full_log_dirs", default="",
                    help="comma-separated SimSo full-log dirs for the fidelity check")
    ap.add_argument("--truth", default="sim", choices=["sim", "log"],
                    help="ground truth: simulator with true periods (paper Alg. 2) or SimSo log")
    ap.add_argument("--plot_dir", default=None, help="save example Gantt PNGs here")
    ap.add_argument("--plot_idx", type=int, default=None, help="plot one specific idx")
    ap.add_argument("--summary_csv", default=None)
    ap.add_argument("--budget_sweep", default="",
                    help="comma-separated budgets (ms) for an exploitability table, "
                         "e.g. 0.25,1,2,5,10")
    args = ap.parse_args()
    dirs  = [d.strip() for d in args.full_log_dirs.split(",") if d.strip()]
    sweep = [float(b) for b in args.budget_sweep.split(",") if b.strip()]
    run_assessment(args.results_dir, args.budget_ms, args.w_max, dirs, args.truth,
                   args.plot_dir, args.plot_idx, args.summary_csv, sweep)
