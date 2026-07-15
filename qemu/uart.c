/* uart.c — bare-metal UART0 output for lm3s6965evb */
#include "uart.h"

/* lm3s6965 UART0 (PL011-compatible) */
#define UART0_DR     (*(volatile uint32_t *)0x4000C000)  /* data register  */
#define UART0_FR     (*(volatile uint32_t *)0x4000C018)  /* flag register  */
#define UART_FR_TXFF (1u << 5)                           /* TX FIFO full   */

void uart_putc(char c)
{
    while (UART0_FR & UART_FR_TXFF) { }   /* wait while TX FIFO is full */
    UART0_DR = (uint32_t)c;
}

void uart_puts(const char *s)
{
    while (*s) {
        uart_putc(*s++);
    }
}

/* print an unsigned 32-bit value in decimal */
void uart_putu32(uint32_t v)
{
    char buf[11];
    int i = 0;

    if (v == 0) {
        uart_putc('0');
        return;
    }
    while (v) {
        buf[i++] = (char)('0' + (v % 10));
        v /= 10;
    }
    while (i--) {
        uart_putc(buf[i]);
    }
}
