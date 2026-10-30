# 實驗室環境用（本機沒有 RISC-V 工具鏈，這裡的目標都沒有在本機驗證過）
#
#   make        # 組譯成 test_avg.elf
#   make dump   # objdump -d，可與 rv32im_sim.py --dump 的機器碼逐條對照
#   make sim    # 用自製模擬器跑一次並印出指令數
#   make fuzz   # 300 組隨機/邊界差分測試
#
# 若工具鏈前綴不同（例如 riscv64-unknown-elf- 或 riscv32-unknown-elf-），
# 用 `make CROSS=riscv64-unknown-elf- ...` 覆蓋。

CROSS   ?= riscv64-elf-
CFLAGS  ?= -march=rv32im -mabi=ilp32 -nostdlib -nostartfiles -Wl,-Ttext=0x0
PYTHON  ?= python

all: test_avg.elf

test_avg.elf: test_avg.S
	$(CROSS)gcc $(CFLAGS) $< -o $@

dump: test_avg.elf
	$(CROSS)objdump -d test_avg.elf

sim:
	$(PYTHON) rv32im_sim.py test_avg.S --run --report

fuzz:
	$(PYTHON) rv32im_sim.py test_avg.S --fuzz 300

clean:
	rm -f test_avg.elf

.PHONY: all dump sim fuzz clean
