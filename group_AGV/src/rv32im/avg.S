.text
.align 2
.globl avg_rv32im
.type avg_rv32im, @function

# a0 = rs1, a1 = rs2; result is returned in a0.
avg_rv32im:
    and  t0, a0, a1
    xor  t1, a0, a1
    srai t1, t1, 1
    add  a0, t0, t1
    ret

.size avg_rv32im, .-avg_rv32im
