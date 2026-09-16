#ifndef AM64X_EXPERIMENT_H
#define AM64X_EXPERIMENT_H

#include "FreeRTOS.h"

/* ---- Hot-path placement -------------------------------------------------
 * Unlike the Cortex-M4F on STM32WB55, the AM64x R5F has 32 KiB of L1 I-cache
 * and 32 KiB of L1 D-cache.  That changes the timing distribution this
 * experiment measures: cache hits and misses become a second source of
 * variance layered on top of scheduler latency.
 *
 * Set EXPERIMENT_HOT_PATH_IN_TCM to choose which system you are measuring:
 *
 *   1 (default)  Spin loops, trace hooks and the context-switch ring buffer
 *                live in zero-wait-state TCM.  Removes cache variance and
 *                gives the closest comparison to the STM32WB55 numbers.
 *
 *   0            Everything runs from cached MSRAM.  Realistic for production
 *                AM64x software, and the configuration to use if the cache
 *                itself is the object of study.
 *
 * Whichever you pick, record it alongside the capture: the two configurations
 * are not comparable with each other.
 */
#ifndef EXPERIMENT_HOT_PATH_IN_TCM
#define EXPERIMENT_HOT_PATH_IN_TCM 1
#endif

#if EXPERIMENT_HOT_PATH_IN_TCM
#define EXPERIMENT_HOT_TEXT __attribute__((section(".tcm_text")))
#define EXPERIMENT_HOT_DATA __attribute__((section(".tcm_data")))
#else
#define EXPERIMENT_HOT_TEXT
#define EXPERIMENT_HOT_DATA
#endif

/* Creates the Critical / Medium / Observer / Logger task set.  Returns pdPASS
 * only if every task and the log queue were created. */
BaseType_t experiment_start(void);

/* Wired to traceTASK_SWITCHED_OUT/IN in FreeRTOSConfig.h.  Declared here so
 * the config header and the implementation cannot drift apart. */
void trace_switched_out(void);
void trace_switched_in(void);

#endif /* AM64X_EXPERIMENT_H */
