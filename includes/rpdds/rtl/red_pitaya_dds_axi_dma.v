`include "rp_dds_defs.vh"

// -------------------------------------------------------------------------
// Module overview
// -------------------------------------------------------------------------
// This block is the top-level DDS generator for the Red Pitaya control path.
// It combines:
//   - two independently configurable DDS output channels,
//   - a shadow/active register scheme for controlled parameter updates,
//   - two arbitrary-waveform LUT banks per channel for bank switching, and
//   - an AXI4-Stream sink used to preload LUT contents from AXI DMA MM2S.
//
// Software writes configuration into the shadow registers through the system
// bus. A write to the CONTROL register issues an APPLY request, which causes
// the configuration FSM to copy the shadow set into the active datapath. A
// PHASE_CLEAR request resets both channel phase accumulators in a controlled
// manner.
//
// Arbitrary waveform memory can be written either manually through dedicated
// system-bus windows or through the AXI DMA stream. Manual writes are blocked
// while the DMA loader is armed or busy so that both sources cannot target the
// same RAM port at the same time. Because each channel owns two LUT banks, the
// inactive bank can be prepared offline and switched in on the next APPLY.
// -------------------------------------------------------------------------
module red_pitaya_dds_axi_dma #(
    parameter PHASE_W        = `RP_DDS_PHASE_W,
    parameter AMP_W          = `RP_DDS_AMP_W,
    parameter DATA_W         = `RP_DDS_DATA_W,
    parameter LUT_AW         = `RP_DDS_LUT_AW,
    parameter SINE_AW        = `RP_DDS_SINE_AW,
    parameter SAMPLE_RATE_HZ = 125_000_000
) (
    input                           clk_i,
    input                           rstn_i,
    output signed [DATA_W-1:0]      dat_a_o,
    output signed [DATA_W-1:0]      dat_b_o,
    input      [31:0]               sys_addr,
    input      [31:0]               sys_wdata,
    input                           sys_wen,
    input                           sys_ren,
    output     [31:0]               sys_rdata,
    output                          sys_err,
    output                          sys_ack,
    input      [31:0]               s_axis_mm2s_tdata_i,
    input      [3:0]                s_axis_mm2s_tkeep_i,
    input                           s_axis_mm2s_tvalid_i,
    output                          s_axis_mm2s_tready_o,
    input                           s_axis_mm2s_tlast_i
);

    // ---------------------------------------------------------------------
    // Configuration and control registers
    // ---------------------------------------------------------------------
    // Shadow registers are software-visible staging registers. Active
    // registers drive the live DDS datapath and only change when the
    // configuration FSM emits cfg_load_pulse_c.
    reg                    apply_req_r;          // One-cycle request to copy shadow configuration into active registers.
    reg                    phase_clear_req_r;    // One-cycle request to clear the DDS phase accumulators.
    wire                   cfg_busy_c;           // FSM busy flag while apply/phase-clear sequencing is in progress.
    wire                   cfg_load_pulse_c;     // FSM pulse that commits shadow registers into the live active set.
    wire                   phase_clear_pulse_c;  // FSM pulse forwarded to both DDS channels to reset phase state.
    wire                   cfg_done_pulse_c;     // FSM completion pulse after the requested control action finishes.
    // Sticky completion flag so software can observe the last apply sequence.
    reg                    cfg_done_latched_r;

    reg                    dma_arm_req_r;
    reg                    dma_clear_status_req_r;
    reg                    dma_abort_req_r;
    // The target metadata is sampled by the DMA sink when a transfer is armed.
    reg                    dma_target_channel_r;
    reg                    dma_target_bank_r;
    reg [31:0]             dma_expected_words_r;

    reg                    ch_a_enable_shadow_r;
    reg [2:0]              ch_a_wave_shadow_r;
    reg [PHASE_W-1:0]      ch_a_ftw_shadow_r;
    reg [PHASE_W-1:0]      ch_a_phase_shadow_r;
    reg signed [AMP_W-1:0] ch_a_amp_shadow_r;
    reg signed [DATA_W-1:0] ch_a_dc_shadow_r;
    reg                    ch_a_arb_bank_shadow_r;

    reg                    ch_b_enable_shadow_r;
    reg [2:0]              ch_b_wave_shadow_r;
    reg [PHASE_W-1:0]      ch_b_ftw_shadow_r;
    reg [PHASE_W-1:0]      ch_b_phase_shadow_r;
    reg signed [AMP_W-1:0] ch_b_amp_shadow_r;
    reg signed [DATA_W-1:0] ch_b_dc_shadow_r;
    reg                    ch_b_arb_bank_shadow_r;

    reg                    ch_a_enable_active_r;
    reg [2:0]              ch_a_wave_active_r;
    reg [PHASE_W-1:0]      ch_a_ftw_active_r;
    reg [PHASE_W-1:0]      ch_a_phase_active_r;
    reg signed [AMP_W-1:0] ch_a_amp_active_r;
    reg signed [DATA_W-1:0] ch_a_dc_active_r;
    reg                    ch_a_arb_bank_active_r;

    reg                    ch_b_enable_active_r;
    reg [2:0]              ch_b_wave_active_r;
    reg [PHASE_W-1:0]      ch_b_ftw_active_r;
    reg [PHASE_W-1:0]      ch_b_phase_active_r;
    reg signed [AMP_W-1:0] ch_b_amp_active_r;
    reg signed [DATA_W-1:0] ch_b_dc_active_r;
    reg                    ch_b_arb_bank_active_r;

    reg [31:0] sys_rdata_r;  // Registered readback data returned on system-bus reads.
    reg        sys_ack_r;    // One-cycle acknowledge pulse for system-bus read/write requests.
    reg        sys_err_r;    // One-cycle error pulse for invalid or blocked system-bus accesses.

    // ---------------------------------------------------------------------
    // LUT RAM interfaces
    // ---------------------------------------------------------------------
    reg                    lut_a0_wr_en_c;
    reg [LUT_AW-1:0]       lut_a0_wr_addr_c;
    reg signed [DATA_W-1:0] lut_a0_wr_data_c;
    wire [LUT_AW-1:0]      lut_a_rd_addr_c;
    wire signed [DATA_W-1:0] lut_a0_rd_data_c;

    reg                    lut_a1_wr_en_c;
    reg [LUT_AW-1:0]       lut_a1_wr_addr_c;
    reg signed [DATA_W-1:0] lut_a1_wr_data_c;
    wire signed [DATA_W-1:0] lut_a1_rd_data_c;

    reg                    lut_b0_wr_en_c;
    reg [LUT_AW-1:0]       lut_b0_wr_addr_c;
    reg signed [DATA_W-1:0] lut_b0_wr_data_c;
    wire [LUT_AW-1:0]      lut_b_rd_addr_c;
    wire signed [DATA_W-1:0] lut_b0_rd_data_c;

    reg                    lut_b1_wr_en_c;
    reg [LUT_AW-1:0]       lut_b1_wr_addr_c;
    reg signed [DATA_W-1:0] lut_b1_wr_data_c;
    wire signed [DATA_W-1:0] lut_b1_rd_data_c;

    wire                   manual_lut_busy_block_c;
    wire                   manual_wr_a0_c;
    wire                   manual_wr_a1_c;
    wire                   manual_wr_b0_c;
    wire                   manual_wr_b1_c;
    wire [LUT_AW-1:0]      manual_wr_addr_c;
    wire signed [DATA_W-1:0] manual_wr_data_c;

    wire signed [DATA_W-1:0] arb_sample_a_c;
    wire signed [DATA_W-1:0] arb_sample_b_c;

    // ---------------------------------------------------------------------
    // Sine ROM interfaces
    // ---------------------------------------------------------------------
    wire [SINE_AW-1:0]       sine_a_addr_c;
    wire signed [DATA_W-1:0] sine_a_data_c;
    wire [SINE_AW-1:0]       sine_b_addr_c;
    wire signed [DATA_W-1:0] sine_b_data_c;

    // ---------------------------------------------------------------------
    // DMA stream loader interfaces
    // ---------------------------------------------------------------------
    wire                   dma_wr_en_c;
    wire                   dma_wr_channel_c;
    wire                   dma_wr_bank_c;
    wire [LUT_AW-1:0]      dma_wr_addr_c;
    wire signed [DATA_W-1:0] dma_wr_data_c;

    wire                   dma_armed_c;
    wire                   dma_busy_c;
    wire                   dma_done_c;
    wire                   dma_error_c;
    wire [31:0]            dma_received_words_c;
    wire [31:0]            dma_error_code_c;

    // The four LUT windows map to channel/bank pairs:
    // 0x1_xxxx = A bank 0, 0x2_xxxx = A bank 1,
    // 0x3_xxxx = B bank 0, 0x4_xxxx = B bank 1.
    // Direct writes are disabled while DMA owns the LUT write path.
    wire [19:0] sys_addr_lo_c = sys_addr[19:0];
    wire [3:0]  sys_region_c  = sys_addr_lo_c[19:16];
    wire        sys_lut_window_c;

    assign sys_lut_window_c        = (sys_region_c >= 4'h1) && (sys_region_c <= 4'h4);
    assign manual_lut_busy_block_c = dma_busy_c || dma_armed_c; // If DMA occupies the data path, block manual writing.
    assign manual_wr_addr_c        = sys_addr_lo_c[15:2];
    assign manual_wr_data_c        = sys_wdata[13:0];
    assign manual_wr_a0_c          = sys_wen && (sys_region_c == 4'h1) && !manual_lut_busy_block_c;
    assign manual_wr_a1_c          = sys_wen && (sys_region_c == 4'h2) && !manual_lut_busy_block_c;
    assign manual_wr_b0_c          = sys_wen && (sys_region_c == 4'h3) && !manual_lut_busy_block_c;
    assign manual_wr_b1_c          = sys_wen && (sys_region_c == 4'h4) && !manual_lut_busy_block_c;

    rp_dds_cfg_fsm i_cfg_fsm (
        .clk_i             (clk_i),
        .rstn_i            (rstn_i),
        .apply_req_i       (apply_req_r),
        .phase_clear_req_i (phase_clear_req_r),
        .busy_o            (cfg_busy_c),
        .load_cfg_o        (cfg_load_pulse_c),
        .phase_clear_o     (phase_clear_pulse_c),
        .done_o            (cfg_done_pulse_c)
    );

    rp_dds_dma_axis_sink #(
        .ADDR_W (LUT_AW),
        .DATA_W (DATA_W)
    ) i_dma_sink (
        .clk_i             (clk_i),
        .rstn_i            (rstn_i),
        .arm_i             (dma_arm_req_r),
        .clear_status_i    (dma_clear_status_req_r),
        .abort_i           (dma_abort_req_r),
        .target_channel_i  (dma_target_channel_r),
        .target_bank_i     (dma_target_bank_r),
        .expected_words_i  (dma_expected_words_r),
        .s_axis_tdata_i    (s_axis_mm2s_tdata_i),
        .s_axis_tkeep_i    (s_axis_mm2s_tkeep_i),
        .s_axis_tvalid_i   (s_axis_mm2s_tvalid_i),
        .s_axis_tready_o   (s_axis_mm2s_tready_o),
        .s_axis_tlast_i    (s_axis_mm2s_tlast_i),
        .wr_en_o           (dma_wr_en_c),
        .wr_channel_o      (dma_wr_channel_c),
        .wr_bank_o         (dma_wr_bank_c),
        .wr_addr_o         (dma_wr_addr_c),
        .wr_data_o         (dma_wr_data_c),
        .armed_o           (dma_armed_c),
        .busy_o            (dma_busy_c),
        .done_o            (dma_done_c),
        .error_o           (dma_error_c),
        .received_words_o  (dma_received_words_c),
        .error_code_o      (dma_error_code_c)
    );

    // ---------------------------------------------------------------------
    // Write arbitration for banked LUT RAMs
    // ---------------------------------------------------------------------
    always @* begin // MUX-like
        lut_a0_wr_en_c   = manual_wr_a0_c;
        lut_a0_wr_addr_c = manual_wr_addr_c;
        lut_a0_wr_data_c = manual_wr_data_c;

        if (dma_wr_en_c && !dma_wr_channel_c && !dma_wr_bank_c) begin
            lut_a0_wr_en_c   = 1'b1;
            lut_a0_wr_addr_c = dma_wr_addr_c;
            lut_a0_wr_data_c = dma_wr_data_c;
        end
    end

    always @* begin
        lut_a1_wr_en_c   = manual_wr_a1_c;
        lut_a1_wr_addr_c = manual_wr_addr_c;
        lut_a1_wr_data_c = manual_wr_data_c;

        if (dma_wr_en_c && !dma_wr_channel_c && dma_wr_bank_c) begin
            lut_a1_wr_en_c   = 1'b1;
            lut_a1_wr_addr_c = dma_wr_addr_c;
            lut_a1_wr_data_c = dma_wr_data_c;
        end
    end

    always @* begin
        lut_b0_wr_en_c   = manual_wr_b0_c;
        lut_b0_wr_addr_c = manual_wr_addr_c;
        lut_b0_wr_data_c = manual_wr_data_c;

        if (dma_wr_en_c && dma_wr_channel_c && !dma_wr_bank_c) begin
            lut_b0_wr_en_c   = 1'b1;
            lut_b0_wr_addr_c = dma_wr_addr_c;
            lut_b0_wr_data_c = dma_wr_data_c;
        end
    end

    always @* begin
        lut_b1_wr_en_c   = manual_wr_b1_c;
        lut_b1_wr_addr_c = manual_wr_addr_c;
        lut_b1_wr_data_c = manual_wr_data_c;

        if (dma_wr_en_c && dma_wr_channel_c && dma_wr_bank_c) begin
            lut_b1_wr_en_c   = 1'b1;
            lut_b1_wr_addr_c = dma_wr_addr_c;
            lut_b1_wr_data_c = dma_wr_data_c;
        end
    end

    rp_dds_lut_ram #(
        .ADDR_W (LUT_AW),
        .DATA_W (DATA_W)
    ) i_lut_a_bank0 (
        .clk_i     (clk_i),
        .wr_addr_i (lut_a0_wr_addr_c),
        .wr_data_i (lut_a0_wr_data_c),
        .wr_en_i   (lut_a0_wr_en_c),
        .rd_addr_i (lut_a_rd_addr_c),
        .rd_data_o (lut_a0_rd_data_c)
    );

    rp_dds_lut_ram #(
        .ADDR_W (LUT_AW),
        .DATA_W (DATA_W)
    ) i_lut_a_bank1 (
        .clk_i     (clk_i),
        .wr_addr_i (lut_a1_wr_addr_c),
        .wr_data_i (lut_a1_wr_data_c),
        .wr_en_i   (lut_a1_wr_en_c),
        .rd_addr_i (lut_a_rd_addr_c),
        .rd_data_o (lut_a1_rd_data_c)
    );

    rp_dds_lut_ram #(
        .ADDR_W (LUT_AW),
        .DATA_W (DATA_W)
    ) i_lut_b_bank0 (
        .clk_i     (clk_i),
        .wr_addr_i (lut_b0_wr_addr_c),
        .wr_data_i (lut_b0_wr_data_c),
        .wr_en_i   (lut_b0_wr_en_c),
        .rd_addr_i (lut_b_rd_addr_c),
        .rd_data_o (lut_b0_rd_data_c)
    );

    rp_dds_lut_ram #(
        .ADDR_W (LUT_AW),
        .DATA_W (DATA_W)
    ) i_lut_b_bank1 (
        .clk_i     (clk_i),
        .wr_addr_i (lut_b1_wr_addr_c),
        .wr_data_i (lut_b1_wr_data_c),
        .wr_en_i   (lut_b1_wr_en_c),
        .rd_addr_i (lut_b_rd_addr_c),
        .rd_data_o (lut_b1_rd_data_c)
    );

    // Select the bank currently exposed to each channel datapath.
    assign arb_sample_a_c = ch_a_arb_bank_active_r ? lut_a1_rd_data_c : lut_a0_rd_data_c;
    assign arb_sample_b_c = ch_b_arb_bank_active_r ? lut_b1_rd_data_c : lut_b0_rd_data_c;

    rp_sine_rom #(
        .ADDR_W   (SINE_AW),
        .DATA_W   (DATA_W),
        .MEM_FILE ("sine4096_14b.mem")
    ) i_sine_a (
        .clk_i  (clk_i),
        .addr_i (sine_a_addr_c),
        .data_o (sine_a_data_c)
    );

    rp_sine_rom #(
        .ADDR_W   (SINE_AW),
        .DATA_W   (DATA_W),
        .MEM_FILE ("sine4096_14b.mem")
    ) i_sine_b (
        .clk_i  (clk_i),
        .addr_i (sine_b_addr_c),
        .data_o (sine_b_data_c)
    );

    rp_dds_channel #(
        .PHASE_W (PHASE_W),
        .AMP_W   (AMP_W),
        .DATA_W  (DATA_W),
        .LUT_AW  (LUT_AW),
        .SINE_AW (SINE_AW)
    ) i_ch_a (
        .clk_i          (clk_i),
        .rstn_i         (rstn_i),
        .enable_i       (ch_a_enable_active_r),
        .wave_sel_i     (ch_a_wave_active_r),
        .ftw_i          (ch_a_ftw_active_r),
        .phase_offset_i (ch_a_phase_active_r),
        .amplitude_i    (ch_a_amp_active_r),
        .dc_offset_i    (ch_a_dc_active_r),
        .phase_clear_i  (phase_clear_pulse_c),
        .arb_addr_o     (lut_a_rd_addr_c),
        .arb_sample_i   (arb_sample_a_c),
        .sine_addr_o    (sine_a_addr_c),
        .sine_sample_i  (sine_a_data_c),
        .data_o         (dat_a_o)
    );

    rp_dds_channel #(
        .PHASE_W (PHASE_W),
        .AMP_W   (AMP_W),
        .DATA_W  (DATA_W),
        .LUT_AW  (LUT_AW),
        .SINE_AW (SINE_AW)
    ) i_ch_b (
        .clk_i          (clk_i),
        .rstn_i         (rstn_i),
        .enable_i       (ch_b_enable_active_r),
        .wave_sel_i     (ch_b_wave_active_r),
        .ftw_i          (ch_b_ftw_active_r),
        .phase_offset_i (ch_b_phase_active_r),
        .amplitude_i    (ch_b_amp_active_r),
        .dc_offset_i    (ch_b_dc_active_r),
        .phase_clear_i  (phase_clear_pulse_c),
        .arb_addr_o     (lut_b_rd_addr_c),
        .arb_sample_i   (arb_sample_b_c),
        .sine_addr_o    (sine_b_addr_c),
        .sine_sample_i  (sine_b_data_c),
        .data_o         (dat_b_o)
    );

    // ---------------------------------------------------------------------
    // Register write and readback logic
    // ---------------------------------------------------------------------
    // The control plane is synchronous to clk_i. Writes update the shadow
    // configuration set or DMA control state. Readback reports the active
    // datapath state so software sees the configuration currently in use.
    //
    // sys_wdata field map by write address (unused upper bits are ignored):
    //   CONTROL            0x00008: [0]=apply_req, [1]=phase_clear_req
    //   DMA_CONTROL        0x0001C: [0]=arm, [1]=clear_status, [2]=abort
    //   DMA_TARGET         0x00020: [0]=channel (0=A, 1=B), [1]=bank (0/1)
    //   DMA_EXPECTED_WORDS 0x00024: [31:0]=expected AXIS words
    //   CHA/CHB_CTRL              : [0]=enable, [3:1]=wave_sel
    //   CHA/CHB_FTW_LO            : [31:0]=ftw[31:0]
    //   CHA/CHB_FTW_HI            : [15:0]=ftw[47:32]
    //   CHA/CHB_PHASE_LO          : [31:0]=phase[31:0]
    //   CHA/CHB_PHASE_HI          : [15:0]=phase[47:32]
    //   CHA/CHB_AMP               : [15:0]=signed amplitude
    //   CHA/CHB_DC                : [13:0]=signed DC offset
    //   CHA/CHB_ARB_BANK          : [0]=active arb bank
    //   LUT windows 0x1xxxx-0x4xxxx use sys_wdata[13:0] as the sample value.
    always @(posedge clk_i) begin
        if (!rstn_i) begin
            apply_req_r            <= 1'b0;
            phase_clear_req_r      <= 1'b0;
            cfg_done_latched_r     <= 1'b0;

            dma_arm_req_r          <= 1'b0;
            dma_clear_status_req_r <= 1'b0;
            dma_abort_req_r        <= 1'b0;
            dma_target_channel_r   <= 1'b0;
            dma_target_bank_r      <= 1'b0;
            dma_expected_words_r   <= 32'd0;

            ch_a_enable_shadow_r   <= 1'b0;
            ch_a_wave_shadow_r     <= `RP_DDS_WAVE_SINE;
            ch_a_ftw_shadow_r      <= {PHASE_W{1'b0}};
            ch_a_phase_shadow_r    <= {PHASE_W{1'b0}};
            ch_a_amp_shadow_r      <= 16'sh7FFF;
            ch_a_dc_shadow_r       <= {DATA_W{1'b0}};
            ch_a_arb_bank_shadow_r <= 1'b0;

            ch_b_enable_shadow_r   <= 1'b0;
            ch_b_wave_shadow_r     <= `RP_DDS_WAVE_SINE;
            ch_b_ftw_shadow_r      <= {PHASE_W{1'b0}};
            ch_b_phase_shadow_r    <= {PHASE_W{1'b0}};
            ch_b_amp_shadow_r      <= 16'sh7FFF;
            ch_b_dc_shadow_r       <= {DATA_W{1'b0}};
            ch_b_arb_bank_shadow_r <= 1'b0;

            ch_a_enable_active_r   <= 1'b0;
            ch_a_wave_active_r     <= `RP_DDS_WAVE_SINE;
            ch_a_ftw_active_r      <= {PHASE_W{1'b0}};
            ch_a_phase_active_r    <= {PHASE_W{1'b0}};
            ch_a_amp_active_r      <= 16'sh7FFF;
            ch_a_dc_active_r       <= {DATA_W{1'b0}};
            ch_a_arb_bank_active_r <= 1'b0;

            ch_b_enable_active_r   <= 1'b0;
            ch_b_wave_active_r     <= `RP_DDS_WAVE_SINE;
            ch_b_ftw_active_r      <= {PHASE_W{1'b0}};
            ch_b_phase_active_r    <= {PHASE_W{1'b0}};
            ch_b_amp_active_r      <= 16'sh7FFF;
            ch_b_dc_active_r       <= {DATA_W{1'b0}};
            ch_b_arb_bank_active_r <= 1'b0;

            sys_rdata_r            <= 32'h0000_0000;
            sys_ack_r              <= 1'b0;
            sys_err_r              <= 1'b0;
        end else begin
            sys_ack_r              <= 1'b0;
            sys_err_r              <= 1'b0;
            apply_req_r            <= 1'b0;
            phase_clear_req_r      <= 1'b0;
            dma_arm_req_r          <= 1'b0;
            dma_clear_status_req_r <= 1'b0;
            dma_abort_req_r        <= 1'b0;

            if (cfg_done_pulse_c) begin
                cfg_done_latched_r <= 1'b1;
            end

            // Commit the pending shadow configuration into the live datapath.
            if (cfg_load_pulse_c) begin
                ch_a_enable_active_r   <= ch_a_enable_shadow_r;
                ch_a_wave_active_r     <= ch_a_wave_shadow_r;
                ch_a_ftw_active_r      <= ch_a_ftw_shadow_r;
                ch_a_phase_active_r    <= ch_a_phase_shadow_r;
                ch_a_amp_active_r      <= ch_a_amp_shadow_r;
                ch_a_dc_active_r       <= ch_a_dc_shadow_r;
                ch_a_arb_bank_active_r <= ch_a_arb_bank_shadow_r;

                ch_b_enable_active_r   <= ch_b_enable_shadow_r;
                ch_b_wave_active_r     <= ch_b_wave_shadow_r;
                ch_b_ftw_active_r      <= ch_b_ftw_shadow_r;
                ch_b_phase_active_r    <= ch_b_phase_shadow_r;
                ch_b_amp_active_r      <= ch_b_amp_shadow_r;
                ch_b_dc_active_r       <= ch_b_dc_shadow_r;
                ch_b_arb_bank_active_r <= ch_b_arb_bank_shadow_r;
            end

            if (sys_wen) begin
                sys_ack_r <= 1'b1;

                case (sys_addr_lo_c)
                    `RP_DDS_REG_CONTROL: begin
                        if (sys_wdata[0]) begin
                            apply_req_r        <= 1'b1;
                            cfg_done_latched_r <= 1'b0;
                        end
                        if (sys_wdata[1]) begin
                            phase_clear_req_r  <= 1'b1;
                            cfg_done_latched_r <= 1'b0;
                        end
                    end

                    `RP_DDS_REG_DMA_CONTROL: begin
                        if (sys_wdata[0]) begin
                            dma_arm_req_r <= 1'b1;
                        end
                        if (sys_wdata[1]) begin
                            dma_clear_status_req_r <= 1'b1;
                        end
                        if (sys_wdata[2]) begin
                            dma_abort_req_r <= 1'b1;
                        end
                    end

                    `RP_DDS_REG_DMA_TARGET: begin
                        dma_target_channel_r <= sys_wdata[0];
                        dma_target_bank_r    <= sys_wdata[1];
                    end

                    `RP_DDS_REG_DMA_EXPECTED_WORDS: begin
                        dma_expected_words_r <= sys_wdata;
                    end

                    `RP_DDS_REG_CHA_CTRL: begin
                        ch_a_enable_shadow_r <= sys_wdata[0];
                        ch_a_wave_shadow_r   <= sys_wdata[3:1];
                    end

                    `RP_DDS_REG_CHA_FTW_LO: begin
                        ch_a_ftw_shadow_r[31:0] <= sys_wdata;
                    end

                    `RP_DDS_REG_CHA_FTW_HI: begin
                        ch_a_ftw_shadow_r[47:32] <= sys_wdata[15:0];
                    end

                    `RP_DDS_REG_CHA_PHASE_LO: begin
                        ch_a_phase_shadow_r[31:0] <= sys_wdata;
                    end

                    `RP_DDS_REG_CHA_PHASE_HI: begin
                        ch_a_phase_shadow_r[47:32] <= sys_wdata[15:0];
                    end

                    `RP_DDS_REG_CHA_AMP: begin
                        ch_a_amp_shadow_r <= sys_wdata[15:0];
                    end

                    `RP_DDS_REG_CHA_DC: begin
                        ch_a_dc_shadow_r <= sys_wdata[13:0];
                    end

                    `RP_DDS_REG_CHA_ARB_BANK: begin
                        ch_a_arb_bank_shadow_r <= sys_wdata[0];
                    end

                    `RP_DDS_REG_CHB_CTRL: begin
                        ch_b_enable_shadow_r <= sys_wdata[0];
                        ch_b_wave_shadow_r   <= sys_wdata[3:1];
                    end

                    `RP_DDS_REG_CHB_FTW_LO: begin
                        ch_b_ftw_shadow_r[31:0] <= sys_wdata;
                    end

                    `RP_DDS_REG_CHB_FTW_HI: begin
                        ch_b_ftw_shadow_r[47:32] <= sys_wdata[15:0];
                    end

                    `RP_DDS_REG_CHB_PHASE_LO: begin
                        ch_b_phase_shadow_r[31:0] <= sys_wdata;
                    end

                    `RP_DDS_REG_CHB_PHASE_HI: begin
                        ch_b_phase_shadow_r[47:32] <= sys_wdata[15:0];
                    end

                    `RP_DDS_REG_CHB_AMP: begin
                        ch_b_amp_shadow_r <= sys_wdata[15:0];
                    end

                    `RP_DDS_REG_CHB_DC: begin
                        ch_b_dc_shadow_r <= sys_wdata[13:0];
                    end

                    `RP_DDS_REG_CHB_ARB_BANK: begin
                        ch_b_arb_bank_shadow_r <= sys_wdata[0];
                    end

                    default: begin
                        // LUT windows are write-only. Any other unmapped write
                        // is reported as a bus error.
                        if (sys_lut_window_c && manual_lut_busy_block_c) begin
                            sys_err_r <= 1'b1;
                        end else if (!sys_lut_window_c) begin
                            sys_err_r <= 1'b1;
                        end
                    end
                endcase
            end else if (sys_ren) begin
                sys_ack_r <= 1'b1;

                case (sys_addr_lo_c)
                    `RP_DDS_REG_ID: begin
                        sys_rdata_r <= 32'h4444_5332;
                    end

                    `RP_DDS_REG_VERSION: begin
                        sys_rdata_r <= 32'h0001_0000;
                    end

                    `RP_DDS_REG_CONTROL: begin
                        sys_rdata_r <= {30'h0, phase_clear_req_r, apply_req_r};
                    end

                    `RP_DDS_REG_STATUS: begin
                        sys_rdata_r <= {
                            22'h0,
                            ch_b_enable_active_r,
                            ch_a_enable_active_r,
                            ch_b_arb_bank_active_r,
                            ch_a_arb_bank_active_r,
                            dma_error_c,
                            dma_done_c,
                            dma_busy_c,
                            dma_armed_c,
                            cfg_done_latched_r,
                            cfg_busy_c
                        };
                    end

                    `RP_DDS_REG_SAMPLE_RATE: begin
                        sys_rdata_r <= SAMPLE_RATE_HZ;
                    end

                    `RP_DDS_REG_LUT_LENGTH: begin
                        sys_rdata_r <= (1 << LUT_AW);
                    end

                    `RP_DDS_REG_FEATURES: begin
                        sys_rdata_r <= 32'h0000_0007;
                    end

                    `RP_DDS_REG_DMA_CONTROL: begin
                        sys_rdata_r <= 32'h0000_0000;
                    end

                    `RP_DDS_REG_DMA_TARGET: begin
                        sys_rdata_r <= {30'h0, dma_target_bank_r, dma_target_channel_r};
                    end

                    `RP_DDS_REG_DMA_EXPECTED_WORDS: begin
                        sys_rdata_r <= dma_expected_words_r;
                    end

                    `RP_DDS_REG_DMA_RECEIVED_WORDS: begin
                        sys_rdata_r <= dma_received_words_c;
                    end

                    `RP_DDS_REG_DMA_ERROR_CODE: begin
                        sys_rdata_r <= dma_error_code_c;
                    end

                    `RP_DDS_REG_CHA_CTRL: begin
                        sys_rdata_r <= {28'h0, ch_a_wave_active_r, ch_a_enable_active_r};
                    end

                    `RP_DDS_REG_CHA_FTW_LO: begin
                        sys_rdata_r <= ch_a_ftw_active_r[31:0];
                    end

                    `RP_DDS_REG_CHA_FTW_HI: begin
                        sys_rdata_r <= {16'h0, ch_a_ftw_active_r[47:32]};
                    end

                    `RP_DDS_REG_CHA_PHASE_LO: begin
                        sys_rdata_r <= ch_a_phase_active_r[31:0];
                    end

                    `RP_DDS_REG_CHA_PHASE_HI: begin
                        sys_rdata_r <= {16'h0, ch_a_phase_active_r[47:32]};
                    end

                    `RP_DDS_REG_CHA_AMP: begin
                        sys_rdata_r <= {{16{ch_a_amp_active_r[15]}}, ch_a_amp_active_r};
                    end

                    `RP_DDS_REG_CHA_DC: begin
                        sys_rdata_r <= {{18{ch_a_dc_active_r[13]}}, ch_a_dc_active_r};
                    end

                    `RP_DDS_REG_CHA_ARB_BANK: begin
                        sys_rdata_r <= {31'h0, ch_a_arb_bank_active_r};
                    end

                    `RP_DDS_REG_CHB_CTRL: begin
                        sys_rdata_r <= {28'h0, ch_b_wave_active_r, ch_b_enable_active_r};
                    end

                    `RP_DDS_REG_CHB_FTW_LO: begin
                        sys_rdata_r <= ch_b_ftw_active_r[31:0];
                    end

                    `RP_DDS_REG_CHB_FTW_HI: begin
                        sys_rdata_r <= {16'h0, ch_b_ftw_active_r[47:32]};
                    end

                    `RP_DDS_REG_CHB_PHASE_LO: begin
                        sys_rdata_r <= ch_b_phase_active_r[31:0];
                    end

                    `RP_DDS_REG_CHB_PHASE_HI: begin
                        sys_rdata_r <= {16'h0, ch_b_phase_active_r[47:32]};
                    end

                    `RP_DDS_REG_CHB_AMP: begin
                        sys_rdata_r <= {{16{ch_b_amp_active_r[15]}}, ch_b_amp_active_r};
                    end

                    `RP_DDS_REG_CHB_DC: begin
                        sys_rdata_r <= {{18{ch_b_dc_active_r[13]}}, ch_b_dc_active_r};
                    end

                    `RP_DDS_REG_CHB_ARB_BANK: begin
                        sys_rdata_r <= {31'h0, ch_b_arb_bank_active_r};
                    end

                    default: begin
                        sys_err_r <= 1'b1;
                    end
                endcase
            end
        end
    end

    assign sys_rdata = sys_rdata_r;
    assign sys_ack   = sys_ack_r;
    assign sys_err   = sys_err_r;

endmodule
