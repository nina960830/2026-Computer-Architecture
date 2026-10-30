# ---------------------------------------------------------------------------
# test_avg_ci.S - CP3: the same test as test_avg.S, but the AVG computation is
# done by a single custom instruction instead of the RV32IM sequence.
#
#   avg rd, rs1, rs2        rd = floor((rs1 + rs2) / 2)     33-bit intermediate
#
# Encoding (HOMEWORK.md section 4: R-type, custom-0)
#
#   31      25 24   20 19   15 14  12 11    7 6       0
#   +----------+-------+-------+------+-------+---------+
#   |  funct7  |  rs2  |  rs1  |funct3|  rd   | 0001011 |
#   +----------+-------+-------+------+-------+---------+
#
#   funct7 = 0000000 (teacher assigned - change AVG_FUNCT7 in rv32im_sim.py)
#   funct3 = 000     (teacher assigned - change AVG_FUNCT3 in rv32im_sim.py)
#
# Compare with test_avg.S:
#   avg_bits   : and / xor / srai / add / ret        5 instructions
#   avg_scalar : avg / ret                           2 instructions
#   avg_loop   : 12 instructions per element
#   avg_loop_ci:  9 instructions per element
#
# Run
#   python rv32im_sim.py test_avg_ci.S --run --report
# ---------------------------------------------------------------------------

        .text
        .globl _start

# --- the custom instruction, wrapped as a normal ilp32 function -------------
avg_scalar:
        avg   a0, a0, a1
        ret

# --- vectorised version using the custom instruction -----------------------
#   a0 = dst, a1 = src_a, a2 = src_b, a3 = n
avg_loop_ci:
        beqz  a3, ci_done
ci_body:
        lw    t0, 0(a1)
        lw    t1, 0(a2)
        avg   t2, t0, t1
        sw    t2, 0(a0)
        addi  a1, a1, 4
        addi  a2, a2, 4
        addi  a0, a0, 4
        addi  a3, a3, -1
        bnez  a3, ci_body
ci_done:
        ret

# --- self test: 12 vectors through avg_scalar, then 16 elements through
#     avg_loop_ci with three spot checks ------------------------------------
_start:
        la    s0, vectors
        la    s1, vectors_end
        li    s2, 0                # failures

ci_next_vector:
        beq   s0, s1, ci_bench
        lw    a0, 0(s0)
        lw    a1, 4(s0)
        lw    s4, 8(s0)
        jal   ra, avg_scalar
        beq   a0, s4, ci_ok
        addi  s2, s2, 1
ci_ok:
        addi  s0, s0, 12
        j     ci_next_vector

ci_bench:
        la    a0, dst
        la    a1, src_a
        la    a2, src_b
        li    a3, 16
        jal   ra, avg_loop_ci

        la    t0, dst
        lw    t1, 16(t0)           # dst[4] must be INT_MAX
        li    t2, 2147483647
        beq   t1, t2, ci_chk_min
        addi  s2, s2, 1
ci_chk_min:
        lw    t1, 20(t0)           # dst[5] must be INT_MIN
        li    t2, -2147483648
        beq   t1, t2, ci_chk_last
        addi  s2, s2, 1
ci_chk_last:
        lw    t1, 60(t0)           # dst[15] must be 0
        beq   t1, zero, ci_finish
        addi  s2, s2, 1

ci_finish:
        mv    a0, s2               # exit code = number of failures
        li    a7, 10
        ecall

# --- same data as test_avg.S ----------------------------------------------
        .data
        .align 2
vectors:
        .word  7, 8, 7
        .word  -3, -4, -4
        .word  1, 0, 0
        .word  -1, 0, -1
        .word  -1, 1, 0
        .word  2147483647, 2147483647, 2147483647
        .word  -2147483648, -2147483648, -2147483648
        .word  2147483647, -2147483648, -1
        .word  5, 5, 5
        .word  -10, 3, -4
        .word  0, 0, 0
        .word  -2147483648, 0, -1073741824
vectors_end:

        .align 2
src_a:
        .word  7, -3, 1, -1, 2147483647, -2147483648, 0, 5
        .word  -10, 100, -100, 12345, -12345, 999, -999, 3
src_b:
        .word  8, -4, 0, 0, 2147483647, -2147483648, 0, 5
        .word  3, -100, 100, -12345, 12345, -999, 999, -3
dst:
        .zero 64
