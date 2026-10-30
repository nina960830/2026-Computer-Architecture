# ---------------------------------------------------------------------------
# test_avg.S - CP2: RV32IM implementation of the AVG custom instruction
#
#   AVG semantics (HOMEWORK.md 6.1):
#       rd = floor((rs1 + rs2) / 2)         33-bit intermediate, never overflows
#
# This one file holds four scalar implementations, one vectorised version and
# a self test, so the numbers in 1013/CP2.md come from exactly the code that
# also runs in Ripes.
#
#   avg_naive : add + srai                        3 instructions   WRONG on overflow
#   avg_wide  : 64-bit style, sign bits + carry  10 instructions   correct
#   avg_shift : (a>>1) + (b>>1) + ((a&b)&1)       7 instructions   correct
#   avg_bits  : (a & b) + ((a ^ b) >> 1)          5 instructions   correct  <- best
#   avg_loop  : avg_bits over N elements         12 instructions per element
#
# Run it
#   Ripes : Assembler -> paste this file -> Run (failures end up in a0)
#   CLI   : python rv32im_sim.py test_avg.S --run --report
#
# ABI note: every function follows ilp32 (a0/a1 in, a0 out, ra return).
# Only t0..t4 are clobbered and no stack frame is used.
# ---------------------------------------------------------------------------

        .text
        .globl _start

# ---------------------------------------------------------------------------
# A. avg_naive - the sequence you write first, and the one that is wrong.
#
#    `add` wraps modulo 2^32, and `srai` then shifts the *wrapped* value, so
#    the bit that left bit 31 is gone.
#      INT_MAX + INT_MAX = 2^32 - 2 -> wraps to -2 -> -2 >> 1 = -1   (want INT_MAX)
#      INT_MIN + INT_MIN = -2^32    -> wraps to  0 ->  0 >> 1 =  0   (want INT_MIN)
# ---------------------------------------------------------------------------
avg_naive:
        add   a0, a0, a1
        srai  a0, a0, 1
        ret

# ---------------------------------------------------------------------------
# B. avg_wide - do it the way a 64-bit compiler would: sign extend both
#    operands, add with carry, then shift the 64-bit result.
#
#    Only bit 0 of the high word survives the shift, and it becomes bit 31 of
#    the result, so the 32-bit result is (high & 1) << 31 | (low >> 1).
#
#    NOTE: this is *not* what `sltu` alone gives you - see 1013/CP2.md
#    section 4.  The carry-out of the unsigned add is not the sign bit of the
#    33-bit sum; the sign bits of both operands have to be added in too.
# ---------------------------------------------------------------------------
avg_wide:
        srai  t0, a0, 31        # 0 or -1 (sign of rs1)
        srai  t1, a1, 31        # 0 or -1 (sign of rs2)
        add   t2, a0, a1        # low 32 bits of rs1 + rs2
        sltu  t3, t2, a0        # carry-out of the 32-bit add
        add   t4, t0, t1        # high word = sign bits
        add   t4, t4, t3        #           + carry
        slli  t4, t4, 31        # bit 0 of the high word -> bit 31
        srli  t2, t2, 1         # low part >> 1 (logical: the sign lives in t4)
        or    a0, t2, t4
        ret

# ---------------------------------------------------------------------------
# C. avg_shift - split each operand around the shift instead of the add.
#
#    With arithmetic shift, a = 2*(a>>1) + (a&1), so
#      floor((a+b)/2) = (a>>1) + (b>>1) + ((a&1) + (b&1))/2
#    and ((a&1)+(b&1))/2 is 1 only when both low bits are 1, i.e. (a&b)&1.
#    Both halves stay in range, so nothing overflows.
# ---------------------------------------------------------------------------
avg_shift:
        srai  t0, a0, 1
        srai  t1, a1, 1
        add   t0, t0, t1        # (a>>1) + (b>>1)
        and   t2, a0, a1
        andi  t2, t2, 1         # 1 when both operands are odd
        add   a0, t0, t2
        ret

# ---------------------------------------------------------------------------
# D. avg_bits - (a + b) = (a & b) + (a ^ b) with no carry into the (a ^ b)
#    term, so (a ^ b) >> 1 can be done on 32 bits with an arithmetic shift and
#    the final add cannot overflow.  This is the hardware hint from
#    HOMEWORK.md 6.1 and the shortest correct version.
# ---------------------------------------------------------------------------
avg_bits:
        and   t0, a0, a1
        xor   t1, a0, a1
        srai  t1, t1, 1
        add   a0, t0, t1
        ret

# ---------------------------------------------------------------------------
# E. avg_loop - the same math over N pairs, used for the per-element count
#    that CP3 compares against a single AVG instruction.
#      a0 = dst, a1 = src_a, a2 = src_b, a3 = n
# ---------------------------------------------------------------------------
avg_loop:
        beqz  a3, loop_done
loop_body:
        lw    t0, 0(a1)
        lw    t1, 0(a2)
        and   t2, t0, t1
        xor   t3, t0, t1
        srai  t3, t3, 1
        add   t2, t2, t3
        sw    t2, 0(a0)
        addi  a1, a1, 4
        addi  a2, a2, 4
        addi  a0, a0, 4
        addi  a3, a3, -1
        bnez  a3, loop_body
loop_done:
        ret

# ---------------------------------------------------------------------------
# Self test: run all 12 vectors through the four scalar versions and count
# mismatches in s2 (naive) / s3 (wide) / s4 (shift) / s6 (bits), then run
# avg_loop over 16 elements and spot check three of the results in s7.
# ---------------------------------------------------------------------------
_start:
        la    s0, vectors
        la    s1, vectors_end
        li    s2, 0                # failures: avg_naive  (expected: 2)
        li    s3, 0                # failures: avg_wide
        li    s4, 0                # failures: avg_shift
        li    s6, 0                # failures: avg_bits
        li    s7, 0                # failures: avg_loop spot checks

next_vector:
        beq   s0, s1, loop_bench
        lw    s5, 8(s0)            # expected value

        lw    a0, 0(s0)
        lw    a1, 4(s0)
        jal   ra, avg_naive
        beq   a0, s5, ok_naive
        addi  s2, s2, 1
ok_naive:

        lw    a0, 0(s0)
        lw    a1, 4(s0)
        jal   ra, avg_wide
        beq   a0, s5, ok_wide
        addi  s3, s3, 1
ok_wide:

        lw    a0, 0(s0)
        lw    a1, 4(s0)
        jal   ra, avg_shift
        beq   a0, s5, ok_shift
        addi  s4, s4, 1
ok_shift:

        lw    a0, 0(s0)
        lw    a1, 4(s0)
        jal   ra, avg_bits
        beq   a0, s5, ok_bits
        addi  s6, s6, 1
ok_bits:

        addi  s0, s0, 12
        j     next_vector

# --- vectorised version over 16 elements -----------------------------------
loop_bench:
        la    a0, dst
        la    a1, src_a
        la    a2, src_b
        li    a3, 16
        jal   ra, avg_loop

        la    t0, dst
        lw    t1, 16(t0)           # dst[4] must be INT_MAX
        li    t2, 2147483647
        beq   t1, t2, chk_min
        addi  s7, s7, 1
chk_min:
        lw    t1, 20(t0)           # dst[5] must be INT_MIN
        li    t2, -2147483648
        beq   t1, t2, chk_last
        addi  s7, s7, 1
chk_last:
        lw    t1, 60(t0)           # dst[15] must be 0
        beq   t1, zero, finish
        addi  s7, s7, 1

finish:
        add   a0, s3, s4           # exit code = failures of the correct versions
        add   a0, a0, s6
        add   a0, a0, s7
        li    a7, 10
        ecall

# ---------------------------------------------------------------------------
# Test vectors: {rs1, rs2, expected}, 12 entries.
# The first 10 are the ones from 1013/README.md; the last two add 0+0 and
# INT_MIN+0.
# ---------------------------------------------------------------------------
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

# --- 16 element arrays for avg_loop ---------------------------------------
        .align 2
src_a:
        .word  7, -3, 1, -1, 2147483647, -2147483648, 0, 5
        .word  -10, 100, -100, 12345, -12345, 999, -999, 3
src_b:
        .word  8, -4, 0, 0, 2147483647, -2147483648, 0, 5
        .word  3, -100, 100, -12345, 12345, -999, 999, -3
dst:
        .zero 64
