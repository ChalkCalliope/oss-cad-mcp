module blinky(input logic clk, input logic rst, output logic [3:0] cnt);
  always_ff @(posedge clk) begin
    if (rst) cnt <= '0;
    else cnt <= cnt + 1'b1;
  end
endmodule