/* FreeRTOS для fsim: POSIX-порт (задачи — pthreads). Основа — targets/f21/config/FreeRTOSConfig.h
 * прошивки без Cortex-M: прерываний, MPU, TrustZone и tickless-сна на хосте нет. */
#pragma once
#include <stdint.h>
#include <errno.h>
#include <time.h>

#define configUSE_PREEMPTION             1
#define configSUPPORT_STATIC_ALLOCATION  1
#define configSUPPORT_DYNAMIC_ALLOCATION 1
#define configUSE_MALLOC_FAILED_HOOK     0
#define configUSE_IDLE_HOOK              1 // холостая задача спит (fsim_hal.c), а не крутится вхолостую
#define configUSE_TICK_HOOK              0
#define configCPU_CLOCK_HZ               (160000000UL)
#define configTICK_RATE_HZ_RAW           1000
#define configTICK_RATE_HZ               ((TickType_t)configTICK_RATE_HZ_RAW)
#define configUSE_16_BIT_TICKS           0
#define configMAX_PRIORITIES             (32)
#define configMINIMAL_STACK_SIZE         ((uint16_t)(PTHREAD_STACK_MIN))
#define configUSE_POSIX_ERRNO            1
#define configMAX_TASK_NAME_LEN          (32)

#define configGENERATE_RUN_TIME_STATS 1
extern unsigned long fsim_run_time_counter(void);
#define portGET_RUN_TIME_COUNTER_VALUE() fsim_run_time_counter()
#define portCONFIGURE_TIMER_FOR_RUN_TIME_STATS()

#define configUSE_TRACE_FACILITY                1
#define configUSE_MUTEXES                       1
#define configQUEUE_REGISTRY_SIZE               0
#define configCHECK_FOR_STACK_OVERFLOW          0
#define configUSE_RECURSIVE_MUTEXES             1
#define configUSE_COUNTING_SEMAPHORES           1
#define configENABLE_BACKWARD_COMPATIBILITY     1 // POSIX-порт использует pdTASK_CODE, portTickType
#define configUSE_PORT_OPTIMISED_TASK_SELECTION 0
#define configUSE_TICKLESS_IDLE                 0
#define configRECORD_STACK_HIGH_ADDRESS         1
#define configUSE_NEWLIB_REENTRANT              0

#define configMESSAGE_BUFFER_LENGTH_TYPE        size_t
#define configNUM_THREAD_LOCAL_STORAGE_POINTERS 2
#define configUSE_CO_ROUTINES                   0

#define configUSE_TIMERS              1
#define configTIMER_TASK_PRIORITY     (2)
#define configTIMER_QUEUE_LENGTH      32
#define configTIMER_TASK_STACK_DEPTH  (PTHREAD_STACK_MIN)
#define configTIMER_SERVICE_TASK_NAME "TimersSrv"
#define configIDLE_TASK_NAME          "(-_-)"
#define configIDLE_TASK_STACK_DEPTH   (PTHREAD_STACK_MIN)

#define INCLUDE_xTaskGetHandle              1
#define INCLUDE_eTaskGetState               1
#define INCLUDE_uxTaskGetStackHighWaterMark 1
#define INCLUDE_uxTaskPriorityGet           1
#define INCLUDE_vTaskCleanUpResources       0
#define INCLUDE_vTaskDelay                  1
#define INCLUDE_vTaskDelayUntil             1
#define INCLUDE_vTaskDelete                 1
#define INCLUDE_vTaskPrioritySet            1
#define INCLUDE_vTaskSuspend                1
#define INCLUDE_xQueueGetMutexHolder        1
#define INCLUDE_xTaskGetCurrentTaskHandle   1
#define INCLUDE_xTaskGetSchedulerState      1
#define INCLUDE_xTimerPendFunctionCall      1
#define INCLUDE_xTaskGetIdleTaskHandle      1
#define INCLUDE_xTaskAbortDelay             1

#define configTASK_NOTIFICATION_ARRAY_ENTRIES 3

#if defined(__EMSCRIPTEN__) || defined(FSIM_ASSERT_TRACE)
/* Отладка порта: какое именно условие FreeRTOS не выполнилось */
#include <stdio.h>
#define configASSERT(x)                                                                  \
    if((x) == 0) {                                                                       \
        fprintf(stderr, "FreeRTOS assert failed: %s (%s:%d)\n", #x, __FILE__, __LINE__); \
        furi_crash("FreeRTOS Assert");                                                   \
    }
#else
#define configASSERT(x)                \
    if((x) == 0) {                     \
        furi_crash("FreeRTOS Assert"); \
    }
#endif

#include <limits.h>
#include <core/check.h>
