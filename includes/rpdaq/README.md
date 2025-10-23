# Red Pitaya STEMlab 125-14 Z7020-LN 双通道连续采集项目

通过 Red Pitaya 官方 ARM C++ 客户端 `rpsa_client` 在板上抓取 `RED PITAYA STEMlab 125-14 Z7020-LN` 的两个 14-bit 快速 ADC 通道，单文件 `.bin` `scp` 回 PC，再用本仓库的 Python 工具解码成 raw counts CSV / 时间-电压 CSV。

## 快速开始

两条命令搞定：采集 + 转电压 CSV。

```bash
# 采集 60 秒 @ decimation=128 (976.5 kSa/s/ch × 2)，结果落到 runs/<时间戳>_976562Sa_s/
./scripts/run_acquire.sh -d 128 -t 60

# 把上一步生成的 .bin 转成时间 + 双通道电压 CSV
# （把目录名换成 launcher 最后一行打印的那个）
uv run --with-editable . rp-rin-bin-to-volts runs/20260503T180702Z_976562Sa_s --out volts.csv
```

跑之前先一次性装好 PC 端依赖：

```bash
# macOS
brew install uv hudochenkov/sshpass/sshpass

# Debian/Ubuntu
curl -LsSf https://astral.sh/uv/install.sh | sh   # 装 uv
sudo apt install sshpass

# 同步项目 Python 依赖
uv sync
```

如果 `run_acquire.sh` 最后一行打印 `Any sample loss: False`，数据就是连续完整的；否则脚本以非 0 退出。

**核心特性**：
- 单文件、连续、零丢失（数据完整性失败让 launcher 退出 3；基础设施失败退出 1。详见 §8）
- decimation=128 满速 976,562.5 Sa/s/ch × 2 = ~3.9 MB/s 实测可持续
- 板上不留残留：launcher 在入口和出口都 `rm -rf /tmp` 工作目录
- PC 端不需要 Red Pitaya 的官方 Python 绑定（macOS 没有此预编译版）

## 1. 设计与官方依据

- ADC 硬件：2 ch，14-bit，125 MS/s。Streaming 子系统让 FPGA decimate 后流到内核 RAM，再由 `streaming-server` 通过 TCP（或本机 loopback）送给客户端。
- 我们用 Red Pitaya 官方的 ARM C++ 客户端 `rpsa_client`（来自 `/opt/redpitaya/streaming/rpsa_client-*-rp.zip`）替代了原项目的 Python 回调路径——板上单核 ARM Cortex-A9 跑 SWIG Python 回调只能消费约 250 kSa/s/ch，而 C++ 客户端实测可达 976 kSa/s/ch 满速、零 FPGA 丢失。
- `resolution=BIT_16` 保留 14-bit ADC 信息（每样本 2 字节）。
- `adc_pass_mode=NET` 让 server 走 TCP（loopback），不写板载 SD。

## 2. 文件结构

```text
rp_rin_stream/
├── README.md
├── pyproject.toml
├── docs/
│   ├── DATA_FORMAT.md              # rpsa_client BIN 格式说明
│   ├── validation_report.pdf       # 采集连续性 / 采样率验证报告
│   └── RIN_report.pdf              # 暗噪声本底 + 激光 RIN 测量报告
├── scripts/
│   └── run_acquire.sh              # 一条命令完成整个采集流程
├── src/rp_rin_stream/
│   ├── __init__.py                 # 包入口，导出 __version__
│   ├── rpsa_reader.py              # rpsa_client BIN 解析器 + log 解析
│   ├── convert.py                  # raw counts CSV 转换 (rp-rin-bin-to-csv)
│   ├── bin_to_volts_csv.py         # 时间/电压 CSV 转换 (rp-rin-bin-to-volts)
│   └── metadata.py                 # 生成 config.json / summary.json
└── tests/
    └── smoke_rpsa_reader.py        # 离线 smoke test
```

## 3. PC 端依赖

```bash
# macOS
brew install uv hudochenkov/sshpass/sshpass

# Debian/Ubuntu
curl -LsSf https://astral.sh/uv/install.sh | sh
sudo apt install sshpass

# Python 依赖（在仓库根目录跑）
uv sync
```

需要 `uv`（Astral 的 Python 包管理器）和 `sshpass`（脚本用密码自动登录板子）。

可选先跑离线 smoke test，验证 parser + 转换器 + metadata 生成：

```bash
uv run --with-editable . python tests/smoke_rpsa_reader.py
```

## 4. 启动采集（一条命令）

```bash
./scripts/run_acquire.sh                                  # 默认 host + decimation=128，Ctrl+C 停止
./scripts/run_acquire.sh -t 60                            # 60 秒
./scripts/run_acquire.sh -d 1024 -t 600                   # 10 分钟 @ ~122 kSa/s/ch
./scripts/run_acquire.sh -r HV -t 30                      # 30 秒，HV 量程 (±20 V)
./scripts/run_acquire.sh -H 192.168.1.29 -t 60            # 用 IP 而非 mDNS
./scripts/run_acquire.sh -o my_runs -t 60                 # 输出到 my_runs/ 而非默认 runs/
./scripts/run_acquire.sh --password mypw -t 60            # 板子密码不是 root
./scripts/run_acquire.sh -d 128 -t 60 --allow-loss        # 实验：即便丢样本也接受（不推荐真实测量）
./scripts/run_acquire.sh --help                           # 全部 flag
```

全部 flag：`-H/--host`、`-d/--decimation`、`-t/--duration`、`-o/--out`、`-r/--input-range`、`--password`、`--allow-loss`、`-h/--help`。

环境变量覆盖（优先级低于 flag）：

```bash
RP_HOST=192.168.1.29 RP_PASSWORD=mypw ./scripts/run_acquire.sh -t 60
```

Launcher 8 步流程：
1. 同步 PC 时间到 RP（板子无 RTC 电池）
2. 清掉 `/tmp/rp_rin_stream` 和 `/tmp/rpsa_pylib`，重新解压 `rpsa_client`
3. 装载 FPGA `stream_app` overlay；若 `streaming-server` 未在 18901 监听则启动
4. 通过 `rpsa_client -c -i KEY=VALUE -w` 把 decimation / channel state / BIT_16 / NET / 量程 / DC 等配置发给 server
5. `rpsa_client -s -h 127.0.0.1 -f bin -m raw -l <samples>` 抓取，单文件落到板上 `/tmp`
6. `scp` 单个 `.bin` + 两个 log 文件回 PC `runs/<UTC>_<Sa/s>Sa_s/`
7. 在 PC 上跑 `rp_rin_stream.metadata` 解析 log + 走一遍 BIN 文件，生成 `config.json` + `summary.json`；如果检测到样本丢失则 **launcher 退出 3**（这一类可以用 `--allow-loss` 跳过）
8. 清掉 RP 上 `/tmp` 残留

注意：基础设施类错误（streaming-server 起不来、log 文件 scp 失败、RP `/tmp` 留下 0 个或多个 .bin、缺 zip 等）走 **exit 1** 路径，**`--allow-loss` 不能跳过**——这些是脚本完整性问题，不是数据问题。

## 5. 输出文件

每次采集生成一个独立 run 目录：

```text
runs/20260503T173905Z_976562Sa_s/
├── config.json                     # 本次采集配置 + 板上 server 接受的所有 key=value
├── summary.json                    # 实际样本数、loss 统计、log 解析结果
├── waveform.bin                    # rpsa_client 单文件 BIN（per-pack 包头/包尾，CH1+CH2 块状）
├── waveform.bin.log.txt            # rpsa_client 文本日志（ADC speed、总样本数、loss）
└── waveform.bin.log.lost.txt       # rpsa_client per-pack 丢失计数
```

详细 BIN 格式见 [docs/DATA_FORMAT.md](docs/DATA_FORMAT.md)。

## 6. BIN 转 CSV

长时间采集**不建议全量转 CSV**——CSV 体积大很多。建议只导出需要检查的小段，原始 `.bin` 留作分析脚本直接读。

### 6.1 时间 + 电压 CSV（推荐）

```bash
# 前 100k 样本（预览）
uv run --with-editable . rp-rin-bin-to-volts \
  runs/20260503T173905Z_976562Sa_s --out preview.csv --max-samples 100000

# 第 1M 样本起的 200k 样本
uv run --with-editable . rp-rin-bin-to-volts \
  runs/20260503T173905Z_976562Sa_s --out segment.csv \
  --start-sample 1000000 --max-samples 200000

# 大文件可加 .gz 自动 gzip
uv run --with-editable . rp-rin-bin-to-volts \
  runs/20260503T173905Z_976562Sa_s --out volts.csv.gz
```

输出：

```csv
time_s,ch1_volts,ch2_volts
0.0,-0.0382080078125,-0.0352783203125
1.024e-06,-0.0362548828125,-0.0382080078125
...
```

量程从 `config.json` 自动读：LV → ±1 V (LSB ≈ 122 µV)，HV → ±20 V (LSB ≈ 2.44 mV)。

### 6.2 Raw int16 counts CSV

如果想看 ADC 原始 counts 值：

```bash
uv run --with-editable . rp-rin-bin-to-csv \
  runs/20260503T173905Z_976562Sa_s --out raw.csv --max-samples 100000
```

输出：

```csv
sample_index,time_s,ch1_raw_i16,ch2_raw_i16
0,0.0,-313,-289
1,1.024e-06,-297,-313
...
```

## 7. 直接读 BIN（Python）

```python
import json
from pathlib import Path
import numpy as np
from rp_rin_stream.rpsa_reader import read_streams

run = Path("runs/20260503T180702Z_976562Sa_s")
cfg = json.loads((run / "config.json").read_text())
fs = cfg["acquisition"]["effective_sample_rate_hz_per_channel"]            # e.g. 976562.5
full_scale_v = cfg["board"]["input_range_volts_peak_nominal"]              # 1.0 (LV) or 20.0 (HV)

ch1, ch2 = read_streams(run / "waveform.bin")     # int16, 自动检测通道数
ch1_volts = ch1.astype(np.float64) * (full_scale_v / 8192.0)
ch2_volts = ch2.astype(np.float64) * (full_scale_v / 8192.0)
t = np.arange(ch1.size) / fs
```

按 pack 流式读取（不一次性塞 RAM，做 PSD/Welch 时常用）：

```python
from rp_rin_stream.rpsa_reader import iter_interleaved_blocks
for block in iter_interleaved_blocks(run / "waveform.bin"):
    # block.shape == (samples_in_pack, 2), int16
    process(block)
```

注意：CSV 里的 `time_s` 和上面的 `t` 都是 `i / Fs`（PC 端纯算术），不是板子发来的硬件时间戳。零丢失 + Fs = `125 MHz / decimation` 精确值，所以相对时间是准的；绝对时间锚点只能粗到 launcher 启动那一刻。

**关于"每个样本是什么"**：当 `decimation ≥ 16` 时，FPGA 内部对每 `decimation` 个 ADC 周期窗口内的样本做**累加 / 平均**后输出 1 个样本（不是简单"每 N 取 1"）。所以每个 ADC 样本在物理上是 `decimation/125 MHz` 长度时间窗的平均值（decimation=128 → 约 1.024 µs 的窗口积分）。这等效于自带抗混叠低通滤波器，**对 RIN 频谱测量是有利的**。详见 [docs/DATA_FORMAT.md §8](docs/DATA_FORMAT.md)。

## 8. 性能与丢失

| decimation | Fs/ch | 数据率 | 60 s wall clock | FPGA loss |
|---|---|---|---|---|
| **128** | 976.5 kSa/s | 3.9 MB/s | **~60 s** ✓（实测） | **0** ✓（实测）|
| 250 | 500 kSa/s | 2.0 MB/s | ~60 s（外推）| 0（外推）|
| 1024 | 122 kSa/s | 0.49 MB/s | ~60 s（外推）| 0（外推）|

只有 decimation=128 60 秒做过完整端到端验证；250 / 1024 比 128 慢，没有理由更差，但没有跑过实测。

Launcher 把"无损"定义得很严，按错误类型分两类退出：

**数据完整性问题 → exit 3**（写完 `summary.json` 后报错，可被 `--allow-loss` 跳过）：
1. rpsa_client 报告 `fpga_lost` / `file_buffer_lost` / `memory_lost` 任一 > 0
2. `.log.lost.txt` 里 per-pack 丢失累计 > 0
3. log 文件本身存在但解析失败（含 ADC speed 行缺失、空的 .log.lost.txt 等）
4. `.bin` 文件解析失败（如 mid-pack 截断）
5. 设了 `-t duration` 但实际样本数 < 期望（且不是 Ctrl+C 中断）
6. rpsa_client 报告收到的样本数比 `.bin` 文件多超过一个 pack 大小（异常截断）
7. `.bin` 第一个 pack 报告 0 个 active channel

**基础设施 / 脚本完整性问题 → exit 1**（`set -e` 直接中止，**`--allow-loss` 不能跳过**，因为这些不是数据问题、是脚本环境问题）：
- 板上 streaming-server 起不来
- 板上没有 `/opt/redpitaya/streaming/rpsa_client-*-rp.zip`
- `waveform.bin` 或两个 log 文件 scp 回 PC 失败
- 板上 `/tmp/rp_rin_stream/cap/` 出现 0 个或多个 `.bin`（理论上 launcher 自己保证一个，多/少都说明意外状态）
- ssh / sshpass 认证失败

`--allow-loss`**只**改变第一类的退出码（exit 3 → exit 0 + warning），仅供实验调试用，不推荐真实测量。

## 9. 输入幅度与 14-bit 利用率

软件以 `BIT_16` 形式保存，保留 14-bit ADC 信息。真正"用满 ADC 14-bit"取决于模拟前端：

- LV，`1:1`，±1 V 满量程
- HV，`1:20`，±20 V 满量程
- 让光电探测器输出接近满量程但不削顶（控制在 ±0.8 V 以内为佳）

短采集时检查 `summary.json` 的 `loss.has_any_loss` 应为 `false`。

## 10. RIN 数据建议

1 Hz–10 kHz 激光 RIN：976 kSa/s 不是必须（Nyquist 远超 10 kHz），但保留更宽裕的数字处理空间。采集后：

1. 做 dark / 电子学背景噪声测量
2. 去 DC、归一化、Welch PSD 平均
3. RIN 形式通常是 `S_v(f)/V_DC²` 或 `S_i(f)/I_DC²`，单位 `1/Hz`，再转 `dBc/Hz`
4. 1 Hz 附近需要足够长的记录；10 秒段频率分辨率是 0.1 Hz，100 秒段 0.01 Hz

详见报告 [docs/RIN_report.pdf](docs/RIN_report.pdf)。

## 11. 常见问题

### 连接不上
- 同一网段、`rp-f0d653.local` mDNS 可解析（或直接用 IP）
- `sshpass` 已装、密码正确（默认 `root`）

### `state: lossy`，launcher 返回 3
说明 rpsa_client 报告了样本丢失。本来 decimation=128 在板上是稳定满速的；如果出现 loss，先排除：
- 板子是否同时被其他高负载占用（例如 jupyter-lab、Web UI、其他 SSH session）
- 网线 / SSH 链路是否有大流量竞争（虽然 launcher 期间 scp 已经避开了）
- 试 `-d 256` 或更高 decimation 降速

### CSV 太大
正常。decimation=128 双通道 1 小时是 ≈ 14.1 GB BIN（精确：`976562.5 Sa/s × 2 ch × 2 byte × 3600 s`）；decimation=125 (1 MSa/s) 才是 14.4 GB。CSV 比 BIN 大 4–10 倍。长测请保留 BIN，用 `rpsa_reader` 直接读，不要全量转 CSV。

### Ctrl+C 中断
`ssh -tt` 把 SIGINT 转发给 `rpsa_client`；它会优雅地写完当前 buffer 并关闭文件。launcher 仍然会 scp 回部分数据，并把 `state` 标记为 `interrupted`。

## 12. ADC 输入电压量程

| 量程              | 满量程           | 绝对最大 | 14-bit LSB |
| ----------------- | ---------------- | -------- | ---------- |
| **LV**（`A_1_1`） | **±1 V (2 Vpp)** | ±6 V     | ~122 µV    |
| HV（`A_1_20`）    | ±20 V (40 Vpp)   | ±30 V    | ~2.44 mV   |

- 本项目默认 `input_range = "LV"`，**实际可测 ±1 V**。
- 留余量：信号峰值控制在 **±0.8–0.9 V** 以内。
- 输入是 **DC-coupled**：DC 电平和 AC 噪声一起进 ADC，做 RIN 时数字端再去 DC + 归一化。
- 仅当信号确实可能超 ±1 V 才切 HV——HV 下分辨率从 ~122 µV 退到 ~2.44 mV，对低噪声 RIN 很不利。
- 切 HV 用 `--input-range HV`，并**确保板上拨片/前面板硬件设置一致**（LV/HV 是软件配合硬件的，软件单独切换没用）。
