`include "rp_dds_defs.vh"

module rp_sine_rom #(
    parameter ADDR_W  = `RP_DDS_SINE_AW,
    parameter DATA_W  = `RP_DDS_DATA_W,
    parameter MEM_FILE = "includes/dds_axi_dma/rtl/sine4096_14b.mem"
) (
    input                          clk_i,
    input      [ADDR_W-1:0]        addr_i,
    output reg signed [DATA_W-1:0] data_o
);

    reg signed [DATA_W-1:0] rom [0:(1 << ADDR_W)-1];

    initial begin
        $readmemh(MEM_FILE, rom);
    end

    always @(posedge clk_i) begin
        data_o <= rom[addr_i];
    end

endmodule
