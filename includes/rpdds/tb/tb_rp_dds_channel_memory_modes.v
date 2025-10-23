`timescale 1ns/1ps
`include "../rtl/rp_dds_defs.vh"

module tb_rp_dds_channel_memory_modes;

    reg clk;
    reg rstn;
    reg enable;
    reg [2:0] wave_sel;
    reg [`RP_DDS_PHASE_W-1:0] ftw;
    reg [`RP_DDS_PHASE_W-1:0] phase_offset;
    reg signed [`RP_DDS_AMP_W-1:0] amplitude;
    reg signed [`RP_DDS_DATA_W-1:0] dc_offset;
    reg phase_clear;

    wire [`RP_DDS_LUT_AW-1:0] arb_addr;
    reg signed [`RP_DDS_DATA_W-1:0] arb_sample;
    wire [`RP_DDS_SINE_AW-1:0] sine_addr;
    reg signed [`RP_DDS_DATA_W-1:0] sine_sample;
    wire signed [`RP_DDS_DATA_W-1:0] data_o;

    localparam [`RP_DDS_PHASE_W-1:0] PHASE_OFFSET_ARB = {14'h0055, 34'd0};
    localparam signed [`RP_DDS_DATA_W-1:0] ZERO_DATA = {`RP_DDS_DATA_W{1'b0}};

    function signed [`RP_DDS_DATA_W-1:0] arb_model;
        input [`RP_DDS_LUT_AW-1:0] addr_i;
        begin
            arb_model = $signed({1'b0, addr_i[7:0]}) - 14'sd64;
        end
    endfunction

    function signed [`RP_DDS_DATA_W-1:0] sine_model;
        input [`RP_DDS_SINE_AW-1:0] addr_i;
        begin
            sine_model = $signed({1'b0, addr_i[7:0]}) - 14'sd32;
        end
    endfunction

    function signed [`RP_DDS_DATA_W-1:0] scaled_model;
        input signed [`RP_DDS_DATA_W-1:0] sample_i;
        begin
            scaled_model = sample_i >>> 1;
        end
    endfunction

    task fail;
        input [8*160-1:0] msg_i;
        begin
            $display("ERROR: %0s", msg_i);
            $finish_and_return(1);
        end
    endtask

    rp_dds_channel dut (
        .clk_i(clk),
        .rstn_i(rstn),
        .enable_i(enable),
        .wave_sel_i(wave_sel),
        .ftw_i(ftw),
        .phase_offset_i(phase_offset),
        .amplitude_i(amplitude),
        .dc_offset_i(dc_offset),
        .phase_clear_i(phase_clear),
        .arb_addr_o(arb_addr),
        .arb_sample_i(arb_sample),
        .sine_addr_o(sine_addr),
        .sine_sample_i(sine_sample),
        .data_o(data_o)
    );

    always #1 clk = ~clk;

    // Simple synchronous models that mimic the one-cycle ROM/BRAM return path.
    always @(posedge clk) begin
        arb_sample  <= arb_model(arb_addr);
        sine_sample <= sine_model(sine_addr);
    end

    initial begin
        clk          = 1'b0;
        rstn         = 1'b0;
        enable       = 1'b0;
        wave_sel     = `RP_DDS_WAVE_ARB;
        ftw          = {`RP_DDS_PHASE_W{1'b0}};
        phase_offset = PHASE_OFFSET_ARB;
        amplitude    = 16'sd16384;
        dc_offset    = ZERO_DATA;
        phase_clear  = 1'b0;
        arb_sample   = ZERO_DATA;
        sine_sample  = ZERO_DATA;

        repeat (4) @(posedge clk);
        rstn   = 1'b1;
        enable = 1'b1;

        repeat (10) @(posedge clk);

        if (data_o != scaled_model(arb_model(PHASE_OFFSET_ARB[`RP_DDS_PHASE_W-1 -: `RP_DDS_LUT_AW]))) begin
            fail("arb mode did not settle to the expected memory-backed sample");
        end

        @(posedge clk);
        phase_clear = 1'b1;
        @(posedge clk);
        phase_clear = 1'b0;

        repeat (2) @(posedge clk);
        if (data_o != ZERO_DATA) begin
            fail("arb mode did not flush to zero after phase_clear");
        end

        repeat (10) @(posedge clk);
        if (data_o != scaled_model(arb_model(PHASE_OFFSET_ARB[`RP_DDS_PHASE_W-1 -: `RP_DDS_LUT_AW]))) begin
            fail("arb mode did not restart from the expected sample after phase_clear");
        end

        wave_sel = `RP_DDS_WAVE_SINE;
        @(posedge clk);
        phase_clear = 1'b1;
        @(posedge clk);
        phase_clear = 1'b0;

        repeat (2) @(posedge clk);
        if (data_o != ZERO_DATA) begin
            fail("sine mode did not flush to zero after phase_clear");
        end

        repeat (10) @(posedge clk);
        if (data_o != scaled_model(sine_model(PHASE_OFFSET_ARB[`RP_DDS_PHASE_W-1 -: `RP_DDS_SINE_AW]))) begin
            fail("sine mode did not settle to the expected ROM-backed sample");
        end

        $display("PASS: rp_dds_channel memory-backed modes completed");
        $finish;
    end

endmodule
