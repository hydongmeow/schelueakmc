/* experiment.c — FreeRTOS scheduler-latency experiment for AM64x
 * MAIN_R5FSS0_CORE0 (Cortex-R5F).
 *
 * The task set, priorities, periods and reporting format are deliberately
 * identical to stm32wb55/main.c so captures from the two boards can be
 * compared directly.  Only the platform bindings differ:
 *
 *   cycle source   DWT CYCCNT        -> PMU PMCCNTR   (cycle_counter.h)
 *   console        USB CDC           -> MAIN_UART0    (console.c)
 *   entry/init     bare-metal main   -> SDK System_init (main.c)
 *
 * Everything below this comment is platform-neutral apart from the hot-path
 * section attributes, which exist because the R5F has caches and the M4F does
 * not.  See experiment.h.
 */
#include <stdint.h>

#include "FreeRTOS.h"
#include "task.h"
#include "queue.h"

#include "cycle_counter.h"
#include "console.h"
#include "experiment.h"

#define CRITICAL_PERIOD_MS       20U
#define CRITICAL_WORK_MS         10U
#define MEDIUM_PERIOD_MS         30U
#define MEDIUM_WORK_MS            5U
#define OBSERVER_PERIOD_MS        1U

/* At 800 MHz, 10 ms is 8,000,000 cycles — comfortably inside uint32_t, but an
 * order of magnitude larger than the STM32's 640,000.  Keep the 64-bit
 * intermediate: configCPU_CLOCK_HZ * ms overflows 32 bits at ~5 ms. */
#define CYCLES_FROM_MS(ms) \
    ((uint32_t)(((uint64_t)configCPU_CLOCK_HZ * (uint64_t)(ms)) / 1000ULL))
#define CRITICAL_WORK_CYCLES CYCLES_FROM_MS(CRITICAL_WORK_MS)
#define MEDIUM_WORK_CYCLES   CYCLES_FROM_MS(MEDIUM_WORK_MS)
#define PREEMPTION_THRESHOLD ((CYCLES_PER_TICK * 3UL) / 2UL)

#define CTXSW_BUF_SIZE       512UL
#define CTXSW_DRAIN_LIMIT     64UL
#define LOG_QUEUE_LENGTH     512U
#define TASK_STACK_WORDS    1024U   /* R5F frames are larger than Cortex-M */

typedef enum {
    LOG_CRITICAL = 0,
    LOG_MEDIUM,
    LOG_PREEMPTION,
    LOG_CTXSW,
    LOG_CTXSW_DROPS,
    LOG_QUEUE_DROPS
} LogKind;

typedef struct {
    uint32_t kind;
    uint32_t value;
} LogEvent;

static QueueHandle_t g_log_queue;
static volatile uint32_t g_log_drops;

/* The ring buffer is written from switch context and read from the Observer.
 * Placing it in TCM keeps the producer side free of D-cache effects, which
 * would otherwise show up as spurious variance in the context-switch
 * distribution rather than in the workload it belongs to. */
EXPERIMENT_HOT_DATA static volatile uint32_t g_ctxsw[CTXSW_BUF_SIZE];
EXPERIMENT_HOT_DATA static volatile uint32_t g_ctxsw_head;
EXPERIMENT_HOT_DATA static volatile uint32_t g_ctxsw_tail;
EXPERIMENT_HOT_DATA static volatile uint32_t g_ctxsw_drops;
EXPERIMENT_HOT_DATA static volatile uint32_t g_switch_out;

EXPERIMENT_HOT_TEXT static void busy_cycles(uint32_t cycles)
{
    const uint32_t start = cycle_now();
    while ((uint32_t)(cycle_now() - start) < cycles) {
        __asm volatile ("nop");
    }
}

EXPERIMENT_HOT_TEXT void trace_switched_out(void)
{
    g_switch_out = cycle_now();
}

EXPERIMENT_HOT_TEXT void trace_switched_in(void)
{
    const uint32_t elapsed = (uint32_t)(cycle_now() - g_switch_out);
    const uint32_t head = g_ctxsw_head;

    if ((uint32_t)(head - g_ctxsw_tail) < CTXSW_BUF_SIZE) {
        g_ctxsw[head & (CTXSW_BUF_SIZE - 1UL)] = elapsed;
        g_ctxsw_head = head + 1UL;
    } else {
        g_ctxsw_drops++;
    }
}

static void log_submit(LogKind kind, uint32_t value)
{
    const LogEvent event = { (uint32_t)kind, value };

    if (xQueueSend(g_log_queue, &event, 0U) != pdPASS) {
        taskENTER_CRITICAL();
        g_log_drops++;
        taskEXIT_CRITICAL();
    }
}

static void ctxsw_drain(uint32_t limit)
{
    uint32_t count = 0UL;

    while ((g_ctxsw_tail != g_ctxsw_head) && (count < limit)) {
        const uint32_t tail = g_ctxsw_tail;
        const uint32_t elapsed = g_ctxsw[tail & (CTXSW_BUF_SIZE - 1UL)];
        g_ctxsw_tail = tail + 1UL;
        count++;

        /* PMCCNTR is wrap-safe under unsigned subtraction.  The upper bound
         * only rejects samples distorted by a debugger halt or an unexpectedly
         * long critical section. */
        if ((elapsed > 0UL) && (elapsed <= (CYCLES_PER_TICK / 2UL))) {
            log_submit(LOG_CTXSW, elapsed);
        }
    }
}

static void print_event(const LogEvent *event)
{
    switch ((LogKind)event->kind) {
    case LOG_CRITICAL:
        console_puts("[CRITICAL] Cycles: ");
        console_putu32(event->value);
        console_puts("\r\n");
        break;
    case LOG_MEDIUM:
        console_puts("[MEDIUM] Cycles: ");
        console_putu32(event->value);
        console_puts("\r\n");
        break;
    case LOG_PREEMPTION:
        console_puts("[OBSERVER] Preemption gap: ");
        console_putu32(event->value);
        console_puts(" cycles\r\n");
        break;
    case LOG_CTXSW:
        console_puts("[CTXSW] Cycles: ");
        console_putu32(event->value);
        console_puts("\r\n");
        break;
    case LOG_CTXSW_DROPS:
        console_puts("[DROPS] Context samples: ");
        console_putu32(event->value);
        console_puts("\r\n");
        break;
    case LOG_QUEUE_DROPS:
        console_puts("[DROPS] Log events: ");
        console_putu32(event->value);
        console_puts("\r\n");
        break;
    default:
        break;
    }
}

static void vTaskCritical(void *argument)
{
    TickType_t last_wake = xTaskGetTickCount();
    (void)argument;

    for (;;) {
        const uint32_t start = cycle_now();
        uint32_t end;

        busy_cycles(CRITICAL_WORK_CYCLES);
        end = cycle_now();
        log_submit(LOG_CRITICAL, (uint32_t)(end - start));

        vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(CRITICAL_PERIOD_MS));
    }
}

static void vTaskMedium(void *argument)
{
    TickType_t last_wake = xTaskGetTickCount();
    (void)argument;

    for (;;) {
        const uint32_t start = cycle_now();
        uint32_t end;

        busy_cycles(MEDIUM_WORK_CYCLES);
        end = cycle_now();
        log_submit(LOG_MEDIUM, (uint32_t)(end - start));

        vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(MEDIUM_PERIOD_MS));
    }
}

static void vTaskObserver(void *argument)
{
    uint32_t last_end = 0UL;
    uint32_t primed = 0UL;
    TickType_t last_drop_report = 0U;
    (void)argument;

    for (;;) {
        const uint32_t start = cycle_now();
        const TickType_t now_tick = xTaskGetTickCount();

        if (primed != 0UL) {
            const uint32_t gap = (uint32_t)(start - last_end);
            if (gap > PREEMPTION_THRESHOLD) {
                log_submit(LOG_PREEMPTION, gap);
            }
        }

        ctxsw_drain(CTXSW_DRAIN_LIMIT);

        if ((TickType_t)(now_tick - last_drop_report) >= pdMS_TO_TICKS(1000U)) {
            log_submit(LOG_CTXSW_DROPS, g_ctxsw_drops);
            log_submit(LOG_QUEUE_DROPS, g_log_drops);
            last_drop_report = now_tick;
        }

        busy_cycles(CYCLES_FROM_MS(1U) / 80UL); /* approximately 12.5 us */
        last_end = cycle_now();
        primed = 1UL;
        vTaskDelay(pdMS_TO_TICKS(OBSERVER_PERIOD_MS));
    }
}

static void vTaskLogger(void *argument)
{
    LogEvent event;
    BaseType_t announced = pdFALSE;
    (void)argument;

    for (;;) {
        if (!console_connected()) {
            announced = pdFALSE;
            vTaskDelay(pdMS_TO_TICKS(20U));
            continue;
        }

        if (announced == pdFALSE) {
            console_puts("=== SK-AM64 R5F FreeRTOS Scheduler Latency Test ===\r\n");
            console_puts("MAIN_R5FSS0_CORE0: Cortex-R5F @ ");
            console_putu32((uint32_t)configCPU_CLOCK_HZ);
            console_puts(" Hz, console: MAIN_UART0\r\n");
            console_puts("hot path in TCM: ");
            console_putu32((uint32_t)EXPERIMENT_HOT_PATH_IN_TCM);
            console_puts("\r\n");
            announced = pdTRUE;
        }

        if (xQueueReceive(g_log_queue, &event, pdMS_TO_TICKS(20U)) == pdPASS) {
            print_event(&event);
        }
    }
}

BaseType_t experiment_start(void)
{
    BaseType_t ok = pdPASS;

    g_log_queue = xQueueCreate(LOG_QUEUE_LENGTH, sizeof(LogEvent));
    if (g_log_queue == NULL) {
        return pdFAIL;
    }

    /* Priorities match the STM32WB55 port.  There is no USB service task here,
     * so the highest priority in the system is Critical at 3 — one fewer
     * source of interference than on the STM32, which is itself a difference
     * worth recording when comparing captures. */
    /* xTaskCreate() returns errCOULD_NOT_ALLOCATE_REQUIRED_MEMORY (-1) on
     * failure, and 1 & -1 == 1, so accumulating with `ok &=` hid failures.
     * Compare each result explicitly. */
    if (xTaskCreate(vTaskCritical, "Critical", TASK_STACK_WORDS,
                    NULL, 3U, NULL) != pdPASS) { ok = pdFAIL; }
    if (xTaskCreate(vTaskMedium, "Medium", TASK_STACK_WORDS,
                    NULL, 2U, NULL) != pdPASS) { ok = pdFAIL; }
    if (xTaskCreate(vTaskObserver, "Observer", TASK_STACK_WORDS,
                    NULL, 1U, NULL) != pdPASS) { ok = pdFAIL; }
    if (xTaskCreate(vTaskLogger, "Logger", TASK_STACK_WORDS,
                    NULL, 0U, NULL) != pdPASS) { ok = pdFAIL; }
    return ok;
}
