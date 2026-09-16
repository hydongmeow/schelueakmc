# SK-AM64 (AM6442) R5F port

This directory runs the FreeRTOS scheduler-latency experiment on
MAIN_R5FSS0_CORE0 (Cortex-R5F) of the TI SK-AM64 starter kit.

The workload is unchanged from the QEMU and STM32WB55 targets: Critical
executes for 10 ms every 20 ms, Medium for 5 ms every 30 ms, and Observer
samples every 1 ms. Logging is decoupled through a queue and a lowest-priority
Logger task so console I/O cannot extend the measured spans.

**Status: written but never built, flashed, or run.** Every figure in this
README is a design intent, not an observation. Read `PORTING.md` before
bring-up — it lists which constants are verified and which still need to be
checked against the TRM and against your SBL.

## Hardware configuration

- Core: MAIN_R5FSS0_CORE0, Cortex-R5F, assumed 800 MHz.
- RTOS tick: 1 kHz, driven by the SDK's ClockP on a DMTimer.
- Cycle source: ARMv7 PMU `PMCCNTR` via CP15 (there is no DWT on ARMv7-R).
- Console: MAIN_UART0 at 115200 8N1, reachable over the onboard XDS110 USB
  bridge — no extra cabling.
- CPU2/A53, the second R5F cluster, the M4F and the PRU-ICSSG are untouched.

## Dependencies

Unlike `stm32wb55/`, this port does **not** vendor its dependencies. It builds
against an installed TI MCU+ SDK for AM64x:

- TI MCU+ SDK for AM64x (FreeRTOS kernel, DPL, board library)
- SysConfig (ships with the SDK)
- GNU Arm Embedded Toolchain for `-mcpu=cortex-r5`

`PORTING.md` explains why the SDK is a dependency here when the STM32 port
needed no HAL: on AM64x, clocks, power and pinmux are only reachable by TISCI
messages to the TIFS firmware, and the upstream FreeRTOS `ARM_CR5` port targets
a GIC rather than TI's VIM.

## Build

```sh
export MCU_PLUS_SDK_PATH=/path/to/mcu_plus_sdk
make syscfg     # generate DPL / pinmux / power-clock configuration
make            # -> build/am64x_sched_latency.elf and .bin
make appimage   # -> build/am64x_sched_latency.appimage for the SBL
```

If the link fails against your SDK release, copy the SDK's own
`examples/kernel/freertos/task_switch/am64x-evm/r5fss0-0_freertos/gcc-armv7/`
makefile and swap in these sources rather than patching `makefile`.

## Load and capture

There is no DFU path on this board. The R5F application is loaded by the SBL,
so bring-up is: flash an SBL to the OSPI flash or SD card once, then have it
load `am64x_sched_latency.appimage`. For iteration during development, loading
the ELF over JTAG with CCS is faster.

Open the MAIN_UART0 console (one of the XDS110 VCPs, 115200 8N1) and save the
text. The firmware announces itself with:

```text
=== SK-AM64 R5F FreeRTOS Scheduler Latency Test ===
MAIN_R5FSS0_CORE0: Cortex-R5F @ 800000000 Hz, console: MAIN_UART0
hot path in TCM: 1
```

Then analyse it:

```sh
python latency.py am64_output.log
python latency.py am64_output.log --cpu-hz 400000000   # if the SBL set 400 MHz
```

The report covers the Observer preemption window, task-selection latency, task
spans, and the context-buffer/log-queue drop counters. A capture with non-zero
drops is incomplete — discard it rather than reporting from it.

Do not halt the core with a debugger while capturing: PMCCNTR keeps running
during a halt on some configurations and stops on others, and either way the
samples spanning the halt are meaningless.

## Cache and TCM

The R5F has 32 KiB L1 I-cache and 32 KiB L1 D-cache; the STM32WB55's M4F has
neither. `EXPERIMENT_HOT_PATH_IN_TCM` (default 1) places the spin loops, trace
hooks and the context-switch ring buffer in zero-wait-state TCM so the two
boards can be compared. Set it to 0 to measure the cached, production-realistic
system instead. The two configurations are not comparable with each other —
record which one produced any given capture.

## Primary references

- [AM64x MCU+ SDK memory map](https://software-dl.ti.com/mcu-plus-sdk/esd/AM64X/latest/exports/docs/api_guide_am64x/MEMORY_MAP.html)
- [AM64x SK-EVM User's Guide (SPRUJ64)](https://www.ti.com/lit/ug/spruj64/spruj64.pdf)
- [AM6442 product page](https://www.ti.com/product/AM6442)
- [TI mcupsdk-core sources](https://github.com/TexasInstruments/mcupsdk-core)
- [k3-am64-main.dtsi (peripheral base addresses)](https://raw.githubusercontent.com/torvalds/linux/master/arch/arm64/boot/dts/ti/k3-am64-main.dtsi)
