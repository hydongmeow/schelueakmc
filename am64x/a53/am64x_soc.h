/* am64x_soc.h — SoC constants for the AM64x A53 SMP build.
 *
 * Much shorter than the R5F equivalent, and that is the point: the A53 port
 * derives almost everything at runtime instead of hardcoding it.
 *
 *   - The time base rate comes from CNTFRQ_EL0, not from a #define.
 *   - The A53 core clock is never needed: all spans are in system-counter
 *     ticks, so DVFS or a different SBL PLL setting cannot rescale results.
 *   - The GIC, the MMU and the second core's release are handled by the SDK.
 *
 * What remains is the UART, which the firmware writes directly.
 *
 *   [VERIFIED] cross-checked against k3-am64-main.dtsi and TI CSL headers.
 *   [ASSUMED]  depends on the SBL / board configuration.
 */
#ifndef AM64X_A53_SOC_H
#define AM64X_A53_SOC_H

#include <stdint.h>

/* [VERIFIED] main_uart0 reg = <0x00 0x02800000 0x00 0x100>.
 * On SK-AM64B this is the console on the CP2105 USB-UART bridge (micro-USB J11,
 * "Standard COM Port"), not the XDS110 debug port J12.
 * 16550-compatible, 32-bit register spacing.
 *
 * Note this is a MAIN-domain peripheral address used from the A53 with the
 * MMU on: the SDK's page tables must map it as Device-nGnRnE.  If the first
 * console write faults, that mapping is the place to look, not this constant.
 */
#define AM64X_UART0_BASE        0x02800000UL

#define AM64X_UART_THR_OFFSET   0x00U
#define AM64X_UART_LSR_OFFSET   0x14U
#define AM64X_UART_LSR_THRE     (1U << 5)
#define AM64X_UART_LSR_TEMT     (1U << 6)

/* [VERIFIED] SSR (supplementary status), 0x44 with 32-bit spacing; bit 0 is
 * TX_FIFO_FULL.  Polling this instead of LSR.THRE lets the writer keep the
 * 64-byte TX FIFO topped up rather than waiting for it to drain to empty
 * before every byte, which cuts the Logger's polling time by an order of
 * magnitude at 921600 baud. */
#define AM64X_UART_SSR_OFFSET   0x44U
#define AM64X_UART_SSR_TX_FIFO_FULL (1U << 0)

/* [ASSUMED] SK-AM64 carries 2 GiB of DDR4 starting at 0x80000000.  Only the
 * linker script uses this; see linker.cmd (the SDK example script) for the region actually claimed and
 * for why the exact offset still needs checking against the SDK example and
 * against whatever else is resident (Linux, DM firmware, remote-core carveouts).
 */
#define AM64X_DDR_BASE          0x80000000UL

/* ---- Deliberately absent -----------------------------------------------
 * GIC-500 distributor/redistributor bases, the SGI number used for inter-core
 * signalling, and the second-core release address are all owned by the SDK's
 * DPL and its SMP startup.  Note only that TI reserves SGI 0 for inter-core
 * communication in SMP FreeRTOS: application code must not use it.
 */

#endif /* AM64X_A53_SOC_H */
