/* console.c — MAIN_UART0 text output for the AM64x scheduler experiment.
 *
 * Why a raw register writer instead of the SDK's UART driver:
 *
 *   The STM32WB55 port had to push logging through a queue and a dedicated
 *   task because USB CDC introduces host-controlled backpressure that would
 *   otherwise stretch the measured spans.  A polled UART has no host in the
 *   loop, but it is still slow (115200 baud ~ 87 us per character), so the
 *   queue + Logger-task structure is kept.  Within the Logger task a blocking
 *   register poll is the least surprising thing that can happen: no interrupt,
 *   no semaphore, no DMA, nothing that can perturb the scheduler under study.
 *
 *   The SDK's UART_write() would also work, but it arms an interrupt and takes
 *   a semaphore, which adds scheduler activity to the very system being
 *   measured.  Keeping the console dumb keeps the side channel clean.
 *
 * The SBL has already done pinmux and set 115200 8N1, so this code never
 * touches LCR, the divisor latches, or MDR1.  If you change the console baud
 * rate, change it in the bootloader, not here.
 */
#include "console.h"
#include "am64x_soc.h"

#define UART_REG(offset) \
    (*(volatile uint32_t *)(AM64X_UART0_BASE + (uint32_t)(offset)))

void console_init(void)
{
    /* Intentionally empty.  Claiming the UART here would fight with whatever
     * the SBL or the SDK's Board_init() has already configured.  See the file
     * header. */
}

bool console_connected(void)
{
    /* A UART has no link state.  The STM32 port used this to wait for the USB
     * host to open the CDC port; here it is always true, which makes the
     * shared experiment body behave as "console always available". */
    return true;
}

void console_putc(char c)
{
    while ((UART_REG(AM64X_UART_LSR_OFFSET) & AM64X_UART_LSR_THRE) == 0U) {
        /* spin until the transmit holding register drains */
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
        /* wait for the shift register to empty as well */
    }
}
