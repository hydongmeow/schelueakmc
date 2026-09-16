/* experiment.c — FreeRTOS SMP scheduler-latency experiment for the AM64x
 * dual Cortex-A53 cluster.
 *
 * Task set, periods and log format follow the single-core targets so captures
 * stay comparable.  Three things had to change for SMP, and each of them is a
 * correctness issue rather than a preference:
 *
 *  1. PER-CORE TRACE STATE.  The single-core ports keep one global
 *     g_switch_out.  Under SMP both cores execute vTaskSwitchContext()
 *     concurrently, so core 0's switch-out timestamp gets overwritten by core
 *     1 before core 0 reads it back.  The resulting L_ctxsw values are wrong
 *     while remaining entirely plausible.  Each core now owns its slot.
 *
 *  2. MEMORY BARRIERS.  ARMv8-A is weakly ordered; Cortex-M is not.  The bare
 *     volatile ring buffer that is safe on the STM32 is not safe here: the
 *     slot write and the head publish can be observed out of order by the
 *     other core.  Publish and consume barriers are explicit now.
 *
 *  3. LOAD SCALING.  0.667 utilisation spread over two cores does not contend.
 *     See EXPERIMENT_LOAD_SCALE in experiment.h.
 *
 * And one change forced by the first capture (2026-09-15):
 *
 *  4. CONTEXT-SWITCH SAMPLES GO OUT AS A PER-SECOND HISTOGRAM, not one line
 *     each.  Two cores at a 1 kHz tick produce thousands of switches per
 *     second; at 115200 baud the console carried ~4 KB/s, the log queue was
 *     permanently full, the load-task records lost the race for queue slots,
 *     and — worst — the drop-counter reports, which went through the same
 *     queue, were dropped too, so the capture looked complete.  Now:
 *       - the Observer accumulates each core's L_ctxsw values into an integer
 *         histogram (one bin per counter tick, lossless) and flushes it once a
 *         second as compact "[CTXSWH] core=N v:c v:c ..." lines;
 *       - values beyond the histogram range are still emitted individually,
 *         so the tail is exact;
 *       - the Logger prints the drop counters itself, straight from the
 *         counters, never through the queue;
 *       - a "[TICK]" record once a second pairs the RTOS tick count with the
 *         system counter so latency.py can verify the time base rate.
 *
 * Every emitted record carries the core it was observed on.  On a single-core
 * target that field would be noise; here it is most of the point.
 */
#include <stdint.h>

#include "FreeRTOS.h"
#include "task.h"
#include "queue.h"

#include "timebase.h"
#include "console.h"
#include "experiment.h"

#define CRITICAL_PERIOD_MS       20U
#define CRITICAL_WORK_MS         10U
#define MEDIUM_PERIOD_MS         30U
#define MEDIUM_WORK_MS            5U
#define OBSERVER_PERIOD_MS        1U
#define REPORT_PERIOD_MS       1000U

#define CTXSW_BUF_SIZE       512UL
#define CTXSW_BUF_MASK       (CTXSW_BUF_SIZE - 1UL)
#define CTXSW_DRAIN_LIMIT     64UL
#define CTXSW_HIST_BINS     4096U   /* ticks; 18 us at 225 MHz, well above p99 */
#define LOG_QUEUE_LENGTH    1024U   /* two cores feed it */
#define TASK_STACK_WORDS    2048U   /* AArch64 frames are wide */
#define HIST_LINE_MAX_CHARS  100U   /* wrap [CTXSWH] lines around here */

#define CORE_COUNT ((uint32_t)configNUM_CORES)

typedef enum {
    LOG_CRITICAL = 0,
    LOG_MEDIUM,
    LOG_PREEMPTION,
    LOG_CTXSW,        /* individual sample, only for values >= CTXSW_HIST_BINS */
    LOG_MIGRATION,
    LOG_CTXSW_HIST,   /* value = L_ctxsw in ticks, count = occurrences */
    LOG_TICK          /* value = RTOS tick count, count = counter / 1000 */
} LogKind;

typedef struct {
    uint32_t kind;
    uint32_t core;
    uint32_t value;
    uint32_t count;
} LogEvent;

/* One cache line is 64 bytes on A53.  Aligning each core's block keeps the
 * two cores' head/tail indices off the same line: without this the cores would
 * ping-pong a shared line on every context switch, which would show up as
 * inflated L_ctxsw caused purely by the instrumentation. */
typedef struct {
    volatile uint64_t switch_out;
    volatile uint32_t head;
    volatile uint32_t tail;
    volatile uint32_t drops;
    volatile uint32_t buf[CTXSW_BUF_SIZE];
} __attribute__((aligned(64))) CoreTrace;

static CoreTrace g_trace[configNUM_CORES];

/* The two properties the SMP trace path silently depends on.  Both are the
 * kind of thing a later edit breaks without any visible symptom, so they are
 * enforced rather than merely documented. */
_Static_assert((CTXSW_BUF_SIZE & CTXSW_BUF_MASK) == 0UL,
               "CTXSW_BUF_SIZE must be a power of two or the ring mask aliases");
_Static_assert((sizeof(CoreTrace) % 64U) == 0U,
               "CoreTrace must occupy whole cache lines; otherwise two cores "
               "share a line and instrumentation traffic inflates L_ctxsw");
_Static_assert(configNUM_CORES >= 1,
               "configNUM_CORES must be at least 1 (TI SMP branch spelling)");

/* Owned by the Observer task alone (it is the only drainer), so no locking. */
static uint32_t g_hist[configNUM_CORES][CTXSW_HIST_BINS];

static QueueHandle_t g_log_queue;
static volatile uint32_t g_log_drops;

/* Derived at startup rather than from a compile-time clock constant.  This is
 * why the A53 port has no equivalent of the R5F port's "assumed 800 MHz" risk:
 * the rate comes from CNTFRQ_EL0 when firmware programmed it, otherwise from
 * the DM firmware's clock query handed in by main.c (see experiment.h). */
static uint64_t g_tb_hz;
static uint32_t g_tb_hz_fallback;          /* from main.c via TISCI, may be 0 */
static const char *g_tb_source = "none";
static uint64_t g_tb_per_rtos_tick;
static uint64_t g_preempt_threshold;
static uint64_t g_ctxsw_max_valid;
static uint64_t g_critical_work;
static uint64_t g_medium_work;
static uint64_t g_observer_work;

static uint64_t ticks_from_us(uint64_t microseconds)
{
    return (g_tb_hz * microseconds) / 1000000ULL;
}

static void busy_ticks(uint64_t span)
{
    const uint64_t start = timebase_now();
    while ((timebase_now() - start) < span) {
        __asm volatile ("nop");
    }
}

void trace_switched_out(void)
{
    CoreTrace *trace = &g_trace[timebase_core_id() % CORE_COUNT];
    trace->switch_out = timebase_now();
}

void trace_switched_in(void)
{
    const uint32_t core = timebase_core_id() % CORE_COUNT;
    CoreTrace *trace = &g_trace[core];
    const uint64_t elapsed = timebase_now() - trace->switch_out;
    const uint32_t head = trace->head;

    /* Single producer (this core) and single consumer (the Observer, wherever
     * it happens to run), so no atomics are needed — only ordering. */
    if ((uint32_t)(head - trace->tail) < CTXSW_BUF_SIZE) {
        trace->buf[head & CTXSW_BUF_MASK] =
            (elapsed > 0xFFFFFFFFULL) ? 0xFFFFFFFFUL : (uint32_t)elapsed;
        TIMEBASE_PUBLISH_BARRIER();   /* fill the slot before publishing it */
        trace->head = head + 1UL;
    } else {
        trace->drops++;
    }
}

static void log_submit_n(LogKind kind, uint32_t core, uint32_t value,
                         uint32_t count)
{
    const LogEvent event = { (uint32_t)kind, core, value, count };

    if (xQueueSend(g_log_queue, &event, 0U) != pdPASS) {
        /* FreeRTOS SMP critical sections take the kernel spinlock, so this is
         * safe from either core. */
        taskENTER_CRITICAL();
        g_log_drops++;
        taskEXIT_CRITICAL();
    }
}

static void log_submit(LogKind kind, uint32_t core, uint32_t value)
{
    log_submit_n(kind, core, value, 1U);
}

/* ---- Context-switch sample path ------------------------------------------ */

static void ctxsw_drain_core(uint32_t core, uint32_t limit)
{
    CoreTrace *trace = &g_trace[core];
    uint32_t count = 0UL;

    while (count < limit) {
        const uint32_t head = trace->head;
        TIMEBASE_CONSUME_BARRIER();   /* read the index before the slot */
        if (trace->tail == head) {
            break;
        }

        const uint32_t tail = trace->tail;
        const uint32_t elapsed = trace->buf[tail & CTXSW_BUF_MASK];
        trace->tail = tail + 1UL;
        count++;

        if ((elapsed == 0UL) || ((uint64_t)elapsed > g_ctxsw_max_valid)) {
            continue;   /* same filter as the other targets */
        }
        if (elapsed < CTXSW_HIST_BINS) {
            g_hist[core][elapsed]++;
        } else {
            log_submit(LOG_CTXSW, core, elapsed);   /* rare: keep the tail exact */
        }
    }
}

static void ctxsw_drain_all(uint32_t limit)
{
    for (uint32_t core = 0U; core < CORE_COUNT; core++) {
        ctxsw_drain_core(core, limit);
    }
}

static void ctxsw_hist_flush(void)
{
    for (uint32_t core = 0U; core < CORE_COUNT; core++) {
        for (uint32_t bin = 1U; bin < CTXSW_HIST_BINS; bin++) {
            const uint32_t count = g_hist[core][bin];
            if (count != 0U) {
                log_submit_n(LOG_CTXSW_HIST, core, bin, count);
                g_hist[core][bin] = 0U;
            }
        }
    }
}

/* ---- Logger output ------------------------------------------------------- */

/* [CTXSWH] events are packed several per line; this is the open line, if any.
 * Only the Logger touches these. */
static int32_t g_hist_line_core = -1;
static uint32_t g_hist_line_chars;

static void hist_line_close(void)
{
    if (g_hist_line_core >= 0) {
        console_puts("\r\n");
        g_hist_line_core = -1;
        g_hist_line_chars = 0U;
    }
}

static void hist_line_append(uint32_t core, uint32_t value, uint32_t count)
{
    if ((g_hist_line_core != (int32_t)core) ||
        (g_hist_line_chars > HIST_LINE_MAX_CHARS)) {
        hist_line_close();
        console_puts("[CTXSWH] core=");
        console_putu32(core);
        g_hist_line_core = (int32_t)core;
        g_hist_line_chars = 15U;
    }
    console_putc(' ');
    console_putu32(value);
    console_putc(':');
    console_putu32(count);
    g_hist_line_chars += 12U;   /* generous estimate; exact width is irrelevant */
}

static void print_event(const LogEvent *event)
{
    static const char *const kind_text[] = {
        "[CRITICAL] ", "[MEDIUM] ", "[OBSERVER] ", "[CTXSW] ",
        "[MIGRATION] ", "[CTXSWH] ", "[TICK] "
    };

    if (event->kind > (uint32_t)LOG_TICK) {
        return;
    }
    if (event->kind == (uint32_t)LOG_CTXSW_HIST) {
        hist_line_append(event->core, event->value, event->count);
        return;
    }
    hist_line_close();

    console_puts(kind_text[event->kind]);
    console_puts("core=");
    console_putu32(event->core);

    switch ((LogKind)event->kind) {
    case LOG_PREEMPTION:
        console_puts(" Preemption gap: ");
        console_putu32(event->value);
        console_puts(" ticks\r\n");
        break;
    case LOG_MIGRATION:
        console_puts(" task=");
        console_putu32(event->value);
        console_puts("\r\n");
        break;
    case LOG_TICK:
        console_puts(" rtos=");
        console_putu32(event->value);
        console_puts(" counter_k=");
        console_putu32(event->count);
        console_puts("\r\n");
        break;
    default:
        console_puts(" Ticks: ");
        console_putu32(event->value);
        console_puts("\r\n");
        break;
    }
}

/* Drop counters are read straight from the producers' counters and printed
 * here, never queued: a report that travels through the queue it is supposed
 * to audit is dropped exactly when it would say something. */
static void print_drop_report(void)
{
    hist_line_close();
    for (uint32_t core = 0U; core < CORE_COUNT; core++) {
        console_puts("[DROPS] core=");
        console_putu32(core);
        console_puts(" Context samples: ");
        console_putu32(g_trace[core].drops);
        console_puts("\r\n");
    }
    console_puts("[DROPS] core=0 Log events: ");
    console_putu32(g_log_drops);
    console_puts("\r\n");
}

/* ---- Load tasks ---------------------------------------------------------
 * One function serves both classes; the instance descriptor carries the work
 * span, period, log kind and an id used for migration reporting.
 */
typedef struct {
    const uint64_t *work;
    uint32_t period_ms;
    uint32_t kind;
    uint32_t id;
} LoadSpec;

static LoadSpec g_load_specs[2U * EXPERIMENT_LOAD_SCALE];

static void vTaskLoad(void *argument)
{
    const LoadSpec *spec = (const LoadSpec *)argument;
    TickType_t last_wake = xTaskGetTickCount();
    uint32_t last_core = timebase_core_id() % CORE_COUNT;

    for (;;) {
        const uint32_t core = timebase_core_id() % CORE_COUNT;
        const uint64_t start = timebase_now();
        uint64_t span;

        /* A load task changing core between activations is a first-class
         * observable on SMP and has no analogue on the single-core targets. */
        if (core != last_core) {
            log_submit(LOG_MIGRATION, core, spec->id);
            last_core = core;
        }

        busy_ticks(*spec->work);
        span = timebase_now() - start;
        log_submit((LogKind)spec->kind, core, (uint32_t)span);

        (void)xTaskDelayUntil(&last_wake, pdMS_TO_TICKS(spec->period_ms));
    }
}

static void vTaskObserver(void *argument)
{
    uint64_t last_end = 0ULL;
    uint32_t primed = 0UL;
    TickType_t last_report = 0U;
    (void)argument;

    for (;;) {
        const uint64_t start = timebase_now();
        const uint32_t core = timebase_core_id() % CORE_COUNT;
        const TickType_t now_tick = xTaskGetTickCount();

        if (primed != 0UL) {
            const uint64_t gap = start - last_end;
            if (gap > g_preempt_threshold) {
                log_submit(LOG_PREEMPTION, core,
                           (gap > 0xFFFFFFFFULL) ? 0xFFFFFFFFUL
                                                 : (uint32_t)gap);
            }
        }

        ctxsw_drain_all(CTXSW_DRAIN_LIMIT);

        if ((TickType_t)(now_tick - last_report) >= pdMS_TO_TICKS(REPORT_PERIOD_MS)) {
            ctxsw_hist_flush();
            /* Time-base cross-check: RTOS ticks vs system counter (in units
             * of 1000 ticks so 32 bits last ~5 h at 225 MHz). */
            log_submit_n(LOG_TICK, core, (uint32_t)now_tick,
                         (uint32_t)(timebase_now() / 1000ULL));
            last_report = now_tick;
        }

        busy_ticks(g_observer_work);
        last_end = timebase_now();
        primed = 1UL;
        vTaskDelay(pdMS_TO_TICKS(OBSERVER_PERIOD_MS));
    }
}

static void vTaskLogger(void *argument)
{
    LogEvent event;
    TickType_t last_report = xTaskGetTickCount();
    (void)argument;

    console_puts("=== SK-AM64 A53 SMP FreeRTOS Scheduler Latency Test ===\r\n");
    console_puts("cores: ");
    console_putu32(CORE_COUNT);
    console_puts("  timebase: ");
    console_putu32((uint32_t)g_tb_hz);
    console_puts(" Hz (CNTVCT_EL0, rate from ");
    console_puts(g_tb_source);
    console_puts(")\r\n");
    console_puts("load scale: ");
    console_putu32((uint32_t)EXPERIMENT_LOAD_SCALE);
    console_puts("  affinity mode: ");
    console_putu32((uint32_t)EXPERIMENT_AFFINITY_MODE);
    console_puts("\r\n");

    for (;;) {
        if (xQueueReceive(g_log_queue, &event, pdMS_TO_TICKS(20U)) == pdPASS) {
            print_event(&event);
        } else {
            hist_line_close();   /* idle: do not leave a partial line hanging */
        }

        const TickType_t now = xTaskGetTickCount();
        if ((TickType_t)(now - last_report) >= pdMS_TO_TICKS(REPORT_PERIOD_MS)) {
            print_drop_report();
            last_report = now;
        }
    }
}

/* ---- Setup -------------------------------------------------------------- */

void experiment_set_timebase_hz(uint32_t hz)
{
    g_tb_hz_fallback = hz;
}

static void fatal(const char *text)
{
    console_puts("FATAL: ");
    console_puts(text);
    console_puts("\r\n");
    console_flush();
    for (;;) { __asm volatile ("wfi"); }
}

static void derive_constants(void)
{
    /* Rate: architectural register first, DM-firmware value second. */
    g_tb_hz = (uint64_t)timebase_hz();
    g_tb_source = "CNTFRQ_EL0";
    if (g_tb_hz == 0ULL) {
        g_tb_hz = (uint64_t)g_tb_hz_fallback;
        g_tb_source = "TISCI";
    }
    if (g_tb_hz == 0ULL) {
        /* Every derived span would be zero, the busy loops would become
         * no-ops and the capture would be full of near-zero spans.  Fail
         * loudly instead. */
        fatal("system counter rate unknown: CNTFRQ_EL0 reads 0 and no TISCI value");
    }

    /* Counting: the GTC block must be powered and its enable bit set (main.c
     * does that).  A stopped counter would silently produce zero spans. */
    {
        const uint64_t before = timebase_now();
        for (volatile uint32_t spin = 0U; spin < 100000U; spin++) { }
        if (timebase_now() == before) {
            fatal("system counter (CNTVCT_EL0) is not counting");
        }
    }

    g_tb_per_rtos_tick = g_tb_hz / (uint64_t)configTICK_RATE_HZ;
    g_preempt_threshold = (g_tb_per_rtos_tick * 3ULL) / 2ULL;
    g_ctxsw_max_valid = g_tb_per_rtos_tick / 2ULL;
    g_critical_work = ticks_from_us(CRITICAL_WORK_MS * 1000ULL);
    g_medium_work = ticks_from_us(MEDIUM_WORK_MS * 1000ULL);
    g_observer_work = ticks_from_us(13ULL);   /* ~12.5 us, as on the M4F */
}

#if (EXPERIMENT_AFFINITY_MODE == 1)
static void pin_task(TaskHandle_t task, uint32_t core)
{
#if (configUSE_CORE_AFFINITY == 1)
    vTaskCoreAffinitySet(task, (UBaseType_t)(1UL << core));
#else
#error "EXPERIMENT_AFFINITY_MODE=1 requires configUSE_CORE_AFFINITY=1"
#endif
}
#endif

static BaseType_t create_task(TaskFunction_t function, const char *name,
                              void *argument, UBaseType_t priority,
                              TaskHandle_t *handle)
{
    /* xTaskCreate() returns errCOULD_NOT_ALLOCATE_REQUIRED_MEMORY (-1) on
     * failure.  The single-core ports accumulate results with `ok &=`, and
     * 1 & -1 == 1, so a failed creation was invisible there.  Compare
     * explicitly. */
    return (xTaskCreate(function, name, TASK_STACK_WORDS, argument,
                        priority, handle) == pdPASS) ? pdPASS : pdFAIL;
}

BaseType_t experiment_start(void)
{
    BaseType_t ok = pdPASS;
    TaskHandle_t observer = NULL;
    uint32_t index = 0U;

    derive_constants();

    g_log_queue = xQueueCreate(LOG_QUEUE_LENGTH, sizeof(LogEvent));
    if (g_log_queue == NULL) {
        return pdFAIL;
    }

    for (uint32_t n = 0U; n < (uint32_t)EXPERIMENT_LOAD_SCALE; n++) {
        TaskHandle_t handle = NULL;

        g_load_specs[index] = (LoadSpec){ &g_critical_work, CRITICAL_PERIOD_MS,
                                          (uint32_t)LOG_CRITICAL, index };
        if (create_task(vTaskLoad, "Critical", &g_load_specs[index], 3U,
                        &handle) != pdPASS) {
            ok = pdFAIL;
        }
#if (EXPERIMENT_AFFINITY_MODE == 1)
        if (handle != NULL) { pin_task(handle, 0U); }
#endif
        index++;

        g_load_specs[index] = (LoadSpec){ &g_medium_work, MEDIUM_PERIOD_MS,
                                          (uint32_t)LOG_MEDIUM, index };
        if (create_task(vTaskLoad, "Medium", &g_load_specs[index], 2U,
                        &handle) != pdPASS) {
            ok = pdFAIL;
        }
#if (EXPERIMENT_AFFINITY_MODE == 1)
        if (handle != NULL) { pin_task(handle, 0U); }
#endif
        index++;
    }

    if (create_task(vTaskObserver, "Observer", NULL, 1U, &observer) != pdPASS) {
        ok = pdFAIL;
    }
#if (EXPERIMENT_AFFINITY_MODE == 1)
    if (observer != NULL) { pin_task(observer, (CORE_COUNT > 1U) ? 1U : 0U); }
#endif

    if (create_task(vTaskLogger, "Logger", NULL, 0U, NULL) != pdPASS) {
        ok = pdFAIL;
    }
    return ok;
}
