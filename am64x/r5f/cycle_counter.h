/* cycle_counter.h — Cortex-R5F cycle counter for AM64x.
 *
 * The STM32WB55 port reads the Cortex-M DWT CYCCNT.  ARMv7-R has no DWT; the
 * equivalent free-running cycle counter is PMCCNTR in the ARMv7 Performance
 * Monitor Unit, reached through CP15 rather than a memory-mapped register.
 *
 * Both are 32-bit and both wrap, so the wrap-safe unsigned subtraction used
 * throughout the experiment carries over unchanged.  What does NOT carry over
 * is the wrap period: at 800 MHz a 32-bit counter wraps every ~5.37 s, versus
 * ~67 s at the STM32's 64 MHz.  Every interval this experiment measures is far
 * below 5 s, so the arithmetic stays valid, but see PORTING.md before reusing
 * this header for anything that measures longer spans.
 *
 * PMCR.D (bit 3) MUST stay clear.  Setting it makes PMCCNTR count once per 64
 * cycles, which would silently divide every reported latency by 64 — the kind
 * of error that still produces plausible-looking numbers.
 */
#ifndef AM64X_CYCLE_COUNTER_H
#define AM64X_CYCLE_COUNTER_H

#include <stdint.h>
#include "FreeRTOS.h"

/* ARMv7 PMU control bits (PMCR, CP15 c9 c12 0). */
#define PMU_PMCR_E              (1UL << 0)  /* enable all counters           */
#define PMU_PMCR_P              (1UL << 1)  /* reset event counters          */
#define PMU_PMCR_C              (1UL << 2)  /* reset cycle counter           */
#define PMU_PMCR_D              (1UL << 3)  /* divide-by-64  (must be 0)     */

/* PMCNTENSET (CP15 c9 c12 1) bit 31 enables the dedicated cycle counter. */
#define PMU_CNTEN_CCNT          (1UL << 31)

#define CYCLES_PER_TICK \
    ((uint32_t)(configCPU_CLOCK_HZ / configTICK_RATE_HZ))

static inline void cycle_counter_init(void)
{
    uint32_t pmcr;

    /* Read-modify-write so we do not clobber counters another component (for
     * example an SDK profiling hook) may already be using. */
    __asm volatile ("mrc p15, 0, %0, c9, c12, 0" : "=r" (pmcr));
    pmcr &= ~PMU_PMCR_D;                       /* 1 cycle per count */
    pmcr |= (PMU_PMCR_E | PMU_PMCR_C);         /* enable, reset CCNT */
    __asm volatile ("mcr p15, 0, %0, c9, c12, 0" :: "r" (pmcr));

    /* Enable the cycle counter itself. */
    __asm volatile ("mcr p15, 0, %0, c9, c12, 1" :: "r" (PMU_CNTEN_CCNT));

    /* Allow overflow to be observed rather than trapped: clear the CCNT
     * overflow flag so a stale bit does not confuse later inspection. */
    __asm volatile ("mcr p15, 0, %0, c9, c12, 3" :: "r" (PMU_CNTEN_CCNT));

    __asm volatile ("dsb" ::: "memory");
    __asm volatile ("isb" ::: "memory");
}

static inline uint32_t cycle_now(void)
{
    uint32_t value;
    __asm volatile ("mrc p15, 0, %0, c9, c13, 0" : "=r" (value));
    return value;
}

#endif /* AM64X_CYCLE_COUNTER_H */
