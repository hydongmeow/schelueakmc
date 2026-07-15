/* main.c — FreeRTOS scheduler-latency experiment (QEMU lm3s6965evb) */
#include "uart.h"
#include "cycle_counter.h"
#include "FreeRTOS.h"
#include "task.h"

/* ---- Workload definition (in cycles @ configCPU_CLOCK_HZ = 16 MHz) -------
 * NOTE: the work loops spin on the cycle counter rather than on a fixed
 * iteration count.  Under QEMU the host executes the guest at an unknown
 * speed, so "for (i = 0; i < 160000; i++)" takes an unpredictable amount of
 * *emulated* time.  Spinning on the timer makes the CPU load deterministic.
 */
#define CRITICAL_WORK_CYCLES   160000u              /* 10 ms */
#define CRITICAL_PERIOD_MS     20
#define MEDIUM_WORK_CYCLES      80000u              /*  5 ms */
#define MEDIUM_PERIOD_MS       30
#define OBSERVER_PERIOD_MS      1

/* Anything longer than 1.5 ticks between two Observer runs means it was
 * held off the CPU by a higher-priority task. */
#define PREEMPTION_THRESHOLD   ((CYCLES_PER_TICK * 3u) / 2u)   /* 24000 */

/* Total utilisation: 10/20 + 5/30 = 66.7 %  -> the Observer still gets CPU. */

static void busy_cycles(uint32_t cycles)
{
    const uint32_t start = cycle_now();
    while ((cycle_now() - start) < cycles) {
        /* spin */
    }
}

/* ---- Context-switch instrumentation --------------------------------------
 * traceTASK_SWITCHED_OUT/IN (see FreeRTOSConfig.h) bracket the task-selection
 * critical section inside vTaskSwitchContext().  We time it with the RAW
 * SysTick down-counter, NOT cycle_now(): during a switch SysTick may reload
 * before its (deferred, same-priority) tick ISR runs, so a cycle_now() delta
 * could span an un-incremented tick and read garbage.  The down-counter delta
 * is wrap-safe for any interval shorter than one tick, which a switch always
 * is.  Samples are captured here (no I/O) and drained to UART by the Observer.
 */
#define CTXSW_BUF 256u                      /* must be a power of two */
static volatile uint32_t g_ctxsw[CTXSW_BUF];
static volatile uint32_t g_ctxsw_head = 0;  /* producer: PendSV context   */
static volatile uint32_t g_ctxsw_drop = 0;  /* samples lost to a full buf */
static uint32_t          g_ctxsw_tail = 0;  /* consumer: Observer task    */
static volatile uint32_t g_switch_out = 0;

void trace_switched_out(void)
{
    g_switch_out = SYST_CVR & 0x00FFFFFFu;      /* remaining count at switch-out */
}

void trace_switched_in(void)
{
    uint32_t t_in = SYST_CVR & 0x00FFFFFFu;
    /* SysTick counts DOWN, so normally t_in <= t_out; if it reloaded once
     * mid-switch, t_in > t_out and true elapsed spans the reload. */
    uint32_t d = (t_in <= g_switch_out)
               ? (g_switch_out - t_in)
               : (g_switch_out + CYCLES_PER_TICK - t_in);

    uint32_t h = g_ctxsw_head;
    if ((h - g_ctxsw_tail) < CTXSW_BUF) {       /* space available */
        g_ctxsw[h & (CTXSW_BUF - 1u)] = d;
        g_ctxsw_head = h + 1u;
    } else {
        g_ctxsw_drop++;
    }
}

/* Drain up to `limit` samples to UART.  Called from the Observer (task
 * context), so UART blocking here does not distort the switch timing. */
static void ctxsw_drain(uint32_t limit)
{
    uint32_t n = 0;
    while (g_ctxsw_tail != g_ctxsw_head && n < limit) {
        uint32_t d = g_ctxsw[g_ctxsw_tail & (CTXSW_BUF - 1u)];
        g_ctxsw_tail++;
        n++;
        /* A real selection is well under half a tick; anything larger is a
         * SysTick-reload artifact and is discarded rather than reported. */
        if (d <= (CYCLES_PER_TICK / 2u)) {
            uart_puts("[CTXSW] Cycles: ");
            uart_putu32(d);
            uart_puts("\r\n");
        }
    }
}

/* Priority 3 — highest.  Never preempted, so its span == its work. */
static void vTaskCritical(void *pv)
{
    TickType_t xLast = xTaskGetTickCount();
    (void)pv;

    for (;;) {
        uint32_t start = cycle_now();
        busy_cycles(CRITICAL_WORK_CYCLES);
        uint32_t end = cycle_now();

        uart_puts("[CRITICAL] Cycles: ");
        uart_putu32(end - start);
        uart_puts("\r\n");

        vTaskDelayUntil(&xLast, pdMS_TO_TICKS(CRITICAL_PERIOD_MS));
    }
}

/* Priority 2 — preempted by CRITICAL, so its span > its work.  The excess is
 * the interference it suffers. */
static void vTaskMedium(void *pv)
{
    TickType_t xLast = xTaskGetTickCount();
    (void)pv;

    for (;;) {
        uint32_t start = cycle_now();
        busy_cycles(MEDIUM_WORK_CYCLES);
        uint32_t end = cycle_now();

        uart_puts("[MEDIUM] Cycles: ");
        uart_putu32(end - start);
        uart_puts("\r\n");

        vTaskDelayUntil(&xLast, pdMS_TO_TICKS(MEDIUM_PERIOD_MS));
    }
}

/* Priority 1 — lowest.  Samples every tick and reports how long it was
 * blocked (the "attacker's view" of the higher-priority workload). */
static void vTaskObserver(void *pv)
{
    uint32_t last_end = 0;
    int      primed   = 0;
    (void)pv;

    for (;;) {
        uint32_t start = cycle_now();

        if (primed) {
            uint32_t gap = start - last_end;      /* wrap-safe */
            if (gap > PREEMPTION_THRESHOLD) {
                uart_puts("[OBSERVER] Preemption gap: ");
                uart_putu32(gap);
                uart_puts(" cycles\r\n");
            }
        }

        /* Emit any context switches captured since the last wake.  Bounded
         * per iteration so a burst can't monopolise the observer.  Done
         * BEFORE last_end is stamped, so this I/O is excluded from the gap. */
        ctxsw_drain(32u);

        busy_cycles(200u);                        /* tiny, ~12 us */

        last_end = cycle_now();
        primed   = 1;
        /* vTaskDelay (not DelayUntil): we want it to re-arm relative to when it
         * actually got to run, so a long block shows up as one large gap. */
        vTaskDelay(pdMS_TO_TICKS(OBSERVER_PERIOD_MS));
    }
}

int main(void)
{
    cycle_counter_init();

    uart_puts("=== FreeRTOS Scheduler Latency Test ===\r\n");

    xTaskCreate(vTaskCritical, "Critical", 256, NULL, 3, NULL);
    xTaskCreate(vTaskMedium,   "Medium",   256, NULL, 2, NULL);
    xTaskCreate(vTaskObserver, "Observer", 256, NULL, 1, NULL);

    vTaskStartScheduler();

    for (;;) { }        /* only reached if the heap is too small */
}
