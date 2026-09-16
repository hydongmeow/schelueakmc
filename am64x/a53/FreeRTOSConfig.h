/* FreeRTOSConfig.h — AM64x dual Cortex-A53, FreeRTOS SMP (TI MCU+ SDK).
 *
 * WHY THIS FILE IS A THIN OVERLAY AND NOT A FULL CONFIG
 * -----------------------------------------------------
 * The MCU+ SDK ships its drivers and board libraries PREBUILT against its own
 * FreeRTOSConfig.h (source/kernel/freertos/config/am64x/a53-smp/).  Those
 * libraries embed kernel structures — TaskP_Object contains a StaticTask_t —
 * so any option that changes the TCB layout (run-time stats, trace facility,
 * stack-overflow checking, ...) would silently desynchronise this application
 * from the prebuilt libraries.  Nothing would fail to link; it would corrupt
 * memory at run time.
 *
 * So this file pulls in TI's configuration unchanged via #include_next (GCC
 * extension; this directory is placed before the SDK config directory on the
 * include path) and then adds ONLY things that do not touch struct layout:
 *
 *   - the traceTASK_SWITCHED_OUT/IN instrumentation hooks (the measurement),
 *   - INCLUDE_ switches for a few API functions the experiment calls,
 *   - compile-time checks that the SMP options the experiment depends on are
 *     still what we think they are.
 *
 * THE HOOKS ONLY TAKE EFFECT IF THE KERNEL IS REBUILT
 * ----------------------------------------------------
 * traceTASK_SWITCHED_OUT/IN are expanded inside tasks.c.  The SDK's prebuilt
 * freertos.am64x.a53-smp.gcc-aarch64.release.lib was compiled without them, so
 * linking against that library gives a capture with NO [CTXSW] records while
 * everything else looks healthy.  The makefile therefore compiles the kernel
 * and DPL sources from the SDK tree against THIS file into a local library and
 * links that instead.  See makefile, target $(KERNEL_LIB).
 */
#ifndef EXPERIMENT_FREERTOS_CONFIG_H
#define EXPERIMENT_FREERTOS_CONFIG_H

#include_next "FreeRTOSConfig.h"   /* TI's config/am64x/a53-smp/FreeRTOSConfig.h */

/* ---- Sanity checks against the SDK config -------------------------------
 * TI's SMP kernel is the FreeRTOS SMP branch (V202110.00-SMP) and spells the
 * core count configNUM_CORES, not the V11 name configNUMBER_OF_CORES.
 */
#ifndef configNUM_CORES
#error "TI SMP config did not define configNUM_CORES; is the include path pointing at config/am64x/a53-smp?"
#endif
#if (configNUM_CORES != 2)
#error "configNUM_CORES must be 2: at 1 this is not an SMP build and the experiment is meaningless"
#endif
#if (configRUN_MULTIPLE_PRIORITIES != 1)
#error "configRUN_MULTIPLE_PRIORITIES must be 1; at 0 only equal priorities co-schedule and the experiment degenerates"
#endif
#if (configUSE_CORE_AFFINITY != 1)
#error "configUSE_CORE_AFFINITY must be 1 (EXPERIMENT_AFFINITY_MODE=1 and the boot-core init task depend on it)"
#endif
#if (configTICK_RATE_HZ != 1000)
#error "configTICK_RATE_HZ must be 1000 to match the other targets"
#endif

/* ---- API functions the experiment calls that TI's config leaves out ------
 * These only gate whether a function is compiled into tasks.c; they do not
 * change any structure layout, so they are safe to add here.
 */
#undef  INCLUDE_xTaskDelayUntil
#define INCLUDE_xTaskDelayUntil                 (1)
#undef  INCLUDE_uxTaskGetStackHighWaterMark
#define INCLUDE_uxTaskGetStackHighWaterMark     (1)
#undef  INCLUDE_xTaskGetSchedulerState
#define INCLUDE_xTaskGetSchedulerState          (1)

/* ---- Behavioural (not layout) alignment with the single-core targets ----
 * TI ships configUSE_TIME_SLICING=0 ("same functionality as SysBIOS6").  The
 * QEMU, STM32WB55 and R5F targets all run with 1, and at EXPERIMENT_LOAD_SCALE
 * >= 2 this build has equal-priority task pairs for which slicing matters.
 * Time slicing changes only what the tick handler does, not any structure, so
 * it is safe to override here.
 */
#undef  configUSE_TIME_SLICING
#define configUSE_TIME_SLICING                  (1)

/* ---- Known, accepted measurement overhead -------------------------------
 * TI's config sets configGENERATE_RUN_TIME_STATS=1 (via configOPTIMIZE_FOR_
 * LATENCY=0).  In this kernel that puts one ClockP_getTimeUsec() call — a
 * DMTimer register read plus a 64-bit multiply — between
 * traceTASK_SWITCHED_OUT() and traceTASK_SWITCHED_IN(), i.e. inside the
 * measured L_ctxsw window.  Turning it off would change the TCB layout and
 * break the prebuilt driver/board libraries, so it is left on.  It is a
 * near-constant offset on every sample on both cores and is documented in
 * README.md; the single-core targets do not carry it.
 */

/* ---- EXPERIMENT: context-switch instrumentation -------------------------
 * Same definition of L_ctxsw as the QEMU, STM32WB55 and R5F targets: the hooks
 * bracket the task-selection critical section inside vTaskSwitchContext().
 *
 * Under SMP these fire on BOTH cores concurrently.  The handlers keep per-core
 * state for exactly that reason — see experiment.c.  Do not "simplify" them
 * back to a single shared timestamp.
 *
 * They are declared here rather than pulled from experiment.h because
 * FreeRTOS.h includes this file before task.h exists; keep them free of
 * kernel types.
 */
void trace_switched_out(void);
void trace_switched_in(void);
#define traceTASK_SWITCHED_OUT() trace_switched_out()
#define traceTASK_SWITCHED_IN()  trace_switched_in()
/* ---- EXPERIMENT: end ---------------------------------------------------- */

#endif /* EXPERIMENT_FREERTOS_CONFIG_H */
