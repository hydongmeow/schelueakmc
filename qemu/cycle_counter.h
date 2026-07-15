/* cycle_counter.h — SysTick-derived cycle counter.
 *
 * QEMU's Cortex-M model does NOT implement the DWT unit: writes to
 * DEMCR.TRCENA / DWT_CTRL are ignored and DWT_CYCCNT always reads back 0.
 * SysTick, however, is fully modelled, so we build a 32-bit "cycle" counter
 * from (FreeRTOS tick count * cycles-per-tick) + (elapsed cycles inside tick).
 *
 * Resolution: 1 cycle.  Wraps every 2^32 cycles (~268 s @ 16 MHz) — uint32_t
 * subtraction handles the wrap correctly for any interval < 268 s.
 */
#ifndef CYCLE_COUNTER_H
#define CYCLE_COUNTER_H

#include <stdint.h>
#include "FreeRTOS.h"
#include "task.h"

#define SYST_CVR (*(volatile uint32_t *)0xE000E018)   /* current value */

/* FreeRTOS programs SYST_RVR = (configCPU_CLOCK_HZ / configTICK_RATE_HZ) - 1 */
#define CYCLES_PER_TICK ((uint32_t)(configCPU_CLOCK_HZ / configTICK_RATE_HZ))

static inline void cycle_counter_init(void) { /* SysTick is set up by the port */ }

static inline uint32_t cycle_now(void)
{
    uint32_t t1, t2, val;

    /* Re-read the tick: SysTick may wrap between the two reads. */
    do {
        t1  = (uint32_t)xTaskGetTickCount();
        val = SYST_CVR & 0x00FFFFFFu;
        t2  = (uint32_t)xTaskGetTickCount();
    } while (t1 != t2);

    if (val >= CYCLES_PER_TICK) {
        val = CYCLES_PER_TICK - 1u;          /* paranoia */
    }
    /* SysTick counts DOWN, so cycles elapsed in this tick = RVR - CVR */
    return (t1 * CYCLES_PER_TICK) + (CYCLES_PER_TICK - 1u - val);
}

#endif /* CYCLE_COUNTER_H */
