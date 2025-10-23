`include "rp_dds_defs.vh"

module rp_dds_cfg_fsm (
    input  clk_i,
    input  rstn_i,
    input  apply_req_i,
    input  phase_clear_req_i,
    output reg busy_o,
    output reg load_cfg_o,
    output reg phase_clear_o,
    output reg done_o
);

    localparam [2:0] ST_IDLE           = 3'd0;
    localparam [2:0] ST_LOAD_CFG       = 3'd1;
    localparam [2:0] ST_LOAD_CFG_CLEAR = 3'd2;
    localparam [2:0] ST_CLEAR_PHASE    = 3'd3;
    localparam [2:0] ST_DONE           = 3'd4;

    reg [2:0] state_r;
    reg [2:0] state_n;

    // ---------------------------------------------------------------------
    // State register process
    // ---------------------------------------------------------------------
    // This process stores the current FSM state.
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
    // This process computes the next FSM state from the current state and
    // request inputs.
    // ---------------------------------------------------------------------
    always @* begin
        state_n = state_r;

        case (state_r)
            ST_IDLE: begin
                if (apply_req_i && phase_clear_req_i) begin
                    state_n = ST_LOAD_CFG_CLEAR;
                end else if (apply_req_i) begin
                    state_n = ST_LOAD_CFG;
                end else if (phase_clear_req_i) begin
                    state_n = ST_CLEAR_PHASE;
                end
            end

            ST_LOAD_CFG: begin
                if (phase_clear_req_i) begin
                    state_n = ST_CLEAR_PHASE;
                end else begin
                    state_n = ST_DONE;
                end
            end

            ST_LOAD_CFG_CLEAR: begin
                state_n = ST_CLEAR_PHASE;
            end

            ST_CLEAR_PHASE: begin
                state_n = ST_DONE;
            end

            ST_DONE: begin
                state_n = ST_IDLE;
            end

            default: begin
                state_n = ST_IDLE;
            end
        endcase
    end

    // ---------------------------------------------------------------------
    // Output decode process
    // ---------------------------------------------------------------------
    // This process decodes the registered state into one-cycle control pulses.
    // ---------------------------------------------------------------------
    always @(*) begin
        busy_o        = 1'b0;
        load_cfg_o    = 1'b0;
        phase_clear_o = 1'b0;
        done_o        = 1'b0;

        case (state_r)
            ST_IDLE: begin
                busy_o = 1'b0;
            end

            ST_LOAD_CFG: begin
                busy_o     = 1'b1;
                load_cfg_o = 1'b1;
            end

            ST_LOAD_CFG_CLEAR: begin
                busy_o     = 1'b1;
                load_cfg_o = 1'b1;
            end

            ST_CLEAR_PHASE: begin
                busy_o        = 1'b1;
                phase_clear_o = 1'b1;
            end

            ST_DONE: begin
                done_o = 1'b1;
            end

            default: begin
                busy_o = 1'b0;
            end
        endcase
    end

endmodule
