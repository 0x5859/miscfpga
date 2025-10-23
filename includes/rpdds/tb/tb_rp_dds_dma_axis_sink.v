`timescale 1ns/1ps
`include "../rtl/rp_dds_defs.vh"

module tb_rp_dds_dma_axis_sink;

    reg clk;
    reg rstn;
    reg arm;
    reg clear_status;
    reg abort_req;
    reg target_channel;
    reg target_bank;
    reg [31:0] expected_words;
    reg [31:0] tdata;
    reg [3:0] tkeep;
    reg tvalid;
    wire tready;
    reg tlast;
    wire wr_en;
    wire wr_channel;
    wire wr_bank;
    wire [13:0] wr_addr;
    wire signed [13:0] wr_data;
    wire armed;
    wire busy;
    wire done;
    wire error;
    wire [31:0] received_words;
    wire [31:0] error_code;

    task fail;
        input [8*120-1:0] msg_i;
        begin
            $display("ERROR: %0s", msg_i);
            $finish_and_return(1);
        end
    endtask

    rp_dds_dma_axis_sink dut (
        .clk_i(clk),
        .rstn_i(rstn),
        .arm_i(arm),
        .clear_status_i(clear_status),
        .abort_i(abort_req),
        .target_channel_i(target_channel),
        .target_bank_i(target_bank),
        .expected_words_i(expected_words),
        // PL slave recieves data from the DMA master side
        // m_axis_mm2s_tdata -> s_axis_mm2s_tdata_i
        // m_axis_mm2s_tkeep -> s_axis_mm2s_tkeep_i
        // m_axis_mm2s_tvalid -> s_axis_mm2s_tvalid_i
        // m_axis_mm2s_tlast -> s_axis_mm2s_tlast_i

        // DMA master: tdata, tkeep, tvalid, tlast
        // PL slave : tready
        .s_axis_tdata_i(tdata),
        .s_axis_tkeep_i(tkeep),
        .s_axis_tvalid_i(tvalid),
        .s_axis_tready_o(tready),
        .s_axis_tlast_i(tlast),
        .wr_en_o(wr_en),
        .wr_channel_o(wr_channel),
        .wr_bank_o(wr_bank),
        .wr_addr_o(wr_addr),
        .wr_data_o(wr_data),
        .armed_o(armed),
        .busy_o(busy),
        .done_o(done),
        .error_o(error),
        .received_words_o(received_words),
        .error_code_o(error_code)
    );

    always #1 clk = ~clk;

    task send_word;
        input [31:0] word_i;
        input last_i;
        begin
            @(posedge clk);
            tdata  = word_i;
            tvalid = 1'b1;
            tlast  = last_i;
            while (!tready) begin
                @(posedge clk);
            end
            @(posedge clk);
            tvalid = 1'b0;
            tlast  = 1'b0;
        end
    endtask
    initial begin
        $dumpfile("tb_rp_dds_dma_axis_sink.vcd");
        $dumpvars(0, tb_rp_dds_dma_axis_sink);
    end

    initial begin
        clk = 1'b0;
        rstn = 1'b0;
        arm = 1'b0;
        clear_status = 1'b0;
        abort_req = 1'b0;
        target_channel = 1'b0;
        target_bank = 1'b1;
        expected_words = 32'd4;
        tdata = 32'd0;
        tkeep = 4'hF;
        tvalid = 1'b0;
        tlast = 1'b0;

        repeat (4) @(posedge clk);
        rstn = 1'b1;

        @(posedge clk);
        arm = 1'b1;
        @(posedge clk);
        arm = 1'b0;

        send_word(32'h0000_0001, 1'b0);
        send_word(32'h0000_0002, 1'b0);
        send_word(32'h0000_0003, 1'b0);
        send_word(32'h0000_0004, 1'b1);

        @(posedge clk);
        if (!done) begin
            fail("DMA sink did not complete successfully");
        end

        if (received_words != 32'd4) begin
            fail("DMA sink received_words mismatch");
        end

        @(posedge clk);
        clear_status = 1'b1;
        @(posedge clk);
        clear_status = 1'b0;

        @(posedge clk);
        if (done || error) begin
            fail("status clear did not return sink to idle");
        end

        $display("PASS: rp_dds_dma_axis_sink basic stream sequence completed");
        $finish;
    end

endmodule
