--
-- F18A
--   A pin-compatible enhanced replacement for the TMS9918A VDP family.
--   https://dnotq.io
--

-- Released under the 3-Clause BSD License:
--
-- Copyright 2011-2018 Matthew Hagerty (matthew <at> dnotq <dot> io)
--
-- Redistribution and use in source and binary forms, with or without
-- modification, are permitted provided that the following conditions are met:
--
-- 1. Redistributions of source code must retain the above copyright notice,
-- this list of conditions and the following disclaimer.
--
-- 2. Redistributions in binary form must reproduce the above copyright
-- notice, this list of conditions and the following disclaimer in the
-- documentation and/or other materials provided with the distribution.
--
-- 3. Neither the name of the copyright holder nor the names of its
-- contributors may be used to endorse or promote products derived from this
-- software without specific prior written permission.
--
-- THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
-- AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
-- IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
-- ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
-- LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
-- CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
-- SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
-- INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
-- CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
-- ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
-- POSSIBILITY OF SUCH DAMAGE.

-- Top module for a stand-alone F18A on an Altera (Intel) Cyclone IV E,
-- equivalent to f18a_top.vhd (Xilinx Spartan-3E).  The only vendor specific
-- part is the PLL (f18a_pll.v), an altpll megafunction instantiated directly
-- so no IP generation step is needed.  The same altpll settings are valid for
-- the Cyclone 10 LP.
--
-- Clocking (50MHz board oscillator):
--    clk_100m0 = 50MHz * 2   F18A core, CPU interface, GPU
--    clk_25m0  = 50MHz / 2   VGA pixel clock, phase aligned with clk_100m0

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;


entity f18a_top_altera is
   port (
      clk_50m0_net   : in  std_logic;

      -- 9918A to Host interface
      reset_n_net    : in  std_logic;
      mode_net       : in  std_logic;
      csw_n_net      : in  std_logic;
      csr_n_net      : in  std_logic;
      int_n_net      : out std_logic;
      clk_grom_net   : out std_logic;  -- 447.443KHz GROMCLK
      clk_cpu_net    : out std_logic;  -- 3.5795MHz CPUCLK
      cd_net         : inout std_logic_vector(0 to 7);

      -- Video generation: 15KHz RGB like a real 9918A / 9929A, for boards
      -- with their own scandoubler.  pal_net = '0' (jumper fitted) selects
      -- PAL (313 lines, 50Hz), otherwise NTSC (262 lines, 60Hz).  Separate
      -- and composite syncs, all active low.
      pal_net        : in  std_logic;
      hsync_net      : out std_logic;
      vsync_net      : out std_logic;
      csync_net      : out std_logic;
      blank_net      : out std_logic;   -- '1' outside the picture (not display enable)
      red_net        : out std_logic_vector(0 to 3);
      grn_net        : out std_logic_vector(0 to 3);
      blu_net        : out std_logic_vector(0 to 3);

      -- User header for feature selection
      usr1_net       : in std_logic;   -- Sprite max
      usr2_net       : in std_logic;   -- Simulated scan lines
      usr3_net       : in std_logic;   -- CPU CLK out pin selection
      usr4_net       : in std_logic;   -- CPU CLK out enable

      -- SPI
      spi_cs_net     : out std_logic;
      spi_mosi_net   : out std_logic;
      spi_miso_net   : in  std_logic;
      spi_clk_net    : out std_logic
   );
end f18a_top_altera;

architecture rtl of f18a_top_altera is

   component f18a_pll is
      port (
         clk_50m0       : in  std_logic;
         clk_100m0      : out std_logic;
         clk_25m0       : out std_logic;
         locked         : out std_logic
      );
   end component;

   -- Main clock generation.
   signal pll_locked_s     : std_logic;
   signal clk_100m0_s      : std_logic;
   signal clk_25m0_s       : std_logic;


   -- Power-On Reset generation.

   -- Reset input synchronizer.
   signal reset_n_i_r1     : std_logic := '0';
   signal reset_n_i_r      : std_logic := '0';

   -- Power-On Reset counter.
   signal reset_por_r      : std_logic := '0';
   signal reset_cnt_r      : unsigned(0 to 2) := "000";

   -- Final reset register.
   signal reset_n_r        : std_logic := '0';

   -- Output routing.
   signal cd_out_s         : std_logic_vector(0 to 7);
   signal pal_s            : std_logic;
   signal hsync15_s, vsync15_s, csync15_s, blank15_s : std_logic;
   signal red15_s, grn15_s, blu15_s : std_logic_vector(0 to 3);

   -- Output GROM and CPU clock generation.
   signal cpuclk_r         : std_logic := '0';
   signal gromclk_r        : std_logic := '0';
   signal cpudiv_r         : unsigned(0 to 3) := (others => '0');
   signal gromdiv_r        : unsigned(0 to 6) := (others => '0');

begin

   --
   -- Clock generation
   --

   pll_inst : f18a_pll
   port map (
      clk_50m0       => clk_50m0_net,
      clk_100m0      => clk_100m0_s,
      clk_25m0       => clk_25m0_s,
      locked         => pll_locked_s
   );


   --
   -- Power-On Reset
   --

   -- Synchronize the real input reset signal.  The core is also held in
   -- reset until the PLL is locked.
   process (clk_100m0_s) begin if rising_edge(clk_100m0_s) then
      reset_n_i_r1 <= reset_n_net and pll_locked_s;
      reset_n_i_r  <= reset_n_i_r1;
   end if; end process;

   -- Some systems have a short Power-On Reset time and are out of reset before
   -- the FPGA is done loading the bit-stream.  In these cases, the F18A still
   -- needs a clean reset which is provided by this counter.  The counter is
   -- initialized to a known value by the bit-stream, so a clean reset can be
   -- generated once the FPGA is operational.
   --
   -- The count is 0..7 to be long enough for the vga_clk (25MHz) to cycle
   -- at least once, ensuring any resets based on the vga_clk have time to
   -- complete.
   process (clk_100m0_s) begin if rising_edge(clk_100m0_s) then
      if reset_cnt_r = "111" then
         reset_por_r <= '1';
      else
         reset_por_r <= '0';
         reset_cnt_r <= reset_cnt_r + 1;
      end if;

      -- Combine both reset signals.
      reset_n_r <= reset_n_i_r and reset_por_r;

   end if; end process;


   --
   -- F18A core
   --

   inst_f18a : entity work.f18a_core
   port map (
      clk_100m0_i    => clk_100m0_s,
      clk_25m0_i     => clk_25m0_s,

      -- 9918A to Host System Interface
      reset_n_i      => reset_n_r,
      mode_i         => mode_net,
      csw_n_i        => csw_n_net,
      csr_n_i        => csr_n_net,
      vr8_ignore_i   => '0',            -- 9918A socket: mask VR8+ writes
      int_n_o        => int_n_net,
      cd_i           => cd_net,
      cd_o           => cd_out_s,

      -- Video Output
      blank_o        => open,
      hsync_o        => open,
      vsync_o        => open,
      red_o          => open,
      grn_o          => open,
      blu_o          => open,

      -- 15KHz Video Output
      clk_out15_i    => '0',
      video_15k_i    => '1',
      pal_i          => pal_s,
      red15_o        => red15_s,
      grn15_o        => grn15_s,
      blu15_o        => blu15_s,
      hsync15_n_o    => hsync15_s,
      vsync15_n_o    => vsync15_s,
      csync15_n_o    => csync15_s,
      blank15_o      => blank15_s,

      -- Feature Selection
      sprite_max_i   => usr1_net,      -- Default sprite max, '0' = 32, '1' = 4
      scanlines_i    => usr2_net,      -- Simulated scan lines, '0' = no, '1' = yes

      -- SPI to GPU
      spi_clk_o      => spi_clk_net,
      spi_cs_o       => spi_cs_net,
      spi_mosi_o     => spi_mosi_net,
      spi_miso_i     => spi_miso_net
   );


   -- PAL / NTSC jumper, read by the core at the end of each frame.
   pal_s <= not pal_net;

   hsync_net <= hsync15_s;
   vsync_net <= vsync15_s;
   csync_net <= csync15_s;
   blank_net <= blank15_s;
   red_net   <= red15_s;
   grn_net   <= grn15_s;
   blu_net   <= blu15_s;


   -- Host interface data bus tristate.
   cd_net <= cd_out_s when csr_n_net = '0' else (others => 'Z');


   -- GROM and CPU clock generation, see f18a_top.vhd.
   --
   -- 100MHz / 3.5795MHz =  27.93, use  28 (3.5714MHz)
   -- 100MHz / 447.44KHz = 223.49, use 224 (446.428KHz)

   ext_clock_gen :
   process (clk_100m0_s)
   begin if rising_edge(clk_100m0_s) then

      -- 224 / 2 = 112, count 0..111 to generate 50% GROMCLK period.
      if gromdiv_r = 111 then
         gromclk_r <= not gromclk_r;
         gromdiv_r <= (others => '0');
      else
         gromdiv_r <= gromdiv_r + 1;
      end if;

      -- 28 / 2 = 14, count 0..13 to generate 50% CPUCLK period.
      if cpudiv_r = 13 then
         cpuclk_r <= not cpuclk_r;
         cpudiv_r <= (others => '0');
      else
         cpudiv_r <= cpudiv_r + 1;
      end if;

   end if;
   end process;


   -- User header, see f18a_top.vhd for the jumper table.
   -- USR3 selects GROMCLK or CPUCLK on pin37.
   clk_grom_net <= gromclk_r when usr3_net = '0' else cpuclk_r;

   -- USR4 controls if pin38 outputs the CPUCLK or not.
   clk_cpu_net <= 'Z' when usr4_net = '0' else cpuclk_r;

end rtl;
