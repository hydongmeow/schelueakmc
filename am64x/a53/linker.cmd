/* linker.cmd — AM64x dual Cortex-A53 SMP (GCC / aarch64-none-elf).
 *
 * This is the MCU+ SDK's own script for the a53ss0-0 FreeRTOS SMP examples
 * (examples/kernel/freertos/smp_task_switch/am64x-evm/a53ss0-0_freertos-smp/
 * gcc-aarch64/linker.cmd), taken verbatim apart from this comment block.
 * The earlier hand-written linker.ld guessed at three things this file
 * settles from the SDK:
 *
 *   ENTRY(_c_int00)      the SDK's AArch64 startup symbol (boot_armv8_asm.S)
 *   .vecs                the section name portASM.S puts the GICv3 vector
 *                        table in (it carries its own .align 11, i.e. 2 KiB)
 *   __TI_STACK_BASE/1    the two per-core stacks boot_armv8_asm.S selects by
 *                        MPIDR.Aff0 — core 0 and core 1 respectively
 *
 * Memory: DDR from 0x80000000, 32 MiB claimed.  The SBL + DM firmware + R5F
 * carveouts on SK-AM64 live in MSRAM (0x70000000) and low DDR is what TI's
 * own examples use, so this is not a guess any more.  If Linux is ever
 * resident on the A53s at the same time this must move; it is not for this
 * experiment.
 *
 * The three shared-memory regions are SDK conventions (IPC, log buffer).  This
 * application uses none of them, but the SDK libraries reference the symbols,
 * so the sections stay.
 *
 * Do not add a .heap large enough for configTOTAL_HEAP_SIZE here: heap_4.c
 * declares its own ucHeap[] in .bss.  __TI_HEAP_SIZE below is newlib's.
 */

ENTRY(_c_int00)

__TI_STACK_SIZE = 65536;
__TI_HEAP_SIZE = 131072;

MEMORY
{
    DDR : ORIGIN =  0x80000000, LENGTH = 0x2000000

    /* shared memory segments */
    /* On A53,
     * - make sure there is a MMU entry which maps below regions as non-cache
     */
    USER_SHM_MEM            : ORIGIN = 0x701D0000, LENGTH = 0x80
    LOG_SHM_MEM             : ORIGIN = 0x701D0000 + 0x80, LENGTH = 0x00004000 - 0x80
    RTOS_NORTOS_IPC_SHM_MEM : ORIGIN = 0x701D4000, LENGTH = 0x0000C000
}

SECTIONS {

    .vecs : {} > DDR
    .text : {} > DDR
    .rodata : {} > DDR

    .data : ALIGN (8) {
        __data_load__ = LOADADDR (.data);
        __data_start__ = .;
        *(.data)
        *(.data*)
        . = ALIGN (8);
        __data_end__ = .;
    } > DDR

    /* General purpose user shared memory, used in some examples */
    .bss.user_shared_mem (NOLOAD) : { KEEP(*(.bss.user_shared_mem)) } > USER_SHM_MEM
    /* this is used when Debug log's to shared memory are enabled, else this is not used */
    .bss.log_shared_mem  (NOLOAD) : { KEEP(*(.bss.log_shared_mem)) } > LOG_SHM_MEM
    /* this is used only when IPC RPMessage is enabled, else this is not used */
    .bss.ipc_vring_mem   (NOLOAD) : { KEEP(*(.bss.ipc_vring_mem)) } > RTOS_NORTOS_IPC_SHM_MEM

    .bss : {
        __bss_start__ = .;
        *(.bss)
        *(.bss.*)
        . = ALIGN (8);
        *(COMMON)
        __bss_end__ = .;
        . = ALIGN (8);
    } > DDR

    .heap (NOLOAD) : {
        __heap_start__ = .;
        KEEP(*(.heap))
        . = . + __TI_HEAP_SIZE;
        __heap_end__ = .;
    } > DDR

    .stack (NOLOAD) : ALIGN(16) {
        __TI_STACK_BASE = .;
        KEEP(*(.stack))
        . = . + __TI_STACK_SIZE;
        __TI_STACK_BASE1 = .;
        KEEP(*(.stack))
        . = . + __TI_STACK_SIZE;
    } > DDR

}
