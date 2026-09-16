/* main.c — entry point for the AM64x scheduler-latency experiment on
 * MAIN_R5FSS0_CORE0.
 *
 * This is the only file that depends on the TI MCU+ SDK.  The split is
 * deliberate: everything the paper measures lives in experiment.c and
 * cycle_counter.h with no SDK involvement, so the SDK cannot be blamed for —
 * or perturb — the numbers.  What the SDK is used for here is exactly the part
 * that cannot responsibly be hand-rolled on this SoC:
 *
 *   System_init()  brings up the DPL: the FreeRTOS tick (ClockP on a DMTimer),
 *                  the interrupt controller (HwiP on the R5F VIM), and the
 *                  SysConfig-generated peripheral configuration.
 *   Board_init()   applies board-level pinmux and power/clock requests, which
 *                  on AM64x are TISCI messages to the TIFS firmware rather
 *                  than register writes.  There is no bare-metal shortcut.
 *
 * See PORTING.md, section "为什么引入 TI MCU+ SDK".
 */
#include <kernel/dpl/DebugP.h>
#include "ti_drivers_config.h"
#include "ti_drivers_open_close.h"
#include "ti_board_open_close.h"

#include "FreeRTOS.h"
#include "task.h"

#include "cycle_counter.h"
#include "console.h"
#include "experiment.h"

static void halt_forever(void)
{
    for (;;) {
        __asm volatile ("wfi");
    }
}

int main(void)
{
    System_init();
    Board_init();

    /* Must run before any task samples the clock.  Enables PMCCNTR and clears
     * the divide-by-64 mode that would otherwise scale every measurement. */
    cycle_counter_init();
    console_init();

    if (experiment_start() != pdPASS) {
        console_puts("FATAL: task or queue creation failed\r\n");
        console_flush();
        halt_forever();
    }

    vTaskStartScheduler();

    /* Only reached if the scheduler could not start — almost always an
     * undersized configTOTAL_HEAP_SIZE. */
    console_puts("FATAL: scheduler did not start\r\n");
    console_flush();
    halt_forever();
    return 0;
}
