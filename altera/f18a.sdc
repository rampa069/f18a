# F18A timing constraints (Cyclone IV E).

create_clock -name clk_50m0 -period 20.000 [get_ports clk_50m0_net]
derive_pll_clocks
derive_clock_uncertainty

# The 9918A host bus is asynchronous: CSW/CSR are synchronized with two
# flip-flops in f18a_cpu, MODE and CD are sampled after the strobes settle.
set_false_path -from [get_ports {reset_n_net mode_net csw_n_net csr_n_net cd_net[*]}]
set_false_path -from [get_ports {usr1_net usr2_net usr3_net usr4_net pal_net spi_miso_net}]

# Outputs to the host and the video DAC have no timing relationship that the
# FPGA can constrain (the VGA outputs are registered in f18a_core).
set_false_path -to [get_ports {int_n_net cd_net[*] clk_grom_net clk_cpu_net}]
set_false_path -to [get_ports {hsync_net vsync_net csync_net red_net[*] grn_net[*] blu_net[*]}]
set_false_path -to [get_ports {spi_cs_net spi_mosi_net spi_clk_net}]
