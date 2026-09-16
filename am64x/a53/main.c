/* main.c — entry point for the AM64x A53 SMP scheduler-latency experiment.
 *
 * SMP BOOT SHAPE (verified against the SDK, not assumed)
 * ------------------------------------------------------
 * The SDK's AArch64 startup (source/kernel/nortos/dpl/a53/boot_armv8.c) does
 * NOT park the secondary core.  Both A53s run _c_int00 -> __system_start ->
 * main().  Core 0 clears .bss and builds the MMU tables while core 1 spins on
 * flags; then BOTH cores enter main().  This is why, in CCS, the two cores must
 * be launched as a sync group: they execute the same binary from the same
 * entry point and rendezvous in software.
 *
 * The scheduler is started asymmetrically, exactly as in the SDK's own
 * smp_task_switch example:
 *
 *   core 0:  create tasks, vTaskStartScheduler()   -> sets ullPortSchedularRunning
 *   core 1:  spin until ullPortSchedularRunning, then xPortStartScheduler()
 *
 * Getting this wrong is silent: calling vTaskStartScheduler() on both cores
 * creates two idle tasks per core and two timer tasks, and "works".
 *
 * Task creation happens in a short-lived init task pinned to core 0 rather
 * than before the scheduler starts, because Drivers_open() (which brings up
 * the UART the SDK's DebugP logging uses) expects a running scheduler.  That
 * task creates the experiment and deletes itself; it never runs again and is
 * not part of the measured task set.
 *
 * As on the R5F port, the SDK is confined to this file.  experiment.c and
 * timebase.h contain no SDK calls, so the measurement path owes nothing to it.
 */
#include <kernel/dpl/DebugP.h>
#include <drivers/sciclient.h>
#include <drivers/hw_include/cslr_soc.h>
#include "ti_drivers_config.h"
#include "ti_drivers_open_close.h"
#include "ti_board_config.h"
#include "ti_board_open_close.h"

#include "FreeRTOS.h"
#include "task.h"

#include "timebase.h"
#include "console.h"
#include "experiment.h"

/* Set by the port on core 0 inside xPortStartScheduler(); core 1 waits on it. */
extern volatile uint64_t ullPortSchedularRunning;

/* ---- System counter (GTC) bring-up ---------------------------------------
 * The ARM Generic Timer on AM64x is fed by the GTC module.  On a Linux system
 * U-Boot powers the GTC, sets its enable bit and writes CNTFRQ_EL0 from EL3.
 * In this RTOS-only boot chain (ROM -> SBL -> us) nobody does any of that:
 * observed on SK-AM64B with SDK 11.01, CNTFRQ_EL0 reads 0 and the counter is
 * stopped.  CNTFRQ_EL0 cannot be fixed from EL1, so the rate is taken from the
 * DM firmware (TISCI clock query) and handed to experiment.c instead; the
 * counter itself is enabled through its CNTCR register.
 *
 * CNTCR is the first register of the GTC "CFG1" block (0x00A90000), the same
 * block and bit U-Boot's k3 code uses.  HDBG (bit 1) is left clear so the
 * counter keeps running if a debugger halts a core.
 */
#define GTC_CNTCR_ADDR   (CSL_GTC0_GTC_CFG1_BASE + 0x00U)
#define GTC_CNTFID0_ADDR (CSL_GTC0_GTC_CFG1_BASE + 0x20U)
#define GTC_CNTCR_EN     (1U << 0)

static uint32_t gtc_bring_up(void)
{
    uint64_t hz = 0ULL;

    if (Sciclient_pmSetModuleState(TISCI_DEV_GTC0,
                                   TISCI_MSG_VALUE_DEVICE_SW_STATE_ON,
                                   TISCI_MSG_FLAG_AOP,
                                   SystemP_WAIT_FOREVER) != SystemP_SUCCESS) {
        console_puts("WARN: TISCI refused to power GTC0\r\n");
        return 0U;
    }
    if (Sciclient_pmGetModuleClkFreq(TISCI_DEV_GTC0, TISCI_DEV_GTC0_GTC_CLK,
                                     &hz, SystemP_WAIT_FOREVER) != SystemP_SUCCESS) {
        console_puts("WARN: TISCI could not report the GTC0 clock rate\r\n");
        return 0U;
    }

    /* Record the rate in the block's own frequency-ID slot (informational,
     * mirrors U-Boot) and start the counter. */
    *(volatile uint32_t *)GTC_CNTFID0_ADDR = (uint32_t)hz;
    *(volatile uint32_t *)GTC_CNTCR_ADDR  |= GTC_CNTCR_EN;

    return (uint32_t)hz;
}

#define INIT_TASK_PRIORITY   (configMAX_PRIORITIES - 1)
#define INIT_TASK_STACK      (16384U / sizeof(configSTACK_DEPTH_TYPE))
#define BOOT_CORE            0U

static StackType_t g_init_stack[INIT_TASK_STACK] __attribute__((aligned(32)));
static StaticTask_t g_init_tcb;

static void halt_forever(void)
{
    for (;;) {
        __asm volatile ("wfi");
    }
}

/* Runs once on core 0 at the highest priority, then disappears. */
static void vTaskInit(void *argument)
{
    (void)argument;

    /* UART0 driver open: needed only so DebugP_log/assert output from the SDK
     * is visible during bring-up.  The experiment's own console writes the
     * THR directly (console.c) and does not go through this driver. */
    Drivers_open();
    Board_driversOpen();

    /* Time base: see gtc_bring_up().  CNTVCT_EL0 itself needs no per-core
     * setup (unlike the R5F's PMU); experiment_start() verifies that the rate
     * is known and that the counter actually advances, and halts otherwise. */
    experiment_set_timebase_hz(gtc_bring_up());

    if (experiment_start() != pdPASS) {
        console_puts("FATAL: task or queue creation failed\r\n");
        console_flush();
        halt_forever();
    }

    vTaskDelete(NULL);
}

int main(void)
{
    /* Both cores run these, as in the SDK example: per-core GIC/timer state
     * is initialised here and the SDK guards the shared parts internally. */
    System_init();
    Board_init();

    if (timebase_core_id() == BOOT_CORE) {
        TaskHandle_t init = xTaskCreateStatic(vTaskInit, "init",
                                              INIT_TASK_STACK, NULL,
                                              INIT_TASK_PRIORITY,
                                              g_init_stack, &g_init_tcb);
        if (init == NULL) {
            console_puts("FATAL: init task creation failed\r\n");
            console_flush();
            halt_forever();
        }
        vTaskCoreAffinitySet(init, (UBaseType_t)(1UL << BOOT_CORE));

        vTaskStartScheduler();

        console_puts("FATAL: scheduler did not start\r\n");
        console_flush();
    } else {
        while (ullPortSchedularRunning == 0U) {
            /* core 0 has not started the kernel yet */
        }
        xPortStartScheduler();
    }

    halt_forever();
    return 0;
}
