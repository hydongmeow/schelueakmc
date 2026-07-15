#include "FreeRTOS.h"
#include "task.h"

/* Minimal SysTick handler */
void SysTick_Handler(void) {
    if (xTaskGetSchedulerState() != taskSCHEDULER_NOT_STARTED) {
        xPortSysTickHandler();
    }
}

/* Context switch handler */
void PendSV_Handler(void) {
    extern void vPortPendSVHandler(void);
    vPortPendSVHandler();
}

/* SVCall handler */
void SVCall_Handler(void) {
    extern void vPortSVCHandler(void);
    vPortSVCHandler();
}

void vPortSetupTimerInterrupt(void) {
    /* Configure SysTick for 1 ms tick */
    volatile uint32_t *SYST_CSR = (uint32_t *)0xE000E010;
    volatile uint32_t *SYST_RVR = (uint32_t *)0xE000E014;
    
    *SYST_RVR = (16000000 / 1000) - 1;  /* 16 MHz / 1000 Hz */
    *SYST_CSR = 0x07;  /* Enable, use CPU clock, enable interrupt */
}

void vPortStartFirstTask(void) {
    __asm volatile (
        "ldr r0, =0xE000ED08\n"
        "ldr r0, [r0]\n"
        "ldr sp, [r0]\n"
        "ldr r0, =0xE000EF34\n"
        "ldr r0, [r0]\n"
        "msr psp, r0\n"
        "movw r0, #2\n"
        "msr control, r0\n"
        "isb\n"
        "svc 0\n"
    );
}
