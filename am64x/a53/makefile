# makefile — SK-AM64 A53 SMP scheduler-latency experiment (GCC AArch64).
#
# Modelled on the SDK's own
#   examples/kernel/freertos/smp_task_switch/am64x-evm/a53ss0-0_freertos-smp/gcc-aarch64/makefile
# and, for the kernel rebuild, on
#   source/kernel/freertos/makefile.am64x.a53-smp.gcc-aarch64
# Both were read from mcupsdk-core (Oct 2025 head).  If your SDK release moves
# a file, those two makefiles are the source of truth and this one follows.
#
# Run with the GNU make that ships with CCS (Windows: <ccs>/utils/bin/gmake).
# imports.mak derives tool paths from TOOLS_PATH (default C:/ti or ~/ti).
#
# WHY THE KERNEL IS REBUILT HERE
# ------------------------------
# The traceTASK_SWITCHED_OUT/IN hooks that produce every [CTXSW] record are
# expanded inside tasks.c.  The SDK's prebuilt FreeRTOS SMP library was built
# without them, so linking against it yields a capture with no context-switch
# samples and no error.  $(KERNEL_LIB) compiles the same kernel + DPL sources
# the SDK library is made of, against ./FreeRTOSConfig.h (TI's config plus the
# hooks), into build/.  The drivers and board libraries stay prebuilt; the
# config overlay changes nothing they depend on (see FreeRTOSConfig.h).
#
# TI's A53 SMP FreeRTOS support is EXPERIMENTAL and explicitly not supported by
# TI.  If something SDK-side breaks, ../r5f is the retreat.

# Per-machine tool locations (gitignored).  imports.mak uses ?= for these, so
# anything set here wins over its defaults.  Example contents:
#   MCU_PLUS_SDK_PATH := D:/ti/mcu_plus_sdk_am64x_11_01_00_17
#   TOOLS_PATH        := D:/ti
#   CCS_PATH          := D:/ti/ccs2030/ccs
#   SYSCFG_PATH       := D:/ti/ccs2030/ccs/utils/sysconfig_1.25.0
-include tools.local.mak

MCU_PLUS_SDK_PATH ?= $(abspath ../../../mcu_plus_sdk)
include $(MCU_PLUS_SDK_PATH)/imports.mak
include $(MCU_PLUS_SDK_PATH)/devconfig/devconfig.mak

# The SysConfig bundled inside CCS (<ccs>/utils/sysconfig_x.y.z) ships no
# nodejs/ directory; only the standalone installer does.  imports.mak assumes
# the standalone layout, so fall back to CCS's own node when it is missing.
ifeq ($(wildcard $(SYSCFG_NODE) $(SYSCFG_NODE).exe),)
  SYSCFG_NODE := $(CCS_NODE)
endif

PROFILE ?= release
ConfigName := $(PROFILE)

CC      := $(CGT_GCC_AARCH64_PATH)/bin/aarch64-none-elf-gcc
LNK     := $(CGT_GCC_AARCH64_PATH)/bin/aarch64-none-elf-gcc
AR      := $(CGT_GCC_AARCH64_PATH)/bin/aarch64-none-elf-gcc-ar
SIZE    := $(CGT_GCC_AARCH64_PATH)/bin/aarch64-none-elf-size
ifeq ($(OS),Windows_NT)
  PYTHON := python
else
  PYTHON := python3
endif

BUILD_DIR  ?= build
SYSCFG_FILE ?= example.syscfg
GEN_DIR    := $(BUILD_DIR)/generated
KOBJ_DIR   := $(BUILD_DIR)/kernel
OUTNAME    := a53_smp_sched_latency
ELF        := $(BUILD_DIR)/$(OUTNAME).$(PROFILE).out
MAP        := $(BUILD_DIR)/$(OUTNAME).$(PROFILE).map
KERNEL_LIB := $(BUILD_DIR)/freertos-smp-experiment.$(PROFILE).lib

KERNEL_ROOT := $(MCU_PLUS_SDK_PATH)/source/kernel
FREERTOS    := $(KERNEL_ROOT)/freertos

# ---- Experiment knobs (see experiment.h) -----------------------------------
EXPERIMENT_LOAD_SCALE    ?= 2
EXPERIMENT_AFFINITY_MODE ?= 0

# ---- Flags: identical to the SDK example so the prebuilt libraries match ----
CPU_FLAGS := \
	-mcpu=cortex-a53+fp+simd \
	-mabi=lp64 \
	-mcmodel=large \
	-mstrict-align \
	-mfix-cortex-a53-835769 \
	-mfix-cortex-a53-843419

DEFINES := \
	-DSOC_AM64X \
	-DOS_FREERTOS \
	-DSMP_FREERTOS \
	-DOS_FREERTOS_SMP \
	-DEXPERIMENT_LOAD_SCALE=$(EXPERIMENT_LOAD_SCALE) \
	-DEXPERIMENT_AFFINITY_MODE=$(EXPERIMENT_AFFINITY_MODE)

# "." MUST come before config/am64x/a53-smp: FreeRTOSConfig.h here wraps TI's
# via #include_next.  Reordering these silently drops the trace hooks.
INCLUDES := \
	-I. \
	-I$(GEN_DIR) \
	-I$(MCU_PLUS_SDK_PATH)/source \
	-I$(FREERTOS)/FreeRTOS-Kernel-smp/include \
	-I$(FREERTOS)/portable_smp/GCC/ARM_CA53 \
	-I$(FREERTOS)/config/am64x/a53-smp \
	-I$(FREERTOS)/FreeRTOS-POSIX/include \
	-I$(FREERTOS)/FreeRTOS-POSIX/include/private \
	-I$(FREERTOS)/FreeRTOS-POSIX/FreeRTOS-Plus-POSIX/include \
	-I$(FREERTOS)/FreeRTOS-POSIX/FreeRTOS-Plus-POSIX/include/portable

# -fcommon: TI's portmacro.h DEFINES three SMP lock arrays (ucOwnedByCore,
# ucRecursionCountByLock, GateWord) in the header, so every translation unit
# carries a tentative definition.  GCC 9.2 (TI's toolchain) merges them via
# COMMON by default; GCC >= 10 defaults to -fno-common and the link fails with
# "multiple definition".  Verified with GCC 14.2.  Harmless on 9.2.
CFLAGS_common  := $(CPU_FLAGS) -g -fcommon -fdata-sections -ffunction-sections
CFLAGS_release := -O2
CFLAGS_debug   := -D_DEBUG_=1

# Application sources: strict.
CFLAGS_app := $(CFLAGS_common) $(CFLAGS_$(PROFILE)) -std=gnu11 \
	-Wall -Wextra -Wshadow -Werror
# SDK kernel/DPL sources: TI's warning set minus -Werror, so a GCC newer than
# TI's 9.2 does not stop the build on a warning TI never saw.
CFLAGS_kernel := $(CFLAGS_common) $(CFLAGS_$(PROFILE)) \
	-Wall -Wno-int-to-pointer-cast -Wno-pointer-to-int-cast \
	-Wno-unused-but-set-variable -Wno-unused-function -Wno-maybe-uninitialized
# SysConfig output: TI's flags.
CFLAGS_gen := $(CFLAGS_common) $(CFLAGS_$(PROFILE)) \
	-Wall -Wno-int-to-pointer-cast -Wno-pointer-to-int-cast \
	-Wno-unused-but-set-variable

LFLAGS := \
	-Wl,-static \
	-Wl,--gc-sections \
	-Wl,--build-id=none \
	-lstdc++ -lgcc -lm -lc -lrdimon

LIBS_PATH := \
	-L$(MCU_PLUS_SDK_PATH)/source/drivers/lib \
	-L$(MCU_PLUS_SDK_PATH)/source/board/lib \
	-L$(CGT_GCC_AARCH64_PATH)/lib

# NOTE: no freertos.am64x.a53-smp.gcc-aarch64.*.lib here — see $(KERNEL_LIB).
LIBS := \
	-l:drivers.am64x.a53.gcc-aarch64.freertos-smp.$(ConfigName).lib \
	-l:board.am64x.a53.gcc-aarch64.freertos-smp.$(ConfigName).lib

# ---- Sources ---------------------------------------------------------------
APP_SRCS := main.c experiment.c console.c
GEN_SRCS := ti_drivers_config.c ti_drivers_open_close.c \
            ti_board_config.c ti_board_open_close.c \
            ti_dpl_config.c ti_pinmux_config.c ti_power_clock_config.c

# Kernel + DPL: the FILES_common / ASMFILES_common lists from
# source/kernel/freertos/makefile.am64x.a53-smp.gcc-aarch64, verbatim.
KERNEL_C := \
	boot_armv8.c DebugP_shmLogWriter.c DebugP_shmLogReader.c \
	DebugP_uartLogWriter.c DebugP_uartScanf.c DebugP_memTraceLogWriter.c \
	DebugP_log.c TimerP.c HwiP_armv8_gic.c MmuP_armv8.c CacheP_armv8.c \
	HeapP_internal.c printf.c AddrTranslateP.c PmuP_armv8.c QueueP_nortos.c \
	ClockP_freertos.c ClockP_freertos_a53.c SemaphoreP_freertos.c \
	HwiP_armv8_handlers_freertos.c HeapP_freertos.c DebugP_freertos.c \
	DebugP_shmLogReader_freertos.c TaskP_freertos.c EventP_freertos.c \
	MailboxP_freertos.c \
	event_groups.c tasks.c timers.c queue.c list.c heap_4.c \
	FreeRTOS_POSIX_clock.c FreeRTOS_POSIX_mqueue.c \
	FreeRTOS_POSIX_pthread_barrier.c FreeRTOS_POSIX_pthread_cond.c \
	FreeRTOS_POSIX_pthread_mutex.c FreeRTOS_POSIX_pthread.c \
	FreeRTOS_POSIX_sched.c FreeRTOS_POSIX_semaphore.c FreeRTOS_POSIX_timer.c \
	FreeRTOS_POSIX_unistd.c FreeRTOS_POSIX_utils.c \
	port.c
KERNEL_S := \
	boot_armv8_asm.S HwiP_armv8_asm.S MmuP_armv8_asm.S CacheP_armv8_asm.S \
	PmuP_armv8_asm.S common_armv8_asm.S portASM.S SpinlockP_armv8.S

vpath %.c $(GEN_DIR) \
          $(KERNEL_ROOT)/nortos/dpl/a53 $(KERNEL_ROOT)/nortos/dpl/common \
          $(FREERTOS)/dpl/common $(FREERTOS)/dpl/a53 \
          $(FREERTOS)/FreeRTOS-Kernel-smp \
          $(FREERTOS)/FreeRTOS-Kernel-smp/portable/MemMang \
          $(FREERTOS)/FreeRTOS-POSIX/FreeRTOS-Plus-POSIX/source \
          $(FREERTOS)/portable_smp/GCC/ARM_CA53
vpath %.S $(KERNEL_ROOT)/nortos/dpl/a53 $(FREERTOS)/portable_smp/GCC/ARM_CA53

APP_OBJS    := $(addprefix $(BUILD_DIR)/,$(APP_SRCS:.c=.obj))
GEN_OBJS    := $(addprefix $(BUILD_DIR)/,$(GEN_SRCS:.c=.obj))
KERNEL_OBJS := $(addprefix $(KOBJ_DIR)/,$(KERNEL_C:.c=.obj) $(KERNEL_S:.S=.obj))
DEPS        := $(APP_OBJS:.obj=.d) $(GEN_OBJS:.obj=.d) $(KERNEL_OBJS:.obj=.d)

.PHONY: all syscfg syscfg-gui kernel appimage clean scrub

all: $(ELF)

$(BUILD_DIR) $(GEN_DIR) $(KOBJ_DIR):
	$(MKDIR) $@

# ---- SysConfig -------------------------------------------------------------
SYSCFG_GEN := $(addprefix $(GEN_DIR)/,$(GEN_SRCS) ti_drivers_config.h \
              ti_drivers_open_close.h ti_board_config.h ti_board_open_close.h \
              ti_dpl_config.h)

syscfg: $(SYSCFG_FILE) | $(GEN_DIR)
	@echo Generating SysConfig files ...
	$(SYSCFG_NODE) $(SYSCFG_CLI_PATH)/dist/cli.js --product $(SYSCFG_SDKPRODUCT) \
		--context a53ss0-0 --part Default --package ALV \
		--output $(GEN_DIR)/ --compiler gcc $(SYSCFG_FILE)

$(SYSCFG_GEN): syscfg

syscfg-gui:
	$(SYSCFG_NWJS) $(SYSCFG_PATH) --product $(SYSCFG_SDKPRODUCT) --device AM64x \
		--context a53ss0-0 --part Default --package ALV \
		--output $(GEN_DIR)/ --compiler gcc $(SYSCFG_FILE)

# ---- Compile ---------------------------------------------------------------
$(BUILD_DIR)/%.obj: %.c $(GEN_DIR)/ti_drivers_config.h | $(BUILD_DIR)
	@echo Compiling app: $<
	$(CC) -c $(CFLAGS_app) $(INCLUDES) $(DEFINES) -MMD -MT $@ -o $@ $<

$(BUILD_DIR)/ti_%.obj: $(GEN_DIR)/ti_%.c | $(BUILD_DIR)
	@echo Compiling syscfg: $<
	$(CC) -c $(CFLAGS_gen) $(INCLUDES) $(DEFINES) -MMD -MT $@ -o $@ $<

$(KOBJ_DIR)/%.obj: %.c | $(KOBJ_DIR)
	@echo Compiling kernel: $<
	$(CC) -c $(CFLAGS_kernel) $(INCLUDES) $(DEFINES) -MMD -MT $@ -o $@ $<

$(KOBJ_DIR)/%.obj: %.S | $(KOBJ_DIR)
	@echo Compiling kernel: $<
	$(CC) -c -x assembler-with-cpp $(CFLAGS_kernel) $(INCLUDES) $(DEFINES) -o $@ $<

# Every kernel object depends on the config overlay: touching the hooks must
# rebuild tasks.c, or the change is silently not in the image.
$(KERNEL_OBJS): FreeRTOSConfig.h

kernel: $(KERNEL_LIB)

$(KERNEL_LIB): $(KERNEL_OBJS)
	@echo Archiving experiment kernel: $@
	$(AR) cr $@ $(KERNEL_OBJS)

# ---- Link ------------------------------------------------------------------
# Objects and the rebuilt kernel go BEFORE the C library inside the group.
# boot_armv8.c defines _sbrk_r; if -lc is scanned first, newer newlibs pull
# their own sbrkr.o for malloc and the link dies with "multiple definition of
# _sbrk_r" (seen with GCC 14.2; TI's 9.2 newlib happens not to trigger it).
$(ELF): $(APP_OBJS) $(GEN_OBJS) $(KERNEL_LIB) linker.cmd
	@echo Linking: $@
	$(LNK) $(LIBS_PATH) -Wl,-Map=$(MAP) -o $@ \
		-Wl,--start-group $(APP_OBJS) $(GEN_OBJS) $(KERNEL_LIB) $(LIBS) $(LFLAGS) -Wl,--end-group \
		-Wl,-T,linker.cmd
	$(SIZE) $@

# ---- Boot image (SBL-loadable) ---------------------------------------------
# Same recipe as the SDK example: ELF -> RPRC -> (xipGen split) -> multicore
# appimage carrying the SAME RPRC for a53ss0-0 (@0) and a53ss0-1 (@1), then the
# x509 wrapper that HS-FS silicon (every SK-AM64 shipped since 2023) requires.
# Load build/<name>.appimage.hs_fs; on a GP device the bare .appimage works.
BOOTIMAGE_CORE_ID_a53ss0-0 := 0
BOOTIMAGE_CORE_ID_a53ss0-1 := 1
SBL_DEV_ID := 55
RPRC       := $(BUILD_DIR)/$(OUTNAME).$(PROFILE).rprc
RPRC_TMP   := $(BUILD_DIR)/$(OUTNAME).$(PROFILE).rprc_tmp
RPRC_XIP   := $(BUILD_DIR)/$(OUTNAME).$(PROFILE).rprc_xip
APPIMAGE   := $(BUILD_DIR)/$(OUTNAME).$(PROFILE).appimage
APPIMAGE_HS_FS := $(APPIMAGE).hs_fs
APPIMAGE_HS    := $(APPIMAGE).hs

MULTI_CORE_IMAGE_GEN := $(SYSCFG_NODE) $(MCU_PLUS_SDK_PATH)/tools/boot/multicoreImageGen/multicoreImageGen.js
OUTRPRC_CMD          := $(SYSCFG_NODE) $(MCU_PLUS_SDK_PATH)/tools/boot/out2rprc/elf2rprc.js
APP_IMAGE_SIGN_CMD   := $(MCU_PLUS_SDK_PATH)/source/security/security_common/tools/boot/signing/appimage_x509_cert_gen.py
ifeq ($(OS),Windows_NT)
  XIPGEN_CMD := $(MCU_PLUS_SDK_PATH)/tools/boot/xipGen/xipGen.exe
else
  XIPGEN_CMD := $(MCU_PLUS_SDK_PATH)/tools/boot/xipGen/xipGen.out
endif

appimage: $(ELF)
	@echo Boot image: $(APPIMAGE)
	$(OUTRPRC_CMD) $(ELF) --out $(RPRC_TMP)
	$(XIPGEN_CMD) -i $(RPRC_TMP) -o $(RPRC) -x $(RPRC_XIP) --flash-start-addr 0x60000000
	$(MULTI_CORE_IMAGE_GEN) --devID $(SBL_DEV_ID) --out $(APPIMAGE) \
		$(RPRC)@$(BOOTIMAGE_CORE_ID_a53ss0-0) $(RPRC)@$(BOOTIMAGE_CORE_ID_a53ss0-1)
	$(RM) $(RPRC_TMP)
ifeq ($(DEVICE_TYPE),HS)
	$(PYTHON) $(APP_IMAGE_SIGN_CMD) --bin $(APPIMAGE) --authtype 1 --key $(APP_SIGNING_KEY) --output $(APPIMAGE_HS)
	@echo Boot image: $(APPIMAGE_HS) Done
else
	$(PYTHON) $(APP_IMAGE_SIGN_CMD) --bin $(APPIMAGE) --authtype 1 --key $(APP_SIGNING_KEY) --output $(APPIMAGE_HS_FS)
	@echo Boot image: $(APPIMAGE_HS_FS) Done
endif

clean:
	$(RMDIR) $(BUILD_DIR)

scrub: clean

-include $(DEPS)
