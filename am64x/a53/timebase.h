/* timebase.h — cross-core time base for the AM64x A53 SMP experiment.
 *
 * WHY NOT THE PMU
 * ---------------
 * The STM32WB55 port reads DWT CYCCNT; the R5F port reads PMU PMCCNTR.  On a
 * 2-core SMP system neither is usable as the primary time base, because each
 * core has its OWN PMU.  Core 0 and core 1 sample independent counters with
 * unrelated origins, so any interval whose endpoints land on different cores —
 * exactly what a migrating task produces — is meaningless.  It would not fail
 * loudly; it would produce plausible garbage.
 *
 * WHAT WE USE INSTEAD
 * -------------------
 * The ARM Generic Timer.  CNTVCT_EL0 is driven by the system counter, which is
 * architecturally common to every core in the cluster, so timestamps taken on
 * different cores are directly comparable.  That is the property this
 * experiment needs and the PMU cannot provide.
 *
 * Three further wins over the Cortex-M and R5F ports:
 *
 *   - 64-bit.  No wrap handling at all, versus ~67 s on the STM32 and a
 *     distinctly uncomfortable ~5.37 s on the R5F at 800 MHz.
 *   - Self-describing.  CNTFRQ_EL0 reports the counter's own frequency, so
 *     the firmware no longer has to *assume* a clock the way the R5F port
 *     assumes 800 MHz.  The banner prints the measured value and latency.py
 *     reads it back, which removes an entire class of silent rescaling error.
 *   - Unaffected by CPU DVFS.  The system counter runs at a fixed rate even
 *     if the A53 clock changes.
 *
 * The cost is resolution: the system counter typically runs in the hundreds of
 * MHz rather than at the ~1 GHz core clock.  For reference, TI's own SMP
 * task-switch example on this SoC measures switches in the 9,000-16,000 ns
 * range, which is thousands of counter ticks — resolution is not the binding
 * constraint here.
 *
 * Units note: this port reports TICKS of the system counter, not CPU cycles.
 * latency.py converts using the frequency from the banner.  Do not compare raw
 * tick counts against the cycle counts from the other three targets.
 */
#ifndef AM64X_A53_TIMEBASE_H
#define AM64X_A53_TIMEBASE_H

#include <stdint.h>

/* Reading CNTVCT_EL0 can be speculated ahead of preceding instructions.  The
 * ISB pins the sample to this point in program order; without it a timestamp
 * can drift out of the region it is supposed to bracket.  This matters most in
 * the trace hooks, where the interval being measured is short. */
static inline uint64_t timebase_now(void)
{
    uint64_t value;
    __asm volatile ("isb\n\t"
                    "mrs %0, cntvct_el0"
                    : "=r" (value) :: "memory");
    return value;
}

/* System counter frequency in Hz, as reported by the hardware itself. */
static inline uint32_t timebase_hz(void)
{
    uint64_t value;
    __asm volatile ("mrs %0, cntfrq_el0" : "=r" (value));
    return (uint32_t)value;
}

/* Physical core index from MPIDR_EL1.Aff0.
 *
 * FreeRTOS SMP also exposes portGET_CORE_ID(), which returns the kernel's view.
 * We deliberately read the hardware register instead: the trace hooks run
 * inside the scheduler, and attributing a sample to the core that actually
 * executed it — rather than to whatever the kernel currently believes — is the
 * property we need.  The two should agree; if they ever disagree, that is
 * itself a finding worth chasing rather than papering over. */
static inline uint32_t timebase_core_id(void)
{
    uint64_t value;
    __asm volatile ("mrs %0, mpidr_el1" : "=r" (value));
    return (uint32_t)(value & 0xFFU);
}

/* ---- Memory ordering ----------------------------------------------------
 * ARMv8-A is weakly ordered.  Cortex-M is not, which is why the STM32 and
 * QEMU ports get away with a bare volatile ring buffer: on those cores the
 * data write and the subsequent index write cannot be observed out of order.
 * On A53 they can, and a consumer running on the other core would then read a
 * slot that has been published but not yet filled.
 *
 * "ish" (inner shareable) is the right domain: it covers the two A53 cores,
 * which is exactly the set of observers that matters here.
 */
#define TIMEBASE_PUBLISH_BARRIER()  __asm volatile ("dmb ish" ::: "memory")
#define TIMEBASE_CONSUME_BARRIER()  __asm volatile ("dmb ish" ::: "memory")

#endif /* AM64X_A53_TIMEBASE_H */
