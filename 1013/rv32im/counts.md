# CP2 量測結果（自動產生）

所有數字由 `rv32im_sim.py` 直接組譯並執行 `test_avg.S` 得到，不是手算。

```powershell
python rv32im_sim.py test_avg.S --run --report
python rv32im_sim.py test_avg.S --fuzz 300
```

- 工具：`rv32im_sim.py` v1.0（自製 RV32IM 組譯器 + 指令階層模擬器）
- 執行環境：Python 3.13.14，Windows 11
- 計數方法：**動態**（模擬器逐條執行，記錄每個 pc 的執行次數）
- 靜態計數：組譯後 `.text` 的實際指令字數（虛擬指令展開後）

## 1. 正確性

12 組測資（10 組來自 `1013/README.md`，另加 `0+0` 與 `INT_MIN+0`）：

| 實作 | 測資失敗數 | 隨機/邊界 300 組失敗數 | 判定 |
|---|---:|---:|---|
| `avg_naive` | 2 / 12 | 80 / 300 | 錯（溢位） |
| `avg_wide` | 0 / 12 | 0 / 300 | 正確 |
| `avg_shift` | 0 / 12 | 0 / 300 | 正確 |
| `avg_bits` | 0 / 12 | 0 / 300 | 正確 |

程式結束時 `a0 = 0`（正確版本零失敗），`s2 = 2`（naive 的失敗數）。

naive 失敗的兩組正是溢位邊界：`INT_MAX + INT_MAX` 與 `INT_MIN + INT_MIN`。

## 2. 靜態指令數

| 實作 | 靜態指令數 | 相對 naive |
|---|---:|---:|
| `avg_naive`（add + srai） | 3 | 100% |
| `avg_wide`（64-bit 風格） | 10 | 333% |
| `avg_shift`（拆移位） | 7 | 233% |
| `avg_bits`（位元技巧） | 5 | 167% |
| `avg_loop`（16 元素迴圈） | 14 | — |
| `_start`（測試主程式） | 61 | — |
| **`.text` 合計** | **100** | — |

## 3. 動態指令數（跑完 12 組測資）

| 函式 | 呼叫次數 | 動態指令數 | 每次呼叫 | 分支 | 跳躍 | load | store | ALU |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `avg_naive` | 12 | 36 | 3 | 0 | 12 | 0 | 0 | 24 |
| `avg_wide` | 12 | 120 | 10 | 0 | 12 | 0 | 0 | 108 |
| `avg_shift` | 12 | 84 | 7 | 0 | 12 | 0 | 0 | 72 |
| `avg_bits` | 12 | 60 | 5 | 0 | 12 | 0 | 0 | 48 |
| `avg_loop`（n=16） | 1 | 194 | 194 | 17 | 1 | 32 | 16 | 128 |
| `_start` | 1 | 277 | 277 | 64 | 61 | 111 | 0 | 32 |
| **合計** | | **771** | | **81** | **110** | **143** | **16** | **412** |

## 4. 分支數

| 項目 | 分支指令（beq/bne/blt/bge/bltu/bgeu） | 跳躍（jal/jalr） |
|---|---:|---:|
| 三個純量實作（各 12 次呼叫） | 0 | 12（每次 `ret`） |
| `avg_loop`（n=16） | 17（1 次 `beqz` + 16 次 `bnez`） | 1（`ret`） |
| `_start` | 64 | 61 |
| **合計** | **81** | **110** |

`avg_loop` 的分支密度：17 / 194 = **8.8%**。

## 5. 每元素成本（迴圈版，n=16）

`avg_loop` 每次迴圈 12 條：

```
lw    t0, 0(a1)        load     1
lw    t1, 0(a2)        load     1
and   t2, t0, t1       ALU      1
xor   t3, t0, t1       ALU      1
srai  t3, t3, 1        ALU      1
add   t2, t2, t3       ALU      1
sw    t2, 0(a0)        store    1
addi  a1, a1, 4        ALU      1
addi  a2, a2, 4        ALU      1
addi  a0, a0, 4        ALU      1
addi  a3, a3, -1       ALU      1
bnez  a3, loop_body    branch   1
```

12 條指令/元素，其中**只有 4 條是 AVG 本體**（and/xor/srai/add），
其餘 8 條是記憶體存取（2 load + 1 store）與迴圈 overhead（4 個 `addi` + 1 個 `bnez`）。
這是 CP3 比較自訂指令時的主要論點。

## 6. 量測輸出原文

```
static instructions (whole .text) : 100
dynamic instructions (whole run)  : 771
exit code in a0 (ecall)           : 0

fuzz: 300 pairs (seed 20261030), 1 of every 5 drawn from the edge set
  avg_naive  mismatches = 80
  avg_wide   mismatches = 0
  avg_shift  mismatches = 0
  avg_bits   mismatches = 0
```
