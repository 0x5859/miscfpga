# Red Pitaya Z7020 DDS AXI DMA implementation guide (pure Verilog edition)

## 1. Design target

This project implements a **PS/PL co-designed DDS signal generation system** on Red Pitaya Z7020-LN with a real AXI DMA arbitrary-waveform loading path.

The custom RTL in this edition is written in **plain Verilog-2001**.
The custom controller blocks use **three-process FSMs**.

## 2. Responsibility split

### 2.1 PL side

- standard DDS / NCO core
- 48-bit phase accumulator per channel
- standard waveform generation:
  - sine
  - square
  - triangle
  - sawtooth
- arbitrary waveform playback through a phase-addressed LUT
- dual-bank LUT structure for glitch-resistant updates
- AXI4-Stream sink that accepts DMA-fed samples and writes the inactive LUT bank

### 2.2 PS side

- parameter configuration:
  - frequency
  - phase
  - amplitude
  - waveform type
- mathematical expression parsing and sampling
- waveform normalization and 14-bit quantization
- DDR staging-buffer fill
- AXI DMA MM2S control
- web backend and browser UI

---

## 3. Why this AXI DMA architecture is the right one

A tempting but incorrect upgrade would be:

```text
DDR -> DMA -> DAC
```

That is useful for a deep-memory sample player, but it no longer behaves like a classic DDS when the user changes frequency through FTW.

This project intentionally keeps the arbitrary waveform path inside the DDS framework:

```text
PS expression engine
    -> LUT samples
    -> reserved DDR staging buffer
    -> AXI DMA MM2S
    -> AXIS sink in PL
    -> inactive LUT bank
    -> phase accumulator addresses active bank
```

### Why this is better for your project

- you still have a standard DDS/NCO story for interviews
- arbitrary waveform mode still uses FTW and phase offset correctly
- the upgrade from v1 is real and meaningful:
  - CPU no longer writes 16k words one by one into PL
  - AXI DMA performs the bulk transfer
- the architecture scales naturally to:
  - faster bank swaps
  - interpolation
  - longer DDR-backed waveform playback later

---

## 4. End-to-end data path

### 4.1 Standard wave path

1. PS writes shadow registers.
2. PS triggers `apply`.
3. PL copies shadow registers into active registers.
4. DDS phase accumulator advances every sample clock.
5. PL generates sine / square / triangle / saw directly.
6. PL applies amplitude and DC offset.
7. PL drives the DAC output path.

### 4.2 Arbitrary waveform path

1. User enters an expression, for example `sin(x)+0.5*sin(3*x)`.
2. PS parses the expression safely.
3. PS samples one period into **16384 points**.
4. PS normalizes to `[-1, 1]`.
5. PS quantizes samples to signed 14-bit integers.
6. PS writes these samples into the reserved DDR staging buffer.
7. PS configures the custom core:
   - target channel
   - target inactive bank
   - expected word count
8. PS arms the PL AXIS sink.
9. PS programs AXI DMA MM2S:
   - source address
   - byte count
10. AXI DMA streams one 32-bit word per sample into PL.
11. The PL AXIS sink writes the inactive LUT bank sequentially.
12. When the transfer completes, PS sets the channel's arbitrary active bank to the newly loaded bank and triggers `apply`.
13. The PL DDS channel now reads from the new bank.

---

## 5. PL design details

### 5.1 Main custom core

Primary file:

- `rtl/red_pitaya_dds_axi_dma.v`

This module contains:

- system-bus register file
- configuration FSM
- two DDS channels
- four LUT RAM instances
  - CH A bank 0
  - CH A bank 1
  - CH B bank 0
  - CH B bank 1
- AXIS sink for DMA-fed sample writes

The key engineering point is that the DDS datapath and the DMA loader live in the same custom core, but the AXI DMA IP itself remains a separate standard AMD block.

### 5.2 Three-process FSM implementation

All custom controller FSMs follow this template:

1. state register process
2. next-state process
3. output decode process

Files:

- `rtl/rp_dds_cfg_fsm.v`
- `rtl/rp_dds_dma_axis_sink.v`

This makes the RTL easy to review in interviews and easy for Codex to patch later.

### 5.3 DDS channel datapath

File:

- `rtl/rp_dds_channel.v`

Per channel, the datapath includes:

- 48-bit phase accumulator
- phase offset adder
- waveform select
- amplitude multiply
- DC offset add
- saturator to 14-bit signed output

The phase accumulator update is:

```text
phase_acc[n+1] = phase_acc[n] + FTW
```

The top phase bits are reused as the LUT address in arbitrary mode.

### 5.4 Standard waveform generation

Standard modes are generated entirely in PL:

- sine:
  - ROM lookup
- square:
  - sign from the MSB of the phase word
- triangle:
  - folded ramp
- saw:
  - upper phase bits reinterpreted as signed amplitude

This keeps standard modes lightweight and synthesis-friendly.

### 5.5 Dual-bank arbitrary LUT design

Each channel has two LUT banks.

Without dual banking, loading a new LUT while the DDS is reading the same LUT is dangerous:

- the waveform can glitch
- the table can be half old / half new
- debugging becomes painful

With dual banking:

- one bank is **active**
- one bank is **inactive**
- DMA always writes the inactive bank
- after the load completes, `apply` changes the active bank

That is the minimum clean engineering solution.

### 5.6 AXIS sink

File:

- `rtl/rp_dds_dma_axis_sink.v`

This module is the PL-side consumer for `M_AXIS_MM2S`.

### Sink responsibilities

- latch target channel and bank on `arm`
- accept sequential stream words
- convert each word into one 14-bit signed sample
- write the destination bank sequentially
- verify transfer framing:
  - zero-length arm
  - early `TLAST`
  - missing `TLAST`
  - word overrun
  - abort

The sink keeps the protocol simple on purpose: one 32-bit stream word equals one LUT sample.

---

## 6. PS software details

### 6.1 Why direct-register-mode AXI DMA is used

For this project, **direct register mode (simple DMA)** is the best PS-side choice because:

- the transfer is fixed-size and short
- the load is synchronous with a user action
- scatter-gather would add driver and descriptor complexity without helping much
- the main design difficulty is the system architecture, not the DMA IP feature matrix

This gives you a real DMA version while keeping the software debuggable.

### 6.2 Python backend structure

#### `ps/expr_engine.py`

- safe AST validation
- supports expressions such as:
  - `sin(x)`
  - `sin(x)+0.5*sin(3*x)`
  - `sin(x)^3`
- samples one period into 16384 points

#### `ps/axi_dma_mm2s.py`

- direct AXI-Lite register access to AXI DMA MM2S
- handles:
  - reset
  - run/stop
  - source address
  - transfer length
  - idle/error polling

#### `ps/dds_hw.py`

- memory-mapped access to custom DDS registers
- active-bank switching
- DMA arm / status handling
- waveform and expression convenience wrappers

#### `ps/dds_service.py`

- HTTP API for the web app
- bridges browser requests to the hardware layer

---

## 7. Pure Verilog file inventory

### 7.1 AXI DMA build path

- `rtl/rp_dds_cfg_fsm.v`
- `rtl/rp_dds_channel.v`
- `rtl/rp_dds_lut_ram.v`
- `rtl/rp_sine_rom.v`
- `rtl/rp_dds_dma_axis_sink.v`
- `rtl/red_pitaya_dds_axi_dma.v`

Filelist:

- `rtl/filelist_axi_dma_verilog.f`

### 7.2 Legacy reference path

- `rtl/red_pitaya_dds_v1_legacy.v`

Filelist:

- `rtl/filelist_legacy_verilog.f`

### 7.3 Simulation support

- `tb/tb_rp_dds_cfg_fsm.v`
- `tb/tb_rp_dds_dma_axis_sink.v`
- `tb/tb_red_pitaya_dds_axi_dma_smoke.v`

---

## 8. Bring-up order

1. Integrate the custom core only and confirm standard sine generation works.
2. Verify manual LUT bank writes through the debug windows.
3. Add the AXIS sink and validate stream-to-bank writes in simulation.
4. Add AXI DMA IP and confirm `TDATA/TVALID/TLAST` activity.
5. Add reserved DDR and PS DMA control.
6. Load the inactive bank and switch it active with `apply`.
7. Hook the web UI and verify the expression workflow end-to-end.

This staged approach prevents you from debugging five subsystems at once.

### 8.1 Vivado BD interface wiring note

When Vivado GUI drag-and-drop refuses to connect `axi_dma_0/M_AXIS_MM2S` to
`axis_clock_converter_0/S_AXIS`, connect the full AXI4-Stream interfaces from
the Tcl console instead of wiring individual signal members:

```tcl
connect_bd_intf_net [get_bd_intf_pins axi_dma_0/M_AXIS_MM2S] [get_bd_intf_pins axis_clock_converter_0/S_AXIS]
```

This command connects the interface bundle directly and is often more reliable
than manual GUI dragging after stream-attribute changes such as `TDATA` width,
`TKEEP`, or `TLAST`.

---

## 9. Manual deployment to Red Pitaya

Before copying the application to the board, confirm that both generated
overlay artifacts are present on the host machine:

- `D:\OneDrive\projects\fpgakit\includes\dds_axi_dma\build\fpga.bit.bin`
- `D:\OneDrive\projects\fpgakit\includes\dds_axi_dma\build\fpga.dtbo`

### 9.1 Copy the full application directory

Copy the entire `includes/dds_axi_dma` directory to the board rather than only
copying the bitstream files. This is the recommended manual deployment path
because `deploy/install.sh` resolves all required assets by relative path and
expects these sibling directories to exist:

- `build/`
- `deploy/`
- `ps/`
- `web/`

From PowerShell on the host machine:

```powershell
scp -r D:\OneDrive\projects\fpgakit\includes\dds_axi_dma root@<redpitaya-ip>:/tmp/dds_axi_dma
```

Replace `<redpitaya-ip>` with the board IP address.

### 9.2 Run the installer on the board

Log in to the board and execute the installer from the copied `deploy/`
directory:

```bash
ssh root@<redpitaya-ip>
cd /tmp/dds_axi_dma/deploy
bash install.sh
```

### 9.3 What `install.sh` does

The install script performs the full app deployment flow:

- copies frontend assets into the Red Pitaya app web root
- copies backend Python files and installs the backend systemd service
- copies `build/fpga.bit.bin` and `build/fpga.dtbo` into the board-side project
  directory
- calls `/opt/redpitaya/sbin/overlay.sh v0.94 ...` when both overlay artifacts
  are present

If either `fpga.bit.bin` or `fpga.dtbo` is missing, the script still deploys
the app files but skips overlay loading.
