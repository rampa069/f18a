// F18A clock PLL for Cyclone IV E / Cyclone 10 LP.
//
//    clk_core = 50MHz * 12 / 7 = 85.71MHz
//    clk_pix  = 50MHz *  3 / 14 = 10.71MHz, phase aligned with clk_core
//
// Written in Verilog because instantiating altpll from VHDL through
// altera_mf_components makes Quartus 21.1 pass Stratix default generics and
// fail to compute the PLL parameters.

module f18a_pll (
   input  wire clk_50m0,
   output wire clk_core,
   output wire clk_pix,
   output wire locked
);

   wire [4:0] clk;

   altpll #(
      .intended_device_family ("Cyclone IV E"),
      .lpm_type               ("altpll"),
      .operation_mode         ("NORMAL"),
      .pll_type               ("AUTO"),
      .compensate_clock       ("CLK0"),
      .inclk0_input_frequency (20000),     // 50MHz, in ps
      .bandwidth_type         ("AUTO"),
      .clk0_multiply_by       (12),        // 85.71MHz
      .clk0_divide_by         (7),
      .clk0_duty_cycle        (50),
      .clk0_phase_shift       ("0"),
      .clk1_multiply_by       (3),         // 10.71MHz
      .clk1_divide_by         (14),
      .clk1_duty_cycle        (50),
      .clk1_phase_shift       ("0"),
      .port_clk0              ("PORT_USED"),
      .port_clk1              ("PORT_USED"),
      .port_locked            ("PORT_USED"),
      .width_clock            (5)
   ) pll_inst (
      .inclk  ({1'b0, clk_50m0}),
      .clk    (clk),
      .locked (locked)
   );

   assign clk_core = clk[0];
   assign clk_pix  = clk[1];

endmodule
