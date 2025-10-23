```
███╗   ███╗██╗███████╗ ██████╗███████╗██████╗  ██████╗  █████╗
████╗ ████║██║██╔════╝██╔════╝██╔════╝██╔══██╗██╔════╝ ██╔══██╗
██╔████╔██║██║███████╗██║     █████╗  ██████╔╝██║  ███╗███████║
██║╚██╔╝██║██║╚════██║██║     ██╔══╝  ██╔═══╝ ██║   ██║██╔══██║
██║ ╚═╝ ██║██║███████║╚██████╗██║     ██║     ╚██████╔╝██║  ██║
╚═╝     ╚═╝╚═╝╚══════╝ ╚═════╝╚═╝     ╚═╝      ╚═════╝ ╚═╝  ╚═╝
```

Miscfpga: 目前主要是围绕 Red Pitaya 生态的小工具集，基于 Red Pitaya STEMlab 125-14 LN（Xilinx Zynq-7020）。`includes/` 下是互相独立、可单独使用的子项目：

- rpdds: 通过 FPGA 让板子输出多种波形（DDS）；支持PS输入任意函数表达式，再由PL控制输出对应波形；
- rpdaq: 双通道连续采集，主要用来测量低频偏处的（1e0-1e4 Hz）激光相对强度噪声（RIN）。

## 仓库结构

```text
miscfpga/
├── includes/
│   ├── rpdds/      # 信号发生端：DDS/任意波形 IP 核（FPGA RTL + 板上 Python 服务 + Web UI）
│   └── rpdaq/      # 采集分析端：双通道连续 ADC 采集 + RIN 噪声测量（PC 端 Python）
└── LICENSE         # MIT
```

## 子项目

### `includes/rpdds` — DDS / 任意波形发生器 IP 核

一个面向 Red Pitaya 的**双通道 DDS/NCO IP 核**，RTL 全部用 **Verilog-2001** 实现。

- 48-bit 相位累加器，亚微赫兹频率分辨率；五种波形：`sine` / `square` / `triangle` / `sawtooth` + **任意波形**（数学表达式或 16384 点原始数组）。
- 任意波形通过 **AXI-DMA** 流入双 bank LUT，实现无毛刺切换；shadow/active 寄存器保证多参数原子更新。
- 含 RTL（`rtl/`）、iverilog 测试平台（`tb/`）、板上 Python 后端 —— HTTP API / CLI / 表达式引擎（`ps/`）、浏览器 UI（`web/`，含辉光管风格频率显示）、部署脚本（`deploy/`）。
- 已在实板端到端验证（`verify.sh` 9/9 通过）。详见子目录 [README](includes/rpdds/README.md)。

### `includes/rpdaq` — 双通道连续采集 + RIN 噪声测量

PC 端工具链，把 Red Pitaya 当成**连续、零丢失的双通道采集卡**。

- 通过官方 ARM C++ 客户端 `rpsa_client` 抓取两路 14-bit ADC，decimation=128 时满速 **976.5 kSa/s/ch 实测零丢失**。
- 一条命令完成采集（`scripts/run_acquire.sh`），PC 端 Python 把 `.bin` 解码为 raw counts / 电压 CSV，并做 **RIN 互相关（cross-correlation）分析**（`src/`）。
- 采集结果按 run 落到 `runs/`（大体积原始 `.bin` 默认不入库）。详见子目录 [README](includes/rpdaq/README.md)。

## 报告（PDF）

三份报告分别记录了两端的设计验证与实测结果，是了解项目能力最快的入口：

| 报告 | 页数 | 内容 |
|---|---|---|
| [`includes/rpdds/docs/report.pdf`](includes/rpdds/docs/report.pdf) | 8 | **DDS IP 核设计 / 验证 / 硬件验证报告**（IEEE 风格）。涵盖 SoC 架构、7 级流水线、AXI-DMA 子系统、Icarus Verilog 仿真，以及示波器实测：正弦 SFDR 65–70 dBc、幅度线性度 R²=0.999995、双通道相位同步误差 < 0.008°。 |
| [`includes/rpdaq/docs/validation_report.pdf`](includes/rpdaq/docs/validation_report.pdf) | 1 | **采集管线连续性与采样率验证**。用 100 kHz AWG 正弦驱动双通道，四项正交检验（周期计数、相干性、相位跳变、边界离群）全部 PASS，证明采集零丢失、采样率稳定。 |
| [`includes/rpdaq/docs/RIN_report.pdf`](includes/rpdaq/docs/RIN_report.pdf) | 2 | **暗噪声本底与激光 RIN 实测**。十组 120–600 s 双通道采集，分解噪声本底并表征 Toptica ECDL 激光的相对强度噪声：单通道本底约 −122 dBc/Hz，互谱本底约 −138 dBc/Hz。 |

## 平台与环境

- **硬件**：Red Pitaya STEMlab 125-14 LN（Zynq-7020，双 14-bit ADC @ 125 MS/s + 双 14-bit DAC）。
- **FPGA 侧**：Vivado（综合/比特流）、Icarus Verilog（仿真）。
- **软件侧**：Python，使用 [`uv`](https://docs.astral.sh/uv/) 管理依赖。

## 许可证

[MIT](LICENSE) © 2026 0x5859
