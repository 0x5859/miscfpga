`include "rp_dds_defs.vh"

module rp_dds_channel #(
    parameter PHASE_W = `RP_DDS_PHASE_W,
    parameter AMP_W   = `RP_DDS_AMP_W,
    parameter DATA_W  = `RP_DDS_DATA_W,
    parameter LUT_AW  = `RP_DDS_LUT_AW,
    parameter SINE_AW = `RP_DDS_SINE_AW
) (
    input                           clk_i,
    input                           rstn_i,
    input                           enable_i,
    input       [2:0]               wave_sel_i,
    input       [PHASE_W-1:0]       ftw_i,
    input       [PHASE_W-1:0]       phase_offset_i,
    input signed [AMP_W-1:0]        amplitude_i,
    input signed [DATA_W-1:0]       dc_offset_i,
    input                           phase_clear_i,

    output reg  [LUT_AW-1:0]        arb_addr_o,
    input signed [DATA_W-1:0]       arb_sample_i,

    output reg  [SINE_AW-1:0]       sine_addr_o,
    input signed [DATA_W-1:0]       sine_sample_i,

    output reg signed [DATA_W-1:0]  data_o
);

    localparam MULT_W  = DATA_W + AMP_W + 2;  // 14 + 14 + 2 = 30
    localparam SCALE_W = 18;
    localparam SUM_W   = 19;

    // ---------------------------------------------------------------------
    // Phase accumulator
    // ---------------------------------------------------------------------
    reg [PHASE_W-1:0] phase_acc_r;

    wire [PHASE_W-1:0] phase_acc_next_w;

    assign phase_acc_next_w =
        phase_clear_i ? {PHASE_W{1'b0}} :
        enable_i      ? phase_acc_r + ftw_i :
                        phase_acc_r;

    // ---------------------------------------------------------------------
    // Stage 0: updated phase + configuration snapshot
    // ---------------------------------------------------------------------
    reg [PHASE_W-1:0]       phase_base_s0;
    reg [PHASE_W-1:0]       phase_offset_s0;
    reg [2:0]               wave_sel_s0;
    reg signed [AMP_W-1:0]  amplitude_s0;
    reg signed [DATA_W-1:0] dc_offset_s0;
    reg                     valid_s0;

    // ---------------------------------------------------------------------
    // Stage 1: phase + offset, issue ROM/BRAM addresses
    // ---------------------------------------------------------------------
    reg [PHASE_W-1:0]       phase_word_s1;
    reg [2:0]               wave_sel_s1;
    reg signed [AMP_W-1:0]  amplitude_s1;
    reg signed [DATA_W-1:0] dc_offset_s1;
    reg                     valid_s1;

    wire [PHASE_W-1:0] phase_word_s1_next_w;
    assign phase_word_s1_next_w = phase_base_s0 + phase_offset_s0;
    wire [PHASE_W-1:0] phase_word_hold_w;
    assign phase_word_hold_w = phase_acc_r + phase_offset_i;

    // ---------------------------------------------------------------------
    // Stage 2: waveform sample select
    // ---------------------------------------------------------------------
    reg signed [DATA_W-1:0] raw_sample_s2;
    reg signed [AMP_W-1:0]  amplitude_s2;
    reg signed [DATA_W-1:0] dc_offset_s2;
    reg                     valid_s2;

    wire signed [DATA_W-1:0] saw_sample_s1_w;
    wire signed [DATA_W-1:0] tri_sample_s1_w;

    assign saw_sample_s1_w = $signed(phase_word_s1[PHASE_W-1 -: DATA_W]);
    assign tri_sample_s1_w = tri_from_phase(phase_word_s1);

    // ---------------------------------------------------------------------
    // Stage 3: DSP multiply
    // ---------------------------------------------------------------------
    wire signed [DATA_W:0] raw_sample_s2_ext;
    wire signed [AMP_W:0]  amplitude_s2_ext;
    wire signed [MULT_W-1:0] mult_next_w;

    assign raw_sample_s2_ext = {raw_sample_s2[DATA_W-1], raw_sample_s2};
    assign amplitude_s2_ext  = {amplitude_s2[AMP_W-1], amplitude_s2};
    assign mult_next_w       = raw_sample_s2_ext * amplitude_s2_ext;

    (* use_dsp = "yes" *) reg signed [MULT_W-1:0] mult_full_s3;
    reg signed [DATA_W-1:0] dc_offset_s3;
    reg                     valid_s3;

    // ---------------------------------------------------------------------
    // Stage 4: scale product
    // ---------------------------------------------------------------------
    reg signed [SCALE_W-1:0] scaled_sample_s4;
    reg signed [DATA_W-1:0]  dc_offset_s4;
    reg                      valid_s4;

    // ---------------------------------------------------------------------
    // Stage 5: add DC offset
    // ---------------------------------------------------------------------
    reg signed [SUM_W-1:0] summed_sample_s5;
    reg                    valid_s5;

    // ---------------------------------------------------------------------
    // Saturation helpers
    // ---------------------------------------------------------------------
    function signed [DATA_W-1:0] sat14_from19;
        input signed [18:0] value_i;
        begin
            if (value_i > 19'sd8191) begin
                sat14_from19 = 14'sd8191;
            end else if (value_i < -19'sd8192) begin
                sat14_from19 = -14'sd8192;
            end else begin
                sat14_from19 = value_i[DATA_W-1:0];
            end
        end
    endfunction

    function signed [DATA_W-1:0] sat14_from15;
        input signed [14:0] value_i;
        begin
            if (value_i > 15'sd8191) begin
                sat14_from15 = 14'sd8191;
            end else if (value_i < -15'sd8192) begin
                sat14_from15 = -14'sd8192;
            end else begin
                sat14_from15 = value_i[DATA_W-1:0];
            end
        end
    endfunction

    function signed [DATA_W-1:0] tri_from_phase;
        input [PHASE_W-1:0] phase_i;
        reg [12:0] tri_ramp;
        reg signed [14:0] tri_tmp;
        begin
            tri_ramp = phase_i[PHASE_W-2 -: 13];

            if (!phase_i[PHASE_W-1]) begin
                tri_tmp = -15'sd8192 + $signed({1'b0, tri_ramp, 1'b0});
            end else begin
                tri_tmp = 15'sd8191 - $signed({1'b0, tri_ramp, 1'b0});
            end

            tri_from_phase = sat14_from15(tri_tmp);
        end
    endfunction

    // ---------------------------------------------------------------------
    // Main pipeline
    // ---------------------------------------------------------------------
    always @(posedge clk_i) begin
        if (!rstn_i) begin
            phase_acc_r      <= {PHASE_W{1'b0}};

            phase_base_s0    <= {PHASE_W{1'b0}};
            phase_offset_s0  <= {PHASE_W{1'b0}};
            wave_sel_s0      <= 3'd0;
            amplitude_s0     <= {AMP_W{1'b0}};
            dc_offset_s0     <= {DATA_W{1'b0}};
            valid_s0         <= 1'b0;

            phase_word_s1    <= {PHASE_W{1'b0}};
            arb_addr_o       <= {LUT_AW{1'b0}};
            sine_addr_o      <= {SINE_AW{1'b0}};
            wave_sel_s1      <= 3'd0;
            amplitude_s1     <= {AMP_W{1'b0}};
            dc_offset_s1     <= {DATA_W{1'b0}};
            valid_s1         <= 1'b0;

            raw_sample_s2    <= {DATA_W{1'b0}};
            amplitude_s2     <= {AMP_W{1'b0}};
            dc_offset_s2     <= {DATA_W{1'b0}};
            valid_s2         <= 1'b0;

            mult_full_s3     <= {MULT_W{1'b0}};
            dc_offset_s3     <= {DATA_W{1'b0}};
            valid_s3         <= 1'b0;

            scaled_sample_s4 <= {SCALE_W{1'b0}};
            dc_offset_s4     <= {DATA_W{1'b0}};
            valid_s4         <= 1'b0;

            summed_sample_s5 <= {SUM_W{1'b0}};
            valid_s5         <= 1'b0;

            data_o           <= {DATA_W{1'b0}};

        end else if (phase_clear_i) begin
            // Synchronous clear. This also flushes the pipeline so stale
            // pre-clear samples do not appear after phase reset.
            phase_acc_r      <= {PHASE_W{1'b0}};

            phase_base_s0    <= {PHASE_W{1'b0}};
            phase_offset_s0  <= phase_offset_i;
            wave_sel_s0      <= wave_sel_i;
            amplitude_s0     <= amplitude_i;
            dc_offset_s0     <= dc_offset_i;
            valid_s0         <= 1'b0;

            phase_word_s1    <= phase_offset_i;
            arb_addr_o       <= phase_offset_i[PHASE_W-1 -: LUT_AW];
            sine_addr_o      <= phase_offset_i[PHASE_W-1 -: SINE_AW];
            wave_sel_s1      <= wave_sel_i;
            amplitude_s1     <= amplitude_i;
            dc_offset_s1     <= dc_offset_i;
            valid_s1         <= 1'b0;

            raw_sample_s2    <= {DATA_W{1'b0}};
            amplitude_s2     <= {AMP_W{1'b0}};
            dc_offset_s2     <= {DATA_W{1'b0}};
            valid_s2         <= 1'b0;

            mult_full_s3     <= {MULT_W{1'b0}};
            dc_offset_s3     <= {DATA_W{1'b0}};
            valid_s3         <= 1'b0;

            scaled_sample_s4 <= {SCALE_W{1'b0}};
            dc_offset_s4     <= {DATA_W{1'b0}};
            valid_s4         <= 1'b0;

            summed_sample_s5 <= {SUM_W{1'b0}};
            valid_s5         <= 1'b0;

            data_o           <= {DATA_W{1'b0}};

        end else if (!enable_i) begin
            // Hold phase while disabled, but flush the pipeline so mute takes
            // effect immediately and stale samples cannot leak on re-enable.
            phase_acc_r      <= phase_acc_r;

            phase_base_s0    <= phase_acc_r;
            phase_offset_s0  <= phase_offset_i;
            wave_sel_s0      <= wave_sel_i;
            amplitude_s0     <= amplitude_i;
            dc_offset_s0     <= dc_offset_i;
            valid_s0         <= 1'b0;

            phase_word_s1    <= phase_word_hold_w;
            arb_addr_o       <= phase_word_hold_w[PHASE_W-1 -: LUT_AW];
            sine_addr_o      <= phase_word_hold_w[PHASE_W-1 -: SINE_AW];
            wave_sel_s1      <= wave_sel_i;
            amplitude_s1     <= amplitude_i;
            dc_offset_s1     <= dc_offset_i;
            valid_s1         <= 1'b0;

            raw_sample_s2    <= {DATA_W{1'b0}};
            amplitude_s2     <= {AMP_W{1'b0}};
            dc_offset_s2     <= {DATA_W{1'b0}};
            valid_s2         <= 1'b0;

            mult_full_s3     <= {MULT_W{1'b0}};
            dc_offset_s3     <= {DATA_W{1'b0}};
            valid_s3         <= 1'b0;

            scaled_sample_s4 <= {SCALE_W{1'b0}};
            dc_offset_s4     <= {DATA_W{1'b0}};
            valid_s4         <= 1'b0;

            summed_sample_s5 <= {SUM_W{1'b0}};
            valid_s5         <= 1'b0;

            data_o           <= {DATA_W{1'b0}};

        end else begin
            // -------------------------------------------------------------
            // S0: phase accumulator + config snapshot
            // -------------------------------------------------------------
            phase_acc_r      <= phase_acc_next_w;
            phase_base_s0    <= phase_acc_next_w;
            phase_offset_s0  <= phase_offset_i;
            wave_sel_s0      <= wave_sel_i;
            amplitude_s0     <= amplitude_i;
            dc_offset_s0     <= dc_offset_i;
            valid_s0         <= enable_i;

            // -------------------------------------------------------------
            // S1: phase + offset, issue addresses
            // -------------------------------------------------------------
            phase_word_s1    <= phase_word_s1_next_w;
            arb_addr_o       <= phase_word_s1_next_w[PHASE_W-1 -: LUT_AW];
            sine_addr_o      <= phase_word_s1_next_w[PHASE_W-1 -: SINE_AW];

            wave_sel_s1      <= wave_sel_s0;
            amplitude_s1     <= amplitude_s0;
            dc_offset_s1     <= dc_offset_s0;
            valid_s1         <= valid_s0;

            // -------------------------------------------------------------
            // S2: sample select.
            //
            // This assumes sine_sample_i / arb_sample_i correspond to the
            // addresses issued in S1 on the previous clock.
            // -------------------------------------------------------------
            case (wave_sel_s1)
                `RP_DDS_WAVE_SINE: begin
                    raw_sample_s2 <= sine_sample_i;
                end

                `RP_DDS_WAVE_SQUARE: begin
                    raw_sample_s2 <= phase_word_s1[PHASE_W-1] ? -14'sd8192 : 14'sd8191;
                end

                `RP_DDS_WAVE_TRIANGLE: begin
                    raw_sample_s2 <= tri_sample_s1_w;
                end

                `RP_DDS_WAVE_SAW: begin
                    raw_sample_s2 <= saw_sample_s1_w;
                end

                `RP_DDS_WAVE_ARB: begin
                    raw_sample_s2 <= arb_sample_i;
                end

                default: begin
                    raw_sample_s2 <= {DATA_W{1'b0}};
                end
            endcase

            amplitude_s2 <= amplitude_s1;
            dc_offset_s2 <= dc_offset_s1;
            valid_s2     <= valid_s1;

            // -------------------------------------------------------------
            // S3: multiply.
            // Registered operands + registered product give Vivado a clean
            // DSP inference opportunity.
            // -------------------------------------------------------------
            mult_full_s3 <= mult_next_w;
            dc_offset_s3 <= dc_offset_s2;
            valid_s3     <= valid_s2;

            // -------------------------------------------------------------
            // S4: scale product.
            // -------------------------------------------------------------
            scaled_sample_s4 <= mult_full_s3 >>> 15;
            dc_offset_s4     <= dc_offset_s3;
            valid_s4         <= valid_s3;

            // -------------------------------------------------------------
            // S5: add DC offset.
            // Explicit sign extension avoids accidental RHS truncation.
            // -------------------------------------------------------------
            summed_sample_s5 <=
                {{(SUM_W-SCALE_W){scaled_sample_s4[SCALE_W-1]}}, scaled_sample_s4} +
                {{(SUM_W-DATA_W ){dc_offset_s4[DATA_W-1]}}, dc_offset_s4};

            valid_s5 <= valid_s4;

            // -------------------------------------------------------------
            // S6: saturation + registered output.
            // -------------------------------------------------------------
            if (valid_s5) begin
                data_o <= sat14_from19(summed_sample_s5);
            end else begin
                data_o <= {DATA_W{1'b0}};
            end
        end
    end

endmodule
