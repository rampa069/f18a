# OCM-PLD VDP replacement timing constraints.

create_clock -name clk21m -period 46.561 [get_ports clk21m]
derive_pll_clocks
derive_clock_uncertainty

# Host bus: the strobes (csw/csr) are synchronized inside the F18A, mode and
# data are stable while a strobe is active, and the read data is sampled at
# the end of the strobe, long after it settled.
set_false_path -from [get_registers {*mode_r *csw_n_r *csr_n_r *cd_r[*]}] -to [get_clocks {*pll1|clk[0]}]
set_false_path -from [get_clocks {*pll1|clk[0]}] -to [get_registers {*dbi_r[*] *int_sync_r[0]}]

# 15KHz output stage (f18a_video_15k, OUT_HALFPX_CLKS > 0): the line pair
# toggle goes through a synchronizer, and the line number / PAL geometry are
# sampled 24 half pixels after they change.  The line buffer is a dual-clock
# RAM read well after it is written.
set_false_path -from [get_registers {*inst_video_15k|pair_tgl_r}] -to [get_registers {*inst_video_15k|*pair_sync_r[0]}]
set_false_path -from [get_registers {*inst_video_15k|wr_line_r[*]}] -to [get_clocks {clk21m}]
set_false_path -from [get_registers {*frame_pal_r}] -to [get_clocks {clk21m}]
