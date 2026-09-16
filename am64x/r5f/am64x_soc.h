/* am64x_soc.h — AM64x (AM6442) SoC constants used by the scheduler-latency
 * experiment on MAIN_R5FSS0_CORE0.
 *
 * Every magic number the port depends on is collected here so a reviewer can
 * check the whole set against one TRM chapter instead of hunting through the
 * sources.  Each entry is tagged with its provenance:
 *
 *   [VERIFIED]  cross-checked against the Linux device tree
 *               (arch/arm64/boot/dts/ti/k3-am64-main.dtsi) and/or the TI MCU+
 *               SDK CSL headers.
 *   [ASSUMED]   depends on what the SBL/board configuration actually
 *               programmed.  Confirm on the bench before trusting a capture.
 *
 * See PORTING.md section "地址与常量核对表" for the full checklist.
 */
#ifndef AM64X_SOC_H
#define AM64X_SOC_H

#include <stdint.h>

/* ---- Core clock ---------------------------------------------------------
 * [ASSUMED] MAIN_R5FSS0 runs at 800 MHz on AM6442 once the SBL has brought up
 * the PLLs.  This value is *not* self-measured by the firmware: it only scales
 * cycles to microseconds, so an incorrect value silently rescales every
 * reported latency.  latency.py takes the same constant and must be kept in
 * sync.  Verify with the SDK's SOC_getSelfCpuClk() during bring-up.
 */
#define AM64X_R5F_CLOCK_HZ      800000000UL

/* ---- MAIN domain UART ---------------------------------------------------
 * [VERIFIED] main_uart0 reg = <0x00 0x02800000 0x00 0x100>, stride 0x10000.
 * On SK-AM64 MAIN_UART0 is the console routed to the onboard XDS110 USB
 * bridge, so it needs no extra wiring.
 *
 * The IP is 16550-compatible with 32-bit register spacing (same as AM335x).
 * The port only ever writes THR after polling LSR.THRE; it does not reprogram
 * the divisor, because the SBL has already configured 115200 8N1 and done the
 * pinmux (which requires TIFS/TISCI and is out of scope for an application).
 */
#define AM64X_UART0_BASE        0x02800000UL

#define AM64X_UART_THR_OFFSET   0x00U   /* transmit holding register (write) */
#define AM64X_UART_LSR_OFFSET   0x14U   /* line status register              */
#define AM64X_UART_LSR_THRE     (1U << 5) /* TX holding register empty       */
#define AM64X_UART_LSR_TEMT     (1U << 6) /* transmitter fully empty         */

/* ---- MAIN domain DMTimer ------------------------------------------------
 * [VERIFIED] main_timer0 reg = <0x00 0x2400000 0x00 0x400>, stride 0x10000,
 * compatible "ti,am654-timer".
 * [ASSUMED]  functional clock 25 MHz (HFOSC0).  The Linux binding notes the
 * timers default to a 25 MHz input because the 32.768 kHz clock may not be
 * wired.
 *
 * These are provided for the bare-metal tick alternative documented in
 * PORTING.md.  The delivered port does NOT drive the timer directly — the
 * FreeRTOS tick comes from the SDK's ClockP, which owns a DMTimer instance
 * selected in SysConfig.  Timer ownership is shared with the Device Manager
 * firmware, so never claim an instance without checking the DM reservation.
 */
#define AM64X_DMTIMER_BASE(n)   (0x02400000UL + ((uint32_t)(n) * 0x10000UL))
#define AM64X_DMTIMER_INPUT_HZ  25000000UL

/* ---- R5F local memory map ----------------------------------------------
 * [VERIFIED] Local (core) view: ATCM 0x00000000 + 32 KiB, BTCM 0x41010000 +
 * 32 KiB.  The SoC view of MAIN_R5FSS0_CORE0 TCM is 0x78000000 / 0x78100000,
 * which is what the A53 or a debugger uses; code running on the R5F itself
 * must use the local addresses.  linker.ld uses the local view.
 *
 * [VERIFIED] MSRAM is 2 MiB in 8 banks of 256 KiB.  The first 512 KiB is
 * reserved for the SBL and the combined boot image; the application region for
 * r5fss0-0 starts at 0x70080000.
 */
#define AM64X_R5F_ATCM_BASE     0x00000000UL
#define AM64X_R5F_ATCM_SIZE     0x00008000UL
#define AM64X_R5F_BTCM_BASE     0x41010000UL
#define AM64X_R5F_BTCM_SIZE     0x00008000UL
#define AM64X_MSRAM_APP_BASE    0x70080000UL
#define AM64X_MSRAM_APP_SIZE    0x00040000UL

/* ---- Deliberately absent -----------------------------------------------
 * The VIM (Vectored Interrupt Manager) base address is NOT defined here.
 * The port does not program the VIM: interrupt setup goes through the SDK's
 * HwiP, which carries the correct per-core VIM base for the build target.
 * Hand-rolling it would mean hardcoding an address this port has no way to
 * validate.  See PORTING.md section "为什么没有自研 VIM 驱动".
 */

#endif /* AM64X_SOC_H */
