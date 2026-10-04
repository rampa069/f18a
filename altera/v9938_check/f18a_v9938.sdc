# Core clock 85.91 MHz and the phase aligned pixel clock (1/8).
create_clock -name clk_core -period 11.640 [get_ports clk_core_i]
create_clock -name clk_pix -period 93.120 [get_ports clk_pix_i]
derive_clock_uncertainty
set_false_path -from [get_ports {reset_n_i mode_i mode1_i csw_n_i csr_n_i cd_i[*] v9938_i vr8_ignore_i pal_i sprite_max_i spi_miso_i}]
set_false_path -to [get_ports {cd_o[*] int_n_o red_o[*] grn_o[*] blu_o[*] hsync_n_o vsync_n_o csync_n_o blank_o spi_*}]
