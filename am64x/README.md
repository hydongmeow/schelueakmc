# AM64x / SK-AM64 ports

The scheduler-latency experiment on the TI SK-AM64 starter kit (AM6442).

Two lines live here:

| | what it is | use it for |
|---|---|---|
| **[`a53/`](a53/)** | Dual Cortex-A53, **FreeRTOS SMP** — one scheduler across two cache-coherent cores | **Primary.** Matches the paper's multi-core RTOS scheduler threat model |
| [`r5f/`](r5f/) | MAIN_R5FSS0_CORE0, Cortex-R5F, single core | Fallback, and the closest analogue to the STM32WB55 capture |

**Read [`PORTING.md`](PORTING.md) first.** It is the handoff document: rewrite
points, what is verified versus assumed, the bring-up checklist, and the
failure modes that produce plausible-but-wrong data.

## Why A53 is the primary line

The four R5F cores have no inter-core cache coherency, so FreeRTOS on them is
AMP — four independent schedulers. There is no shared scheduler to attack, and
the only cross-core channel left is resource contention (MSRAM, DDR,
interconnect), which is a different threat model.

The two A53s sit in one cache-coherent cluster and can run true SMP: a single
runqueue that both the observer and the victim tasks live in. That is the
configuration the paper's narrative needs.

The cost is that TI's A53 SMP FreeRTOS support is **experimental and explicitly
not supported by TI**. `r5f/` is kept as the retreat.

## Status

The A53 line runs on an SK-AM64B and has produced valid captures. The R5F line
has not been built with the SDK. What has been verified is recorded in
PORTING.md's status table — briefly:

- **A53 (primary):** re-aligned in September 2026 against TI's actual
  mcupsdk-core sources (kernel `V202110.00-SMP`, `portable_smp/GCC/ARM_CA53`,
  the SDK's own `linker.cmd`, two-core `main()` boot shape). All three
  application sources compile warning-free against the real SDK headers with a
  real AArch64 toolchain (Arm GNU 14.2); the four inline-assembly sites were
  checked in the disassembly; the SDK kernel + DPL sources (52 files) compile
  with the experiment's config overlay and `tasks.obj` references the trace
  hooks; a stub-linked image places `.vecs`, entry, per-core stacks and the
  per-core trace state where they should be. On 2026-09-15 the full build
  went through with the installed MCU+ SDK 11.01.00.17, SysConfig 1.25.0 and
  TI's GCC 9.2: `gmake clean; gmake all appimage` produces the CCS-loadable
  `.out` and the SBL-loadable, HS-FS-signed `.appimage.hs_fs` with zero
  errors. On the board it boots through `sbl_uart` and standalone from OSPI.
  The OSPI path needs the fixed SBL in `a53/sbl/`. Each boot path produced a
  valid capture in `a53/captures/`.
- **R5F (fallback):** sources compile for real including the CP15 assembly and
  the linker script places sections correctly. No `example.syscfg` yet.

Measured latency figures come only from the reports in `a53/captures/`. Any
other number in this directory is a design value, not a measurement.
