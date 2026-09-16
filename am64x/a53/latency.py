#!/usr/bin/env python3
"""Parse SK-AM64 A53 SMP scheduler-experiment logs.

Differs from the single-core parsers in three ways that matter:

* Spans are in **system-counter ticks** (CNTVCT_EL0), not CPU cycles.  The rate
  comes from the firmware banner (CNTFRQ_EL0 if firmware programmed it, else
  the DM firmware's answer over TISCI - on an SBL-booted AM64x it is the
  latter, 225 MHz on SK-AM64B) - so unlike the R5F and STM32 parsers there is
  no assumed clock to get wrong.  If the banner is missing the script refuses
  to guess, and the per-second [TICK] records let it check the rate against
  the RTOS tick.
* Context-switch samples arrive as per-second lossless histograms ([CTXSWH])
  and are expanded back to individual samples, so the statistics are the same
  as for the one-line-per-sample logs of the other targets.
* Every record carries the core it was observed on, so results are reported
  per core as well as aggregated.  A large asymmetry between cores is a finding,
  not noise: TI routes all SPI interrupts to A53 core 0.
* Task migrations are counted.  They have no analogue on the single-core
  targets and are part of what an SMP scheduler leaks.
"""

import argparse
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

TICK_HZ = 1_000

BANNER_TB = re.compile(r"timebase:\s*(\d+)\s*Hz")
BANNER_TB_SOURCE = re.compile(r"rate from (\w+)")
BANNER_CORES = re.compile(r"cores:\s*(\d+)")
BANNER_LOAD = re.compile(r"load scale:\s*(\d+)")
BANNER_AFFINITY = re.compile(r"affinity mode:\s*(\d+)")

PATTERNS = {
    "preempt": re.compile(r"\[OBSERVER\]\s+core=(\d+)\s+Preemption gap:\s+(\d+)"),
    "ctxsw": re.compile(r"\[CTXSW\]\s+core=(\d+)\s+Ticks:\s+(\d+)"),
    "critical": re.compile(r"\[CRITICAL\]\s+core=(\d+)\s+Ticks:\s+(\d+)"),
    "medium": re.compile(r"\[MEDIUM\]\s+core=(\d+)\s+Ticks:\s+(\d+)"),
    "migration": re.compile(r"\[MIGRATION\]\s+core=(\d+)\s+task=(\d+)"),
    "ctx_drops": re.compile(r"\[DROPS\]\s+core=(\d+)\s+Context samples:\s+(\d+)"),
    "log_drops": re.compile(r"\[DROPS\]\s+core=(\d+)\s+Log events:\s+(\d+)"),
    # (rtos tick count, system counter / 1000) once a second, for the rate check
    "tick": re.compile(r"\[TICK\]\s+core=\d+\s+rtos=(\d+)\s+counter_k=(\d+)"),
}

# Per-second context-switch histogram: "[CTXSWH] core=N v:c v:c ...", where v is
# L_ctxsw in counter ticks and c how often it occurred.  Lossless - expanded
# back into individual samples below, so the statistics are identical to the
# one-line-per-sample format the other targets use.
CTXSW_HIST = re.compile(r"\[CTXSWH\]\s+core=(\d+)((?:\s+\d+:\d+)+)")
CTXSW_HIST_PAIR = re.compile(r"(\d+):(\d+)")


def percentile(values: list[int], percent: float) -> float:
    if not values:
        return 0.0
    position = (len(values) - 1) * percent / 100.0
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def load(path: Path) -> tuple[dict[str, list[tuple[int, int]]], dict[str, int]]:
    data: dict[str, list[tuple[int, int]]] = {name: [] for name in PATTERNS}
    banner: dict[str, int] = {}

    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            for key, pattern in (("timebase_hz", BANNER_TB),
                                 ("cores", BANNER_CORES),
                                 ("load_scale", BANNER_LOAD),
                                 ("affinity", BANNER_AFFINITY)):
                match = pattern.search(line)
                if match and key not in banner:
                    banner[key] = int(match.group(1))
            source = BANNER_TB_SOURCE.search(line)
            if source and "timebase_source" not in banner:
                banner["timebase_source"] = source.group(1)  # type: ignore[assignment]

            hist = CTXSW_HIST.search(line)
            if hist:
                core = int(hist.group(1))
                for value, count in CTXSW_HIST_PAIR.findall(hist.group(2)):
                    data["ctxsw"].extend([(core, int(value))] * int(count))
                continue

            for name, pattern in PATTERNS.items():
                match = pattern.search(line)
                if match:
                    data[name].append((int(match.group(1)), int(match.group(2))))
                    break
    return data, banner


def summarize(title: str, samples: list[tuple[int, int]], tb_hz: int,
              cores: int) -> None:
    values = [value for _, value in samples]
    print(f"\n{title}  (n = {len(values)})")
    if not values:
        print("  no samples")
        return

    def block(label: str, subset: list[int], indent: str = "  ") -> None:
        if not subset:
            print(f"{indent}{label}: no samples")
            return
        ordered = sorted(subset)

        def fmt(value: float) -> str:
            micros = value / tb_hz * 1_000_000.0
            return f"{value:>10.1f} tk ({micros:>9.2f} us)"

        print(f"{indent}{label}  n={len(ordered)}")
        print(f"{indent}  Median: {fmt(statistics.median(ordered))}")
        print(f"{indent}  Mean:   {fmt(statistics.mean(ordered))}")
        if len(ordered) > 1:
            print(f"{indent}  Stdev:  {statistics.stdev(ordered):>10.1f} tk")
        print(f"{indent}  p95:    {fmt(percentile(ordered, 95))}")
        print(f"{indent}  p99:    {fmt(percentile(ordered, 99))}")
        print(f"{indent}  Min:    {fmt(ordered[0])}")
        print(f"{indent}  Max:    {fmt(ordered[-1])}")

    block("all cores", values)
    if cores > 1:
        by_core: dict[int, list[int]] = defaultdict(list)
        for core, value in samples:
            by_core[core].append(value)
        for core in sorted(by_core):
            block(f"core {core}", by_core[core], indent="  ")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path, help="captured MAIN_UART0 text log")
    parser.add_argument("--timebase-hz", type=int, default=None,
                        help="override the banner's CNTFRQ_EL0 value")
    args = parser.parse_args()

    data, banner = load(args.log)

    tb_hz = args.timebase_hz or banner.get("timebase_hz")
    if not tb_hz:
        sys.exit("ERROR: no 'timebase: <n> Hz' banner in the log and no "
                 "--timebase-hz given.  Refusing to guess the time base - "
                 "every figure would be scaled by an unknown factor.  "
                 "Re-capture including the banner.")

    cores = banner.get("cores", 2)
    ticks_per_rtos_tick = tb_hz // TICK_HZ
    ctxsw_max_valid = ticks_per_rtos_tick // 2

    print("=" * 70)
    print("SK-AM64 (AM6442) DUAL CORTEX-A53 SMP SCHEDULER-LATENCY RESULTS")
    print("=" * 70)
    print(f"  time base:    {tb_hz} Hz (CNTVCT_EL0, rate from "
          f"{banner.get('timebase_source', 'unknown')})")
    print(f"  cores:        {cores}")

    # Cross-check the rate against the RTOS tick: the firmware pairs the tick
    # count with the counter once a second.  A rate that is wrong scales every
    # microsecond figure in this report, so say so loudly.
    ticks = data["tick"]
    if len(ticks) >= 2:
        (rtos0, ctr0), (rtos1, ctr1) = ticks[0], ticks[-1]
        if rtos1 > rtos0:
            measured = (ctr1 - ctr0) * 1000 * TICK_HZ / (rtos1 - rtos0)
            error = (measured - tb_hz) / tb_hz * 100
            print(f"  rate check:   {measured:.0f} Hz measured against the "
                  f"{TICK_HZ} Hz RTOS tick over {(rtos1 - rtos0) / TICK_HZ:.0f} s "
                  f"({error:+.2f}% vs banner)")
            if abs(error) > 1.0:
                print("  -> WARNING: time base rate and RTOS tick disagree by more "
                      "than 1%; one of them is wrong and so is every us figure")
    else:
        print("  rate check:   not possible (fewer than two [TICK] records)")
    if "load_scale" in banner:
        scale = banner["load_scale"]
        util = scale * (10 / 20 + 5 / 30)
        print(f"  load scale:   {scale}  "
              f"(utilisation {util:.3f} over {cores} cores = "
              f"{util / cores * 100:.1f}% per core)")
    if "affinity" in banner:
        mode = banner["affinity"]
        label = ("free migration (scheduler channel)" if mode == 0
                 else "pinned (cross-core interference control)")
        print(f"  affinity:     {mode} - {label}")

    summarize("Preemption gap (OBSERVER)", data["preempt"], tb_hz, cores)

    clean_ctx = [(c, v) for c, v in data["ctxsw"] if 0 < v <= ctxsw_max_valid]
    summarize("Context switch (selection section)", clean_ctx, tb_hz, cores)
    print(f"  filtered: {len(data['ctxsw']) - len(clean_ctx)} of "
          f"{len(data['ctxsw'])} raw samples")

    summarize("CRITICAL span", data["critical"], tb_hz, cores)
    summarize("MEDIUM span", data["medium"], tb_hz, cores)

    print(f"\nTask migrations  (n = {len(data['migration'])})")
    if data["migration"]:
        onto = Counter(core for core, _ in data["migration"])
        per_task = Counter(task for _, task in data["migration"])
        for core in sorted(onto):
            print(f"  onto core {core}: {onto[core]}")
        print("  per task id: " +
              ", ".join(f"{t}={per_task[t]}" for t in sorted(per_task)))
    else:
        print("  none - tasks never changed core.")
        print("  If affinity mode is 0 this is suspicious: either the load is "
              "too light to force placement changes, or SMP is not actually "
              "active (check configNUM_CORES and the CCS sync group).")

    print("\nCapture integrity")
    last_ctx: dict[int, int] = {}
    for core, value in data["ctx_drops"]:
        last_ctx[core] = value
    last_log = data["log_drops"][-1][1] if data["log_drops"] else 0
    for core in sorted(last_ctx):
        print(f"  core {core} context-buffer drops: {last_ctx[core]}")
    print(f"  log-queue drops:            {last_log}")
    print(f"  drop reports seen:          {len(data['log_drops'])}")
    if any(last_ctx.values()) or last_log:
        print("  -> non-zero drops: the capture is incomplete, discard it")
    if not data["log_drops"]:
        print("  -> NO drop reports at all: the Logger never got to print them, "
              "so the capture is incomplete regardless of what it contains")


if __name__ == "__main__":
    main()
