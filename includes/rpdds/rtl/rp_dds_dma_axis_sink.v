`include "rp_dds_defs.vh"

module rp_dds_dma_axis_sink #(
    parameter ADDR_W = `RP_DDS_LUT_AW,
    parameter DATA_W = `RP_DDS_DATA_W
) (
    input                           clk_i,
    input                           rstn_i,
    input                           arm_i,
    input                           clear_status_i,
    input                           abort_i,
    input                           target_channel_i, // This signal may can be directly connected to `wr_channel_o`
    input                           target_bank_i,
    input      [31:0]               expected_words_i,
    input      [31:0]               s_axis_tdata_i,
    input      [3:0]                s_axis_tkeep_i,
    input                           s_axis_tvalid_i,
    output reg                      s_axis_tready_o,
    input                           s_axis_tlast_i,
    output reg                      wr_en_o,
    output reg                      wr_channel_o,
    output reg                      wr_bank_o,
    output reg [ADDR_W-1:0]         wr_addr_o,
    output reg signed [DATA_W-1:0]  wr_data_o,
    output reg                      armed_o,
    output reg                      busy_o,
    output reg                      done_o,
    output reg                      error_o,
    output     [31:0]               received_words_o,
    output     [31:0]               error_code_o
);

    localparam [2:0] ST_IDLE    = 3'd0;
    localparam [2:0] ST_ARMED   = 3'd1;
    localparam [2:0] ST_RECEIVE = 3'd2;
    localparam [2:0] ST_DONE    = 3'd3;
    localparam [2:0] ST_ERROR   = 3'd4;

    reg [2:0] state_r;
    reg [2:0] state_n;

    reg        target_channel_r;
    reg        target_bank_r;
    reg [31:0] expected_words_r; // The total number of beats expected for the current transfer.
    reg [31:0] received_words_r; // Counts the number of accepted beats for the current transfer.
    reg [31:0] error_code_r;

    wire beat_accept_c;
    wire zero_length_arm_c;
    wire is_last_expected_c;
    wire overrun_c;
    wire early_tlast_c;
    wire missing_tlast_c;
    wire abort_hit_c;
    wire error_hit_c;

    assign beat_accept_c      = s_axis_tvalid_i && s_axis_tready_o; // One AXIS beat is accepted on this cycle.
    assign zero_length_arm_c  = arm_i && (expected_words_i == 32'd0); // Arming with zero expected words is invalid.
    assign is_last_expected_c = (received_words_r + 32'd1) == expected_words_r; // The next accepted beat should be the final one.
    assign overrun_c          = beat_accept_c && (received_words_r >= expected_words_r); // More beats arrived than declared by expected_words.
    assign early_tlast_c      = beat_accept_c && s_axis_tlast_i && !is_last_expected_c; // TLAST arrived before the final expected beat.
    assign missing_tlast_c    = beat_accept_c && !s_axis_tlast_i && is_last_expected_c; // Final expected beat arrived without TLAST.
    assign abort_hit_c        = abort_i && ((state_r == ST_ARMED) || (state_r == ST_RECEIVE)); // Abort is only meaningful while a transfer is pending.
    assign error_hit_c        = overrun_c || early_tlast_c || missing_tlast_c; // Protocol framing errors detected on the accepted beat.

    // ---------------------------------------------------------------------
    // State register process
    // ---------------------------------------------------------------------
    // This process stores the current loader state.
    // ---------------------------------------------------------------------
    always @(posedge clk_i) begin
        if (!rstn_i) begin
            state_r <= ST_IDLE;
        end else begin
            state_r <= state_n;
        end
    end

    // ---------------------------------------------------------------------
    // Next-state process
    // ---------------------------------------------------------------------
    // This process computes the next loader state from the current state and
    // the AXI4-Stream handshake results.
    // ---------------------------------------------------------------------
    always @* begin
        state_n = state_r;

        case (state_r)
            ST_IDLE: begin
                if (zero_length_arm_c) begin
                    state_n = ST_ERROR;
                end else if (arm_i) begin
                    state_n = ST_ARMED;
                end
            end

            ST_ARMED: begin
                if (abort_hit_c) begin
                    state_n = ST_ERROR;
                end else if (beat_accept_c && error_hit_c) begin
                    state_n = ST_ERROR;
                end else if (beat_accept_c && is_last_expected_c && s_axis_tlast_i) begin
                    state_n = ST_DONE;
                end else if (beat_accept_c) begin
                    state_n = ST_RECEIVE;
                end
            end

            ST_RECEIVE: begin
                if (abort_hit_c) begin
                    state_n = ST_ERROR;
                end else if (beat_accept_c && error_hit_c) begin
                    state_n = ST_ERROR;
                end else if (beat_accept_c && is_last_expected_c && s_axis_tlast_i) begin
                    state_n = ST_DONE;
                end
            end

            ST_DONE: begin
                if (clear_status_i) begin
                    state_n = ST_IDLE;
                end
            end

            ST_ERROR: begin
                if (clear_status_i) begin
                    state_n = ST_IDLE;
                end
            end

            default: begin
                state_n = ST_IDLE;
            end
        endcase
    end

    // ---------------------------------------------------------------------
    // Output decode process
    // ---------------------------------------------------------------------
    // This process decodes the registered state into control outputs.
    // ---------------------------------------------------------------------
    always @* begin
        s_axis_tready_o = 1'b0;
        wr_en_o         = 1'b0;
        wr_channel_o    = target_channel_r;
        wr_bank_o       = target_bank_r;
        wr_addr_o       = received_words_r[ADDR_W-1:0];
        wr_data_o       = s_axis_tdata_i[DATA_W-1:0];

        armed_o = 1'b0;
        busy_o  = 1'b0;
        done_o  = 1'b0;
        error_o = 1'b0;

        case (state_r)
            ST_IDLE: begin
                armed_o = 1'b0;
            end

            ST_ARMED: begin
                armed_o        = 1'b1;
                busy_o         = 1'b1;
                s_axis_tready_o = 1'b1;
                wr_en_o        = beat_accept_c && !error_hit_c;
            end

            ST_RECEIVE: begin
                busy_o          = 1'b1;
                s_axis_tready_o = 1'b1;
                wr_en_o         = beat_accept_c && !error_hit_c;
            end

            ST_DONE: begin
                done_o = 1'b1;
            end

            ST_ERROR: begin
                error_o = 1'b1;
            end

            default: begin
                armed_o = 1'b0;
            end
        endcase
    end

    // ---------------------------------------------------------------------
    // Data and status register process
    // ---------------------------------------------------------------------
    // This process stores the target metadata, word counter and error code.
    // ---------------------------------------------------------------------
    always @(posedge clk_i) begin
        if (!rstn_i) begin
            target_channel_r <= 1'b0;
            target_bank_r    <= 1'b0;
            expected_words_r <= 32'd0;
            received_words_r <= 32'd0;
            error_code_r     <= `RP_DDS_DMA_ERR_NONE;
        end else begin
            // Clear the status when receiving the clear command while the FSM is not in tranmitting state.
            if (clear_status_i && ((state_r == ST_DONE) || (state_r == ST_ERROR))) begin
                expected_words_r <= 32'd0;
                received_words_r <= 32'd0;
                error_code_r     <= `RP_DDS_DMA_ERR_NONE;
            end

            if (state_r == ST_IDLE) begin
                if (arm_i) begin
                    target_channel_r <= target_channel_i;
                    target_bank_r    <= target_bank_i;
                    expected_words_r <= expected_words_i;
                    received_words_r <= 32'd0;
                    error_code_r     <= `RP_DDS_DMA_ERR_NONE;
                end
            end else if ((state_r == ST_ARMED) || (state_r == ST_RECEIVE)) begin
                if (abort_hit_c) begin
                    error_code_r <= `RP_DDS_DMA_ERR_ABORT;
                end else if (beat_accept_c && overrun_c) begin
                    error_code_r <= `RP_DDS_DMA_ERR_OVERRUN;
                end else if (beat_accept_c && early_tlast_c) begin
                    error_code_r <= `RP_DDS_DMA_ERR_EARLY_TLAST;
                end else if (beat_accept_c && missing_tlast_c) begin
                    error_code_r <= `RP_DDS_DMA_ERR_MISSING_TLAST;
                end else if (beat_accept_c) begin
                    received_words_r <= received_words_r + 32'd1;
                end
            end

            if (zero_length_arm_c) begin
                error_code_r <= `RP_DDS_DMA_ERR_EXPECT_ZERO;
            end
        end
    end

    // This sink assumes full-word samples from the AXI DMA MM2S channel.
    // TKEEP is intentionally ignored because the PS always submits aligned
    // 32-bit words and MM2S_LENGTH is programmed as 4 * sample_count bytes.
    wire unused_tkeep;
    assign unused_tkeep = &s_axis_tkeep_i;

    assign received_words_o = received_words_r;
    assign error_code_o     = error_code_r;

endmodule
