# makefile — SK-AM64 scheduler-latency experiment, MAIN_R5FSS0_CORE0, GCC.
#
# This is a standalone makefile that borrows the MCU+ SDK's toolchain and
# library layout rather than living inside the SDK tree.  It is deliberately
# readable instead of complete: the SDK's own generated makefiles carry a much
# larger set of include paths and libraries that vary between SDK releases.
#
# If this does not link against your SDK version, the reliable fix is not to
# patch this file — copy examples/kernel/freertos/task_switch/am64x-evm/
# r5fss0-0_freertos/gcc-armv7/ from your SDK, then replace its sources with the
# ones here and add linker.ld.  See PORTING.md, section "构建方式".

MCU_PLUS_SDK_PATH ?= $(abspath ../../mcu_plus_sdk)
-include $(MCU_PLUS_SDK_PATH)/imports.mak

CGT_GCC_ARMV7_PATH ?= $(abspath /usr/share/gcc-arm-none-eabi)
SYSCFG_CLI_PATH    ?= $(abspath /usr/share/sysconfig)

CC      := $(CGT_GCC_ARMV7_PATH)/bin/arm-none-eabi-gcc
OBJCOPY := $(CGT_GCC_ARMV7_PATH)/bin/arm-none-eabi-objcopy
SIZE    := $(CGT_GCC_ARMV7_PATH)/bin/arm-none-eabi-size

BUILD_DIR ?= build
OUT_NAME  := am64x_sched_latency

# Cortex-R5F, VFPv3-D16, hard float.  -mfloat-abi must match every SDK library
# you link against; mixing soft and hard float produces a link error that names
# an unrelated object file.
CPU_FLAGS := -mcpu=cortex-r5 -mthumb-interwork -marm \
             -mfloat-abi=hard -mfpu=vfpv3-d16

# R5F_CORE / SOC selects the SDK's per-core configuration headers.
CPPFLAGS := -DSOC_AM64X -DR5F_CORE -I. \
            -I$(MCU_PLUS_SDK_PATH)/source \
            -I$(MCU_PLUS_SDK_PATH)/source/kernel/freertos/FreeRTOS-Kernel/include \
            -I$(MCU_PLUS_SDK_PATH)/source/kernel/freertos/portable/GCC/ARM_CR5F \
            -I$(MCU_PLUS_SDK_PATH)/source/kernel/freertos/config/am64x/r5f

CFLAGS := $(CPU_FLAGS) -std=gnu11 -O2 -g3 -ffreestanding \
          -ffunction-sections -fdata-sections -fno-common \
          -Wall -Wextra -Wshadow -Wundef -MMD -MP

LDFLAGS := $(CPU_FLAGS) -nostartfiles -T linker.ld \
           -Wl,--gc-sections -Wl,-Map,$(BUILD_DIR)/$(OUT_NAME).map

# Order matters: the FreeRTOS and DPL libraries reference each other.
LDLIBS := -Wl,--start-group \
          $(MCU_PLUS_SDK_PATH)/source/kernel/freertos/lib/am64x-r5f.gcc-armv7.freertos.lib \
          $(MCU_PLUS_SDK_PATH)/source/drivers/lib/am64x-r5f.gcc-armv7.release.lib \
          $(MCU_PLUS_SDK_PATH)/source/board/lib/am64x-r5f.gcc-armv7.release.lib \
          -lgcc -lc -lm -lnosys \
          -Wl,--end-group

# ti_drivers_config.c / ti_board_config.c are SysConfig output; run `make
# syscfg` before the first build or after editing example.syscfg.
SOURCES := main.c experiment.c console.c \
           $(BUILD_DIR)/syscfg/ti_drivers_config.c \
           $(BUILD_DIR)/syscfg/ti_drivers_open_close.c \
           $(BUILD_DIR)/syscfg/ti_board_config.c \
           $(BUILD_DIR)/syscfg/ti_board_open_close.c \
           $(BUILD_DIR)/syscfg/ti_dpl_config.c \
           $(BUILD_DIR)/syscfg/ti_pinmux_config.c \
           $(BUILD_DIR)/syscfg/ti_power_clock_config.c

OBJECTS := $(patsubst %.c,$(BUILD_DIR)/%.o,$(notdir $(SOURCES)))
DEPS    := $(OBJECTS:.o=.d)

ELF := $(BUILD_DIR)/$(OUT_NAME).elf
BIN := $(BUILD_DIR)/$(OUT_NAME).bin

.PHONY: all syscfg appimage clean

all: $(BIN)

$(BUILD_DIR) $(BUILD_DIR)/syscfg:
	mkdir -p $@

# SysConfig generates the DPL, pinmux and power/clock configuration consumed by
# System_init() and Board_init().
syscfg: | $(BUILD_DIR)/syscfg
	$(SYSCFG_CLI_PATH)/nodejs/node $(SYSCFG_CLI_PATH)/dist/cli.js \
		--script example.syscfg \
		-o $(BUILD_DIR)/syscfg \
		--compiler gcc \
		--product $(MCU_PLUS_SDK_PATH)/.metadata/product.json \
		--part Default --package ALW

$(BUILD_DIR)/%.o: %.c | $(BUILD_DIR)
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

$(BUILD_DIR)/%.o: $(BUILD_DIR)/syscfg/%.c | $(BUILD_DIR)
	$(CC) $(CPPFLAGS) $(CFLAGS) -I$(BUILD_DIR)/syscfg -c $< -o $@

$(ELF): $(OBJECTS) linker.ld
	$(CC) $(LDFLAGS) -o $@ $(OBJECTS) $(LDLIBS)
	$(SIZE) $@

$(BIN): $(ELF)
	$(OBJCOPY) -O binary $< $@

# The SBL boots a TI appimage, not a raw .bin.  This wraps the ELF with the
# multicore header the bootloader expects; the ".launch" suffix is what the SBL
# on the SD card looks for.
appimage: $(ELF)
	$(MCU_PLUS_SDK_PATH)/tools/boot/multicoreImageGen/multicoreImageGen.py \
		--devID 55 \
		--out $(BUILD_DIR)/$(OUT_NAME).appimage \
		$(ELF)@4

clean:
	rm -rf $(BUILD_DIR)

-include $(DEPS)
