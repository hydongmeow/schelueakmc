import pandas as pd
import glob
import ast
import numpy as np
from math import gcd
import ast
import pandas as pd
import matplotlib.pyplot as plt

# ── 1. Run process_sample on every row (suppress per-row prints if noisy) ──
import io, contextlib
"""
########################################
1. Data preprocessing
########################################
This method assume an aggregated result with predicted periods, true periods, and metadata from the modality_3 in the dataset setup.
Our data was built like below.

	prediction	true_value	modality_3	mse_error
0	[7.0, 17.0, 35.0]	[10.0, 20.0, 40.0]	[16.0, 4.0, 80.0, 1.0, 3.5, 2.0, 8.0, 3.0, 8.0]	0.057153
1	[4.0, 10.0, 20.0]	[5.0, 10.0, 20.0]	[6.0, 4.0, 40.0, 1.0, 2.0, 2.0, 3.0, 3.0, 2.0]	0.016598

"""
def process_list(df):
    df['prediction'] = df['prediction'].apply(lambda x: ast.literal_eval(x) if isinstance(x, str) else x)
    df['true_value']  = df['true_value'].apply(lambda x: ast.literal_eval(x) if isinstance(x, str) else x)

    def compute_log_mse(row):
        pred = np.array(row["prediction"])  # shape (3,)
        true = np.array(row["true_value"])  # shape (3,)
        K = len(pred)
        loss = np.mean((np.log(pred) - np.log(true)) ** 2)
        return loss

    df["mse_error"] = df.apply(compute_log_mse, axis=1)
    return df


"""
########################################
2. Reconstruct the scheduler
########################################

"""
def compute_lcm(a, b):
    return int(a) * int(b) // gcd(int(a), int(b))


def compute_observation_window(periods):
    return int(max(periods))


def reconstruct_schedule_RM(periods, wcets, window_ms, dt=1):
    """
    Rate-Monotonic preemptive scheduler simulation.
    Returns timeline list: each slot = task index (0-based) or None (IDLE).
    """
    tasks = sorted(
        enumerate(zip(periods, wcets)),
        key=lambda x: x[1][0]  # sort by period → RM priority
    )
    n_tasks = len(tasks)
    timeline = []
    remaining = [0.0] * n_tasks

    for t in range(0, window_ms, dt):
        for rank, (orig_idx, (period, wcet)) in enumerate(tasks):
            if t % int(period) == 0:
                remaining[rank] = float(wcet)

        # Pick highest-priority ready task
        selected_rank = None
        for rank in range(n_tasks):
            if remaining[rank] > 0:
                selected_rank = rank
                break

        if selected_rank is not None:
            orig_task_idx = tasks[selected_rank][0]
            timeline.append(orig_task_idx)
            remaining[selected_rank] -= dt
        else:
            timeline.append(None)

    return timeline

"""
########################################
3. Plot the scheduler and evaluate with the ground truth
########################################
"""
def draw_ascii_gantt(timeline, task_labels, window_ms, title="Schedule"):
    BAR = "█"
    OFF = "░"
    IDLE = "·"

    print(f"  {title}   [0 ─── {window_ms} ms]")

    for i, label in enumerate(task_labels):
        row = "".join(BAR if slot == i else OFF for slot in timeline)
        print(f"  Task {label:>3s} │{row}│")

    idle_row = "".join(IDLE if slot is None else OFF for slot in timeline)
    print(f"  IDLE     │{idle_row}│")

    # Tick ruler (mod 10)
    ruler = "".join(str(t % 10) for t in range(len(timeline)))
    print(f"  t (ms)   │{ruler}│  (each char = 1 ms)")


def parse_modality3(m3_raw):
    if isinstance(m3_raw, str):
        m3 = ast.literal_eval(m3_raw)
    else:
        m3 = list(m3_raw)
    return [float(x) for x in m3]

"""
########################################
4. Process data and evaluate
########################################

"""

def process_sample(row, sample_num=1):
    pred_periods = list(row["prediction"])
    true_periods = list(row["true_value"])
    m3 = parse_modality3(row["modality_3"])
    mse = float(row["mse_error"])

    task_ids = [int(m3[3]), int(m3[5]), int(m3[7])]
    task_wcets = [m3[4], m3[6], m3[8]]
    task_labels = [str(tid) for tid in task_ids]

    # ── Observation windows ──────────────────────────────────────
    W_true = compute_observation_window(true_periods)
    W_pred = compute_observation_window(pred_periods)

    # ── Reconstruct schedules ────────────────────────────────────
    sched_pred = reconstruct_schedule_RM(pred_periods, task_wcets, W_pred)
    sched_true = reconstruct_schedule_RM(true_periods, task_wcets, W_true)

    print(f"\n  Predicted periods : {pred_periods}")
    print(f"  True periods      : {true_periods}")
    print(f"  Task IDs          : {task_ids}")
    print(f"  Task WCETs (ms)   : {[round(w, 3) for w in task_wcets]}")
    print(f"  MSE error         : {mse:.6f}")

    draw_ascii_gantt(sched_pred, task_labels, W_pred,
                     title=f"INFERRED Schedule (MSE={mse:.4f})")
    draw_ascii_gantt(sched_true, task_labels, W_true,
                     title="TRUE Schedule             ")

    # ── Utilisation summary ──────────────────────────────────────
    print(f"  Utilisation (true schedule, W={W_true} ms):")
    for i, label in enumerate(task_labels):
        slots = sum(1 for s in sched_true if s == i)
        util = slots / W_true * 100
        print(f"    Task {label:>3s}: {slots:>3d} ms busy  "
              f"({util:5.1f}%  |  C={task_wcets[i]:.3f}, T={true_periods[i]:.1f})")
    idle_slots = sum(1 for s in sched_true if s is None)
    print(f"    IDLE   : {idle_slots:>3d} ms  ({idle_slots / W_true * 100:5.1f}%)\n")

    # ── Schedule-level error (Hamming distance, normalised) ──────
    # Align both timelines to the shorter window before comparing
    W_cmp = min(W_true, W_pred)
    s_t = sched_true[:W_cmp]
    s_p = sched_pred[:W_cmp]
    mismatch = sum(1 for a, b in zip(s_t, s_p) if a != b)
    sched_err = mismatch / W_cmp
    E = 0.5 * mse + 0.5 * sched_err

    print(f"  Schedule mismatch (Hamming / W_cmp={W_cmp} ms) : "
          f"{mismatch} slots  →  {sched_err:.4f}  ({sched_err * 100:.1f}%)\n")

    return {
        "row_idx": row["idx"],
        "W_true": W_true,
        "W_pred": W_pred,
        "W_cmp": W_cmp,
        "sched_true": sched_true,
        "sched_pred": sched_pred,
        "task_labels": task_labels,
        "task_wcets": task_wcets,
        "true_periods": true_periods,
        "pred_periods": pred_periods,
        "mse": mse,
        "sched_err": sched_err,
        "E": E
    }

if __name__ == "__main__":
    files = glob.glob('results/*.csv')
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    df = process_list(df)
    results = []
    for _, row in df.iterrows():
        with contextlib.redirect_stdout(io.StringIO()):  # silence verbose prints
            res = process_sample(row)
        results.append(res)

    summary = pd.DataFrame(results)

    # ── 2. Distribution: predicted W vs true W ─────────────────────────────────
    fig, axes = plt.subplots(2, 1, figsize=(12, 16))

    axes[0].hist(summary["W_true"], bins=30, alpha=0.6, label="W true", color="steelblue")
    axes[0].hist(summary["W_pred"], bins=30, alpha=0.6, label="W pred", color="tomato")
    axes[0].set_xlabel("Observation window W (ms)")
    axes[0].set_ylabel("Count")
    axes[0].set_title("Distribution of W = max(T₁,T₂,…,Tₙ)")
    axes[0].legend()

    axes[1].scatter(summary["W_true"], summary["W_pred"], alpha=0.4, s=10, color="purple")
    lim = max(summary[["W_true", "W_pred"]].max())
    axes[1].plot([0, lim], [0, lim], "k--", lw=1, label="perfect")
    axes[1].set_xlabel("W true (ms)")
    axes[1].set_ylabel("W pred (ms)")
    axes[1].set_title("W_pred vs W_true (scatter)")
    axes[1].legend()

    plt.tight_layout()
    plt.show()

    # ── 3. Statistics table ────────────────────────────────────────────────────
    stats = summary[["row_idx", "W_true", "W_pred", "mse", "sched_err", "E"]].copy()
    stats["W_delta"] = (summary["W_pred"] - summary["W_true"]).abs()
    stats["sched_err%"] = (summary["sched_err"] * 100).round(2)
    stats["mse"] = summary["mse"].round(6)

    print("\n── Per-record summary ──────────────────────────────────────────")
    print(stats.to_string(index=False))

    print("\n── Aggregate statistics ────────────────────────────────────────")
    agg = stats[["mse", "W_delta", "sched_err%", "E"]].agg(["mean", "median", "std", "min", "max"])
    print(agg.round(4).to_string())
