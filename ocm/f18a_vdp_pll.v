// PLL for the F18A OCM-PLD wrapper (ocm/f18a_vdp_ocm.vhd).
//
//    clk_100m = 21.477MHz * 14 / 3 = 100.23MHz
//    clk_25m  = 21.477MHz *  7 / 6 =  25.06MHz, phase aligned with clk_100m
//
// Set intended_device_family to the FPGA of the target core if Quartus
// complains (the altpll parameters are the same for Cyclone I to 10 LP).

module f18a_vdp_pll (
   input  wire clk_21m,
   output wire clk_100m,
   output wire clk_25m,
   output wire locked
);

   wire [4:0] clk;

   altpll #(
      .intended_device_family ("Cyclone IV E"),
      .lpm_type               ("altpll"),
      .operation_mode         ("NORMAL"),
      .pll_type               ("AUTO"),
      .compensate_clock       ("CLK0"),
      .inclk0_input_frequency (46561),     // 21.477MHz, in ps
      .bandwidth_type         ("AUTO"),
      .clk0_multiply_by       (14),        // 100.23MHz
      .clk0_divide_by         (3),
      .clk0_duty_cycle        (50),
      .clk0_phase_shift       ("0"),
      .clk1_multiply_by       (7),         // 25.06MHz
      .clk1_divide_by         (6),
      .clk1_duty_cycle        (50),
      .clk1_phase_shift       ("0"),
      .port_clk0              ("PORT_USED"),
      .port_clk1              ("PORT_USED"),
      .port_locked            ("PORT_USED"),
      .width_clock            (5)
   ) pll_inst (
      .inclk  ({1'b0, clk_21m}),
      .clk    (clk),
      .locked (locked)
   );

   assign clk_100m = clk[0];
   assign clk_25m  = clk[1];

endmodule
