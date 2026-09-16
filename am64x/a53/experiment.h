#ifndef AM64X_A53_EXPERIMENT_H
#define AM64X_A53_EXPERIMENT_H

#include "FreeRTOS.h"

/* ---- Load scaling -------------------------------------------------------
 * The single-core targets run one Critical (10 ms every 20 ms, 50 % duty) and
 * one Medium (5 ms every 30 ms, 16.7 %), for a total utilisation of 0.667 —
 * enough to starve the Observer regularly and produce a signal.
 *
 * Transplanting that workload unchanged onto two cores spreads 0.667 across
 * 2.0 of capacity: about 33 % per core.  The Observer would then almost never
 * be denied a core, the preemption-gap channel would go nearly silent, and the
 * capture would look "clean" while actually measuring nothing.  This is the
 * single easiest way to get a meaningless result out of this port.
 *
 * EXPERIMENT_LOAD_SCALE instantiates that many copies of each load task.  The
 * default of 2 restores 2 x 0.667 = 1.333 over two cores — 66.7 % per core,
 * matching the single-core targets' per-core pressure, which is the figure
 * that makes cross-platform comparison defensible.
 *
 * Raising it past 3 pushes utilisation above 2.0 and the task set becomes
 * unschedulable; deadlines are missed by design and the spans stop meaning
 * what they mean elsewhere.  That is a legitimate experiment (the paper's
 * task_configs_l.json uses utilisation 1.1-1.2, which is infeasible on one
 * core and is presumably meant for exactly this) but it is a different one.
 */
#ifndef EXPERIMENT_LOAD_SCALE
#define EXPERIMENT_LOAD_SCALE 2
#endif

/* ---- Affinity mode ------------------------------------------------------
 * 0  Free migration.  Every task may run on either core and the scheduler
 *    places them.  This is real SMP and the configuration that matches the
 *    "multi-core RTOS scheduler" threat model: the attacker task does not get
 *    to choose where it runs.  Default.
 *
 * 1  Observer pinned to core 1, load pinned to core 0.  There is then no
 *    shared runqueue between attacker and victim, so what remains is the
 *    cross-core interference channel (shared L2, DDR, interconnect) rather
 *    than a scheduler channel.  Useful as a control: whatever signal survives
 *    in mode 1 is NOT attributable to the scheduler.
 *
 * Requires configUSE_CORE_AFFINITY (TI's a53-smp config sets it).  Record the mode with every capture; the
 * two are not comparable.
 */
#ifndef EXPERIMENT_AFFINITY_MODE
#define EXPERIMENT_AFFINITY_MODE 0
#endif

#if (EXPERIMENT_LOAD_SCALE < 1) || (EXPERIMENT_LOAD_SCALE > 4)
#error "EXPERIMENT_LOAD_SCALE must be 1..4"
#endif

/* Time-base rate fallback.  CNTFRQ_EL0 is the architectural source for the
 * system counter frequency, but it is writable only at EL3 and on an
 * SBL-booted AM64x nothing (no U-Boot, no ATF) ever programs it: it reads 0
 * (observed on SK-AM64B, SDK 11.01).  main.c obtains the GTC clock rate from
 * the DM firmware over TISCI and hands it in here before experiment_start().
 * derive_constants() still prefers a non-zero CNTFRQ_EL0; the banner records
 * which source was used.  hz == 0 means "no fallback available". */
void experiment_set_timebase_hz(uint32_t hz);

/* Creates the load, Observer and Logger tasks.  pdPASS only if everything was
 * created and the log queue exists. */
BaseType_t experiment_start(void);

/* Wired to traceTASK_SWITCHED_OUT/IN in FreeRTOSConfig.h.
 *
 * Unlike the single-core ports these keep PER-CORE state.  See experiment.c;
 * a single shared g_switch_out is silently wrong under SMP.
 */
void trace_switched_out(void);
void trace_switched_in(void);

#endif /* AM64X_A53_EXPERIMENT_H */
