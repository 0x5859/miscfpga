# DDS AXI DMA 使用指南

面向已经把项目部署到板上（SSH 可连、`verify.sh` 9/9 绿）后，**实际使用 DDS 核**的用户。如果还没装上，先看 [RED_PITAYA_INSTALL_AND_POSTMORTEM.md](RED_PITAYA_INSTALL_AND_POSTMORTEM.md)。

> 💡 **只想复制粘贴一把梭验证各种波形？** → **[COMMANDS.md](COMMANDS.md)**（命令速查菜单：预设 / 幅度 / 频率 / DC / 自定义 / 双通道 / 诊断）。
> 本文档是完整手册（含概念、API、故障排查）；COMMANDS.md 是纯命令清单。

---

## 目录

1. [上板部署到启用（10 分钟）](#1-上板部署到启用10-分钟)
2. [核心概念](#2-核心概念)
3. [三种控制接口](#3-三种控制接口)
4. [波形模式切换](#4-波形模式切换)
5. [自定义波形](#5-自定义波形)
6. [常用操作食谱](#6-常用操作食谱)
7. [直接寄存器操作（底层）](#7-直接寄存器操作底层)
8. [Web UI](#8-web-ui)
9. [故障排查](#9-故障排查)
10. [附录：HTTP API 全集](#10-附录http-api-全集)

---

## 1. 上板部署到启用（10 分钟）

从 Windows 开发机把项目送到板卡、安装到位、启用 DDS IP 核的**完整**流程。已经装好、只想用的话可以直接跳到 [§1.4](#14-启用-dds-ip-核)。

### 1.1 本地项目布局（哪些东西要上板）

开发机上的主工作目录：`D:\projects\fpgakit_parallel\dds_allinone\dds_axi_dma\`

只有这 4 个子目录 / 文件需要上板（其它 rtl/、tb/、vivado/、docs/ 都是本地用的，rtl 已经编译进 bitstream 了）：

```text
dds_axi_dma/
├── deploy/                            # 部署脚本
│   ├── install.sh                     # 一把梭安装脚本
│   ├── install_split.sh.example       # 分阶段安装（推荐，见 §1.2）
│   ├── load_overlay.sh.example        # PL 加载器（含 overlay.sh quirk 规避）
│   ├── verify.sh.example              # 9 项 sanity check
│   ├── startup.sh.example             # 手动 launch 辅助
│   ├── dds_dma_reserved_mem.dts.template
│   └── nginx.conf                     # 仅参考；install.sh 会自动生成
├── build/                             # Vivado 产物
│   ├── fpga.bit.bin                   # 4 MB bitstream（fpgautil 用）
│   └── fpga.dtbo                      # 688 B 设备树 overlay
├── ps/                                # Python 后端（上板后放到 backend/）
│   ├── dds_service.py                 # HTTP 服务入口，听 127.0.0.1:18888
│   ├── dds_hw.py                      # mmap 寄存器 + DMA 封装
│   ├── dds_regs.py                    # 寄存器地址常量
│   ├── dds_cli.py                     # 命令行工具
│   ├── expr_engine.py                 # 自定义波形表达式引擎
│   └── axi_dma_mm2s.py                # AXI DMA MM2S 封装
└── web/                               # Web UI
    ├── index.html
    ├── app.js
    └── glow-tube-display.js
```

tarball 打这些就够了，约 430 KB。

### 1.2 打包 → 上传 → 安装

**在开发机 Git-Bash 里**：

```bash
# 0. 切到工作目录
cd D:/projects/fpgakit_parallel/dds_allinone/dds_axi_dma

# 1. 行尾归一化（Windows git 常把脚本 checkout 成 CRLF，板上 bash 会报
#    `set: pipefail: invalid option name`）
for f in deploy/*.sh deploy/*.example ps/*.py; do sed -i 's/\r$//' "$f"; done

# 2. 打最小必需 tarball
tar -czf /tmp/dds_deploy.tar.gz deploy build/fpga.bit.bin build/fpga.dtbo ps web

# 3. pscp 上传（hostkey 每次 SD 重刷都会变；取新 hostkey 的方法见
#    RED_PITAYA_INSTALL_AND_POSTMORTEM.md）
HOSTKEY="ssh-ed25519 256 SHA256:a1jdRmmsuFS6b5eMLFeKAQsE8dYu9AUxS4iqwmYhuJc"
/d/software/PuTTY/pscp -batch -hostkey "$HOSTKEY" -pw root \
    /tmp/dds_deploy.tar.gz root@rp-f0d653.local:/root/dds_deploy.tar.gz
```

**安装有两条路径，二选一。** 推荐方式 B（稳）。

**方式 A — 一把梭**（快；已知在本板上可能在 [5/8] 附近硬重启，见 [POSTMORTEM.md Part C](RED_PITAYA_INSTALL_AND_POSTMORTEM.md)）：

```bash
/d/software/PuTTY/plink -batch -ssh -hostkey "$HOSTKEY" -pw root root@rp-f0d653.local \
    "rm -rf /root/dds_deploy_stage; mkdir /root/dds_deploy_stage; \
     cd /root/dds_deploy_stage && tar -xzf /root/dds_deploy.tar.gz && \
     bash deploy/install.sh"
```

**方式 B — 三阶段分开**（每阶段一个独立 plink 调用，各自幂等，崩了继续下一阶段即可）：

```bash
plink_run() {
    /d/software/PuTTY/plink -batch -ssh -hostkey "$HOSTKEY" -pw root root@rp-f0d653.local "$@"
}

# 解包
plink_run "rm -rf /root/dds_deploy_stage && mkdir /root/dds_deploy_stage && \
           cd /root/dds_deploy_stage && tar -xzf /root/dds_deploy.tar.gz"

# 阶段 1：拷贝前端 / 后端 / 辅助文件
plink_run "cd /root/dds_deploy_stage && bash deploy/install_split.sh.example files"

# 阶段 2：写 + 启用 systemd 服务单元
plink_run "cd /root/dds_deploy_stage && bash deploy/install_split.sh.example systemd"

# 阶段 3：拷 bitstream + dtbo + 加载 PL + 启动服务
plink_run "cd /root/dds_deploy_stage && bash deploy/install_split.sh.example fpga"
```

跑完 `fpga` 阶段后 DDS 已经在跑，可以直接用（`verify.sh`、`dds-cli`、`curl`）。

install.sh / install_split.sh 的权衡详情：[POSTMORTEM.md Part E.3](RED_PITAYA_INSTALL_AND_POSTMORTEM.md)。

### 1.3 上板后文件都去哪了

install（无论 A 还是 B）跑完后，板上的布局：

| 类型 | 板上路径 | 来自本地 |
|---|---|---|
| PL bitstream | `/opt/redpitaya/fpga/z20_125/dds_axi_dma_workbench/fpga.bit.bin` | `build/fpga.bit.bin` |
| 设备树 overlay | `/opt/redpitaya/fpga/z20_125/dds_axi_dma_workbench/fpga.dtbo` | `build/fpga.dtbo` |
| PL 加载器 | `/opt/redpitaya/fpga/z20_125/dds_axi_dma_workbench/load_overlay.sh` | `deploy/load_overlay.sh.example` |
| 校验脚本 | `/opt/redpitaya/fpga/z20_125/dds_axi_dma_workbench/verify.sh` | `deploy/verify.sh.example` |
| DTS 模板 | `/opt/redpitaya/fpga/z20_125/dds_axi_dma_workbench/dds_dma_reserved_mem.dts.template` | `deploy/dds_dma_reserved_mem.dts.template` |
| Python backend | `/opt/redpitaya/www/apps/dds_axi_dma_workbench/backend/*.py` | `ps/*.py` |
| Web 前端 | `/opt/redpitaya/www/apps/dds_axi_dma_workbench/{index.html,app.js,glow-tube-display.js}` | `web/*` |
| nginx 代理片段 | `/opt/redpitaya/www/apps/dds_axi_dma_workbench/nginx.conf` | install.sh 自动生成 |
| systemd 服务单元 | `/etc/systemd/system/rp-dds-axi-dma.service` | install.sh heredoc 生成 |
| 启动时 symlink | `/etc/systemd/system/multi-user.target.wants/rp-dds-axi-dma.service` | `systemctl enable` 创建 |

命名约定：

- `z20_125` 来自 `/opt/redpitaya/bin/monitor -f`，对应板卡型号；如果你换了硬件变型，路径里的型号字段要跟着变。
- `dds_axi_dma_workbench` 是 install.sh 里的 `APP_NAME`，默认值。可以改环境变量 `APP_NAME=xxx bash deploy/install.sh` 部署成别的名字——但后面所有路径也要跟着换。

**注意**：`/opt/redpitaya/*` 在板上是 **vfat（`/dev/mmcblk0p1`，和 `/boot` 共享分区）**。这是脆弱的文件系统，绝对不要在上面用 `mv` 跨目录或 `mktemp + mv` 之类的模式，install.sh 已经规避过。背景见 [POSTMORTEM.md Part C](RED_PITAYA_INSTALL_AND_POSTMORTEM.md)。

### 1.4 启用 DDS IP 核

"启用"的意思：把 `fpga.bit.bin` 写到 PL + 把 `fpga.dtbo` 注入 configfs + 启动 Python backend。

**首次 install 完就已经启用了**——install.sh / install_split.sh 的最后一步会自动做，你直接用即可。

**板卡重启（断电 / `systemctl reboot`）后 DDS 不会自动恢复**，因为 boot-time 自动加载路径在本板上不稳，我们刻意弃用了（见 [POSTMORTEM.md Part E](RED_PITAYA_INSTALL_AND_POSTMORTEM.md)）。手动恢复三步走：

```bash
ssh root@rp-f0d653.local

# 1. 加载 PL（防御性，遇到 overlay.sh cache-skip 会自动 fallback 到 fpgautil -b）
/opt/redpitaya/fpga/z20_125/dds_axi_dma_workbench/load_overlay.sh

# 2. 重启 backend 让它重新 mmap 到新的 PL 寄存器
systemctl restart rp-dds-axi-dma.service

# 3. 验证：9 项全绿说明 DDS 可用
/opt/redpitaya/fpga/z20_125/dds_axi_dma_workbench/verify.sh
```

预期 `verify.sh` 输出：

```
=== fpga_manager state ===
  [ OK ]  /sys/class/fpga_manager/fpga0/state = operating
=== DDS registers ===
  [ OK ]  ID             @ 0x40200000 = 0x44445332
  [ OK ]  VERSION        @ 0x40200004 = 0x00010000
  [ OK ]  SAMPLE_RATE    @ 0x40200010 = 0x07735940
  [ OK ]  LUT_LENGTH     @ 0x40200014 = 0x00004000
  [ OK ]  FEATURES       @ 0x40200018 = 0x00000007
  [ OK ]  CHA_AMP        @ 0x40200114 = 0x00007fff
=== Backend service ===
  [ OK ]  rp-dds-axi-dma.service is active
=== HTTP /api/status ===
  [ OK ]  http://127.0.0.1:18888/api/status -> 200
Summary: 9 passed, 0 failed
```

`load_overlay.sh` 内部做了什么（完整逻辑见 [deploy/load_overlay.sh.example](../deploy/load_overlay.sh.example)）：

1. `rmdir /sys/kernel/config/device-tree/overlays/Full`——清掉 fpga_manager 对上一次 overlay 的缓存（否则它会误判"已加载"而跳过实际编程）
2. 调 `/opt/redpitaya/sbin/overlay.sh dds_axi_dma_workbench`——内部是 `fpgautil -b ... -o ... -n Full`
3. 读 `0x40200000`，看到 `0x44445332`（ASCII "DDS2" 魔数）→ 成功退出
4. 否则 fallback：再清 Full → 直接 `fpgautil -b fpga.bit.bin` 编程 PL（耗时应 ~73 ms 而非跳过时的 ~5 ms）→ 手动 `cat fpga.dtbo > /sys/kernel/config/device-tree/overlays/Full/dtbo` → 再校验 ID
5. 还不匹配就 `exit 1` 并 dump 诊断（fpga_manager state、configfs 目录、`/tmp/update_fpga.txt` 尾部）

### 1.5 验证：起一个 1 kHz 正弦

```bash
# 用示波器接 Red Pitaya 的 OUT1 SMA
python3 /opt/redpitaya/www/apps/dds_axi_dma_workbench/backend/dds_cli.py config \
    --channel a --wave sine --freq 1000 --amp 1.0 --enable
```

看到 1 kHz 满幅正弦就说明从本地到板、从硬件到软件的链路都通了。

### 1.6 建议的 shell alias（强烈推荐加上）

每次打全路径很烦。在板上 `~/.bashrc` 末尾加：

```bash
alias dds-cli='python3 /opt/redpitaya/www/apps/dds_axi_dma_workbench/backend/dds_cli.py'
alias dds-up='/opt/redpitaya/fpga/z20_125/dds_axi_dma_workbench/load_overlay.sh && systemctl restart rp-dds-axi-dma.service'
alias dds-verify='/opt/redpitaya/fpga/z20_125/dds_axi_dma_workbench/verify.sh'
alias dds-monitor='/opt/redpitaya/bin/monitor'
```

然后 `source ~/.bashrc`。之后本文档里所有 `python3 /opt/.../dds_cli.py ...` 都可以写成 `dds-cli ...`，`monitor 0x40200...` 写成 `dds-monitor 0x40200...`，而且**每次重启后一句 `dds-up` 就启用了**。

> **小贴士**：这些 alias 的定义也可以加进 install.sh 的 `~/.bashrc` 注入——不过那会改用户的 shell 配置，install.sh 保守起见没做。你想自动化就加一行 `echo "alias dds-cli=..." >> ~/.bashrc`。

---

## 2. 核心概念

### 2.1 硬件结构

- **两个独立通道**：CH A（对应物理 **OUT1**）和 CH B（对应 **OUT2**）
- **DAC 采样率**：125 MHz（固定）→ 理论最大基频 ~62.5 MHz
- **每通道参数**：enable / wave / frequency / phase / amplitude / DC offset / arb bank
- **每通道两个 LUT bank**（bank0、bank1，各 16384×14bit）：用来双缓冲任意波形，无毛刺切换

### 2.2 影子寄存器（shadow）/ 活动寄存器（active）的两阶段更新

写配置寄存器时，值先进 **shadow**。真正影响输出的是 **active**。只有显式 **apply**（写 `REG_CONTROL` bit0）时才把 shadow 拷到 active。这样你可以连续写一堆参数，然后一次性切换——避免中间过程产生奇怪的波形。

> **CLI / HTTP 自动帮你 apply。** 直接操作寄存器时自己要记得最后写 `REG_CONTROL = 0x1`。

### 2.3 数值编码速查

| 物理量 | 硬件格式 | 软件层输入 | 举例 |
|---|---|---|---|
| 频率 | 48-bit FTW | `float freq_hz` | 1000.0 Hz → FTW = 1000 × 2⁴⁸ / 125e6 ≈ 0x8637BD06 |
| 相位 | 48-bit phase word | `float phase_deg` (0~360) | 90° → 0x4000_0000_0000 |
| 幅度 | 带符号 Q1.15 | `float amplitude` (-1.0~+1.0) | 1.0 → 0x7FFF |
| DC 偏置 | 带符号 14-bit | `float dc_offset` (-1.0~+1.0) | 0.5 → 0x0FFF |
| 波形 | wave_sel (3-bit) | `str wave_name` | `"sine"` → 0 |

你用 CLI/HTTP 时这些转换都是自动的，不必手算。只有直接戳寄存器才需要对着 [REGISTER_MAP.md](REGISTER_MAP.md) 看。

### 2.4 `wave_name` 的 5 种取值

| 值 | 说明 |
|---|---|
| `sine` | 4096 点 sin 查找表做插值，最干净 |
| `square` | ±满幅方波（50% duty） |
| `triangle` | 线性升降三角波 |
| `saw` / `sawtooth` | 锯齿（线性升到+1，跳到-1） |
| `arb` / `arbitrary` | 使用当前通道 `arb_bank` 指向的 LUT bank（见 §5） |

---

## 3. 三种控制接口

| 接口 | 优点 | 限制 | 适合 |
|---|---|---|---|
| **CLI**（`dds-cli`） | 一条命令即可；最直白 | 要 SSH 到板；表达式单次限命令行一行 | 人工调试、手动实验 |
| **HTTP API** | 任何语言、任何机器可调；支持大 payload（大 LUT） | 要 nginx 在跑（或本机 loopback :18888） | 远程脚本、上位机 GUI、批量数据写 |
| **raw 寄存器**（`monitor`） | 不依赖 Python 服务；板子全部其他东西都挂了也能戳 | 手算 FTW / 手动 apply；LUT 写入只有"调试窗口"模式（慢） | 故障排查、极少数绕过上位机的场合 |

### 3.1 CLI：`dds-cli` 命令大全

（示例都假设已经设了 `dds-cli` alias；没设就替换成完整 `python3 /opt/.../dds_cli.py`）

```bash
# 读 status（硬件 + 两通道当前配置 + DMA loader 状态）
dds-cli status

# 只看 DMA loader 状态
dds-cli dma-status

# 写通道配置
dds-cli config --channel {a|b} \
    --wave {sine|square|triangle|saw|arb} \
    --freq <Hz> \
    [--phase <deg>] \
    [--amp <Q1.15, 默认 1.0>] \
    [--dc <-1.0~+1.0>] \
    [--enable] \
    [--arb-bank {0|1}] \
    [--clear-phase]

# 用数学表达式生成 LUT 并加载
dds-cli expr --channel {a|b} \
    --expression "<表达式>" \
    --freq <Hz> \
    [--phase <deg>] \
    [--amp <Q1.15, 默认 1.0>] \
    [--dc <-1.0~+1.0>] \
    [--bank {0|1}] \
    [--no-clear-phase]
```

所有子命令都会在最后打印 JSON 格式的状态回执，方便你眼看或 `jq` 解析。

### 3.2 HTTP API：三条最常用

Python 服务听在 `127.0.0.1:18888`，nginx 代理到 `http://<板IP>/dds_axi_dma_workbench/api/...`（路径等价）。

```bash
# 本机（板上）用 loopback 即可
BASE=http://127.0.0.1:18888/api

# 从其他机器，前提 redpitaya_nginx 跑着
BASE=http://rp-f0d653.local/dds_axi_dma_workbench/api

# 读状态
curl -s ${BASE}/status | python3 -m json.tool

# 写通道配置
curl -s -X POST ${BASE}/channel/a/config \
  -H 'Content-Type: application/json' \
  -d '{"enabled":true,"wave_name":"sine","freq_hz":1000,"amplitude":1.0}'

# 应用已写的 shadow（如果你只改单字段但不想跑 /config）
curl -s -X POST ${BASE}/apply -H 'Content-Type: application/json' -d '{"clear_phase":false}'
```

完整端点见 §10。

### 3.3 raw 寄存器：`monitor`

```bash
# 读
/opt/redpitaya/bin/monitor 0x40200000       # ID，应该是 0x44445332
/opt/redpitaya/bin/monitor 0x40200100       # CH A CTRL

# 写
/opt/redpitaya/bin/monitor 0x40200100 0x1   # CH A 使能正弦
/opt/redpitaya/bin/monitor 0x40200008 0x1   # 写 REG_CONTROL 触发 apply
```

偏移表：[§7](#7-直接寄存器操作底层) 和 [REGISTER_MAP.md](REGISTER_MAP.md)。

---

## 4. 波形模式切换

### 4.1 内置 4 种 + 任意波

#### 正弦（最常用）

```bash
dds-cli config --channel a --wave sine --freq 1000 --amp 1.0 --enable
```

#### 方波

```bash
# 1 kHz，满幅方波
dds-cli config --channel a --wave square --freq 1000 --amp 1.0 --enable

# 50% 幅度方波 + 0.3 DC 偏置（即在 -0.2 和 +0.8 之间跳）
dds-cli config --channel a --wave square --freq 1000 --amp 0.5 --dc 0.3 --enable
```

#### 三角波

```bash
dds-cli config --channel a --wave triangle --freq 5000 --amp 0.7 --enable
```

#### 锯齿

```bash
dds-cli config --channel a --wave saw --freq 2000 --amp 1.0 --enable
```

#### 任意波（先参见 §5 往 LUT 里灌样点）

```bash
dds-cli config --channel a --wave arb --freq 1000 --amp 1.0 --enable --arb-bank 0
```

### 4.2 常改的参数

- **改频率**：再跑一次 `config`，传新 `--freq`。其他参数要保留就也一并传进来（CLI 不会自动记忆）
- **改相位**：`--phase <deg>`
- **改幅度**：`--amp <-1.0~+1.0>`（负值就是反相）
- **改 DC**：`--dc <-1.0~+1.0>`
- **关闭通道**：不加 `--enable`（CLI 会写 enable=0）

### 4.3 相位清零（两通道同步）

```bash
dds-cli config --channel a --wave sine --freq 1000 --amp 1.0 --enable --clear-phase
dds-cli config --channel b --wave sine --freq 1000 --amp 1.0 --enable --clear-phase
```

`--clear-phase` 在 apply 那一瞬间把两边相位累加器都清零，用来实现通道间锁相。

---

## 5. 自定义波形

把 **16384 点、每点 14-bit 有符号整数**（范围 -8192 ~ +8191）灌进某通道的某个 LUT bank。两条路径：

### 5.1 数学表达式路径（推荐，方便）

写一个关于 `x` 或 `t` 的表达式，服务端 sample 成 16384 点、自动幅值归一、通过 AXI DMA 烧进 PL。

- `x`：从 `0` 扫到 `2*pi`（一个周期）
- `t`：从 `0` 扫到 `1`（同周期的归一化时间）
- 常量：`pi`、`e`
- 函数：`sin cos tan asin acos atan sinh cosh tanh exp log log10 sqrt abs floor ceil`
- 运算：`+ - * / ** % ^`（`^` 自动转 `**`）

**CLI**：

```bash
# 方波的傅里叶级数展开（前 3 项）
dds-cli expr --channel a --freq 1000 --amp 1.0 \
    --expression "sin(x) + sin(3*x)/3 + sin(5*x)/5"

# 高斯脉冲（周期性）
dds-cli expr --channel a --freq 500 \
    --expression "exp(-((x-pi)^2)/0.1)"

# 啁啾（线性扫频）的一个周期快照
dds-cli expr --channel b --freq 100 \
    --expression "sin(2*pi*(1 + 10*t)*t*pi)"

# 陡升脉冲（用 tanh 逼近方波）
dds-cli expr --channel a --freq 2000 --amp 1.0 \
    --expression "tanh(10*sin(x))"

# 指数衰减正弦
dds-cli expr --channel a --freq 1000 \
    --expression "exp(-3*t) * sin(5*x)"

# 绝对值做成的"馒头波"
dds-cli expr --channel a --freq 1000 \
    --expression "abs(sin(x))"
```

每次 `expr` 调用都会：
1. 在板上用 Python sample 16384 点
2. 幅值归一到 [-1, 1]（再乘你传入的 `--amp`）
3. 通过 AXI DMA MM2S 把样点流进 PL 的 `rp_dds_dma_axis_sink` 模块
4. sink 把样点写到指定通道的**不活动**的那个 LUT bank
5. apply → 通道配置切换到 `wave_name="arb"` + 切到刚写好的 bank

**HTTP 等价**：

```bash
curl -s -X POST http://127.0.0.1:18888/api/channel/a/expression \
  -H 'Content-Type: application/json' \
  -d '{"expression":"sin(x)+sin(3*x)/3","freq_hz":1000,"amplitude":1.0,"enabled":true}' \
  | python3 -m json.tool
```

### 5.2 直接喂样点数组（适合离线生成）

你在 MATLAB/Python/C 里生成好 16384 个 int14，想直接灌进去：

**Python（在板上或任意机器）**：

```python
import json, math, urllib.request

N = 16384

# 自定义样点：心跳样波形（一个真正的周期）
def heartbeat(i):
    t = i / N
    # P 波 + QRS + T 波的简化形状
    qrs = 0.9 * math.exp(-((t-0.4)**2)*200)
    t_wave = 0.3 * math.exp(-((t-0.65)**2)*50)
    p_wave = 0.2 * math.exp(-((t-0.1)**2)*100)
    return p_wave + qrs + t_wave - 0.2

samples = [int(8000 * heartbeat(i)) for i in range(N)]  # 范围 -8192..+8191，会自动 clip

req = urllib.request.Request(
    "http://127.0.0.1:18888/api/channel/a/lut",
    data=json.dumps({
        "samples": samples,
        "freq_hz": 1.0,          # 心跳 1 Hz
        "amplitude": 1.0,
        "enabled": True,
    }).encode(),
    headers={"Content-Type": "application/json"},
)
print(urllib.request.urlopen(req).read().decode())
```

约束：
- `samples` 必须**正好** 16384 个 int
- 每点范围 -8192 ~ +8191；超出会 clip 到边界
- JSON payload 大约 200 KB，确保 nginx `client_max_body_size` 够（默认一般 1 MB 没问题）

CLI 目前没有封装这条（因为命令行不适合传 16K 数字），HTTP 是唯一入口。

### 5.3 双 bank 无缝切换（运行中更新）

每个通道两个 LUT bank：bank0、bank1。`REG_STATUS` 的 `cha_active_bank` / `chb_active_bank` 告诉你当前活动那一个。`dds-cli expr` 和 `/api/.../expression` **默认** target 写到不活动那侧，apply 后切过去——这样运行中切换波形**不产生不连续点**。

想显式指定目标 bank：

```bash
dds-cli expr --channel a --freq 1000 --bank 1 --expression "sin(x)"
```

或 HTTP `"target_bank": 1`。一般不需要手动指定，除非你想预先把两个 bank 都填好、然后只通过写 `REG_CHA_ARB_BANK` + apply 来快速切换。

---

## 6. 常用操作食谱

### 6.1 两通道独立出不同波形

```bash
dds-cli config --channel a --wave sine --freq 1000 --amp 1.0 --enable
dds-cli config --channel b --wave saw  --freq 2000 --amp 0.8 --enable
```

### 6.2 关掉某通道（保留配置）

```bash
dds-cli config --channel a --wave sine --freq 1000 --amp 1.0   # 不加 --enable
```

### 6.3 双通道同频、相位差 90° 同步

```bash
dds-cli config --channel a --wave sine --freq 1000 --amp 1.0 --enable --clear-phase
dds-cli config --channel b --wave sine --freq 1000 --amp 1.0 --phase 90 --enable --clear-phase
```

### 6.4 在线改频不改波形

只传你要改的字段，HTTP 最直观：

```bash
curl -s -X POST http://127.0.0.1:18888/api/channel/a/config \
  -H 'Content-Type: application/json' \
  -d '{"freq_hz":10000}'      # 其他参数服务端会保留当前值
```

CLI 对应写法需要把当前值都再传一遍（CLI 不做读-改-写）：

```bash
dds-cli config --channel a --wave sine --freq 10000 --amp 1.0 --enable
```

### 6.5 用 DC 偏置做"直流发生器"

```bash
# CH A 固定输出 +0.5 V（相对 DAC 满量程）
dds-cli config --channel a --wave sine --freq 0 --amp 0.0 --dc 0.5 --enable
```

幅度 0 → AC 部分哑掉；DC 直接通到 DAC。

### 6.6 扫频（软件循环实现）

```bash
for f in 100 200 500 1000 2000 5000 10000; do
  dds-cli config --channel a --wave sine --freq $f --amp 1.0 --enable
  sleep 1
done
```

真正高速扫频（单指令级、ns 量级）要改 RTL。当前 core 没有硬件扫频 FSM。

### 6.7 一次写好两个 bank，再用寄存器瞬切

```bash
# 预先把 bank0 填成方波傅里叶、bank1 填成高斯脉冲
dds-cli expr --channel a --freq 1000 --bank 0 --expression "sin(x)+sin(3*x)/3+sin(5*x)/5"
dds-cli expr --channel a --freq 1000 --bank 1 --expression "exp(-((x-pi)^2)/0.1)"

# 之后快速切（在 <<1 µs 完成 bank 切换）
dds-monitor 0x4020011C 0                   # CH A arb_bank shadow = 0
dds-monitor 0x40200008 0x1                 # apply
# ... 想切到另一个 ...
dds-monitor 0x4020011C 1
dds-monitor 0x40200008 0x1
```

### 6.8 脚本化：定时切换波形

```bash
#!/bin/bash
# waveform_demo.sh
while true; do
  dds-cli expr --channel a --freq 1000 --expression "sin(x)"
  sleep 3
  dds-cli expr --channel a --freq 1000 --expression "sin(x)+sin(3*x)/3"
  sleep 3
  dds-cli expr --channel a --freq 1000 --expression "exp(-((x-pi)^2)/0.1)"
  sleep 3
done
```

---

## 7. 直接寄存器操作（底层）

### 7.1 寄存器速查（CH A 简写，CH B 把 `0x0010x` 换成 `0x0020x`）

| 地址 | 名称 | 写法举例 |
|---|---|---|
| `0x40200000` | REG_ID（R） | 读到 `0x44445332` 证明 DDS 在跑 |
| `0x4020000C` | REG_STATUS（R） | bit8=CHA_EN、bit9=CHB_EN、bit6=CHA活动bank、bit1=cfg_done |
| `0x40200008` | REG_CONTROL（W） | `0x1` = apply、`0x2` = phase_clear |
| `0x40200100` | REG_CHA_CTRL（W/R） | bit0=enable、bits[3:1]=wave_sel（0=sine,1=square,2=tri,3=saw,4=arb） |
| `0x40200104` | REG_CHA_FTW_LO | FTW 低 32 位 |
| `0x40200108` | REG_CHA_FTW_HI | FTW 高 16 位（bit15:0） |
| `0x4020010C` | REG_CHA_PHASE_LO | 相位字低 32 位 |
| `0x40200110` | REG_CHA_PHASE_HI | 相位字高 16 位 |
| `0x40200114` | REG_CHA_AMP | 幅度 Q1.15，满幅 = `0x7FFF`，半幅 = `0x4000`，反相满幅 = `0x8000` |
| `0x40200118` | REG_CHA_DC | DC 偏置 14-bit signed |
| `0x4020011C` | REG_CHA_ARB_BANK | 0 或 1 |

### 7.2 从零敲寄存器起一个 CH A 1 kHz 正弦

```bash
# FTW = 1000 * 2^48 / 125e6 = 2251799813.685 ≈ 0x8637BD06（48-bit，高 16 位是 0）
dds-monitor 0x40200104 0x8637BD06       # FTW_LO
dds-monitor 0x40200108 0x00000000       # FTW_HI
dds-monitor 0x4020010C 0x00000000       # PHASE_LO
dds-monitor 0x40200110 0x00000000       # PHASE_HI
dds-monitor 0x40200114 0x00007FFF       # AMP = 满幅
dds-monitor 0x40200118 0x00000000       # DC = 0
dds-monitor 0x4020011C 0x00000000       # arb_bank 不管（不是 arb 模式）
dds-monitor 0x40200100 0x00000001       # CTRL: enable=1, wave=sine(0)
dds-monitor 0x40200008 0x00000001       # CONTROL: apply
dds-monitor 0x4020000C                  # 读 STATUS 应该看到 bit1(cfg_done)+bit8(CHA_EN) = 0x102
```

### 7.3 DMA 加载调试（一般用不到）

DMA 加载涉及 `REG_DMA_CONTROL`、`REG_DMA_TARGET`、`REG_DMA_EXPECTED_WORDS`、AXI DMA 控制器（0x80400000）本身。手戳太繁琐，建议用 `dds-cli expr` / HTTP。想 debug 失败：

```bash
dds-cli dma-status
```

会打印 `armed / busy / done / error / received_words / error_name` 等。错误码含义：

| error_code | name |
|---|---|
| 0 | none |
| 1 | expected_words_zero |
| 2 | early_tlast |
| 3 | missing_tlast |
| 4 | overrun |
| 5 | abort |

---

## 8. Web UI

浏览器访问 `http://rp-f0d653.local/dds_axi_dma_workbench/index.html`（或换成板卡 IP）能看到一个交互式前端（glow-tube 风格的示波显示 + 配置面板）。

**前置条件**：`systemctl is-active redpitaya_nginx` 返回 `active`。如果没跑，`systemctl start redpitaya_nginx` —— 注意这个命令在本板上会触发 30 秒左右的板级重启流程（参见 [RED_PITAYA_INSTALL_AND_POSTMORTEM.md](RED_PITAYA_INSTALL_AND_POSTMORTEM.md) Part C.4）；等就是了。

Web UI 调用的是同一套 HTTP API，功能等价于 CLI。

---

## 9. 故障排查

### 9.1 `dds-cli status` 报错 / 返回的 `id` 不是 `1145328434`（0x44445332）

DDS bitstream 没在 PL 上。跑：

```bash
/opt/redpitaya/fpga/z20_125/dds_axi_dma_workbench/load_overlay.sh
systemctl restart rp-dds-axi-dma.service
```

还是不行就跑 `verify.sh` 看哪项挂了。

### 9.2 HTTP 调用 `curl: connection refused` on 18888

Python 服务没跑：

```bash
systemctl status rp-dds-axi-dma.service
systemctl restart rp-dds-axi-dma.service
journalctl -u rp-dds-axi-dma.service --no-pager -n 30       # 看错误
```

### 9.3 `expr` / `/lut` 返回 `dma_loader` error

- `expected_words_zero`：传进来的样点数是 0，检查 samples 数组
- `early_tlast` / `missing_tlast` / `overrun`：AXI DMA 协议问题，基本不应出现；出了就是内存对齐或 DMA 驱动异常
- `abort`：有人中途调了 `abort`

先 `dds-cli dma-status` 看一眼当前 loader 状态，再 `dds-cli config --channel a --wave sine --freq 1000 --enable`（任何一个非 `expr` 的 config 会顺便复位）。

### 9.4 设了参数但输出没变

- 可能没 apply：CLI 和 `/api/channel/.../config`、`/api/.../expression`、`/api/.../lut` 都**自动**帮你 apply。只有直接戳寄存器时要手动 `monitor 0x40200008 0x1`。
- 检查 `REG_STATUS` bit1（`cfg_done`）：应该是 1
- 检查你改的那个通道的 enable 位：`REG_CHA_CTRL` bit0 应该是 1

### 9.5 两个通道的相位对不齐

Red Pitaya DAC 时钟是共享的，相位累加器是**每通道独立**的。要同步必须用 `--clear-phase`（apply 时 pulse 一下 phase_clear，两通道一起清零）。

### 9.6 示波器看波形有毛刺

- 可能 `arb_bank` 切换时机不对（不应该，硬件保证 apply 原子切）
- 可能 DAC 输出有量化噪声（Red Pitaya 标准 14-bit，本来就有 ~80 dBc SFDR）
- 可能是 DAC 耦合：OUT1 / OUT2 是 AC 还是 DC 耦合看你的 LV/HV 跳线

### 9.7 板子"挂了" / SSH 不通

先看 [RED_PITAYA_INSTALL_AND_POSTMORTEM.md](RED_PITAYA_INSTALL_AND_POSTMORTEM.md) —— 这个板在重启流程上有已知问题。等 60 s 以上再试 `ping rp-f0d653.local`。

---

## 10. 附录：HTTP API 全集

基础 URL：`http://127.0.0.1:18888/api`（本机 loopback）或 `http://<板IP>/dds_axi_dma_workbench/api`（经 nginx）

所有请求/响应都是 JSON，`Content-Type: application/json`。

### GET `/status`

无参。返回完整快照：

```json
{
  "hardware": {
    "id": 1145328434, "version": 65536, "status": 258,
    "sample_rate_hz": 125000000, "lut_length": 16384, "features": 7,
    "cfg_busy": false, "cfg_done": true,
    "dma_armed": false, "dma_busy": false, "dma_done": false, "dma_error": false,
    "active_bank_a": 0, "active_bank_b": 0,
    "channel_a_enabled": true, "channel_b_enabled": false
  },
  "channels": {
    "a": {"enabled": true, "wave_name": "sine", "freq_hz": 1000.0,
          "phase_deg": 0.0, "amplitude": 1.0, "dc_offset": 0.0, "arb_bank": 0},
    "b": {...}
  },
  "dma_loader": {"armed": false, "busy": false, "done": false, "error": false,
                 "target_channel": "a", "target_bank": 0,
                 "expected_words": 0, "received_words": 0,
                 "error_code": 0, "error_name": "none",
                 "mm2s": {...}},
  "metadata": {...}
}
```

### POST `/apply`

手动触发 apply。

```json
{"clear_phase": false}        // 可选，默认 false
```

### POST `/channel/{a|b}/config`

写通道配置（只传要改的字段，未提供的用当前值）：

```json
{
  "enabled": true,              // 可选
  "wave_name": "sine",          // 可选："sine"/"square"/"triangle"/"saw"/"arb"
  "freq_hz": 1000.0,            // 可选，Hz
  "phase_deg": 0.0,             // 可选，度
  "amplitude": 1.0,             // 可选，-1.0 ~ +1.0
  "dc_offset": 0.0,             // 可选，-1.0 ~ +1.0
  "arb_bank": 0,                // 可选，0 或 1
  "clear_phase": false          // 可选，本次 apply 是否清相位
}
```

响应：`200` + 完整 status 快照。

### POST `/channel/{a|b}/expression`

生成表达式 LUT 并加载：

```json
{
  "expression": "sin(x) + sin(3*x)/3",    // 必需
  "freq_hz": 1000.0,                       // 必需
  "enabled": true,                         // 可选，默认 true
  "phase_deg": 0.0,                        // 可选
  "amplitude": 1.0,                        // 可选
  "dc_offset": 0.0,                        // 可选
  "target_bank": 1,                        // 可选，默认自动选非活动 bank
  "clear_phase": true                      // 可选，默认 true
}
```

### POST `/channel/{a|b}/lut`

直接灌 raw 样点：

```json
{
  "samples": [0, 100, 200, ..., -100],    // 必需，长度**必须** 16384，int14 范围
  "freq_hz": 1000.0,                       // 必需
  "enabled": true,                         // 可选，默认保留当前 enable
  "wave_name": "arb",                      // 可选，默认 "arb"
  "phase_deg": 0.0,                        // 可选
  "amplitude": 1.0,                        // 可选
  "dc_offset": 0.0,                        // 可选
  "target_bank": 1,                        // 可选
  "clear_phase": true                      // 可选，默认 true
}
```

### 错误响应

- `400 Bad Request` + `{"error": "..."}`：参数错（不合法 wave_name、表达式语法错、samples 长度不对、DMA loader 返回错误等）
- `404 Not Found` + `{"error": "Unknown endpoint"}`：路径不对或通道不是 `a`/`b`
- 服务端异常（应该不会发生）：HTTP 5xx

---

## 相关文件

| 文件 | 作用 |
|---|---|
| [ps/dds_cli.py](../ps/dds_cli.py) | CLI 入口 |
| [ps/dds_service.py](../ps/dds_service.py) | HTTP 服务入口 |
| [ps/dds_hw.py](../ps/dds_hw.py) | 低层 mmap / 寄存器读写 / DMA 加载 |
| [ps/dds_regs.py](../ps/dds_regs.py) | 寄存器地址与常量 |
| [ps/expr_engine.py](../ps/expr_engine.py) | 表达式解析 + 采样 |
| [docs/REGISTER_MAP.md](REGISTER_MAP.md) | 完整寄存器 map（含 LUT 调试窗口） |
| [rtl/red_pitaya_dds_axi_dma.v](../rtl/red_pitaya_dds_axi_dma.v) | RTL 顶层（寄存器语义的权威来源） |
