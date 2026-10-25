/* Куча furi (memmgr_heap.c) берёт границы из символов линкера __heap_start__/__heap_end__.
   На хосте это статический массив в .bss. */
    .section .bss
    .balign 16
    .globl __heap_start__
    .globl __heap_end__
__heap_start__:
    .skip 64 * 1024 * 1024
__heap_end__:
    .section .note.GNU-stack,"",@progbits
