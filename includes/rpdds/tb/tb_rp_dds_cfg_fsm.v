`timescale 1ns/1ps
`include "../rtl/rp_dds_defs.vh"

module tb_rp_dds_cfg_fsm;

    reg clk;
    reg rstn;
    reg apply_req;
    reg phase_clear_req;
    wire busy;
    wire load_cfg;
    wire phase_clear;
    wire done;

    task fail;
        input [8*120-1:0] msg_i;
        begin
            $display("ERROR: %0s", msg_i);
            $finish_and_return(1);
        end
    endtask

    rp_dds_cfg_fsm dut (
        .clk_i(clk),
        .rstn_i(rstn),
        .apply_req_i(apply_req),
        .phase_clear_req_i(phase_clear_req),
        .busy_o(busy),
        .load_cfg_o(load_cfg),
        .phase_clear_o(phase_clear),
        .done_o(done)
    );

    always #4 clk = ~clk;

    initial begin
        clk = 1'b0;
        rstn = 1'b0;
        apply_req = 1'b0;
        phase_clear_req = 1'b0;

        repeat (4) @(posedge clk);
        rstn = 1'b1;

        @(posedge clk);
        apply_req = 1'b1;
        @(posedge clk);
        apply_req = 1'b0;

        @(posedge clk);
        if (!load_cfg) begin
            fail("load_cfg pulse was not observed");
        end

        @(posedge clk);
        if (!done) begin
            fail("done pulse was not observed after apply");
        end

        @(posedge clk);
        phase_clear_req = 1'b1;
        @(posedge clk);
        phase_clear_req = 1'b0;

        @(posedge clk);
        if (!phase_clear) begin
            fail("phase_clear pulse was not observed");
        end

        @(posedge clk);
        if (!done) begin
            fail("done pulse was not observed after phase clear");
        end

        @(posedge clk);
        apply_req = 1'b1;
        phase_clear_req = 1'b1;
        @(posedge clk);
        apply_req = 1'b0;
        phase_clear_req = 1'b0;

        @(posedge clk);
        if (!load_cfg || phase_clear) begin
            fail("combined apply+phase_clear did not start with load_cfg only");
        end

        @(posedge clk);
        if (!phase_clear || load_cfg) begin
            fail("combined apply+phase_clear did not emit the delayed phase_clear pulse");
        end

        @(posedge clk);
        if (!done) begin
            fail("done pulse was not observed after combined apply+phase_clear");
        end

        @(posedge clk);
        apply_req = 1'b1;
        @(posedge clk);
        apply_req = 1'b0;
        phase_clear_req = 1'b1;

        @(posedge clk);
        phase_clear_req = 1'b0;
        if (!load_cfg || phase_clear) begin
            fail("apply followed by next-cycle phase_clear did not keep load_cfg in the first cycle");
        end

        @(posedge clk);
        if (!phase_clear || load_cfg) begin
            fail("apply followed by next-cycle phase_clear did not emit phase_clear in ST_LOAD_CFG");
        end

        @(posedge clk);
        if (!done) begin
            fail("done pulse was not observed after staggered apply/phase_clear");
        end

        $display("PASS: rp_dds_cfg_fsm basic and combined-control sequences completed");
        $finish;
    end

endmodule
