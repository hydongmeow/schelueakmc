/* linker.ld — AM64x MAIN_R5FSS0_CORE0 (GCC / arm-none-eabi).
 *
 * Addresses use the R5F *local* view.  The same memories appear at
 * 0x78000000 / 0x78100000 in the SoC view used by the A53, the debugger and
 * the SBL's load headers — do not mix the two.
 *
 * MSRAM: the SoC has 2 MiB in 8 banks of 256 KiB.  The first 512 KiB
 * (0x70000000..0x7007FFFF) belongs to the SBL and the combined boot image and
 * must not be claimed here.  0x70080000 is the conventional r5fss0-0 region;
 * if you later run more than one core, re-check this against the other cores'
 * linker scripts and the Device Manager reservations, because nothing at build
 * time will catch an overlap.
 */

MEMORY
{
    R5F_VECS  (X)   : ORIGIN = 0x00000000, LENGTH = 0x00000040
    R5F_TCMA  (RX)  : ORIGIN = 0x00000040, LENGTH = 0x00007FC0
    R5F_TCMB0 (RW)  : ORIGIN = 0x41010000, LENGTH = 0x00008000
    MSRAM     (RWX) : ORIGIN = 0x70080000, LENGTH = 0x00040000
}

/* Entry symbol comes from the SDK's FreeRTOS r5f startup, not from this port.
 * The name differs between SDK releases and between the GCC and TI-Clang
 * builds — if the link reports an undefined entry symbol, take the ENTRY()
 * line from your SDK's own r5f FreeRTOS linker script rather than guessing. */
ENTRY(_freertosresetvectors)

SECTIONS
{
    .vectors : ALIGN(64)
    {
        KEEP(*(.vectors))
    } > R5F_VECS

    /* ---- Hot path in TCM ------------------------------------------------
     * busy_cycles(), the trace hooks and the context-switch ring buffer.
     * ATCM/BTCM are zero-wait-state and outside the caches, so samples taken
     * here carry no cache variance.  Dropping these two output sections is
     * what EXPERIMENT_HOT_PATH_IN_TCM=0 effectively does at the source level;
     * the sections stay defined either way so the script works for both
     * builds.
     *
     * If .tcm_text overflows R5F_TCMA the link fails loudly, which is the
     * intended behaviour — silently spilling the hot path back into cached
     * MSRAM would corrupt the measurement without any warning.
     */
    .tcm_text : ALIGN(8)
    {
        *(.tcm_text)
        *(.tcm_text.*)
    } > R5F_TCMA

    .tcm_data : ALIGN(8)
    {
        *(.tcm_data)
        *(.tcm_data.*)
    } > R5F_TCMB0

    .text : ALIGN(8)
    {
        *(.text)
        *(.text.*)
        *(.gnu.linkonce.t.*)
    } > MSRAM

    .rodata : ALIGN(8)
    {
        *(.rodata)
        *(.rodata.*)
        *(.gnu.linkonce.r.*)
    } > MSRAM

    .ARM.exidx : ALIGN(8)
    {
        __exidx_start = .;
        *(.ARM.exidx* .gnu.linkonce.armexidx.*)
        __exidx_end = .;
    } > MSRAM

    .data : ALIGN(8)
    {
        __data_start__ = .;
        *(.data)
        *(.data.*)
        __data_end__ = .;
    } > MSRAM

    .bss : ALIGN(8)
    {
        __bss_start__ = .;
        *(.bss)
        *(.bss.*)
        *(COMMON)
        __bss_end__ = .;
    } > MSRAM

    /* FreeRTOS heap and the SDK's own pools. */
    .heap (NOLOAD) : ALIGN(8)
    {
        __heap_start__ = .;
        KEEP(*(.heap))
        . = . + 0x8000;
        __heap_end__ = .;
    } > MSRAM

    /* ---- R5F mode stacks ------------------------------------------------
     * ARMv7-R has a separate stack per processor mode.  This has no Cortex-M
     * equivalent: on the STM32 port a single MSP covered everything.  An
     * undersized IRQ or SVC stack here corrupts memory silently rather than
     * faulting cleanly, so leave these generous.
     */
    .stack (NOLOAD) : ALIGN(16)
    {
        __stack_start__ = .;
        . = . + 0x4000;   /* SYS/USR — FreeRTOS task context */
        __stack_end__ = .;

        __irq_stack_start__ = .;
        . = . + 0x1000;
        __irq_stack_end__ = .;

        __fiq_stack_start__ = .;
        . = . + 0x0400;
        __fiq_stack_end__ = .;

        __svc_stack_start__ = .;
        . = . + 0x1000;
        __svc_stack_end__ = .;

        __abort_stack_start__ = .;
        . = . + 0x0400;
        __abort_stack_end__ = .;

        __undef_stack_start__ = .;
        . = . + 0x0400;
        __undef_stack_end__ = .;
    } > MSRAM
}
