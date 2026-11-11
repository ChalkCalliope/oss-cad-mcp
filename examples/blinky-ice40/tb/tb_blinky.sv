module tb_blinky;
  logic clk = 0;
  logic rst = 1;
  logic [3:0] cnt;

  blinky uut(.clk(clk), .rst(rst), .cnt(cnt));

  always #5 clk = ~clk;

  initial begin
    $dumpfile("wave.vcd");
    $dumpvars(0, tb_blinky);
    repeat (3) @(posedge clk);
    rst <= 0;
    repeat (20) @(posedge clk);
    $display("cnt=%0d", cnt);
    $finish;
  end
endmodule