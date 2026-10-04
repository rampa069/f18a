-- Simulation wrapper for f18a_core, driven by the cocotb tests in sim/.
--
-- Generates the phase aligned core and pixel clocks (85.91MHz and 10.74MHz,
-- 8:1) and, while capture_en_i is set, dumps every complete frame as an
-- ASCII PPM (P3, 4-bit per channel, one sample per pixel clock) to
-- CAPTURE_DIR/frame_<n>.ppm.  The height depends on NTSC / PAL, so it is
-- written as 0 and the reader derives it from the pixel count.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.textio.all;

entity f18a_tb is
   generic (
      CAPTURE_DIR    : string := "."
   );
   port (
      -- 9918A host interface, driven by cocotb.
      reset_n_i      : in  std_logic;
      mode_i         : in  std_logic;
      csw_n_i        : in  std_logic;
      csr_n_i        : in  std_logic;
      int_n_o        : out std_logic;
      cd_i           : in  std_logic_vector(0 to 7);
      cd_o           : out std_logic_vector(0 to 7);

      -- Configuration.
      sprite_max_i   : in  std_logic;
      pal_i          : in  std_logic;

      -- Video, observed by cocotb.
      clk_core_o     : out std_logic;
      clk_pix_o      : out std_logic;
      red_o          : out std_logic_vector(0 to 3);
      grn_o          : out std_logic_vector(0 to 3);
      blu_o          : out std_logic_vector(0 to 3);
      hsync_n_o      : out std_logic;
      vsync_n_o      : out std_logic;
      csync_n_o      : out std_logic;
      blank_o        : out std_logic;

      -- Frame capture control.
      capture_en_i   : in  std_logic;
      frames_o       : out integer      -- number of frames written so far
   );
end f18a_tb;

architecture sim of f18a_tb is

   -- 21.477MHz * 4 and / 2.
   constant T_CORE_HALF : time := 5820 ps;

   signal clk_core   : std_logic := '0';
   signal clk_pix    : std_logic := '0';

   signal red_s      : std_logic_vector(0 to 3);
   signal grn_s      : std_logic_vector(0 to 3);
   signal blu_s      : std_logic_vector(0 to 3);
   signal vsync_s    : std_logic;
   signal blank_s    : std_logic;

   signal frames     : integer := 0;

begin

   clk_core <= not clk_core after T_CORE_HALF;

   -- Pixel clock: toggles every 4 core clocks, rising with a core edge.
   process (clk_core)
      variable div : integer range 0 to 3 := 3;
   begin
      if rising_edge(clk_core) then
         if div = 3 then
            clk_pix <= not clk_pix;
            div := 0;
         else
            div := div + 1;
         end if;
      end if;
   end process;

   inst_core : entity work.f18a_core
   port map (
      clk_core_i     => clk_core,
      clk_pix_i      => clk_pix,
      reset_n_i      => reset_n_i,
      mode_i         => mode_i,
      csw_n_i        => csw_n_i,
      csr_n_i        => csr_n_i,
      vr8_ignore_i   => '0',
      int_n_o        => int_n_o,
      cd_i           => cd_i,
      cd_o           => cd_o,
      pal_i          => pal_i,
      red_o          => red_s,
      grn_o          => grn_s,
      blu_o          => blu_s,
      hsync_n_o      => hsync_n_o,
      vsync_n_o      => vsync_s,
      csync_n_o      => csync_n_o,
      blank_o        => blank_s,
      sprite_max_i   => sprite_max_i,
      spi_clk_o      => open,
      spi_cs_o       => open,
      spi_mosi_o     => open,
      spi_miso_i     => '1'
   );

   clk_core_o  <= clk_core;
   clk_pix_o   <= clk_pix;
   red_o       <= red_s;
   grn_o       <= grn_s;
   blu_o       <= blu_s;
   vsync_n_o   <= vsync_s;
   blank_o     <= blank_s;
   frames_o    <= frames;


   -- Frame capture.  A frame starts with the first picture pixel after the
   -- vsync pulse and contains every non-blanked pixel until the next vsync.
   capture : process (clk_pix)
      file     f         : text;
      variable l         : line;
      variable open_v    : boolean := false;
      variable armed     : boolean := false;
      variable vsync_d   : std_logic := '1';
      variable blank_d   : std_logic := '1';
   begin
      if rising_edge(clk_pix) then

         -- Falling edge of vsync (active low): close the current frame and
         -- arm the capture of the next one.
         if vsync_d = '1' and vsync_s = '0' then
            if open_v then
               file_close(f);
               open_v := false;
               frames <= frames + 1;
            end if;
            armed := capture_en_i = '1';
         end if;

         if blank_s = '0' then
            if armed and not open_v then
               file_open(f, CAPTURE_DIR & "/frame_" & integer'image(frames) & ".ppm", write_mode);
               write(l, string'("P3"));
               writeline(f, l);
               write(l, string'("568 0"));
               writeline(f, l);
               write(l, string'("15"));
               writeline(f, l);
               open_v := true;
               armed := false;
            end if;
            if open_v then
               write(l, to_integer(unsigned(red_s)));
               write(l, ' ');
               write(l, to_integer(unsigned(grn_s)));
               write(l, ' ');
               write(l, to_integer(unsigned(blu_s)));
               write(l, ' ');
            end if;
         elsif blank_d = '0' and open_v then
            -- End of a picture line.
            writeline(f, l);
         end if;

         vsync_d := vsync_s;
         blank_d := blank_s;
      end if;
   end process;

end sim;
