# Pure Verilog simulation notes

This folder contains lightweight Verilog testbenches for the main FSM and top-level smoke path.

Suggested local commands:

```bash
iverilog -g2005 -o sim_cfg \
  rtl/rp_dds_cfg_fsm.v tb/tb_rp_dds_cfg_fsm.v
vvp sim_cfg

iverilog -g2005 -o sim_dma \
  rtl/rp_dds_dma_axis_sink.v tb/tb_rp_dds_dma_axis_sink.v
vvp sim_dma

iverilog -g2005 -o sim_top \
  rtl/rp_dds_cfg_fsm.v rtl/rp_dds_channel.v rtl/rp_dds_lut_ram.v \
  rtl/rp_sine_rom.v rtl/rp_dds_dma_axis_sink.v rtl/red_pitaya_dds_axi_dma.v \
  tb/tb_red_pitaya_dds_axi_dma_smoke.v
vvp sim_top
```

Even though the DUT files are written in plain Verilog, the `-g2005` switch is a practical choice with Icarus Verilog because it relaxes parser behavior around ANSI-style signed ports and memory initialization. The generated DUT itself does not rely on SystemVerilog-only syntax or language features.
