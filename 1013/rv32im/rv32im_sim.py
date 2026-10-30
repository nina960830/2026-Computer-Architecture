#!/usr/bin/env python3
"""rv32im_sim.py - a minimal RV32IM assembler + simulator, used for CP2 counting.

Why this exists
---------------
The CP2 report needs *static* and *dynamic* instruction counts plus branch
counts for the hand written RV32IM version of AVG.  The machine this was
written on has no RISC-V GNU toolchain (no riscv64-elf-gcc, no binutils), so
this file does two jobs in one place:

  1. assemble  : turns test_avg.S into real RV32IM machine code
  2. simulate  : executes that machine code and counts, per function,
                 instructions / branches / jumps / loads / stores

Because the simulator decodes the *encoded words* (not the source lines), a
wrong encoding shows up as a wrong result - the counts and the encodings are
checked together.

The same test_avg.S can be pasted into Ripes to cross-check the numbers.

Supported subset (everything test_avg.S uses)
--------------------------------------------
  ALU   add sub and or xor sll srl sra slt sltu
  IMM   addi andi ori xori slli srli srai slti sltiu
  MEM   lw sw lb lbu lh lhu sb sh
  CTRL  beq bne blt bge bltu bgeu jal jalr lui auipc ecall ebreak fence
  PSEUDO li la mv nop ret j beqz bnez neg not
  DIR   .text .data .section .globl/.global .align .word .byte .zero .size .type .option

This is NOT a complete RV32IM implementation and is not meant to be.

Usage
-----
  python rv32im_sim.py --selftest
  python rv32im_sim.py test_avg.S --run
  python rv32im_sim.py test_avg.S --run --report
  python rv32im_sim.py test_avg.S --dump
  python rv32im_sim.py test_avg.S --func avg_bits --args 7,8
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field

VERSION = "1.0"
TEXT_BASE = 0x0000
DATA_BASE = 0x1000
MEM_SIZE = 0x0002_0000  # 128 KiB, plenty for this program

REG_NAMES = [
    "zero", "ra", "sp", "gp", "tp", "t0", "t1", "t2",
    "s0", "s1", "a0", "a1", "a2", "a3", "a4", "a5",
    "a6", "a7", "s2", "s3", "s4", "s5", "s6", "s7",
    "s8", "s9", "s10", "s11", "t3", "t4", "t5", "t6",
]
REG = {name: i for i, name in enumerate(REG_NAMES)}
REG.update({f"x{i}": i for i in range(32)})
REG["fp"] = 8

R_OPS = {
    "add": (0x00, 0), "sub": (0x20, 0), "sll": (0x00, 1), "slt": (0x00, 2),
    "sltu": (0x00, 3), "xor": (0x00, 4), "srl": (0x00, 5), "sra": (0x20, 5),
    "or": (0x00, 6), "and": (0x00, 7),
}
I_OPS = {"addi": 0, "slti": 2, "sltiu": 3, "xori": 4, "ori": 6, "andi": 7}
SHIFT_OPS = {"slli": (1, 0x00), "srli": (5, 0x00), "srai": (5, 0x20)}
LOAD_OPS = {"lb": 0, "lh": 1, "lw": 2, "lbu": 4, "lhu": 5}
STORE_OPS = {"sb": 0, "sh": 1, "sw": 2}
BRANCH_OPS = {"beq": 0, "bne": 1, "blt": 4, "bge": 5, "bltu": 6, "bgeu": 7}
PSEUDO = {"li", "la", "mv", "nop", "ret", "j", "beqz", "bnez", "neg", "not", "call"}

# ---------------------------------------------------------------------------
# Custom instruction (HOMEWORK.md section 4: R-type, opcode = custom-0).
# AVG is the only custom instruction in this project:
#     avg rd, rs1, rs2      rd = floor((rs1 + rs2) / 2)   33-bit intermediate
# funct3 / funct7 are assigned by the teacher; if the assigned values differ,
# change these two constants and nothing else.
# ---------------------------------------------------------------------------
CUSTOM_OPCODE = 0x0B
AVG_FUNCT3 = 0b000
AVG_FUNCT7 = 0b0000000
CUSTOM_MNEMONICS = {"avg": (AVG_FUNCT7, AVG_FUNCT3)}

CATEGORY = {}
for _m in CUSTOM_MNEMONICS:
    CATEGORY[_m] = "custom"
for _m in R_OPS:
    CATEGORY[_m] = "alu"
for _m in I_OPS:
    CATEGORY[_m] = "alu_imm"
for _m in SHIFT_OPS:
    CATEGORY[_m] = "shift"
for _m in LOAD_OPS:
    CATEGORY[_m] = "load"
for _m in STORE_OPS:
    CATEGORY[_m] = "store"
for _m in BRANCH_OPS:
    CATEGORY[_m] = "branch"
for _m in ("jal", "jalr"):
    CATEGORY[_m] = "jump"
for _m in ("lui", "auipc"):
    CATEGORY[_m] = "upper"
CATEGORY.update({"ecall": "system", "ebreak": "system", "fence": "system"})


# --------------------------------------------------------------------------
# encoding helpers (bit layouts follow the RISC-V unprivileged spec)
# --------------------------------------------------------------------------
def sext(value: int, bits: int) -> int:
    sign = 1 << (bits - 1)
    return (value & ((1 << bits) - 1)) - (value & sign) * 2


def enc_r(f7: int, rs2: int, rs1: int, f3: int, rd: int, op: int) -> int:
    return ((f7 & 0x7F) << 25) | ((rs2 & 0x1F) << 20) | ((rs1 & 0x1F) << 15) \
        | ((f3 & 7) << 12) | ((rd & 0x1F) << 7) | (op & 0x7F)


def enc_i(imm: int, rs1: int, f3: int, rd: int, op: int) -> int:
    return ((imm & 0xFFF) << 20) | ((rs1 & 0x1F) << 15) | ((f3 & 7) << 12) \
        | ((rd & 0x1F) << 7) | (op & 0x7F)


def enc_s(imm: int, rs2: int, rs1: int, f3: int, op: int) -> int:
    return (((imm >> 5) & 0x7F) << 25) | ((rs2 & 0x1F) << 20) | ((rs1 & 0x1F) << 15) \
        | ((f3 & 7) << 12) | ((imm & 0x1F) << 7) | (op & 0x7F)


def enc_b(imm: int, rs2: int, rs1: int, f3: int, op: int) -> int:
    imm &= 0x1FFF
    return (((imm >> 12) & 1) << 31) | (((imm >> 5) & 0x3F) << 25) | ((rs2 & 0x1F) << 20) \
        | ((rs1 & 0x1F) << 15) | ((f3 & 7) << 12) | (((imm >> 1) & 0xF) << 8) \
        | (((imm >> 11) & 1) << 7) | (op & 0x7F)


def enc_u(imm20: int, rd: int, op: int) -> int:
    return ((imm20 & 0xFFFFF) << 12) | ((rd & 0x1F) << 7) | (op & 0x7F)


def enc_j(imm: int, rd: int, op: int) -> int:
    imm &= 0x1FFFFF
    return (((imm >> 20) & 1) << 31) | (((imm >> 1) & 0x3FF) << 21) \
        | (((imm >> 11) & 1) << 20) | (((imm >> 12) & 0xFF) << 12) \
        | ((rd & 0x1F) << 7) | (op & 0x7F)


# --------------------------------------------------------------------------
# assembler
# --------------------------------------------------------------------------
@dataclass
class Insn:
    addr: int
    size: int
    mnem: str
    ops: list[str]
    src: str
    lineno: int
    words: list[int] = field(default_factory=list)

    @property
    def cat(self) -> str:
        return CATEGORY.get(self.mnem, "pseudo")


class AsmError(Exception):
    pass


class Assembler:
    def __init__(self, path: str):
        self.path = path
        self.symbols: dict[str, int] = {}
        self.insns: list[Insn] = []
        self.bytes: dict[int, int] = {}
        self.text_end = TEXT_BASE
        self.data_end = DATA_BASE
        self.section = "text"

    # -- helpers ---------------------------------------------------------
    def _cur(self) -> int:
        return self.text_end if self.section == "text" else self.data_end

    def _advance(self, n: int) -> None:
        if self.section == "text":
            self.text_end += n
        else:
            self.data_end += n

    def _emit_byte(self, value: int) -> None:
        addr = self._cur()
        self.bytes[addr] = value & 0xFF
        self._advance(1)

    def _emit_word(self, value: int) -> None:
        for i in range(4):
            self._emit_byte((value >> (8 * i)) & 0xFF)

    def _eval(self, expr: str) -> int:
        """Evaluate a number, a label, or a small arithmetic expression."""
        expr = expr.strip()
        if not expr:
            raise AsmError("empty expression")
        # replace known labels with their addresses
        def repl(match: re.Match) -> str:
            name = match.group(0)
            if name in self.symbols:
                return str(self.symbols[name])
            return name

        substituted = re.sub(r"[A-Za-z_.$][\w.$]*", repl, expr)
        if not re.fullmatch(r"[0-9a-fA-FxX+\-*/()<>&|~ \t]+", substituted):
            raise AsmError(f"cannot evaluate expression: {expr!r}")
        try:
            return int(eval(substituted, {"__builtins__": {}}, {}))  # noqa: S307
        except Exception as exc:  # pragma: no cover - defensive
            raise AsmError(f"cannot evaluate expression: {expr!r} ({exc})") from exc

    def _reg(self, tok: str) -> int:
        key = tok.strip().lower()
        if key not in REG:
            raise AsmError(f"unknown register {tok!r}")
        return REG[key]

    @staticmethod
    def _mem(tok: str) -> tuple[int, str]:
        m = re.fullmatch(r"\s*(.*?)\s*\(\s*([\w.$]+)\s*\)\s*", tok)
        if not m:
            raise AsmError(f"bad memory operand {tok!r}")
        return (int(m.group(1), 0) if m.group(1) else 0), m.group(2)

    @staticmethod
    def _split_ops(text: str) -> list[str]:
        if not text.strip():
            return []
        return [p.strip() for p in text.split(",")]

    def _insn_size(self, mnem: str, ops: list[str]) -> int:
        if mnem == "li":
            return 4 if -2048 <= self._eval(ops[1]) <= 2047 else 8
        if mnem in ("la", "call"):
            return 8
        return 4

    # -- pass 1 ----------------------------------------------------------
    def pass1(self, lines: list[str]) -> None:
        for lineno, raw in enumerate(lines, 1):
            line = raw.split("#")[0].split("//")[0].rstrip()
            if not line.strip():
                continue

            # label definition (possibly on its own line)
            label_match = re.match(r"^\s*([A-Za-z_.$][\w.$]*)\s*:\s*(.*)$", line)
            if label_match:
                name, rest = label_match.groups()
                if name in self.symbols:
                    raise AsmError(f"line {lineno}: duplicate label {name!r}")
                self.symbols[name] = self._cur()
                line = rest
                if not line.strip():
                    continue

            stripped = line.strip()
            if stripped.startswith("."):
                self._directive(stripped, lineno)
                continue

            parts = stripped.split(None, 1)
            mnem = parts[0].lower()
            ops = self._split_ops(parts[1] if len(parts) > 1 else "")
            if mnem not in CATEGORY and mnem not in PSEUDO:
                raise AsmError(f"line {lineno}: unsupported mnemonic {mnem!r}")
            size = self._insn_size(mnem, ops)
            self.insns.append(Insn(self._cur(), size, mnem, ops, stripped, lineno))
            self._advance(size)

    def _directive(self, text: str, lineno: int) -> None:
        parts = text.split(None, 1)
        name = parts[0].lower()
        args = parts[1].strip() if len(parts) > 1 else ""

        if name in (".text", ".section"):
            if name == ".section" and args and "data" in args:
                self.section = "data"
            elif name == ".section" and args and "text" in args:
                self.section = "text"
            else:
                self.section = "text"
        elif name == ".data":
            self.section = "data"
        elif name in (".globl", ".global", ".size", ".type", ".option", ".file", ".ident"):
            pass
        elif name == ".align":
            n = 1 << self._eval(args)
            pad = (-self._cur()) % n
            self._advance(pad)
        elif name == ".word":
            for item in self._split_ops(args):
                if self.section != "data":
                    raise AsmError(f"line {lineno}: .word outside .data")
                self._emit_word(self._eval(item))
        elif name == ".byte":
            for item in self._split_ops(args):
                if self.section != "data":
                    raise AsmError(f"line {lineno}: .byte outside .data")
                self._emit_byte(self._eval(item))
        elif name == ".zero":
            for _ in range(self._eval(args)):
                if self.section != "data":
                    raise AsmError(f"line {lineno}: .zero outside .data")
                self._emit_byte(0)
        else:
            raise AsmError(f"line {lineno}: unsupported directive {name!r}")

    # -- pass 2 ----------------------------------------------------------
    def pass2(self) -> None:
        for insn in self.insns:
            insn.words = self._encode(insn)
            if len(insn.words) * 4 != insn.size:
                raise AsmError(f"line {insn.lineno}: size mismatch for {insn.mnem}")
            for offset, word in enumerate(insn.words):
                for i in range(4):
                    self.bytes[insn.addr + offset * 4 + i] = (word >> (8 * i)) & 0xFF

    def _encode(self, insn: Insn) -> list[int]:
        m, ops = insn.mnem, insn.ops
        try:
            if m == "li":
                rd = self._reg(ops[0])
                imm = self._eval(ops[1])
                if insn.size == 4:
                    return [enc_i(imm, 0, 0, rd, 0x13)]
                hi = ((imm + 0x800) >> 12) & 0xFFFFF
                lo = imm - (sext(hi, 20) << 12)
                return [enc_u(hi, rd, 0x37), enc_i(lo, rd, 0, rd, 0x13)]

            if m == "la":
                rd = self._reg(ops[0])
                target = self._eval(ops[1])
                delta = target - insn.addr
                hi = ((delta + 0x800) >> 12) & 0xFFFFF
                lo = delta - (sext(hi, 20) << 12)
                return [enc_u(hi, rd, 0x17), enc_i(lo, rd, 0, rd, 0x13)]

            if m == "mv":
                return [enc_i(0, self._reg(ops[1]), 0, self._reg(ops[0]), 0x13)]
            if m == "nop":
                return [enc_i(0, 0, 0, 0, 0x13)]
            if m == "ret":
                return [enc_i(0, REG["ra"], 0, 0, 0x67)]
            if m == "not":
                return [enc_i(-1, self._reg(ops[1]), 4, self._reg(ops[0]), 0x13)]
            if m == "neg":
                return [enc_r(0x20, self._reg(ops[1]), 0, 0, self._reg(ops[0]), 0x33)]
            if m == "j":
                offset = self._eval(ops[0]) - insn.addr
                return [enc_j(offset, 0, 0x6F)]
            if m in ("beqz", "bnez"):
                rs = self._reg(ops[0])
                offset = self._eval(ops[1]) - insn.addr
                f3 = 0 if m == "beqz" else 1
                return [enc_b(offset, 0, rs, f3, 0x63)]

            if m in CUSTOM_MNEMONICS:
                f7, f3 = CUSTOM_MNEMONICS[m]
                return [enc_r(f7, self._reg(ops[2]), self._reg(ops[1]), f3,
                              self._reg(ops[0]), CUSTOM_OPCODE)]

            if m in R_OPS:
                f7, f3 = R_OPS[m]
                return [enc_r(f7, self._reg(ops[2]), self._reg(ops[1]), f3,
                              self._reg(ops[0]), 0x33)]
            if m in I_OPS:
                return [enc_i(self._eval(ops[2]), self._reg(ops[1]), I_OPS[m],
                              self._reg(ops[0]), 0x13)]
            if m in SHIFT_OPS:
                f3, f7bits = SHIFT_OPS[m]
                imm = (f7bits << 5) | (self._eval(ops[2]) & 0x1F)
                return [enc_i(imm, self._reg(ops[1]), f3, self._reg(ops[0]), 0x13)]
            if m in LOAD_OPS:
                imm, base = self._mem(ops[1])
                return [enc_i(imm, self._reg(base), LOAD_OPS[m],
                              self._reg(ops[0]), 0x03)]
            if m in STORE_OPS:
                imm, base = self._mem(ops[1])
                return [enc_s(imm, self._reg(ops[0]), self._reg(base),
                              STORE_OPS[m], 0x23)]
            if m in BRANCH_OPS:
                offset = self._eval(ops[2]) - insn.addr
                return [enc_b(offset, self._reg(ops[1]), self._reg(ops[0]),
                              BRANCH_OPS[m], 0x63)]
            if m == "jal":
                if len(ops) == 1:
                    rd, target = REG["ra"], ops[0]
                else:
                    rd, target = self._reg(ops[0]), ops[1]
                return [enc_j(self._eval(target) - insn.addr, rd, 0x6F)]
            if m == "jalr":
                rd = self._reg(ops[0])
                if len(ops) == 1:
                    imm, base = 0, REG[ops[0]] if False else self._reg(ops[0])
                    # "jalr rs" means jalr ra, 0(rs)
                    return [enc_i(0, self._reg(ops[0]), 0, REG["ra"], 0x67)]
                imm, base = self._mem(ops[1])
                return [enc_i(imm, self._reg(base), 0, rd, 0x67)]
            if m == "lui":
                return [enc_u(self._eval(ops[1]) & 0xFFFFF, self._reg(ops[0]), 0x37)]
            if m == "auipc":
                return [enc_u(self._eval(ops[1]) & 0xFFFFF, self._reg(ops[0]), 0x17)]
            if m == "ecall":
                return [0x00000073]
            if m == "ebreak":
                return [0x00100073]
            if m == "fence":
                return [0x0FF0000F]
            if m == "call":
                target = self._eval(ops[0])
                delta = target - insn.addr
                hi = ((delta + 0x800) >> 12) & 0xFFFFF
                lo = delta - (sext(hi, 20) << 12)
                return [enc_u(hi, REG["ra"], 0x17), enc_i(lo, REG["ra"], 0, REG["ra"], 0x67)]
        except IndexError as exc:
            raise AsmError(f"line {insn.lineno}: wrong operand count for {m}: {ops}") from exc
        raise AsmError(f"line {insn.lineno}: unsupported instruction {m!r}")

    # -- driver ----------------------------------------------------------
    def assemble(self, extra_prefix: str = "") -> "Program":
        with open(self.path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        if lines:
            lines[0] = lines[0].lstrip("\ufeff")  # tolerate a UTF-8 BOM
        if extra_prefix:
            lines = [line + "\n" for line in extra_prefix.splitlines()] + lines
        self.pass1(lines)
        self.pass2()
        return Program(self)


class Program:
    def __init__(self, asm: Assembler):
        self.asm = asm
        self.image = bytearray(MEM_SIZE)
        for addr, byte in asm.bytes.items():
            if addr >= MEM_SIZE:
                raise AsmError(f"address 0x{addr:x} outside simulated memory")
            self.image[addr] = byte
        self.image_len = max([TEXT_BASE] + [a + 1 for a in asm.bytes])
        self.text_insns = [i for i in asm.insns if i.addr < DATA_BASE]
        self.symbols = dict(asm.symbols)

    def word_at(self, addr: int) -> int:
        return int.from_bytes(self.image[addr:addr + 4], "little")

    def functions(self) -> list[str]:
        """Function labels, in address order.

        A label counts as a function when it is the target of a `jal` or when
        it is `_start`.  Inner labels (loop bodies, ok_*) are therefore not
        mistaken for function boundaries.
        """
        names = {"_start"} if "_start" in self.symbols else set()
        for insn in self.text_insns:
            if insn.mnem == "jal" and len(insn.ops) == 2:
                names.add(insn.ops[1])
        return [n for n in sorted(names, key=lambda n: self.symbols[n]) if n in self.symbols]

    def func_range(self, name: str) -> tuple[int, int]:
        if name not in self.symbols:
            raise AsmError(f"no such label: {name!r}")
        order = self.functions()
        if name not in order:
            raise AsmError(f"{name!r} is not a function label (not a jal target)")
        idx = order.index(name)
        start = self.symbols[name]
        end = self.symbols[order[idx + 1]] if idx + 1 < len(order) else self.asm.text_end
        return start, end

    def insns_in(self, start: int, end: int) -> list[Insn]:
        return [i for i in self.text_insns if start <= i.addr < end]


# --------------------------------------------------------------------------
# simulator
# --------------------------------------------------------------------------
class Sim:
    def __init__(self, prog: Program, entry: int | None = None):
        self.prog = prog
        self.mem = bytearray(prog.image)
        self.x = [0] * 32
        self.pc = entry if entry is not None else 0
        self.x[2] = MEM_SIZE - 16           # sp
        self.exec_count: dict[int, int] = {}
        self.cat_count: dict[str, int] = {}
        self.steps = 0
        self.halt = False
        self.exit_code = None

    # memory helpers
    def ld(self, addr: int, size: int, signed: bool) -> int:
        raw = int.from_bytes(self.mem[addr:addr + size], "little")
        if signed:
            return sext(raw, size * 8)
        return raw

    def st(self, addr: int, value: int, size: int) -> None:
        self.mem[addr:addr + size] = (value & ((1 << (size * 8)) - 1)).to_bytes(size, "little")

    def run(self, max_steps: int = 2_000_000) -> None:
        while not self.halt and self.steps < max_steps:
            self.step()
        if self.steps >= max_steps:
            raise RuntimeError("simulation did not halt (step limit reached)")

    def step(self) -> None:
        pc = self.pc
        word = self.prog.word_at(pc)
        opcode = word & 0x7F
        rd = (word >> 7) & 0x1F
        f3 = (word >> 12) & 7
        rs1 = (word >> 15) & 0x1F
        rs2 = (word >> 20) & 0x1F
        f7 = (word >> 25) & 0x7F
        a = self.x[rs1]
        b = self.x[rs2]
        next_pc = pc + 4
        cat = "other"

        def wr(i: int, v: int) -> None:
            if i:
                self.x[i] = v & 0xFFFFFFFF

        if opcode == 0x33:
            cat = "alu"
            sh = b & 0x1F
            if f3 == 0:
                wr(rd, (a - b) if f7 == 0x20 else (a + b))
            elif f3 == 1:
                wr(rd, a << sh)
            elif f3 == 2:
                wr(rd, 1 if sext(a, 32) < sext(b, 32) else 0)
            elif f3 == 3:
                wr(rd, 1 if a < b else 0)
            elif f3 == 4:
                wr(rd, a ^ b)
            elif f3 == 5:
                wr(rd, (sext(a, 32) >> sh) if f7 == 0x20 else (a >> sh))
            elif f3 == 6:
                wr(rd, a | b)
            elif f3 == 7:
                wr(rd, a & b)
            else:
                raise RuntimeError(f"bad funct3 {f3} at 0x{pc:x}")
        elif opcode == 0x13:
            cat = "alu_imm"
            imm = sext((word >> 20) & 0xFFF, 12)
            if f3 == 0:
                wr(rd, a + imm)
            elif f3 == 2:
                wr(rd, 1 if sext(a, 32) < imm else 0)
            elif f3 == 3:
                wr(rd, 1 if a < (imm & 0xFFFFFFFF) else 0)
            elif f3 == 4:
                wr(rd, a ^ imm)
            elif f3 == 6:
                wr(rd, a | imm)
            elif f3 == 7:
                wr(rd, a & imm)
            elif f3 == 1:
                wr(rd, a << (imm & 0x1F))
            elif f3 == 5:
                sh = imm & 0x1F
                wr(rd, (sext(a, 32) >> sh) if (imm & 0x400) else (a >> sh))
            else:
                raise RuntimeError(f"bad funct3 {f3} at 0x{pc:x}")
        elif opcode == 0x03:
            cat = "load"
            imm = sext((word >> 20) & 0xFFF, 12)
            addr = (a + imm) & 0xFFFFFFFF
            if f3 == 0:
                wr(rd, self.ld(addr, 1, True))
            elif f3 == 1:
                wr(rd, self.ld(addr, 2, True))
            elif f3 == 2:
                wr(rd, self.ld(addr, 4, True))
            elif f3 == 4:
                wr(rd, self.ld(addr, 1, False))
            elif f3 == 5:
                wr(rd, self.ld(addr, 2, False))
            else:
                raise RuntimeError(f"bad load funct3 {f3} at 0x{pc:x}")
        elif opcode == 0x23:
            cat = "store"
            imm = sext(((word >> 25) << 5) | ((word >> 7) & 0x1F), 12)
            addr = (a + imm) & 0xFFFFFFFF
            size = {0: 1, 1: 2, 2: 4}.get(f3)
            if size is None:
                raise RuntimeError(f"bad store funct3 {f3} at 0x{pc:x}")
            self.st(addr, b, size)
        elif opcode == 0x63:
            cat = "branch"
            imm = sext((((word >> 31) & 1) << 12) | (((word >> 7) & 1) << 11)
                       | (((word >> 25) & 0x3F) << 5) | (((word >> 8) & 0xF) << 1), 13)
            taken = {
                0: a == b, 1: a != b,
                4: sext(a, 32) < sext(b, 32), 5: sext(a, 32) >= sext(b, 32),
                6: a < b, 7: a >= b,
            }.get(f3)
            if taken is None:
                raise RuntimeError(f"bad branch funct3 {f3} at 0x{pc:x}")
            if taken:
                next_pc = pc + imm
        elif opcode == 0x67:
            cat = "jump"
            imm = sext((word >> 20) & 0xFFF, 12)
            target = (a + imm) & 0xFFFFFFFF
            wr(rd, pc + 4)
            next_pc = target
        elif opcode == 0x6F:
            cat = "jump"
            imm = sext((((word >> 31) & 1) << 20) | (((word >> 12) & 0xFF) << 12)
                       | (((word >> 20) & 1) << 11) | (((word >> 21) & 0x3FF) << 1), 21)
            wr(rd, pc + 4)
            next_pc = pc + imm
        elif opcode == 0x37:
            cat = "upper"
            wr(rd, word & 0xFFFFF000)
        elif opcode == 0x17:
            cat = "upper"
            wr(rd, (pc + (word & 0xFFFFF000)) & 0xFFFFFFFF)
        elif opcode == 0x0F:
            cat = "system"
        elif opcode == CUSTOM_OPCODE:
            cat = "custom"
            if f3 == AVG_FUNCT3 and f7 == AVG_FUNCT7:
                # floor((rs1 + rs2) / 2) with a 33-bit intermediate
                wr(rd, (sext(a, 32) + sext(b, 32)) // 2)
            else:
                raise RuntimeError(
                    f"unknown custom instruction f7={f7:#09b} f3={f3:#05b} at 0x{pc:x}")
        elif opcode == 0x73:
            cat = "system"
            self.exit_code = self.x[10]
            self.halt = True
        else:
            raise RuntimeError(f"unsupported opcode 0x{opcode:02x} at 0x{pc:x}")

        self.exec_count[pc] = self.exec_count.get(pc, 0) + 1
        self.cat_count[cat] = self.cat_count.get(cat, 0) + 1
        self.steps += 1
        self.pc = next_pc
        if next_pc >= self.prog.image_len and cat in ("branch", "jump"):
            raise RuntimeError(f"control flow left the program at 0x{next_pc:x}")


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
def cat_of_word(word: int) -> str:
    """Category of an *encoded* instruction (pseudo instructions expand to two
    words, so counting per word keeps ALU/upper honest)."""
    op = word & 0x7F
    f3 = (word >> 12) & 7
    if op == 0x33:
        return "alu"
    if op == 0x13:
        return "shift" if f3 in (1, 5) else "alu_imm"
    if op == 0x03:
        return "load"
    if op == 0x23:
        return "store"
    if op == 0x63:
        return "branch"
    if op in (0x67, 0x6F):
        return "jump"
    if op in (0x37, 0x17):
        return "upper"
    if op == CUSTOM_OPCODE:
        return "custom"
    return "system"


def summarise(prog: Program, sim: Sim) -> dict:
    funcs = []
    for name in prog.functions():
        start, end = prog.func_range(name)
        insns = prog.insns_in(start, end)
        cats: dict[str, int] = {}
        dynamic = 0
        for i in insns:
            for k, word in enumerate(i.words):
                n = sim.exec_count.get(i.addr + 4 * k, 0)
                if n:
                    dynamic += n
                    cat = cat_of_word(word)
                    cats[cat] = cats.get(cat, 0) + n
        funcs.append({
            "name": name,
            "start": start,
            "end": end,
            "static": sum(len(i.words) for i in insns),
            "dynamic": dynamic,
            "calls": sim.exec_count.get(start, 0),
            "branch": cats.get("branch", 0),
            "jump": cats.get("jump", 0),
            "load": cats.get("load", 0),
            "store": cats.get("store", 0),
            "alu": cats.get("alu", 0) + cats.get("alu_imm", 0) + cats.get("shift", 0),
            "custom": cats.get("custom", 0),
        })
    return {
        "funcs": funcs,
        "total_static": sum(len(i.words) for i in prog.text_insns),
        "total_dynamic": sim.steps,
        "exit_code": sim.exit_code,
        "cats": sim.cat_count,
    }


def print_report(info: dict, prog: Program, sim: Sim) -> None:
    print("=" * 78)
    print(f"static instructions (whole .text) : {info['total_static']}")
    print(f"dynamic instructions (whole run)  : {info['total_dynamic']}")
    print(f"exit code in a0 (ecall)           : {info['exit_code']}")
    print("-" * 78)
    header = f"{'function':<12}{'static':>8}{'dynamic':>9}{'calls':>7}{'branch':>8}{'jump':>6}{'load':>6}{'store':>7}{'alu':>7}{'custom':>8}"
    print(header)
    for f in info["funcs"]:
        print(f"{f['name']:<12}{f['static']:>8}{f['dynamic']:>9}{f['calls']:>7}"
              f"{f['branch']:>8}{f['jump']:>6}{f['load']:>6}{f['store']:>7}{f['alu']:>7}"
              f"{f['custom']:>8}")
    print("-" * 78)
    print("register file (non zero):")
    for i in range(32):
        if sim.x[i]:
            print(f"  {REG_NAMES[i]:<5} (x{i:<2}) = {sext(sim.x[i], 32):>12}   0x{sim.x[i]:08x}")


def print_dump(prog: Program) -> None:
    for insn in prog.text_insns:
        for k, word in enumerate(insn.words):
            addr = insn.addr + 4 * k
            text = insn.src if k == 0 else "  (upper half of pseudo)"
            print(f"{addr:08x}  {word:08x}  {text}")


def print_markdown(info: dict, prog: Program) -> None:
    print("\n| function | static | calls | dynamic | branch | jump | load | store | ALU | custom |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for f in info["funcs"]:
        print(f"| `{f['name']}` | {f['static']} | {f['calls']} | {f['dynamic']} | "
              f"{f['branch']} | {f['jump']} | {f['load']} | {f['store']} | {f['alu']} | {f['custom']} |")
    print(f"| **total (.text)** | {info['total_static']} | | {info['total_dynamic']} | "
          f"{info['cats'].get('branch', 0)} | {info['cats'].get('jump', 0)} | "
          f"{info['cats'].get('load', 0)} | {info['cats'].get('store', 0)} | "
          f"{sum(info['cats'].get(k, 0) for k in ('alu', 'alu_imm', 'shift'))} | "
          f"{info['cats'].get('custom', 0)} |")


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------
def selftest() -> int:
    checks = [
        ("addi a0, zero, 1", 0x00100513),
        ("ret", 0x00008067),
        ("add a0, a0, a1", 0x00B50533),
        ("lw t0, 0(a1)", 0x0005A283),
        ("sw t2, 0(a0)", 0x00752023),
        ("beq s0, s1, 8", 0x00940463),
        ("srai t1, t1, 1", 0x40135313),
        ("sltu t1, t0, a0", 0x00A2B333),
        ("lui a0, 1", 0x00001537),
        ("avg a0, a0, a1", 0x00B5050B),
    ]
    failures = 0
    for text, expected in checks:
        src = ".text\n" + text + "\n"
        path = "_selftest.S"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(src)
        asm = Assembler(path)
        prog = asm.assemble()
        got = prog.word_at(0)
        ok = got == expected
        failures += 0 if ok else 1
        print(f"[{'OK ' if ok else 'BAD'}] {text:<24} got 0x{got:08x} want 0x{expected:08x}")
        os.remove(path)

    # semantic check: the bit-twiddle AVG over a small sweep
    def avg_bits(a: int, b: int) -> int:
        return sext(((a & b) + (sext(a ^ b, 32) >> 1)) & 0xFFFFFFFF, 32)

    for a, b in [(7, 8), (-3, -4), (2**31 - 1, 2**31 - 1),
                 (-(2**31), -(2**31)), (2**31 - 1, -(2**31)), (-10, 3)]:
        want = (a + b) // 2
        got = avg_bits(a, b)
        ok = got == want
        failures += 0 if ok else 1
        print(f"[{'OK ' if ok else 'BAD'}] avg_bits({a}, {b}) = {got} (want {want})")

    print("selftest:", "PASS" if failures == 0 else f"{failures} FAILURE(S)")
    return 0 if failures == 0 else 1


# --------------------------------------------------------------------------
# differential fuzz against the C semantics
# --------------------------------------------------------------------------
def fuzz(source: str, funcs: list[str], n: int, seed: int) -> int:
    import random

    rng = random.Random(seed)
    fails = {f: 0 for f in funcs}
    edges = [-(2**31), -(2**31) + 1, -1, 0, 1, 2**31 - 2, 2**31 - 1]

    for i in range(n):
        if i % 5 == 0:
            a, b = rng.choice(edges), rng.choice(edges)
        else:
            a, b = rng.randint(-(2**31), 2**31 - 1), rng.randint(-(2**31), 2**31 - 1)
        want = (a + b) // 2          # python // is floor, i.e. the spec
        for f in funcs:
            prefix = (".text\n_drv:\n"
                      f"        li a0, {a}\n"
                      f"        li a1, {b}\n"
                      f"        jal ra, {f}\n"
                      f"        ecall\n")
            prog = Assembler(source).assemble(extra_prefix=prefix)
            sim = Sim(prog, entry=prog.symbols["_drv"])
            sim.run()
            got = sext(sim.x[10], 32)
            if got != want:
                fails[f] += 1
                if fails[f] <= 3:
                    print(f"  MISMATCH {f}({a}, {b}) = {got}, want {want}")

    print(f"fuzz: {n} pairs (seed {seed}), 1 of every 5 drawn from the edge set")
    for f in funcs:
        print(f"  {f:<10} mismatches = {fails[f]}")
    broken = [f for f in funcs if f != "avg_naive" and fails[f]]
    return 0 if not broken else 1


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv: list[str]) -> int:
    try:  # keep Chinese output from breaking on a cp950 console
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # pragma: no cover
        pass
    ap = argparse.ArgumentParser(description="minimal RV32IM assembler + simulator")
    ap.add_argument("source", nargs="?", help="assembly file (.S)")
    ap.add_argument("--run", action="store_true", help="assemble and run from _start")
    ap.add_argument("--func", help="call a single function with --args and count it")
    ap.add_argument("--args", default="", help="comma separated int32 arguments, e.g. 7,8")
    ap.add_argument("--dump", action="store_true", help="print machine code listing")
    ap.add_argument("--report", action="store_true", help="also print markdown tables")
    ap.add_argument("--selftest", action="store_true", help="run encoder/semantics checks")
    ap.add_argument("--fuzz", type=int, metavar="N",
                    help="differential test N random pairs against floor((a+b)/2)")
    ap.add_argument("--seed", type=int, default=20261030, help="fuzz seed")
    ap.add_argument("--no-run", action="store_true", help="assemble only")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.source:
        ap.print_help()
        return 2

    if args.fuzz:
        funcs = ["avg_naive", "avg_wide", "avg_shift", "avg_bits"]
        return fuzz(args.source, funcs, args.fuzz, args.seed)

    if args.func:
        vals = [int(v, 0) for v in args.args.split(",") if v.strip()]
        regs = ["a0", "a1", "a2", "a3", "a4", "a5", "a6", "a7"][:len(vals)]
        prefix = ".text\n_drv:\n"
        for r, v in zip(regs, vals):
            prefix += f"        li {r}, {v}\n"
        prefix += f"        jal ra, {args.func}\n        ecall\n"
        asm = Assembler(args.source)
        prog = asm.assemble(extra_prefix=prefix)
        sim = Sim(prog, entry=prog.symbols["_drv"])
        sim.run()
        start, end = prog.func_range(args.func)
        body = prog.insns_in(start, end)
        dyn = sum(sim.exec_count.get(i.addr, 0) for i in body)
        print(f"{args.func}({args.args}) = {sext(sim.x[10], 32)}")
        print(f"dynamic instructions (incl. ret) = {dyn}   static = {sum(len(i.words) for i in body)}")
        return 0

    asm = Assembler(args.source)
    prog = asm.assemble()
    if args.dump:
        print_dump(prog)
    if args.no_run or not args.run:
        if not args.dump:
            print(f"assembled: {len(prog.text_insns)} instructions in .text, "
                  f"text_end=0x{asm.text_end:x}, data_end=0x{asm.data_end:x}")
        return 0

    sim = Sim(prog, entry=prog.symbols.get("_start", 0))
    sim.run()
    info = summarise(prog, sim)
    print_report(info, prog, sim)
    if args.report:
        print_markdown(info, prog)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
