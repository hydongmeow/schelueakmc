#!/usr/bin/env python3
"""
Parse qemu_output.log and report the two directly-measured latencies:

  * Preemption gap   (L_preempt) -- vulnerability window w, observed by the
                                     lowest-priority task.
  * Context switch   (L_ctxsw)   -- duration of vTaskSwitchContext()'s
                                     task-selection critical section,
                                     bracketed by traceTASK_SWITCHED_OUT/IN.

Also reports the CRITICAL / MEDIUM task spans as a sanity check on the
workload model.

NOTE (honesty): QEMU is a functional, not cycle-accurate, emulator, so these
are consistent relative measures, not silicon-exact cycle counts.  For the
context switch we report the MEDIAN and percentiles rather than the mean,
because the distribution has a long right tail (switches that coincide with
a SysTick boundary).
"""
import re
import statistics

LOG    = "qemu_output.log"
CPU_HZ = 16_000_000
TICK_HZ = 1_000
CYCLES_PER_TICK = CPU_HZ // TICK_HZ          # 16000

# A real selection is far shorter than a tick; larger values are SysTick
# reload artifacts and are dropped.
CTXSW_MAX_VALID = CYCLES_PER_TICK // 2       # 8000

PATTERNS = {
    "preempt":  re.compile(r"\[OBSERVER\]\s+Preemption gap:\s+(\d+)"),
    "ctxsw":    re.compile(r"\[CTXSW\]\s+Cycles:\s+(\d+)"),
    "critical": re.compile(r"\[CRITICAL\]\s+Cycles:\s+(\d+)"),
    "medium":   re.compile(r"\[MEDIUM\]\s+Cycles:\s+(\d+)"),
}

def us(c):
    return c / CPU_HZ * 1e6

def pct(sorted_vals, p):
    if not sorted_vals:
        return 0
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)

def load():
    data = {k: [] for k in PATTERNS}
    with open(LOG) as f:
        for line in f:
            for name, pat in PATTERNS.items():
                m = pat.search(line)
                if m:
                    data[name].append(int(m.group(1)))
                    break
    return data

def summarize(name, vals, unit_us=True):
    print(f"\n{name}  (n = {len(vals)})")
    if not vals:
        print("  no samples")
        return
    s = sorted(vals)
    def fmt(c):
        return f"{c:>10.1f} cyc" + (f"  ({us(c):>9.1f} us)" if unit_us else "")
    print(f"  Median: {fmt(statistics.median(s))}")
    print(f"  Mean:   {fmt(statistics.mean(s))}")
    if len(s) > 1:
        print(f"  Stdev:  {statistics.stdev(s):>10.1f} cyc")
    print(f"  p95:    {fmt(pct(s, 95))}")
    print(f"  p99:    {fmt(pct(s, 99))}")
    print(f"  Min:    {fmt(s[0])}")
    print(f"  Max:    {fmt(s[-1])}")

def main():
    d = load()

    # ---- L_preempt : the vulnerability window w --------------------------
    print("=" * 60)
    print("VULNERABILITY WINDOW  w  =  L_preempt")
    print("=" * 60)
    summarize("Preemption gap (OBSERVER)", d["preempt"])

    # ---- L_ctxsw : context-switch (task-selection) latency ---------------
    print("\n" + "=" * 60)
    print("CONTEXT-SWITCH LATENCY  L_ctxsw")
    print("=" * 60)
    raw = d["ctxsw"]
    clean = [c for c in raw if 0 < c <= CTXSW_MAX_VALID]
    dropped = len(raw) - len(clean)
    summarize("Context switch (selection section)", clean)
    print(f"  [filtered {dropped} of {len(raw)} raw samples: "
          f"zero-init or SysTick-reload artifacts]")

    # ---- workload sanity check -------------------------------------------
    print("\n" + "=" * 60)
    print("WORKLOAD SANITY CHECK (task compute spans)")
    print("=" * 60)
    summarize("CRITICAL span", d["critical"])
    summarize("MEDIUM span",   d["medium"])

if __name__ == "__main__":
    main()
