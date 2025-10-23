`include "rp_dds_defs.vh"

module red_pitaya_dds #(
    parameter PHASE_W        = `RP_DDS_PHASE_W,
    parameter AMP_W          = `RP_DDS_AMP_W,
    parameter DATA_W         = `RP_DDS_DATA_W,
    parameter LUT_AW         = `RP_DDS_LUT_AW,
    parameter SINE_AW        = `RP_DDS_SINE_AW,
    parameter SAMPLE_RATE_HZ = 125_000_000
) (
    input                          clk_i,
    input                          rstn_i,
    output signed [DATA_W-1:0]     dat_a_o,
    output signed [DATA_W-1:0]     dat_b_o,
    input      [19:0]              sys_addr,
    input      [31:0]              sys_wdata,
    input                          sys_wen,
    input                          sys_ren,
    output     [31:0]              sys_rdata,
    output                         sys_err,
    output                         sys_ack
);

    // ---------------------------------------------------------------------
    // Register storage
    // ---------------------------------------------------------------------
    // Shadow registers are written by the PS. Active registers are copied from
    // the shadow set by the configuration FSM.
    // ---------------------------------------------------------------------
    reg                    apply_req_r;
    reg                    phase_clear_req_r;
    wire                   cfg_busy_c;
    wire                   cfg_load_pulse_c;
    wire                   phase_clear_pulse_c;
    wire                   cfg_done_pulse_c;

    reg                    ch_a_enable_shadow_r;
    reg [2:0]              ch_a_wave_shadow_r;
    reg [PHASE_W-1:0]      ch_a_ftw_shadow_r;
    reg [PHASE_W-1:0]      ch_a_phase_shadow_r;
    reg signed [AMP_W-1:0] ch_a_amp_shadow_r;
    reg signed [DATA_W-1:0] ch_a_dc_shadow_r;

    reg                    ch_b_enable_shadow_r;
    reg [2:0]              ch_b_wave_shadow_r;
    reg [PHASE_W-1:0]      ch_b_ftw_shadow_r;
    reg [PHASE_W-1:0]      ch_b_phase_shadow_r;
    reg signed [AMP_W-1:0] ch_b_amp_shadow_r;
    reg signed [DATA_W-1:0] ch_b_dc_shadow_r;

    reg                    ch_a_enable_active_r;
    reg [2:0]              ch_a_wave_active_r;
    reg [PHASE_W-1:0]      ch_a_ftw_active_r;
    reg [PHASE_W-1:0]      ch_a_phase_active_r;
    reg signed [AMP_W-1:0] ch_a_amp_active_r;
    reg signed [DATA_W-1:0] ch_a_dc_active_r;

    reg                    ch_b_enable_active_r;
    reg [2:0]              ch_b_wave_active_r;
    reg [PHASE_W-1:0]      ch_b_ftw_active_r;
    reg [PHASE_W-1:0]      ch_b_phase_active_r;
    reg signed [AMP_W-1:0] ch_b_amp_active_r;
    reg signed [DATA_W-1:0] ch_b_dc_active_r;

    reg [31:0] sys_rdata_r;
    reg        sys_ack_r;
    reg        sys_err_r;

    // ---------------------------------------------------------------------
    // LUT write path
    // ---------------------------------------------------------------------
    wire                    lut_a_wr_en_c;
    wire [LUT_AW-1:0]       lut_a_wr_addr_c;
    wire signed [DATA_W-1:0] lut_a_wr_data_c;
    wire [LUT_AW-1:0]       lut_a_rd_addr_c;
    wire signed [DATA_W-1:0] lut_a_rd_data_c;

    wire                    lut_b_wr_en_c;
    wire [LUT_AW-1:0]       lut_b_wr_addr_c;
    wire signed [DATA_W-1:0] lut_b_wr_data_c;
    wire [LUT_AW-1:0]       lut_b_rd_addr_c;
    wire signed [DATA_W-1:0] lut_b_rd_data_c;

    // ---------------------------------------------------------------------
    // Sine ROM path
    // ---------------------------------------------------------------------
    wire [SINE_AW-1:0]       sine_a_addr_c;
    wire signed [DATA_W-1:0] sine_a_data_c;
    wire [SINE_AW-1:0]       sine_b_addr_c;
    wire signed [DATA_W-1:0] sine_b_data_c;

    assign lut_a_wr_en_c   = sys_wen && (sys_addr[19:16] == 4'h1);
    assign lut_a_wr_addr_c = sys_addr[15:2];
    assign lut_a_wr_data_c = sys_wdata[13:0];

    assign lut_b_wr_en_c   = sys_wen && (sys_addr[19:16] == 4'h2);
    assign lut_b_wr_addr_c = sys_addr[15:2];
    assign lut_b_wr_data_c = sys_wdata[13:0];

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

    rp_dds_lut_ram #(
        .ADDR_W (LUT_AW),
        .DATA_W (DATA_W)
    ) i_lut_a (
        .clk_i     (clk_i),
        .wr_addr_i (lut_a_wr_addr_c),
        .wr_data_i (lut_a_wr_data_c),
        .wr_en_i   (lut_a_wr_en_c),
        .rd_addr_i (lut_a_rd_addr_c),
        .rd_data_o (lut_a_rd_data_c)
    );

    rp_dds_lut_ram #(
        .ADDR_W (LUT_AW),
        .DATA_W (DATA_W)
    ) i_lut_b (
        .clk_i     (clk_i),
        .wr_addr_i (lut_b_wr_addr_c),
        .wr_data_i (lut_b_wr_data_c),
        .wr_en_i   (lut_b_wr_en_c),
        .rd_addr_i (lut_b_rd_addr_c),
        .rd_data_o (lut_b_rd_data_c)
    );

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
        .arb_sample_i   (lut_a_rd_data_c),
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
        .arb_sample_i   (lut_b_rd_data_c),
        .sine_addr_o    (sine_b_addr_c),
        .sine_sample_i  (sine_b_data_c),
        .data_o         (dat_b_o)
    );

    // ---------------------------------------------------------------------
    // Register write and readback logic
    // ---------------------------------------------------------------------
    // The Red Pitaya system bus is a simple synchronous bus. For this scaffold,
    // the LUT windows are write-only and intended for PS-side bulk transfers.
    // Register reads are supported for the control plane.
    // ---------------------------------------------------------------------
    always @(posedge clk_i or negedge rstn_i) begin
        if (!rstn_i) begin
            apply_req_r          <= 1'b0;
            phase_clear_req_r    <= 1'b0;

            ch_a_enable_shadow_r <= 1'b0;
            ch_a_wave_shadow_r   <= `RP_DDS_WAVE_SINE;
            ch_a_ftw_shadow_r    <= {PHASE_W{1'b0}};
            ch_a_phase_shadow_r  <= {PHASE_W{1'b0}};
            ch_a_amp_shadow_r    <= 16'sh7FFF;
            ch_a_dc_shadow_r     <= {DATA_W{1'b0}};

            ch_b_enable_shadow_r <= 1'b0;
            ch_b_wave_shadow_r   <= `RP_DDS_WAVE_SINE;
            ch_b_ftw_shadow_r    <= {PHASE_W{1'b0}};
            ch_b_phase_shadow_r  <= {PHASE_W{1'b0}};
            ch_b_amp_shadow_r    <= 16'sh7FFF;
            ch_b_dc_shadow_r     <= {DATA_W{1'b0}};

            ch_a_enable_active_r <= 1'b0;
            ch_a_wave_active_r   <= `RP_DDS_WAVE_SINE;
            ch_a_ftw_active_r    <= {PHASE_W{1'b0}};
            ch_a_phase_active_r  <= {PHASE_W{1'b0}};
            ch_a_amp_active_r    <= 16'sh7FFF;
            ch_a_dc_active_r     <= {DATA_W{1'b0}};

            ch_b_enable_active_r <= 1'b0;
            ch_b_wave_active_r   <= `RP_DDS_WAVE_SINE;
            ch_b_ftw_active_r    <= {PHASE_W{1'b0}};
            ch_b_phase_active_r  <= {PHASE_W{1'b0}};
            ch_b_amp_active_r    <= 16'sh7FFF;
            ch_b_dc_active_r     <= {DATA_W{1'b0}};

            sys_rdata_r <= 32'h0000_0000;
            sys_ack_r   <= 1'b0;
            sys_err_r   <= 1'b0;
        end else begin
            sys_ack_r <= 1'b0;
            sys_err_r <= 1'b0;

            if (cfg_load_pulse_c) begin
                ch_a_enable_active_r <= ch_a_enable_shadow_r;
                ch_a_wave_active_r   <= ch_a_wave_shadow_r;
                ch_a_ftw_active_r    <= ch_a_ftw_shadow_r;
                ch_a_phase_active_r  <= ch_a_phase_shadow_r;
                ch_a_amp_active_r    <= ch_a_amp_shadow_r;
                ch_a_dc_active_r     <= ch_a_dc_shadow_r;

                ch_b_enable_active_r <= ch_b_enable_shadow_r;
                ch_b_wave_active_r   <= ch_b_wave_shadow_r;
                ch_b_ftw_active_r    <= ch_b_ftw_shadow_r;
                ch_b_phase_active_r  <= ch_b_phase_shadow_r;
                ch_b_amp_active_r    <= ch_b_amp_shadow_r;
                ch_b_dc_active_r     <= ch_b_dc_shadow_r;

                apply_req_r <= 1'b0;
            end

            if (phase_clear_pulse_c) begin
                phase_clear_req_r <= 1'b0;
            end

            if (sys_wen) begin
                sys_ack_r <= 1'b1;

                case (sys_addr)
                    `RP_DDS_REG_CONTROL: begin
                        if (sys_wdata[0]) begin
                            apply_req_r <= 1'b1;
                        end
                        if (sys_wdata[1]) begin
                            phase_clear_req_r <= 1'b1;
                        end
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

                    default: begin
                        if (!((sys_addr[19:16] == 4'h1) || (sys_addr[19:16] == 4'h2))) begin
                            sys_err_r <= 1'b1;
                        end
                    end
                endcase
            end else if (sys_ren) begin
                sys_ack_r <= 1'b1;

                case (sys_addr)
                    `RP_DDS_REG_ID: begin
                        sys_rdata_r <= 32'h4444_5331;
                    end

                    `RP_DDS_REG_VERSION: begin
                        sys_rdata_r <= 32'h0001_0001;
                    end

                    `RP_DDS_REG_CONTROL: begin
                        sys_rdata_r <= {30'h0, phase_clear_req_r, apply_req_r};
                    end

                    `RP_DDS_REG_STATUS: begin
                        sys_rdata_r <= {
                            30'h0,
                            cfg_done_pulse_c,
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
                        sys_rdata_r <= 32'h0000_0001;
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
