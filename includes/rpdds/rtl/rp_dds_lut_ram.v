`include "rp_dds_defs.vh"

module rp_dds_lut_ram #(
    parameter ADDR_W = `RP_DDS_LUT_AW,
    parameter DATA_W = `RP_DDS_DATA_W
) (
    input                           clk_i,
    input      [ADDR_W-1:0]         wr_addr_i,
    input signed [DATA_W-1:0]       wr_data_i,
    input                           wr_en_i,
    input      [ADDR_W-1:0]         rd_addr_i,
    output reg signed [DATA_W-1:0]  rd_data_o
);

    // ---------------------------------------------------------------------
    // Dual-port memory
    // ---------------------------------------------------------------------
    // Port A is used for writes from either the debug window or the DMA loader.
    // Port B is used for reads from the DDS datapath.
    // ---------------------------------------------------------------------
    reg signed [DATA_W-1:0] ram [0:(1 << ADDR_W)-1];

    always @(posedge clk_i) begin
        if (wr_en_i) begin
            ram[wr_addr_i] <= wr_data_i;
        end

        rd_data_o <= ram[rd_addr_i];
    end

endmodule
