# 1013/rv32im — AVG 的 RV32IM 實作（CP2）

AVG 的語意（HOMEWORK.md §6.1）：

```
rd = floor((rs1 + rs2) / 2)      中間值需 33-bit，不得溢位
```

## 檔案

| 檔案 | 內容 |
|---|---|
| `test_avg.S` | 四個純量實作 + 一個迴圈版 + 自我測試（Ripes 可直接載入） |
| `test_avg_ci.S` | 同一組測資，改用 AVG 自訂指令（`avg rd, rs1, rs2`） |
| `rv32im_sim.py` | 自製 RV32IM 組譯器 + 模擬器，用來量測指令數（本機無工具鏈） |
| `counts.md` | 量測結果（靜態/動態指令數、分支數、每元素成本） |
| `Makefile` | 實驗室工具鏈用（`make dump` 可看 objdump） |

## 五個實作

| 標籤 | 指令數 | 正確 | 說明 |
|---|---:|---|---|
| `avg_naive` | 3 | ✗ | `add` + `srai`，溢位就錯（負面示範） |
| `avg_wide` | 10 | ✓ | 64-bit 風格：符號位 + 進位組成高位字 |
| `avg_shift` | 7 | ✓ | `(a>>1)+(b>>1)+((a&b)&1)` |
| `avg_bits` | 5 | ✓ | `(a&b) + ((a^b)>>1)`，規格 §6.1 的提示，最短正確版 |
| `avg_loop` | 14（12/元素） | ✓ | 對 16 個元素做向量化平均 |

## 在 Ripes 執行

1. Ripes → `Assembler` 分頁 → 貼上 `test_avg.S`
2. 選 `Single Cycle Processor`（或 5-stage，CP4 之後）
3. Run

結束時：

- `a0 = 0` → 正確版本全數通過
- `s2 = 2` → `avg_naive` 在 2 組溢位測資上失敗（預期如此）
- `s3 = s4 = s6 = s7 = 0` → 其他實作與迴圈版都正確

## 在命令列執行（不需要 RISC-V 工具鏈）

```powershell
python rv32im_sim.py test_avg.S --run --report      # 指令數總表
python rv32im_sim.py test_avg_ci.S --run --report   # 自訂指令版（CP3）
python rv32im_sim.py test_avg.S --fuzz 300          # 300 組隨機/邊界差分測試
python rv32im_sim.py test_avg.S --dump              # 機器碼 listing
python rv32im_sim.py test_avg.S --func avg_bits --args=7,8
python rv32im_sim.py --selftest                     # 驗證編碼器
```

`rv32im_sim.py` 會把 `.S` 組譯成真正的 RV32IM 機器碼再逐條執行，所以：

- 指令數是**執行出來的**，不是數原始碼行數
- 編碼寫錯會直接反映成錯誤結果（不是只影響計數）
- 只支援 `test_avg.S` 用到的指令子集，不是完整的 RV32IM

## 自訂指令（CP3）

`test_avg_ci.S` 用同一組測資，但把 AVG 換成自訂指令：

```asm
        avg   t2, t0, t1        # rd = floor((rs1 + rs2) / 2)
```

| 項目 | baseline | 自訂指令 | 差異 |
|---|---:|---:|---:|
| 單次呼叫（含 `ret`） | 5 | 2 | −60% |
| 每元素（迴圈） | 12 | 9 | −25% |
| 16 元素迴圈（動態） | 194 | 146 | −24.7% |

編碼：R-type，`opcode = 0001011`（custom-0），`funct3 = 000`、`funct7 = 0000000`
（教師分配，已確認）。兩者以常數定義在 `rv32im_sim.py` 開頭的
`AVG_FUNCT3` / `AVG_FUNCT7`。

```powershell
python rv32im_sim.py test_avg_ci.S --run --report
python rv32im_sim.py test_avg_ci.S --dump
```

## 實驗室環境（有 `riscv64-elf-gcc` 時）

```bash
make            # 組譯成 test_avg.elf
make dump       # objdump -d，可與 --dump 的機器碼對照
make fuzz
```

> `Makefile` 尚未在本機驗證（本機沒有 RISC-V 工具鏈），請在實驗室環境執行一次確認。
