# Integration notes for `red_pitaya_dds_axi_dma.v`

This file explains how to stitch the custom pure Verilog core into a Red Pitaya v0.94-style project while adding AXI DMA as a separate IP block.

---

## 1. Design philosophy

Do **not** replace the DDS datapath with a raw sample streamer.

Instead:

- keep the DDS/NCO in PL
- keep arbitrary waveform playback as a phase-addressed LUT
- use AXI DMA only for fast bank loading

That gives you:

- standard DDS semantics for frequency / phase control
- fast arbitrary LUT updates
- a clean upgrade path toward deeper DMA/DDR playback later

---

## 2. Modules in this package

### Main custom core

- `red_pitaya_dds_axi_dma.v`

### Helper modules

- `rp_dds_dma_axis_sink.v`
- `rp_dds_cfg_fsm.v`
- `rp_dds_channel.v`
- `rp_dds_lut_ram.v`
- `rp_sine_rom.v`

---

## 3. High-level block diagram

```text
                    +-------------------------------+
                    |           Zynq PS             |
                    |                               |
Web/CLI -> Python ->|  cfg regs + expr engine      |
                    |  reserved DDR staging buffer  |
                    |  AXI DMA register control     |
                    +---------------+---------------+
                                    |
                                    | M_AXI_GP1 / AXI-Lite
                                    v
                             +------+------+
                             |   AXI DMA   |
                             |   MM2S only |
                             +------+------+
                                    |
                                    | M_AXI_MM2S
                                    v
                                PS DDR via HP0
                                    |
                                    | M_AXIS_MM2S (stream)
                                    v
 +------------------------------------------------------------------+
 |                         red_pitaya_dds_axi_dma                   |
 |                                                                  |
 |  sys bus regs   shadow->active FSM   AXIS sink   dual-bank LUTs  |
 |       |                  |               |             |          |
 |       +------------------+---------------+-------------+          |
 |                                             active bank -> DDS    |
 |                                                       phase/LUT   |
 +------------------------------------------------------------------+
                                                          |
                                                          v
                                                         DAC
```

---

## 4. Where to attach the custom core

Reuse the same general idea as the Red Pitaya “modify project” tutorial:

- keep the official v0.94 base
- replace the `PID`-style custom slot in `sys[3]`
- keep the DAC routing pattern that adds the custom output into the generation path

In other words, your custom DDS core still lives in the familiar Red Pitaya system bus region around `0x4030_0000`.

---

## 5. Where to attach the AXI DMA

AXI DMA is **not** carried by the Red Pitaya system bus.

Use a standard AXI attachment:

- `S_AXI_LITE` of AXI DMA
  - connect to PS `M_AXI_GP1`
  - base address `0x4300_0000`
- `M_AXI_MM2S`
  - connect to PS `S_AXI_HP0`
- `M_AXIS_MM2S`
  - connect to `red_pitaya_dds_axi_dma.v` stream sink ports

### Recommended address plan

| Block | Suggested base |
|---|---|
| custom DDS core on Red Pitaya system bus | `0x4030_0000` |
| AXI DMA AXI-Lite | `0x4300_0000` |
| reserved DDR staging buffer | `0x1E000000` |

---

## 6. Recommended clocking

Use two clock domains inside the AXI DMA IP as intended:

- `s_axi_lite_aclk`
  - PS fabric clock, usually `FCLK_CLK0`
- `m_axi_mm2s_aclk`
  - same PS fabric clock as above
- `m_axis_mm2s_aclk`
  - connect to the same PL sample clock that writes the LUT RAMs
  - recommended: `adc_clk` / DDS core clock

Why this split is useful:

- MM2S memory-side traffic naturally lives in the PS/HP clocking domain
- stream output can be synchronized to the LUT write clock in the DDS core
- no extra external CDC wrapper is needed; AXI DMA handles the internal clock crossing between its memory-mapped and streaming sides

---

## 7. AXI DMA configuration checklist

Configure AMD AXI DMA in **direct register mode**, MM2S only:

- scatter-gather: **disabled**
- MM2S channel: **enabled**
- S2MM channel: **disabled**
- MM2S data width:
  - stream width: `32`
  - memory width: `32` or `64` is acceptable; `32` keeps mental mapping simple
- DRE:
  - can stay **disabled** because software uses 32-bit aligned source addresses
- Status/Control stream sidebands:
  - disabled
- burst size:
  - moderate value such as `16` is fine for LUT loads

This project deliberately keeps the DMA IP simple. The complexity is in the system architecture, not in fancy DMA features.

---

## 8. Port mapping between AXI DMA and the custom core

Connect these ports:

| AXI DMA port | Custom core port |
|---|---|
| `m_axis_mm2s_tdata` | `s_axis_mm2s_tdata_i` |
| `m_axis_mm2s_tkeep` | `s_axis_mm2s_tkeep_i` |
| `m_axis_mm2s_tvalid` | `s_axis_mm2s_tvalid_i` |
| `m_axis_mm2s_tready` | `s_axis_mm2s_tready_o` |
| `m_axis_mm2s_tlast` | `s_axis_mm2s_tlast_i` |

The custom core expects one signed 14-bit sample per 32-bit stream word:

- valid sample bits: `TDATA[13:0]`
- all higher bits are ignored by the PL sink

---

## 9. Reserved DDR buffer

The PS writes LUT samples into a physically fixed reserved-memory region.

Recommended carve-out:

```text
base = 0x1E000000
size = 0x00400000
```

That is intentionally much larger than the current minimum requirement so later upgrades can add:

- multi-buffer staging
- queued loads
- cyclic DMA experiments
- larger test payloads

---

## 10. Sample top-level connection strategy

At top-level, think about the integration in three layers:

### 10.1 Red Pitaya native layer

- ADC/DAC clocking
- DAC output mux/sum path
- Red Pitaya system bus
- existing `red_pitaya_top.sv` from the vendor project

### 10.2 Custom DDS layer

- `red_pitaya_dds_axi_dma.v`
- LUT banks
- NCO
- AXIS sink

### 10.3 AXI infrastructure layer

- AXI DMA IP
- AXI interconnect for AXI-Lite if required
- PS HP0 connection
- PS GP1 connection

Keep the custom DDS core self-contained and let AXI DMA stay as a standard IP block next to it.

---

## 11. Practical bring-up order

Bring the design up in this exact order:

1. Integrate custom core only and confirm standard sine generation works.
2. Add dual-bank arbitrary LUT read path and manually write debug windows.
3. Add AXIS sink and simulate simple sequential writes.
4. Add AXI DMA IP and confirm MM2S `TDATA/TVALID/TLAST`.
5. Add reserved DDR + PS software and confirm one LUT load to the inactive bank.
6. Switch bank with `apply + clear_phase` and verify the waveform changes cleanly.
7. Hook the web UI and confirm the end-to-end expression workflow.

This staged approach prevents you from debugging five subsystems at the same time.
