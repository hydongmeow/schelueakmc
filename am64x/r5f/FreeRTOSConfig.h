/* FreeRTOSConfig.h — AM64x MAIN_R5FSS0_CORE0.
 *
 * Derived from the TI MCU+ SDK FreeRTOS r5f template.  If you regenerate the
 * project from SysConfig, SysConfig will overwrite this file: re-apply the two
 * blocks marked EXPERIMENT below, or the capture will be silently missing all
 * [CTXSW] samples while otherwise looking healthy.
 */
#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H

#include <stdint.h>

/* ---- Clock --------------------------------------------------------------
 * Must match AM64X_R5F_CLOCK_HZ in am64x_soc.h and CPU_HZ in latency.py.
 * These three constants are the whole cycles-to-microseconds chain; a
 * mismatch rescales every published number without any visible symptom.
 */
#define configCPU_CLOCK_HZ                      800000000U
#define configTICK_RATE_HZ                      1000U

#define configUSE_PREEMPTION                    1
#define configUSE_TIME_SLICING                  1
#define configUSE_PORT_OPTIMISED_TASK_SELECTION 0
#define configMAX_PRIORITIES                    16
#define configMINIMAL_STACK_SIZE                512
#define configMAX_TASK_NAME_LEN                 16
#define configUSE_16_BIT_TICKS                  0
#define configIDLE_SHOULD_YIELD                 1

/* The Critical task alone is 8 MiB/s of log traffic at peak; the queue and the
 * four task stacks dominate. 128 KiB leaves headroom for the SDK's own
 * allocations. */
#define configTOTAL_HEAP_SIZE                   (128U * 1024U)
#define configSUPPORT_DYNAMIC_ALLOCATION        1
#define configSUPPORT_STATIC_ALLOCATION         1

#define configUSE_MUTEXES                       1
#define configUSE_RECURSIVE_MUTEXES             1
#define configUSE_COUNTING_SEMAPHORES           1
#define configQUEUE_REGISTRY_SIZE               16
#define configUSE_QUEUE_SETS                    0

#define configUSE_TIMERS                        1
#define configTIMER_TASK_PRIORITY               (configMAX_PRIORITIES - 1)
#define configTIMER_QUEUE_LENGTH                16
#define configTIMER_TASK_STACK_DEPTH            1024

#define configUSE_IDLE_HOOK                     0
#define configUSE_TICK_HOOK                     0
#define configUSE_MALLOC_FAILED_HOOK            1
#define configCHECK_FOR_STACK_OVERFLOW          2

/* ---- Diagnostics --------------------------------------------------------
 * Runtime stats are OFF on purpose.  configGENERATE_RUN_TIME_STATS installs a
 * counter read on every context switch, which lands inside the very window
 * trace_switched_out/in is measuring and would inflate L_ctxsw.  Turn it on
 * only when you are not collecting publishable numbers.
 */
#define configUSE_TRACE_FACILITY                1
#define configGENERATE_RUN_TIME_STATS           0
#define configUSE_STATS_FORMATTING_FUNCTIONS    0

#define INCLUDE_vTaskPrioritySet                1
#define INCLUDE_uxTaskPriorityGet               1
#define INCLUDE_vTaskDelete                     1
#define INCLUDE_vTaskSuspend                    1
#define INCLUDE_xTaskDelayUntil                 1
#define INCLUDE_vTaskDelay                      1
#define INCLUDE_xTaskGetSchedulerState          1
#define INCLUDE_uxTaskGetStackHighWaterMark     1

#define configASSERT(x) \
    if ((x) == 0) { taskDISABLE_INTERRUPTS(); for (;;) {} }

/* ---- EXPERIMENT: context-switch instrumentation -------------------------
 * traceTASK_SWITCHED_OUT/IN bracket the task-selection critical section inside
 * vTaskSwitchContext().  Identical to the QEMU and STM32WB55 ports; this is
 * the definition of L_ctxsw for all three targets.
 *
 * These run with interrupts masked in switch context: capture only, never I/O.
 */
void trace_switched_out(void);
void trace_switched_in(void);
#define traceTASK_SWITCHED_OUT() trace_switched_out()
#define traceTASK_SWITCHED_IN()  trace_switched_in()

/* ---- EXPERIMENT: end ---------------------------------------------------- */

#endif /* FREERTOS_CONFIG_H */
