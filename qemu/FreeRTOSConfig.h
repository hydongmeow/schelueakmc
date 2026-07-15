#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H

#define vPortSVCHandler      SVC_Handler
#define xPortPendSVHandler   PendSV_Handler
#define xPortSysTickHandler  SysTick_Handler

#define INCLUDE_vTaskDelay              1
#define INCLUDE_vTaskDelayUntil         1
#define INCLUDE_vTaskPrioritySet        1
#define INCLUDE_uxTaskPriorityGet       1
#define INCLUDE_vTaskDelete             1
#define INCLUDE_vTaskSuspend            1
#define INCLUDE_xTaskGetSchedulerState  1

#define configUSE_PREEMPTION                    1
#define configUSE_TIME_SLICING                  1
#define configUSE_IDLE_HOOK                     0
#define configUSE_TICK_HOOK                     0
#define configUSE_TIMERS                        0
#define configCPU_CLOCK_HZ                      16000000
#define configTICK_RATE_HZ                      1000
#define configMAX_PRIORITIES                    5
#define configMINIMAL_STACK_SIZE                128
#define configTOTAL_HEAP_SIZE                   8192
#define configMAX_TASK_NAME_LEN                 16
#define configUSE_TRACE_FACILITY                1
#define configUSE_16_BIT_TICKS                  0
#define configIDLE_SHOULD_YIELD                 1
#define configQUEUE_REGISTRY_SIZE               8
#define configUSE_MUTEXES                       1
#define configUSE_RECURSIVE_MUTEXES             1
#define configUSE_COUNTING_SEMAPHORES           1
#define configCHECK_FOR_STACK_OVERFLOW          2
#define configUSE_MALLOC_FAILED_HOOK            1

/* ---- Context-switch instrumentation --------------------------------------
 * These hooks bracket the task-selection critical section inside
 * vTaskSwitchContext().  Implemented in main.c; they only stamp a counter
 * (no I/O), so they are safe to run in the PendSV context. */
void trace_switched_out( void );
void trace_switched_in( void );
#define traceTASK_SWITCHED_OUT()   trace_switched_out()
#define traceTASK_SWITCHED_IN()    trace_switched_in()
#define configSUPPORT_DYNAMIC_ALLOCATION        1
#define configSUPPORT_STATIC_ALLOCATION         0

/* Was 1, but no portCONFIGURE_TIMER_FOR_RUN_TIME_STATS /
 * portGET_RUN_TIME_COUNTER_VALUE was supplied -> #error in FreeRTOS.h */
#define configGENERATE_RUN_TIME_STATS           0

#define configASSERT( x ) if( ( x ) == 0 ) { taskDISABLE_INTERRUPTS(); for( ;; ); }

/* ARM Cortex-M specific */
#define configKERNEL_INTERRUPT_PRIORITY         255
/* 191 (0xBF) has the sub-priority bit set; QEMU implements all 8 priority
 * bits, so the CM3 port asserts on it.  Use 190 (0xBE). */
#define configMAX_SYSCALL_INTERRUPT_PRIORITY    190

#endif /* FREERTOS_CONFIG_H */
