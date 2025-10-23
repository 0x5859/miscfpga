`timescale 1ns/1ps
`include "../rtl/rp_dds_defs.vh"

module tb_red_pitaya_dds_axi_dma_smoke;

    reg clk;
    reg rstn;
    wire signed [13:0] dat_a;
    wire signed [13:0] dat_b;
    reg [31:0] sys_addr;
    reg [31:0] sys_wdata;
    reg sys_wen;
    reg sys_ren;
    wire [31:0] sys_rdata;
    wire sys_err;
    wire sys_ack;
    reg [31:0] tdata;
    reg [3:0]  tkeep;
    reg        tvalid;
    wire       tready;
    reg        tlast;

    task fail;
        input [8*160-1:0] msg_i;
        begin
            $display("ERROR: %0s", msg_i);
            $finish_and_return(1);
        end
    endtask

    red_pitaya_dds_axi_dma dut (
        .clk_i(clk),
        .rstn_i(rstn),
        .dat_a_o(dat_a),
        .dat_b_o(dat_b),
        .sys_addr(sys_addr),
        .sys_wdata(sys_wdata),
        .sys_wen(sys_wen),
        .sys_ren(sys_ren),
        .sys_rdata(sys_rdata),
        .sys_err(sys_err),
        .sys_ack(sys_ack),
        .s_axis_mm2s_tdata_i(tdata),
        .s_axis_mm2s_tkeep_i(tkeep),
        .s_axis_mm2s_tvalid_i(tvalid),
        .s_axis_mm2s_tready_o(tready),
        .s_axis_mm2s_tlast_i(tlast)
    );

    always #1 clk = ~clk;

    initial begin
        $dumpfile("tb_red_pitaya_dds_axi_dma_smoke.vcd");
        $dumpvars(0, tb_red_pitaya_dds_axi_dma_smoke);
    end

    task bus_write;
        input [31:0] addr_i;
        input [31:0] data_i;
        begin
            @(posedge clk);
            sys_addr  = addr_i;
            sys_wdata = data_i;
            sys_wen   = 1'b1;
            @(posedge clk);
            sys_wen   = 1'b0;
        end
    endtask

    initial begin
        clk = 1'b0;
        rstn = 1'b0;
        sys_addr = 32'h0;
        sys_wdata = 32'h0;
        sys_wen = 1'b0;
        sys_ren = 1'b0;
        tdata = 32'h0;
        tkeep = 4'hF;
        tvalid = 1'b0;
        tlast = 1'b0;

        repeat (4) @(posedge clk);
        rstn = 1'b1;

        bus_write(`RP_DDS_REG_CHA_FTW_LO, 32'h0000_0000);
        bus_write(`RP_DDS_REG_CHA_FTW_HI, 32'h0000_0800);
        bus_write(`RP_DDS_REG_CHA_AMP, 32'h0000_7FFF);
        bus_write(`RP_DDS_REG_CHA_CTRL, {28'h0, `RP_DDS_WAVE_SAW, 1'b1});
        bus_write(`RP_DDS_REG_CONTROL, 32'h0000_0001);

        repeat (64) @(posedge clk);

        if (^dat_a === 1'bx) begin
            fail("channel A output is unknown after initial apply");
        end

        if (dat_a === 14'sd0) begin
            fail("channel A output did not move away from zero after apply");
        end

        bus_write(`RP_DDS_REG_CHA_CTRL, {28'h0, `RP_DDS_WAVE_SAW, 1'b0});
        bus_write(`RP_DDS_REG_CONTROL, 32'h0000_0001);

        repeat (8) @(posedge clk);

        if (^dat_a === 1'bx) begin
            fail("channel A output became unknown after disable apply");
        end

        if (dat_a != 14'sd0) begin
            fail("channel A output did not mute after disable apply");
        end

        bus_write(`RP_DDS_REG_CHA_CTRL, {28'h0, `RP_DDS_WAVE_SAW, 1'b1});
        bus_write(`RP_DDS_REG_CONTROL, 32'h0000_0001);

        repeat (64) @(posedge clk);

        if (dat_a === 14'sd0) begin
            fail("channel A output did not resume after re-enable apply");
        end

        bus_write(`RP_DDS_REG_CONTROL, 32'h0000_0003);

        repeat (8) @(posedge clk);

        if (^dat_a === 1'bx) begin
            fail("channel A output became unknown after apply+phase_clear");
        end

        if (dat_a != 14'sd0) begin
            fail("channel A output did not mute after apply+phase_clear");
        end

        repeat (2000) @(posedge clk);

        if (dat_a === 14'sd0) begin
            fail("channel A output did not restart after apply+phase_clear");
        end

        $display("PASS: red_pitaya_dds_axi_dma smoke sequence completed");
        $finish;
    end

endmodule
