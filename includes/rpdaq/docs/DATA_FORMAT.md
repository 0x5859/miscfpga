# 波形数据格式

本项目存储的是 Red Pitaya 官方 `rpsa_client` 的二进制输出（单文件）。文件由若干 *pack* 顺序拼接而成，每个 pack 自带头/尾。Python 解码器在 `src/rp_rin_stream/rpsa_reader.py`，已对 `convert_tool` 转出的 CSV 做过逐字节验证。

## 1. 文件命名

每次采集生成一个 run 目录：

```text
runs/20260503T173905Z_976562Sa_s/
├── config.json                   # 本次采集配置（PC 生成）
├── summary.json                  # 实际样本数 + loss 统计（PC 生成）
├── waveform.bin                  # rpsa_client 单个 .bin
├── waveform.bin.log.txt          # rpsa_client 文本日志（loss 统计、ADC speed）
└── waveform.bin.log.lost.txt     # rpsa_client per-pack 丢失计数
```

## 2. `waveform.bin` 二进制布局

整个文件 = 多个 *pack* 顺序拼接。每个 pack 结构如下：

```text
偏移        长度         内容
0           112 B        pack header（多数字段意义未公开；详见下表）
112         data_bytes   原始数据：CH1 块（int16 LE）紧跟 CH2 块
112+data    12 B         footer，固定 12 字节 0xFF
```

### 2.1 已知 header 字段（little-endian）

| 偏移 | 大小 | 名称 | 说明 |
|---|---|---|---|
| 0x00 | 1 | CH1 active | `0x02` = 启用，`0x00` = 关闭 |
| 0x01 | 1 | CH2 active | 同上 |
| 0x40 | 8 | CH1 baseRate | 等于 `Fs_per_channel`，例如 `0x000ee6b2 = 976562` |
| 0x48 | 8 | CH2 baseRate | 同上 |
| **0x68** | 4 | **data_bytes** | 本 pack 数据区字节数（关键，解析靠它）|
| 0x6C | 4 | timestamp？ | 单调递增，疑似微/纳秒 tick |

其余字段未解析（多为 0）。Parser 只用 0x68 处的 `data_bytes`。

### 2.2 数据区（block per channel）

```text
data_bytes = N_samples_per_channel × 2 bytes/sample × N_active_channels

CH1: int16 LE × N_samples_per_channel
CH2: int16 LE × N_samples_per_channel    （只有当 CH2 启用时存在）
```

注意是**块结构**而不是 sample-级 interleaved。两个通道是先 CH1 整段再 CH2 整段。

### 2.3 多 pack

每个 pack 对应 streaming-server 一次发包，常规 pack 大小 = 131,072 samples per channel = 524,288 数据字节 + 124 字节 header/footer 开销 = **524,412 字节/pack**。

`-l N` 限制下，最后一个 pack 会被截断到剩余样本数（同样的 header/footer 包装、`data_bytes` 反映截断后的大小）。

## 3. Python 读取

### 3.1 仓库内置的 reader（推荐）

```python
from rp_rin_stream.rpsa_reader import (
    read_streams,             # 一次性读全部
    iter_interleaved_blocks,  # 流式读，每 pack 一个 (n, 2) 数组
    parse_rpsa_logs,          # 解析 .log.txt + .log.lost.txt，得到 loss 报告
    total_samples_per_channel,
)
```

```python
from pathlib import Path
import json
import numpy as np
from rp_rin_stream.rpsa_reader import read_streams

run = Path("runs/20260503T173905Z_976562Sa_s")
fs = json.loads((run / "config.json").read_text())["acquisition"]["effective_sample_rate_hz_per_channel"]
ch1, ch2 = read_streams(run / "waveform.bin")        # int16 LE
t = np.arange(ch1.size) / fs
```

### 3.2 手写解码（如果想脱离本仓库读）

```python
import struct, numpy as np

with open("waveform.bin", "rb") as f:
    data = f.read()

ch1_chunks, ch2_chunks = [], []
pos = 0
while pos < len(data):
    header = data[pos:pos+112]
    db = struct.unpack_from("<I", header, 0x68)[0]    # data_bytes
    pos += 112
    raw = np.frombuffer(data[pos:pos+db], dtype="<i2")
    n = db // 4    # 2 channels × 2 bytes/sample
    ch1_chunks.append(raw[:n])
    ch2_chunks.append(raw[n:])
    pos += db
    assert data[pos:pos+12] == b"\xff" * 12          # footer
    pos += 12
ch1 = np.concatenate(ch1_chunks)
ch2 = np.concatenate(ch2_chunks)
```

## 4. MATLAB 读取示例

```matlab
fid = fopen('waveform.bin', 'rb');
ch1_all = []; ch2_all = [];
while ~feof(fid)
    header = fread(fid, 112, 'uint8');
    if isempty(header), break; end
    data_bytes = typecast(uint8(header(105:108)), 'uint32');
    raw = fread(fid, data_bytes/2, 'int16=>int16');
    n = data_bytes / 4;            % 2 channels × 2 bytes/sample
    ch1_all = [ch1_all; raw(1:n)];
    ch2_all = [ch2_all; raw(n+1:end)];
    fread(fid, 12, 'uint8');       % skip footer
end
fclose(fid);
```

## 5. JSON 文件

### 5.1 `config.json`

PC 端 launcher 生成。关键字段（converters 用到的）：

```json
{
  "schema": "redpitaya-rin-stream/config-v2-rpsa",
  "acquisition": {
    "decimation": 128,
    "effective_sample_rate_hz_per_channel": 976562.5,
    "stop_after_samples_per_channel": 58593750,
    "channels_enabled": [1, 2],
    "rpsa_client_argv": ["-s", "-h", "127.0.0.1", "-f", "bin", ...]
  },
  "board": {
    "input_range_setting": "LV",
    "input_range_volts_peak_nominal": 1.0
  },
  "redpitaya_streaming": {
    "rpsa_client_path": "/tmp/rpsa_pylib/rpsa_client",
    "streaming_server_config_sent": { "adc_decimation": "128", ... }
  }
}
```

### 5.2 `summary.json`

```json
{
  "schema": "redpitaya-rin-stream/final-summary-v2-rpsa",
  "state": "finished",                 // finished | interrupted | lossy
  "samples_received_per_channel": 58593750,
  "duration_recorded_s": 60.0,
  "loss": {
    "has_any_loss": false,
    "fpga_lost_per_channel": {"1": 0, "2": 0},
    "file_buffer_lost": 0,
    "memory_lost": 0,
    "per_pack_lost_total": 0,
    "per_pack_lost_max": 0,
    "per_pack_lost_count": 896
  }
}
```

`per_pack_lost_count` 是 `.log.lost.txt` 里的记录条数（packs 数）；`per_pack_lost_total` 是丢失样本数累计。launcher 默认 `has_any_loss == True` 就退出非零。

## 6. 数据量估算

```text
bytes_per_second = Fs_per_channel × 2 channels × 2 bytes  +  ~0.024% per-pack overhead
```

| decimation | Fs / channel | 双通道数据率 | 1 小时 BIN |
|---:|---:|---:|---:|
| 128 | 976.5 kSa/s | 3.91 MB/s | ~14.1 GB |
| 250 | 500 kSa/s | 2.00 MB/s | ~7.2 GB |
| 1024 | 122 kSa/s | 0.49 MB/s | ~1.76 GB |

## 8. 重要：decimation ≥ 16 时 FPGA 是"平均"，不是"抽取"

Red Pitaya STEMlab 125-14 的 FPGA streaming decimator 行为：

- **decimation = 1, 2, 4, 8** → 简单抽取（每 N 个原始样本取 1 个）
- **decimation ≥ 16** → 在 decimation 个 ADC 周期窗口内**累加 / 平均**后输出 1 个样本（CIC / 平均滤波）

来源：`RedPitaya-FPGA/prj/stream_app/ip/rp_oscilloscope/osc_decimator.v`

**含义**：
- 输出**采样率**仍然精确等于 `125 MHz / decimation`（例如 decimation=128 → 976,562.5 Sa/s/ch）
- 每个输出样本不是某一时刻的瞬时电压，而是 **decimation/125 MHz 长度时间窗内** ADC 读数的平均值
  - 对 decimation=128：每样本 = 128 / 125e6 ≈ **1.024 µs 窗口的平均**
- 这等效于自带一个**抗混叠低通滤波器**，截止频率约 `Fs/2`
- 对 RIN 1 Hz–10 kHz 测量：**这是好事**——窗口积分天然抑制 488 kHz 以上的高频噪声混叠
- 不影响"样本之间间隔精确 1/Fs"这个时间维度的承诺；只影响"每个样本是平均还是瞬时值"的物理解读

## 7. 转 CSV

```bash
# 时间 + 电压（推荐）
uv run --with-editable . rp-rin-bin-to-volts <run_dir> --out volts.csv --max-samples 100000

# raw int16 counts
uv run --with-editable . rp-rin-bin-to-csv <run_dir> --out raw.csv --max-samples 100000
```

CSV 列：

```text
volts: time_s, ch1_volts, ch2_volts
raw  : sample_index, time_s, ch1_raw_i16, ch2_raw_i16
```

LV 量程下电压 = `raw / 8192`，HV 下 = `raw / 8192 × 20`。
