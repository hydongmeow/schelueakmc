#!/bin/bash
# Runs the firmware and tees the UART output to qemu_output.log for latency.py
LOG=qemu_output.log

timeout 20s qemu-system-arm \
    -machine lm3s6965evb \
    -cpu cortex-m3 \
    -kernel firmware.elf \
    -nographic \
    -monitor none \
    -semihosting \
    -semihosting-config enable=on,target=native \
    < /dev/null | tee "$LOG"

echo "--- saved $(wc -l < "$LOG") lines to $LOG ---"
