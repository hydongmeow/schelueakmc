#!/usr/bin/env python3
"""Parse SK-AM64 MAIN_UART0 logs from the R5F scheduler experiment.

Same report as stm32wb55/latency.py, with two AM64x-specific differences:

* The default clock is 800 MHz rather than 64 MHz.  That constant is an
  assumption about what the SBL programmed, not something the firmware
  measures, so ``--cpu-hz`` is offered to rescale a capture after the fact
  instead of forcing a reflash.
* The firmware prints its clock and hot-path placement in the banner.  When
  those are present they are cross-checked against the values used here, since
  a mismatch rescales every number without otherwise looking wrong.
"""

import argparse
import re
import statistics
from pathlib import Path

DEFAULT_CPU_HZ = 800_000_000
TICK_HZ = 1_000

PATTERNS = {
    "preempt": re.compile(r"\[OBSERVER\]\s+Preemption gap:\s+(\d+)"),
    "ctxsw": re.compile(r"\[CTXSW\]\s+Cycles:\s+(\d+)"),
    "critical": re.compile(r"\[CRITICAL\]\s+Cycles:\s+(\d+)"),
    "medium": re.compile(r"\[MEDIUM\]\s+Cycles:\s+(\d+)"),
    "ctx_drops": re.compile(r"\[DROPS\]\s+Context samples:\s+(\d+)"),
    "log_drops": re.compile(r"\[DROPS\]\s+Log events:\s+(\d+)"),
}

BANNER_CLOCK = re.compile(r"Cortex-R5F @ (\d+) Hz")
BANNER_TCM = re.compile(r"hot path in TCM:\s*(\d+)")


def percentile(values: list[int], percent: float) -> float:
    if not values:
        return 0.0
    position = (len(values) - 1) * percent / 100.0
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def load(path: Path) -> tuple[dict[str, list[int]], dict[str, int]]:
    data = {name: [] for name in PATTERNS}
    banner: dict[str, int] = {}

    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            clock = BANNER_CLOCK.search(line)
            if clock:
                banner["cpu_hz"] = int(clock.group(1))
                continue
            tcm = BANNER_TCM.search(line)
            if tcm:
                banner["tcm"] = int(tcm.group(1))
                continue
            for name, pattern in PATTERNS.items():
                match = pattern.search(line)
                if match:
                    data[name].append(int(match.group(1)))
                    break
    return data, banner


def summarize(name: str, values: list[int], cpu_hz: int) -> None:
    print(f"\n{name}  (n = {len(values)})")
    if not values:
        print("  no samples")
        return

    ordered = sorted(values)

    def fmt(value: float) -> str:
        micros = value / cpu_hz * 1_000_000.0
        return f"{value:>12.1f} cyc  ({micros:>9.2f} us)"

    print(f"  Median: {fmt(statistics.median(ordered))}")
    print(f"  Mean:   {fmt(statistics.mean(ordered))}")
    if len(ordered) > 1:
        print(f"  Stdev:  {statistics.stdev(ordered):>12.1f} cyc")
    print(f"  p95:    {fmt(percentile(ordered, 95))}")
    print(f"  p99:    {fmt(percentile(ordered, 99))}")
    print(f"  Min:    {fmt(ordered[0])}")
    print(f"  Max:    {fmt(ordered[-1])}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path, help="captured MAIN_UART0 text log")
    parser.add_argument("--cpu-hz", type=int, default=DEFAULT_CPU_HZ,
                        help=f"R5F clock in Hz (default {DEFAULT_CPU_HZ})")
    args = parser.parse_args()

    data, banner = load(args.log)
    cpu_hz = args.cpu_hz

    if "cpu_hz" in banner and banner["cpu_hz"] != cpu_hz:
        print(f"WARNING: firmware reported {banner['cpu_hz']} Hz but this run "
              f"uses {cpu_hz} Hz.  Every figure below is scaled by "
              f"{banner['cpu_hz'] / cpu_hz:.3f}x relative to the firmware's "
              f"own view.  Pass --cpu-hz {banner['cpu_hz']} to agree with it.")

    cycles_per_tick = cpu_hz // TICK_HZ
    ctxsw_max_valid = cycles_per_tick // 2

    print("=" * 68)
    print("SK-AM64 (AM6442) MAIN_R5FSS0_CORE0 SCHEDULER-LATENCY RESULTS")
    print("=" * 68)
    print(f"  clock: {cpu_hz} Hz    cycles/tick: {cycles_per_tick}")
    if "tcm" in banner:
        placement = "TCM (cache-free)" if banner["tcm"] else "MSRAM (cached)"
        print(f"  hot path: {placement}")
    else:
        print("  hot path: UNKNOWN - banner missing, capture may be truncated")

    summarize("Preemption gap (OBSERVER)", data["preempt"], cpu_hz)

    clean_ctx = [value for value in data["ctxsw"] if 0 < value <= ctxsw_max_valid]
    summarize("Context switch (selection section)", clean_ctx, cpu_hz)
    print(f"  filtered: {len(data['ctxsw']) - len(clean_ctx)} of "
          f"{len(data['ctxsw'])} raw samples")

    summarize("CRITICAL span", data["critical"], cpu_hz)
    summarize("MEDIUM span", data["medium"], cpu_hz)

    last_ctx_drops = data["ctx_drops"][-1] if data["ctx_drops"] else 0
    last_log_drops = data["log_drops"][-1] if data["log_drops"] else 0
    print("\nCapture integrity")
    print(f"  context-buffer drops: {last_ctx_drops}")
    print(f"  log-queue drops:      {last_log_drops}")
    if last_ctx_drops or last_log_drops:
        print("  -> non-zero drops: the capture is incomplete, discard it")


if __name__ == "__main__":
    main()
