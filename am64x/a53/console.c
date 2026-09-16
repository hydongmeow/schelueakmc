/* console.c — MAIN_UART0 text output, A53 SMP build.
 *
 * Identical register handling to the R5F port (same UART, same 16550-compatible
 * layout, same polled write).  The SMP-specific concern is concurrency:
 *
 *   There is NO lock here.  That is safe only because exactly one task — the
 *   Logger — prints during a run, and FreeRTOS SMP never schedules a single
 *   task on two cores at once.  The startup FATAL paths in main.c and
 *   experiment.c print before the scheduler starts, when only the boot core is
 *   running, so they are safe for the same reason.
 *
 *   If you add printing from anywhere else, add a spinlock first.  Two cores
 *   interleaving into one THR produces scrambled lines, and latency.py will
 *   quietly drop the malformed ones rather than complain — you would lose
 *   samples without noticing.
 *
 * Baud rate, pinmux and divisor come from the SDK UART driver opened in
 * main.c (921600, see example.syscfg).  Nothing here reprograms them.
 */
#include "console.h"
#include "am64x_soc.h"

#define UART_REG(offset) \
    (*(volatile uint32_t *)(AM64X_UART0_BASE + (uint32_t)(offset)))

void console_init(void)
{
    /* Intentionally empty — the bootloader owns the UART configuration. */
}

void console_putc(char c)
{
    /* Wait for room in the TX FIFO, not for the FIFO to be empty: the
     * transmitter then stays busy while the Logger is off doing something
     * else, and the Logger spends far less time spinning here. */
    while ((UART_REG(AM64X_UART_SSR_OFFSET) & AM64X_UART_SSR_TX_FIFO_FULL) != 0U) {
    }
    UART_REG(AM64X_UART_THR_OFFSET) = (uint32_t)(uint8_t)c;
}

void console_puts(const char *text)
{
    while (*text != '\0') {
        console_putc(*text++);
    }
}

void console_putu32(uint32_t value)
{
    char buffer[11];
    int index = 0;

    if (value == 0U) {
        console_putc('0');
        return;
    }

    while (value != 0U) {
        buffer[index++] = (char)('0' + (value % 10U));
        value /= 10U;
    }
    while (index-- > 0) {
        console_putc(buffer[index]);
    }
}

void console_flush(void)
{
    while ((UART_REG(AM64X_UART_LSR_OFFSET) & AM64X_UART_LSR_TEMT) == 0U) {
    }
}
