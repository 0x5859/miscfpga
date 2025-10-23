# Red Pitaya DDS AXI DMA scaffold (pure Verilog edition)

This package is the **AXI DMA upgrade** of the earlier Red Pitaya DDS/NCO scaffold, rewritten so the custom RTL is **plain Verilog-2001** rather than SystemVerilog.

## Orientation — jump to the right doc

Pick the row that matches what you want to do:

| I want to… | Start here |
|---|---|
| **Just copy-paste commands** to verify all waveforms on a scope (presets / amplitude / frequency / DC / custom / dual-channel) | **[docs/COMMANDS.md](docs/COMMANDS.md)** — command cheatsheet |
| **Use it** — drive the DDS from SSH / HTTP, set frequencies, load custom waveforms | [docs/USAGE.md](docs/USAGE.md) |
| **Deploy / re-deploy to the Red Pitaya** (fresh SD or update) | [docs/USAGE.md §1](docs/USAGE.md) (pack → upload → install → enable) and [docs/RED_PITAYA_INSTALL_AND_POSTMORTEM.md](docs/RED_PITAYA_INSTALL_AND_POSTMORTEM.md) (known issues + recovery) |
| **Look up a register** (bit field, offset, expected value) | [docs/REGISTER_MAP.md](docs/REGISTER_MAP.md) |
| **Call the HTTP API from another machine / scripts** | [docs/USAGE.md §10](docs/USAGE.md) (full endpoint reference) |
| **Modify RTL** (add a waveform mode, change the register layout, tweak the DMA sink) | [docs/IMPLEMENTATION_GUIDE.md](docs/IMPLEMENTATION_GUIDE.md), [docs/PROJECT_PATHS.md](docs/PROJECT_PATHS.md), and the RTL module reference later in this file |
| **Modify the PS backend** (Python service, CLI, expression engine) | [ps/](ps/) sources + [docs/CODEX_HANDOFF.md](docs/CODEX_HANDOFF.md) |
| **Re-build the bitstream** | [docs/PROJECT_PATHS.md](docs/PROJECT_PATHS.md) (which Vivado project to open), [build/fpga.bif](build/fpga.bif) (bootgen recipe) |
| **Run iverilog testbenches** | [docs/DEVELOPMENT_MANUAL.md](docs/DEVELOPMENT_MANUAL.md) |

**Project status (last verified 2026-04-23)**: DDS IP core runs end-to-end on the board — `verify.sh` 9/9 green, 4 built-in waveforms (`sine` / `square` / `triangle` / `saw`) and custom `arb` waveforms (math expressions and raw 16384-sample arrays) all confirmed from CH A OUT1. Boot-persistence was attempted and abandoned (twice bricked the SD card — see POSTMORTEM Part E); DDS is now loaded **on demand** per SSH session with `load_overlay.sh` + `systemctl restart rp-dds-axi-dma.service`.

---

## What is inside this edition

- custom DDS + AXI DMA sink RTL written in `.v`
- all controller FSMs use **three-process state machines**
- all RTL comments are written in English
- the AXI DMA architecture is preserved:

```text
PS expression engine
    -> reserved DDR staging buffer
    -> AXI DMA MM2S
    -> AXI4-Stream sink in PL
    -> inactive LUT bank
    -> apply + optional phase clear
    -> active DDS channel reads the new bank
```

The output datapath is still a real DDS/NCO:

- PL keeps the phase accumulator
- FTW still determines output frequency
- standard sine / square / triangle / saw are generated in PL
- arbitrary waveform mode still behaves as a one-period phase-addressed LUT

That is the key architectural decision: **DMA is used for fast LUT loading, not to replace the DDS with a raw sample streamer**.

## Directory layout

- `rtl/`
  - pure Verilog AXI DMA DDS core
  - pure Verilog AXIS sink
  - channel datapath
  - banked LUT RAMs
  - filelists for synthesis/simulation
- `tb/`
  - lightweight pure Verilog testbenches
- `ps/`
  - expression engine
  - register access layer
  - AXI DMA MM2S direct-register-mode driver
  - HTTP backend
  - CLI helper
- `web/`
  - browser UI
  - large nixie-style frequency display
- `deploy/`
  - install script
  - overlay loading example
  - reserved-memory DTS template
- `docs/`
  - implementation guide
  - register map
  - Codex handoff document

## Main custom RTL entry points

AXI DMA version:

- `rtl/red_pitaya_dds_axi_dma.v`

Legacy non-DMA reference:

- `rtl/red_pitaya_dds_v1_legacy.v`

Key helper modules:

- `rtl/rp_dds_cfg_fsm.v`
- `rtl/rp_dds_dma_axis_sink.v`
- `rtl/rp_dds_channel.v`
- `rtl/rp_dds_lut_ram.v`
- `rtl/rp_sine_rom.v`

## RTL module interface reference

This section documents the external interfaces of the custom RTL blocks in a
style similar to a Xilinx IP product guide. Signal direction is shown from the
perspective of the module being described.

### Common interface conventions

| Item | Description |
|---|---|
| Clock domain | All custom RTL in this package is synchronous to `clk_i`. |
| Reset polarity | `rstn_i` is an active-low reset input. |
| Sample format | DDS and LUT sample paths use signed 14-bit data by default. |
| AXI4-Stream payload | The MM2S path carries one 32-bit word per sample; the PL sink consumes `tdata[13:0]` as the signed LUT sample value. |

### Module: `red_pitaya_dds_axi_dma`

File: `rtl/red_pitaya_dds_axi_dma.v`

Function: Top-level custom IP that integrates the system-bus register file,
configuration FSM, two DDS datapaths, four LUT banks, and the AXI4-Stream DMA
load path.

#### Parameters

| Parameter | Default | Description |
|---|---:|---|
| `PHASE_W` | `48` | Phase accumulator and phase offset width. |
| `AMP_W` | `16` | Amplitude control width. |
| `DATA_W` | `14` | Signed output and LUT sample width. |
| `LUT_AW` | `14` | Arbitrary LUT address width. |
| `SINE_AW` | `12` | Sine ROM address width. |
| `SAMPLE_RATE_HZ` | `125000000` | Sample-rate constant returned through the register interface. |

#### Ports

| Port | Width | Direction | Description |
|---|---:|---|---|
| `clk_i` | 1 | Input | Clock for the complete DDS core, control plane, and datapath. |
| `rstn_i` | 1 | Input | Active-low reset for registers, FSMs, and datapath state. |
| `dat_a_o` | `DATA_W` | Output | Channel A signed DDS sample output. |
| `dat_b_o` | `DATA_W` | Output | Channel B signed DDS sample output. |
| `sys_addr` | 20 | Input | System-bus word address used for register access and manual LUT window selection. |
| `sys_wdata` | 32 | Input | System-bus write data for register fields or manual LUT writes. |
| `sys_wen` | 1 | Input | System-bus write strobe. |
| `sys_ren` | 1 | Input | System-bus read strobe. |
| `sys_rdata` | 32 | Output | System-bus readback data. |
| `sys_err` | 1 | Output | System-bus error response for unmapped accesses or blocked manual LUT writes. |
| `sys_ack` | 1 | Output | System-bus acknowledge pulse for completed reads or writes. |
| `s_axis_mm2s_tdata_i` | 32 | Input | AXI4-Stream payload from AXI DMA MM2S. One accepted word maps to one LUT sample. |
| `s_axis_mm2s_tkeep_i` | 4 | Input | AXI4-Stream byte qualifier from MM2S. The sink expects aligned full-word transfers. |
| `s_axis_mm2s_tvalid_i` | 1 | Input | AXI4-Stream valid handshake from MM2S. |
| `s_axis_mm2s_tready_o` | 1 | Output | AXI4-Stream ready handshake driven by the PL DMA sink. |
| `s_axis_mm2s_tlast_i` | 1 | Input | AXI4-Stream frame termination indicator used to validate transfer length. |

### Module: `red_pitaya_dds` (legacy reference)

File: `rtl/red_pitaya_dds_v1_legacy.v`

Function: Earlier non-DMA top-level reference. It keeps the dual-channel DDS
core and manual LUT write windows but does not include dual-bank loading or an
AXI4-Stream sink.

#### Parameters

| Parameter | Default | Description |
|---|---:|---|
| `PHASE_W` | `48` | Phase accumulator and phase offset width. |
| `AMP_W` | `16` | Amplitude control width. |
| `DATA_W` | `14` | Signed output and LUT sample width. |
| `LUT_AW` | `14` | Arbitrary LUT address width. |
| `SINE_AW` | `12` | Sine ROM address width. |
| `SAMPLE_RATE_HZ` | `125000000` | Sample-rate constant returned through the register interface. |

#### Ports

| Port | Width | Direction | Description |
|---|---:|---|---|
| `clk_i` | 1 | Input | Clock for the legacy DDS control path and datapath. |
| `rstn_i` | 1 | Input | Active-low reset for the legacy DDS core. |
| `dat_a_o` | `DATA_W` | Output | Channel A signed DDS sample output. |
| `dat_b_o` | `DATA_W` | Output | Channel B signed DDS sample output. |
| `sys_addr` | 20 | Input | System-bus word address for control registers and LUT write windows. |
| `sys_wdata` | 32 | Input | System-bus write data. |
| `sys_wen` | 1 | Input | System-bus write strobe. |
| `sys_ren` | 1 | Input | System-bus read strobe. |
| `sys_rdata` | 32 | Output | System-bus readback data. |
| `sys_err` | 1 | Output | System-bus error response for unmapped accesses. |
| `sys_ack` | 1 | Output | System-bus acknowledge pulse for completed accesses. |

### Module: `rp_dds_cfg_fsm`

File: `rtl/rp_dds_cfg_fsm.v`

Function: Small control FSM that sequences configuration commit and phase-clear
operations into single-cycle pulses for the live datapath.

#### Ports

| Port | Width | Direction | Description |
|---|---:|---|---|
| `clk_i` | 1 | Input | Clock input for the FSM state machine. |
| `rstn_i` | 1 | Input | Active-low reset for the FSM state. |
| `apply_req_i` | 1 | Input | Request to copy shadow configuration into active registers. |
| `phase_clear_req_i` | 1 | Input | Request to clear both DDS phase accumulators. |
| `busy_o` | 1 | Output | Indicates that the FSM is servicing a configuration request. |
| `load_cfg_o` | 1 | Output | One-cycle pulse used to commit shadow registers into the active datapath. |
| `phase_clear_o` | 1 | Output | One-cycle pulse used to clear channel phase accumulators. |
| `done_o` | 1 | Output | One-cycle completion pulse asserted after the requested action finishes. |

### Module: `rp_dds_dma_axis_sink`

File: `rtl/rp_dds_dma_axis_sink.v`

Function: AXI4-Stream receive-side loader that accepts MM2S data, verifies
framing, and generates sequential LUT write commands plus status information.

#### Parameters

| Parameter | Default | Description |
|---|---:|---|
| `ADDR_W` | `14` | Width of the generated LUT write address. |
| `DATA_W` | `14` | Width of the extracted signed LUT sample. |

#### Ports

| Port | Width | Direction | Description |
|---|---:|---|---|
| `clk_i` | 1 | Input | Clock for stream reception, status tracking, and write command generation. |
| `rstn_i` | 1 | Input | Active-low reset for the loader FSM and counters. |
| `arm_i` | 1 | Input | Starts a new transfer context and latches the destination metadata. |
| `clear_status_i` | 1 | Input | Clears the done or error state and returns the loader to idle. |
| `abort_i` | 1 | Input | Forces an in-progress or armed transfer into the error state. |
| `target_channel_i` | 1 | Input | Channel selector captured when `arm_i` is asserted. |
| `target_bank_i` | 1 | Input | Bank selector captured when `arm_i` is asserted. |
| `expected_words_i` | 32 | Input | Declared number of 32-bit stream words expected in the transfer. |
| `s_axis_tdata_i` | 32 | Input | AXI4-Stream payload; low `DATA_W` bits carry the signed sample value. |
| `s_axis_tkeep_i` | 4 | Input | AXI4-Stream byte qualifier input. Included for interface completeness. |
| `s_axis_tvalid_i` | 1 | Input | AXI4-Stream valid handshake from the source. |
| `s_axis_tready_o` | 1 | Output | AXI4-Stream ready handshake from the sink. |
| `s_axis_tlast_i` | 1 | Input | AXI4-Stream frame terminator used for length checking. |
| `wr_en_o` | 1 | Output | Write-enable pulse for the destination LUT memory. |
| `wr_channel_o` | 1 | Output | Latched destination channel associated with the current transfer. |
| `wr_bank_o` | 1 | Output | Latched destination bank associated with the current transfer. |
| `wr_addr_o` | `ADDR_W` | Output | Sequential LUT write address for each accepted stream beat. |
| `wr_data_o` | `DATA_W` | Output | Signed sample extracted from the accepted AXI4-Stream word. |
| `armed_o` | 1 | Output | Indicates that the sink has been armed and is waiting for the first word. |
| `busy_o` | 1 | Output | Indicates that the sink is actively accepting stream data. |
| `done_o` | 1 | Output | Indicates that the declared transfer completed successfully. |
| `error_o` | 1 | Output | Indicates that the loader detected a protocol or control error. |
| `received_words_o` | 32 | Output | Count of accepted stream words in the current or last transfer. |
| `error_code_o` | 32 | Output | Encoded cause of the last loader error. |

### Module: `rp_dds_channel`

File: `rtl/rp_dds_channel.v`

Function: Per-channel DDS datapath that performs phase accumulation, waveform
selection, amplitude scaling, DC offset addition, and output saturation.

#### Parameters

| Parameter | Default | Description |
|---|---:|---|
| `PHASE_W` | `48` | Width of the phase accumulator and phase word. |
| `AMP_W` | `16` | Width of the amplitude control input. |
| `DATA_W` | `14` | Width of the waveform sample path and output. |
| `LUT_AW` | `14` | Width of the arbitrary-waveform LUT address output. |
| `SINE_AW` | `12` | Width of the sine ROM address output. |

#### Ports

| Port | Width | Direction | Description |
|---|---:|---|---|
| `clk_i` | 1 | Input | Sample clock for the phase accumulator. |
| `rstn_i` | 1 | Input | Active-low reset for the phase accumulator register. |
| `enable_i` | 1 | Input | Enables waveform generation and phase accumulation. |
| `wave_sel_i` | 3 | Input | Selects sine, square, triangle, saw, or arbitrary LUT mode. |
| `ftw_i` | `PHASE_W` | Input | Frequency tuning word added to the accumulator each enabled clock. |
| `phase_offset_i` | `PHASE_W` | Input | Static phase offset added to the accumulator output before waveform lookup. |
| `amplitude_i` | `AMP_W` | Input | Signed amplitude scale factor applied to the raw waveform sample. |
| `dc_offset_i` | `DATA_W` | Input | Signed DC offset added after amplitude scaling. |
| `phase_clear_i` | 1 | Input | Clears the phase accumulator to zero. |
| `arb_addr_o` | `LUT_AW` | Output | Arbitrary-waveform LUT address derived from the upper phase bits. |
| `arb_sample_i` | `DATA_W` | Input | Arbitrary-waveform sample returned from the selected LUT bank. |
| `sine_addr_o` | `SINE_AW` | Output | Sine ROM address derived from the upper phase bits. |
| `sine_sample_i` | `DATA_W` | Input | Sine sample returned from the sine ROM. |
| `data_o` | `DATA_W` | Output | Final saturated signed channel output sample. |

### Module: `rp_dds_lut_ram`

File: `rtl/rp_dds_lut_ram.v`

Function: Simple synchronous dual-port LUT memory used for arbitrary-waveform
storage. One port handles writes, and one port services datapath reads.

#### Parameters

| Parameter | Default | Description |
|---|---:|---|
| `ADDR_W` | `14` | LUT address width. |
| `DATA_W` | `14` | LUT sample width. |

#### Ports

| Port | Width | Direction | Description |
|---|---:|---|---|
| `clk_i` | 1 | Input | Shared clock for the write and read ports. |
| `wr_addr_i` | `ADDR_W` | Input | Write address for the incoming sample. |
| `wr_data_i` | `DATA_W` | Input | Signed sample written into the LUT. |
| `wr_en_i` | 1 | Input | Write enable for the LUT write port. |
| `rd_addr_i` | `ADDR_W` | Input | Read address driven by the DDS datapath. |
| `rd_data_o` | `DATA_W` | Output | Registered signed sample returned from the LUT. |

### Module: `rp_sine_rom`

File: `rtl/rp_sine_rom.v`

Function: Synchronous lookup ROM for the standard sine waveform.

#### Parameters

| Parameter | Default | Description |
|---|---:|---|
| `ADDR_W` | `12` | Sine ROM address width. |
| `DATA_W` | `14` | Sine sample width. |
| `MEM_FILE` | `"sine4096_14b.mem"` | Hex memory initialization file used by `$readmemh`. |

#### Ports

| Port | Width | Direction | Description |
|---|---:|---|---|
| `clk_i` | 1 | Input | Clock for synchronous ROM readout. |
| `addr_i` | `ADDR_W` | Input | ROM address supplied by the DDS channel. |
| `data_o` | `DATA_W` | Output | Registered signed sine sample read from the ROM. |

## Important note about the AXI DMA IP itself

This package includes the custom RTL around the DMA path and the exact Vivado integration instructions, but it does **not** include generated vendor IP output products or a synthesized bitstream.

You still need to generate:

- AXI DMA IP
- final bitstream
- final `.dtbo`

See:

- `docs/IMPLEMENTATION_GUIDE.md`
- `docs/CODEX_HANDOFF.md`
- `rtl/integration_notes_red_pitaya_top_axi_dma.md`
- `build/PLACE_BITSTREAM_HERE.md`
