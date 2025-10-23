# DDS 命令速查 · 波形验证菜单

**用途**：插上示波器，SSH 到板上，按下面命令一条一条复制粘贴即可验证各种波形。不解释原理、不说故事，只给可运行的命令。原理见 [USAGE.md](USAGE.md)。

**适用前提**：已经 `install.sh` 过、板卡在跑（`verify.sh` 能过）。

---

## §0 前置（每次 SSH 进来先跑一次）

```bash
# 加载 DDS bitstream + 启动 backend（每次板卡上电/重启后必做一次）
/opt/redpitaya/fpga/z20_125/dds_axi_dma_workbench/load_overlay.sh
systemctl restart rp-dds-axi-dma.service

# 建议设 alias（下面命令都靠它）；也可以加进 ~/.bashrc 一劳永逸
alias dds-cli='python3 /opt/redpitaya/www/apps/dds_axi_dma_workbench/backend/dds_cli.py'
alias dds-verify='/opt/redpitaya/fpga/z20_125/dds_axi_dma_workbench/verify.sh'

# 9 项全绿再继续
dds-verify
```

---

## §1 四种预设波形（都是 CH A、1 kHz、满幅）

```bash
dds-cli config --channel a --wave sine     --freq 1000 --amp 1.0 --enable
dds-cli config --channel a --wave square   --freq 1000 --amp 1.0 --enable
dds-cli config --channel a --wave triangle --freq 1000 --amp 1.0 --enable
dds-cli config --channel a --wave saw      --freq 1000 --amp 1.0 --enable
```

---

## §2 改幅度（正弦、1 kHz）

```bash
dds-cli config --channel a --wave sine --freq 1000 --amp 1.0   --enable   # 满幅  ±1 V
dds-cli config --channel a --wave sine --freq 1000 --amp 0.5   --enable   # 半幅  ±0.5 V
dds-cli config --channel a --wave sine --freq 1000 --amp 0.1   --enable   # 1/10  ±0.1 V
dds-cli config --channel a --wave sine --freq 1000 --amp 0.01  --enable   # ±10 mV（看底噪）
dds-cli config --channel a --wave sine --freq 1000 --amp -1.0  --enable   # 反相满幅
```

---

## §3 改频率（正弦、满幅）

```bash
dds-cli config --channel a --wave sine --freq 10          --amp 1.0 --enable   # 10 Hz 慢扫
dds-cli config --channel a --wave sine --freq 1000        --amp 1.0 --enable   # 1 kHz
dds-cli config --channel a --wave sine --freq 100000      --amp 1.0 --enable   # 100 kHz
dds-cli config --channel a --wave sine --freq 1000000     --amp 1.0 --enable   # 1 MHz
dds-cli config --channel a --wave sine --freq 10000000    --amp 1.0 --enable   # 10 MHz
dds-cli config --channel a --wave sine --freq 30000000    --amp 1.0 --enable   # 30 MHz（接近 Nyquist 62.5 MHz）
dds-cli config --channel a --wave sine --freq 0.5         --amp 1.0 --enable   # 0.5 Hz 看相位慢慢转
```

---

## §4 DC 偏置 / 纯 DC 发生器

```bash
# 带 DC 的正弦：AC ±0.5 + DC 0.3 → 输出在 -0.2 到 +0.8 之间
dds-cli config --channel a --wave sine --freq 1000 --amp 0.5 --dc 0.3  --enable

# AC ±0.3 + DC -0.5 → 输出在 -0.8 到 -0.2 之间
dds-cli config --channel a --wave sine --freq 1000 --amp 0.3 --dc -0.5 --enable

# 纯 DC +0.5 V（AC 部分振幅 0）
dds-cli config --channel a --wave sine --freq 0    --amp 0.0 --dc 0.5  --enable

# 纯 DC -0.7 V
dds-cli config --channel a --wave sine --freq 0    --amp 0.0 --dc -0.7 --enable
```

---

## §5 相位 & 双通道同步

```bash
# CH A 基准 0°，CH B 移 90°，两路都 1 kHz 满幅，同时清相位累加器
dds-cli config --channel a --wave sine --freq 1000 --amp 1.0 --enable --clear-phase
dds-cli config --channel b --wave sine --freq 1000 --amp 1.0 --phase 90 --enable --clear-phase

# 同频反相（CH A 0°、CH B 180°）：示波器双通道显示应该完全镜像
dds-cli config --channel a --wave sine --freq 1000 --amp 1.0 --enable --clear-phase
dds-cli config --channel b --wave sine --freq 1000 --amp 1.0 --phase 180 --enable --clear-phase
```

---

## §6 自定义波形（`expr`，全部 CH A、1 kHz、满幅）

```bash
# 方波的 4 项傅里叶展开（有明显 Gibbs 振铃）
dds-cli expr --channel a --freq 1000 --amp 1.0 \
    --expression 'sin(x) + sin(3*x)/3 + sin(5*x)/5 + sin(7*x)/7'

# 高斯脉冲（每周期中点一个 ~84 µs 尖峰）
dds-cli expr --channel a --freq 1000 --amp 1.0 \
    --expression 'exp(-((x-pi)^2)/0.1)'

# 极窄高斯脉冲（把 0.1 改成 0.01 → 脉冲窄很多）
dds-cli expr --channel a --freq 1000 --amp 1.0 \
    --expression 'exp(-((x-pi)^2)/0.01)'

# 半波整流（|sin|）
dds-cli expr --channel a --freq 1000 --amp 1.0 \
    --expression 'abs(sin(x))'

# 陡峭方波（tanh 逼近，边沿比内置 square 稍软，但无 Gibbs）
dds-cli expr --channel a --freq 1000 --amp 1.0 \
    --expression 'tanh(10*sin(x))'

# 阻尼振荡：8 次振荡 + 指数衰减包络
dds-cli expr --channel a --freq 1000 --amp 1.0 \
    --expression 'exp(-3*t)*sin(16*pi*t)'

# 啁啾（周期内线性扫频）
dds-cli expr --channel a --freq 500 --amp 1.0 \
    --expression 'sin(2*pi*(1+5*t)*t*5)'

# 基波 + 3 次谐波（小"驼峰"）
dds-cli expr --channel a --freq 1000 --amp 1.0 \
    --expression 'sin(x) + 0.3*sin(3*x+pi/4)'

# 锯齿 + 余弦调制（给锯齿加"涟漪"）
dds-cli expr --channel a --freq 1000 --amp 1.0 \
    --expression 'x/pi - 1 + 0.2*cos(15*x)'

# "心跳"样：一个 P 波 + QRS + T 波
dds-cli expr --channel a --freq 2 --amp 1.0 \
    --expression '0.2*exp(-((x-0.6)^2)*100) + 0.9*exp(-((x-2.5)^2)*200) + 0.3*exp(-((x-4.0)^2)*50)'

# 方波脉冲宽度调制（占空比约 10%）
dds-cli expr --channel a --freq 1000 --amp 1.0 \
    --expression '1/(1 + exp(50*(x-0.628)))'
```

---

## §7 双通道同时出不同波形

```bash
# CH A 正弦 1 kHz + CH B 锯齿 500 Hz
dds-cli config --channel a --wave sine --freq 1000 --amp 1.0 --enable
dds-cli config --channel b --wave saw  --freq 500  --amp 0.8 --enable

# CH A 4 项傅里叶方波 + CH B 三角波（独立输出）
dds-cli expr   --channel a --freq 1000 --amp 1.0 --expression 'sin(x)+sin(3*x)/3+sin(5*x)/5+sin(7*x)/7'
dds-cli config --channel b --wave triangle --freq 2000 --amp 0.5 --enable
```

---

## §8 关通道 / 查看状态 / 诊断

```bash
# 关掉 CH A（保持配置，只是不输出）
dds-cli config --channel a --wave sine --freq 1000 --amp 1.0
#                                                  （不加 --enable）

# 读当前状态（硬件 + 两通道配置 + DMA loader）
dds-cli status

# 看 DMA 加载器状态（查 error_code / received_words / mm2s 等）
dds-cli dma-status

# 读 raw 寄存器（跳过 Python 服务）
/opt/redpitaya/bin/monitor 0x40200000        # ID 应该是 0x44445332
/opt/redpitaya/bin/monitor 0x40200100        # CH A CTRL（bit0=en, bits3:1=wave_sel）
/opt/redpitaya/bin/monitor 0x40200104        # CH A FTW_LO
/opt/redpitaya/bin/monitor 0x4020000C        # STATUS
```

---

## 示波器设置提示

| 信号 | 时基建议 | V/div 建议（LV 跳线） |
|---|---|---|
| 1 kHz | 200 µs/div | 200-500 mV/div |
| 100 kHz | 2 µs/div | 200-500 mV/div |
| 10 MHz | 20 ns/div | 200-500 mV/div |
| 高斯脉冲 (1 kHz) | 20-50 µs/div | 200 mV/div |
| 心跳样 (2 Hz) | 100 ms/div | 200 mV/div |

每条 `config` / `expr` 命令发出后**立即生效**（CLI 自动 apply），不需要额外步骤。双 bank 切换保证运行中更换波形**无毛刺**。

---

## 相关文档

- [USAGE.md](USAGE.md) — 完整使用手册（概念、API、HTTP、故障排查）
- [REGISTER_MAP.md](REGISTER_MAP.md) — 寄存器完整定义
- [RED_PITAYA_INSTALL_AND_POSTMORTEM.md](RED_PITAYA_INSTALL_AND_POSTMORTEM.md) — 部署 + 已知问题
