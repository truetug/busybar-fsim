/* Минимальная замена CMSIS для хоста: furi подключает её ради атрибутов и __get_* */
#pragma once
#include <stdint.h>
#ifndef __STATIC_INLINE
#define __STATIC_INLINE static inline
#endif
#ifndef __STATIC_FORCEINLINE
#define __STATIC_FORCEINLINE static inline __attribute__((always_inline))
#endif
#ifndef __ALWAYS_INLINE
#define __ALWAYS_INLINE inline __attribute__((always_inline))
#endif
#ifndef __WEAK
#define __WEAK __attribute__((weak))
#endif
#ifndef __PACKED
#define __PACKED __attribute__((packed))
#endif
#ifndef __USED
#define __USED __attribute__((used))
#endif
#ifndef __NO_RETURN
#define __NO_RETURN __attribute__((__noreturn__))
#endif
__STATIC_INLINE uint32_t __get_IPSR(void) { return 0; }
__STATIC_INLINE uint32_t __get_PRIMASK(void) { return 0; }
__STATIC_INLINE void __disable_irq(void) {}
__STATIC_INLINE void __enable_irq(void) {}
__STATIC_INLINE void __DSB(void) {}
__STATIC_INLINE void __ISB(void) {}
__STATIC_INLINE void __NOP(void) {}
