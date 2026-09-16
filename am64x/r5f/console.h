#ifndef AM64X_CONSOLE_H
#define AM64X_CONSOLE_H

#include <stdbool.h>
#include <stdint.h>

/* Mirrors the usb_cdc.h surface used by the STM32WB55 port so the experiment
 * body is identical across the two targets. */
void console_init(void);
bool console_connected(void);
void console_putc(char c);
void console_puts(const char *text);
void console_putu32(uint32_t value);
void console_flush(void);

#endif /* AM64X_CONSOLE_H */
