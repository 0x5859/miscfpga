# DDS AXI DMA register map (pure Verilog edition)

Base address of the custom DDS core:

```text
0x4030_0000
```

This remains inside the Red Pitaya `sys[3]` / `PID` replacement slot so the custom signal path still fits naturally into the v0.94 style top-level design.

---

## 1. Global registers

| Offset | Name | Access | Description |
|---|---:|---|---|
| `0x00000` | `REG_ID` | R | Core ID, returns `0x44445332` (`"DDS2"`) |
| `0x00004` | `REG_VERSION` | R | RTL/API version, current `0x00020001` |
| `0x00008` | `REG_CONTROL` | W/R | `bit0=apply`, `bit1=phase_clear` |
| `0x0000C` | `REG_STATUS` | R | Global status bits |
| `0x00010` | `REG_SAMPLE_RATE` | R | DAC sample rate, default `125000000` |
| `0x00014` | `REG_LUT_LENGTH` | R | LUT length, default `16384` |
| `0x00018` | `REG_FEATURES` | R | Feature bitmap |
| `0x0001C` | `REG_DMA_CONTROL` | W | `bit0=arm`, `bit1=clear_status`, `bit2=abort` |
| `0x00020` | `REG_DMA_TARGET` | W/R | `bit0=channel`, `bit1=bank` |
| `0x00024` | `REG_DMA_EXPECTED_WORDS` | W/R | Number of 32-bit stream words expected |
| `0x00028` | `REG_DMA_RECEIVED_WORDS` | R | Number of words accepted by the AXIS sink |
| `0x0002C` | `REG_DMA_ERROR_CODE` | R | Latched loader error code |

### 1.1 `REG_STATUS` bit assignment

| Bit | Name | Meaning |
|---:|---|---|
| 0 | `cfg_busy` | Configuration FSM busy |
| 1 | `cfg_done` | Configuration FSM finished since last apply |
| 2 | `dma_armed` | AXIS sink is armed and waiting for stream data |
| 3 | `dma_busy` | AXIS sink is currently consuming stream data |
| 4 | `dma_done` | AXIS sink finished the expected transfer |
| 5 | `dma_error` | AXIS sink detected a protocol/load error |
| 6 | `cha_active_bank` | Active arbitrary LUT bank for channel A |
| 7 | `chb_active_bank` | Active arbitrary LUT bank for channel B |
| 8 | `cha_enable` | Channel A active enable |
| 9 | `chb_enable` | Channel B active enable |

### 1.2 `REG_FEATURES` bit assignment

| Bit | Meaning |
|---:|---|
| 0 | dual-bank arbitrary LUT present |
| 1 | AXI DMA loader path present |
| 2 | manual debug write windows present |

---

## 2. Per-channel configuration registers

### 2.1 Channel A

| Offset | Name | Access | Description |
|---|---:|---|---|
| `0x00100` | `REG_CHA_CTRL` | W/R | `bit0=enable`, `bits[3:1]=wave_sel` |
| `0x00104` | `REG_CHA_FTW_LO` | W/R | FTW low 32 bits |
| `0x00108` | `REG_CHA_FTW_HI` | W/R | FTW high 16 bits |
| `0x0010C` | `REG_CHA_PHASE_LO` | W/R | phase offset low 32 bits |
| `0x00110` | `REG_CHA_PHASE_HI` | W/R | phase offset high 16 bits |
| `0x00114` | `REG_CHA_AMP` | W/R | amplitude in signed Q1.15 |
| `0x00118` | `REG_CHA_DC` | W/R | DC offset in signed 14-bit |
| `0x0011C` | `REG_CHA_ARB_BANK` | W/R | arbitrary-waveform active bank after apply |

### 2.2 Channel B

| Offset | Name | Access | Description |
|---|---:|---|---|
| `0x00200` | `REG_CHB_CTRL` | W/R | `bit0=enable`, `bits[3:1]=wave_sel` |
| `0x00204` | `REG_CHB_FTW_LO` | W/R | FTW low 32 bits |
| `0x00208` | `REG_CHB_FTW_HI` | W/R | FTW high 16 bits |
| `0x0020C` | `REG_CHB_PHASE_LO` | W/R | phase offset low 32 bits |
| `0x00210` | `REG_CHB_PHASE_HI` | W/R | phase offset high 16 bits |
| `0x00214` | `REG_CHB_AMP` | W/R | amplitude in signed Q1.15 |
| `0x00218` | `REG_CHB_DC` | W/R | DC offset in signed 14-bit |
| `0x0021C` | `REG_CHB_ARB_BANK` | W/R | arbitrary-waveform active bank after apply |

### 2.3 Waveform select encoding

| Code | Waveform |
|---:|---|
| 0 | sine |
| 1 | square |
| 2 | triangle |
| 3 | sawtooth |
| 4 | arbitrary LUT |

---

## 3. Manual debug LUT windows

These windows are still present for debug and bring-up, but they are **not the main arbitrary-waveform path anymore**. The main path is AXI DMA.

Each entry is a 32-bit word whose low 14 bits carry one signed sample.

| Base offset | Window | Points |
|---|---|---:|
| `0x10000` | CH A bank 0 | 16384 |
| `0x20000` | CH A bank 1 | 16384 |
| `0x30000` | CH B bank 0 | 16384 |
| `0x40000` | CH B bank 1 | 16384 |

The software intentionally blocks manual writes while the DMA sink is armed or busy, so accidental overlap between a debug write and a DMA transfer does not corrupt the target bank.

---

## 4. AXI DMA AXI-Lite register map

The AXI DMA core itself is mapped separately at:

```text
0x4300_0000
```

This package uses **MM2S direct register mode** only.

Important MM2S offsets:

| AXI DMA offset | Name | Description |
|---|---|---|
| `0x00` | `MM2S_DMACR` | control register (`RS`, `Reset`) |
| `0x04` | `MM2S_DMASR` | status register (`Halted`, `Idle`, error bits) |
| `0x18` | `MM2S_SA` | source address low |
| `0x1C` | `MM2S_SA_MSB` | source address high |
| `0x28` | `MM2S_LENGTH` | transfer length in bytes, must be written last |

The PS software writes `MM2S_LENGTH` last because that is what starts the MM2S transaction in direct register mode.

---

## 5. Error code encoding for the PL AXIS sink

| Code | Name | Meaning |
|---:|---|---|
| 0 | `none` | no error |
| 1 | `expected_words_zero` | sink armed with zero expected length |
| 2 | `early_tlast` | `TLAST` arrived before the final expected word |
| 3 | `missing_tlast` | final expected word arrived without `TLAST` |
| 4 | `overrun` | more words arrived than `expected_words` |
| 5 | `abort` | PS requested abort while armed/busy |
