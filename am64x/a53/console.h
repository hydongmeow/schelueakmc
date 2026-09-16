#ifndef AM64X_A53_CONSOLE_H
#define AM64X_A53_CONSOLE_H

#include <stdbool.h>
#include <stdint.h>

void console_init(void);
void console_putc(char c);
void console_puts(const char *text);
void console_putu32(uint32_t value);
void console_flush(void);

#endif /* AM64X_A53_CONSOLE_H */
