#include "FreeRTOS.h"
#include "task.h"

void vApplicationMallocFailedHook(void) { for (;;); }
void vApplicationStackOverflowHook(TaskHandle_t xTask, char *pcName) {
    (void)xTask; (void)pcName;
    for (;;);
}

extern unsigned long _estack;
extern unsigned long _sidata, _sdata, _edata, _sbss, _ebss;

int main(void);                 /* declared, NOT defined — lives in main.c */

void Reset_Handler(void);
void Default_Handler(void);

/* Provided by the FreeRTOS kernel via the FreeRTOSConfig.h #defines */
void SVC_Handler(void);
void PendSV_Handler(void);
void SysTick_Handler(void);

__attribute__((section(".isr_vector"), used))
void (* const g_vectors[])(void) = {
    (void (*)(void))&_estack,   /* 0  initial stack pointer */
    Reset_Handler,              /* 1  reset                 */
    Default_Handler,            /* 2  NMI                   */
    Default_Handler,            /* 3  HardFault             */
    Default_Handler,            /* 4  MemManage             */
    Default_Handler,            /* 5  BusFault              */
    Default_Handler,            /* 6  UsageFault            */
    0, 0, 0, 0,                 /* 7-10 reserved            */
    SVC_Handler,                /* 11 SVCall                */
    Default_Handler,            /* 12 DebugMon              */
    0,                          /* 13 reserved              */
    PendSV_Handler,             /* 14 PendSV                */
    SysTick_Handler,            /* 15 SysTick               */
};

void Reset_Handler(void)
{
    unsigned long *src = &_sidata;
    unsigned long *dst = &_sdata;
    while (dst < &_edata) *dst++ = *src++;   /* copy .data to RAM */

    dst = &_sbss;
    while (dst < &_ebss) *dst++ = 0;         /* zero .bss */

    main();                                  /* your main.c */
    for (;;);                                /* main should never return */
}

void Default_Handler(void) { for (;;); }
