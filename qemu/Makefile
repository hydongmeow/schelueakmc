ARM_GCC = arm-none-eabi-gcc
ARM_LD = arm-none-eabi-ld
ARM_OBJCOPY = arm-none-eabi-objcopy

FREERTOS_DIR = ../FreeRTOS-Kernel
CFLAGS = -mcpu=cortex-m3 -mthumb -Wall -O2 -g \
         -ffunction-sections -fdata-sections -MMD -MP
LDFLAGS = -mcpu=cortex-m3 -mthumb -T link.ld -Wl,--gc-sections

INCLUDES = -I. \
           -I$(FREERTOS_DIR)/include \
           -I$(FREERTOS_DIR)/portable/GCC/ARM_CM3

# NOTE: the local port.c has been deleted.  The real port lives in
# $(FREERTOS_DIR)/portable/GCC/ARM_CM3/port.c and is already listed below.
SOURCES = main.c
SOURCES += startup.c
SOURCES += uart.c
SOURCES += $(FREERTOS_DIR)/tasks.c
SOURCES += $(FREERTOS_DIR)/queue.c
SOURCES += $(FREERTOS_DIR)/list.c
SOURCES += $(FREERTOS_DIR)/timers.c
SOURCES += $(FREERTOS_DIR)/portable/GCC/ARM_CM3/port.c
SOURCES += $(FREERTOS_DIR)/portable/MemMang/heap_4.c

OBJECTS = $(SOURCES:.c=.o)
DEPS    = $(OBJECTS:.o=.d)
TARGET = firmware.elf
BIN = firmware.bin

all: $(BIN)

$(BIN): $(TARGET)
	$(ARM_OBJCOPY) -O binary $< $@

$(TARGET): $(OBJECTS)
	$(ARM_GCC) $(LDFLAGS) -o $@ $^

%.o: %.c
	$(ARM_GCC) $(CFLAGS) $(INCLUDES) -c $< -o $@

# -MMD/-MP generate these: editing FreeRTOSConfig.h now forces a rebuild.
-include $(DEPS)

clean:
	rm -f $(OBJECTS) $(DEPS) $(TARGET) $(BIN) qemu_output.log

.PHONY: all clean
