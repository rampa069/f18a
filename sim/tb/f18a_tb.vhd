-- Simulation wrapper for f18a_core, driven by the cocotb tests in sim/.
--
-- Generates the phase aligned 100MHz and 25MHz clocks (as the DCM does in
-- f18a_top) and, while capture_en = '1', dumps every complete VGA frame as an
-- ASCII PPM (P3, 4-bit per channel) to CAPTURE_DIR/frame_<n>.ppm.  Capturing
-- in VHDL is far faster than sampling 300K pixels per frame from Python.

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

      -- Feature selection.
      sprite_max_i   : in  std_logic;
      scanlines_i    : in  std_logic;

      -- Video, observed by cocotb.
      clk_100m0_o    : out std_logic;
      clk_25m0_o     : out std_logic;
      blank_o        : out std_logic;
      hsync_o        : out std_logic;
      vsync_o        : out std_logic;
      red_o          : out std_logic_vector(0 to 3);
      grn_o          : out std_logic_vector(0 to 3);
      blu_o          : out std_logic_vector(0 to 3);

      -- 15KHz video.
      video_15k_i    : in  std_logic;
      pal_i          : in  std_logic;
      red15_o        : out std_logic_vector(0 to 3);
      grn15_o        : out std_logic_vector(0 to 3);
      blu15_o        : out std_logic_vector(0 to 3);
      hsync15_n_o    : out std_logic;
      vsync15_n_o    : out std_logic;
      csync15_n_o    : out std_logic;
      blank15_o      : out std_logic;

      -- Frame capture control.
      capture_en_i   : in  std_logic;
      frames_o       : out integer;     -- number of VGA frames written so far
      capture15_en_i : in  std_logic;
      frames15_o     : out integer      -- number of 15KHz frames written so far
   );
end f18a_tb;

architecture sim of f18a_tb is

   signal clk_100m0  : std_logic := '0';
   signal clk_25m0   : std_logic := '0';

   signal blank_s    : std_logic;
   signal hsync_s    : std_logic;
   signal vsync_s    : std_logic;
   signal red_s      : std_logic_vector(0 to 3);
   signal grn_s      : std_logic_vector(0 to 3);
   signal blu_s      : std_logic_vector(0 to 3);

   signal frames     : integer := 0;

   signal red15_s    : std_logic_vector(0 to 3);
   signal grn15_s    : std_logic_vector(0 to 3);
   signal blu15_s    : std_logic_vector(0 to 3);
   signal vsync15_s  : std_logic;
   signal blank15_s  : std_logic;
   signal frames15   : integer := 0;

begin

   -- Phase aligned clocks: 25MHz rises on every 4th 100MHz rising edge.
   clk_100m0 <= not clk_100m0 after 5 ns;

   process (clk_100m0)
      variable div : integer range 0 to 1 := 0;
   begin
      if rising_edge(clk_100m0) then
         if div = 1 then
            clk_25m0 <= not clk_25m0;
            div := 0;
         else
            div := div + 1;
         end if;
      end if;
   end process;

   inst_core : entity work.f18a_core
   port map (
      clk_100m0_i    => clk_100m0,
      clk_25m0_i     => clk_25m0,
      reset_n_i      => reset_n_i,
      mode_i         => mode_i,
      csw_n_i        => csw_n_i,
      csr_n_i        => csr_n_i,
      int_n_o        => int_n_o,
      cd_i           => cd_i,
      cd_o           => cd_o,
      blank_o        => blank_s,
      hsync_o        => hsync_s,
      vsync_o        => vsync_s,
      red_o          => red_s,
      grn_o          => grn_s,
      blu_o          => blu_s,
      sprite_max_i   => sprite_max_i,
      scanlines_i    => scanlines_i,
      video_15k_i    => video_15k_i,
      pal_i          => pal_i,
      red15_o        => red15_s,
      grn15_o        => grn15_s,
      blu15_o        => blu15_s,
      hsync15_n_o    => hsync15_n_o,
      vsync15_n_o    => vsync15_s,
      csync15_n_o    => csync15_n_o,
      blank15_o      => blank15_s,
      spi_clk_o      => open,
      spi_cs_o       => open,
      spi_mosi_o     => open,
      spi_miso_i     => '1'
   );

   clk_100m0_o <= clk_100m0;
   clk_25m0_o  <= clk_25m0;
   blank_o     <= blank_s;
   hsync_o     <= hsync_s;
   vsync_o     <= vsync_s;
   red_o       <= red_s;
   grn_o       <= grn_s;
   blu_o       <= blu_s;
   frames_o    <= frames;
   red15_o     <= red15_s;
   grn15_o     <= grn15_s;
   blu15_o     <= blu15_s;
   vsync15_n_o <= vsync15_s;
   blank15_o   <= blank15_s;
   frames15_o  <= frames15;


   -- Frame capture.  A frame starts with the first active pixel after the
   -- vsync pulse and contains every non-blanked pixel until the next vsync.
   capture : process (clk_25m0)
      file     f         : text;
      variable l         : line;
      variable open_v    : boolean := false;
      variable armed     : boolean := false;
      variable vsync_d   : std_logic := '1';
      variable blank_d   : std_logic := '1';
   begin
      if rising_edge(clk_25m0) then

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
               write(l, string'("640 480"));
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
            -- End of an active line.
            writeline(f, l);
         end if;

         vsync_d := vsync_s;
         blank_d := blank_s;
      end if;
   end process;



   -- 15KHz frame capture: one sample per half pixel, 568 per line.  The
   -- number of lines depends on NTSC / PAL, so the PPM height is written as
   -- 0 and the reader derives it from the pixel count.
   -- The output pixel for a new half pixel position is stable two clocks
   -- after the position counter in f18a_video_15k changes; sample it one
   -- clock later.
   capture15 : process (clk_100m0)
      alias hpos is << signal .f18a_tb.inst_core.inst_video_15k.hpos_r : unsigned(0 to 9) >>;
      type hpos_dly_t is array (1 to 4) of unsigned(0 to 9);
      variable hpos_d  : hpos_dly_t := (others => (others => '0'));
      file     f       : text;
      variable l       : line;
      variable open_v  : boolean := false;
      variable armed   : boolean := false;
      variable vsync_d : std_logic := '1';
      variable blank_d : std_logic := '1';
   begin
      if rising_edge(clk_100m0) then

         if vsync_d = '1' and vsync15_s = '0' then
            if open_v then
               file_close(f);
               open_v := false;
               frames15 <= frames15 + 1;
            end if;
            armed := capture15_en_i = '1';
         end if;

         if blank15_s = '0' then
            if armed and not open_v then
               file_open(f, CAPTURE_DIR & "/frame15_" & integer'image(frames15) & ".ppm", write_mode);
               write(l, string'("P3"));
               writeline(f, l);
               write(l, string'("568 0"));
               writeline(f, l);
               write(l, string'("15"));
               writeline(f, l);
               open_v := true;
               armed := false;
            end if;
            if open_v and hpos_d(3) /= hpos_d(4) then
               write(l, to_integer(unsigned(red15_s)));
               write(l, ' ');
               write(l, to_integer(unsigned(grn15_s)));
               write(l, ' ');
               write(l, to_integer(unsigned(blu15_s)));
               write(l, ' ');
            end if;
         elsif blank_d = '0' and open_v then
            writeline(f, l);
         end if;

         hpos_d := hpos & hpos_d(1 to 3);
         vsync_d := vsync15_s;
         blank_d := blank15_s;
      end if;
   end process;

end sim;
