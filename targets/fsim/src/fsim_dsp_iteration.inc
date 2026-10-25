/* fsim: переносимая замена dsp_2d_kernel_iteration из lib/toolbox/dsp.c (там ARM: uxtb/smlabb/bfi).
   Та же арифметика: канал (0..255) × нижние 16 бит ядра со знаком в 32-битный аккумулятор,
   в результат идут биты 15..8 каждого аккумулятора (деление на 256 без насыщения). */
static uint32_t dsp_2d_kernel_iteration(
    const uint32_t* source,
    size_t stride,
    size_t kernel_sz,
    const int32_t* kernel) {
    uint32_t accumulator[4] = {0, 0, 0, 0};
    size_t ky = kernel_sz;
    while(ky--) {
        size_t kx = kernel_sz;
        while(kx--) {
            uint32_t px = *(source++);
            int32_t krnl = (int16_t)(*(kernel++) & 0xFFFF);
            for(int channel = 0; channel < 4; channel++) {
                int32_t value = (int32_t)((px >> (8 * channel)) & 0xFF);
                accumulator[channel] += (uint32_t)(value * krnl);
            }
        }
        source += stride;
    }
    return (((accumulator[3] >> 8) & 0xFF) << 24) | (((accumulator[2] >> 8) & 0xFF) << 16) |
           (((accumulator[1] >> 8) & 0xFF) << 8) | ((accumulator[0] >> 8) & 0xFF);
}

