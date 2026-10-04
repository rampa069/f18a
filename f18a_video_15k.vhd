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

-- 15KHz RGB video output, like the analog RGB of a real 9918A / MSX.
--
-- The VGA timing renders every VDP line twice, so one 15KHz line takes
-- exactly the time of a pair of VGA lines.  The first VGA line of each pair
-- is stored in a line buffer and played back at the 15KHz rate during the
-- pair, so the rest of the core is unchanged.
--
-- The VGA controller must be in a 15KHz raster geometry (f18a_video_pkg):
-- 796 pixels per raster line, so the 15KHz line is 63.68us, and 524 (NTSC)
-- or 626 (PAL) raster lines, giving 262 or 313 15KHz lines.
--
-- The 15KHz line is divided in 342 9918A pixels (684 half pixels, one per
-- stored VGA pixel), with the same layout as the 9918A:
--
--    left border   13 px   VGA x  39 ..  64
--    active       256 px   VGA x  65 .. 576   (the VDP area starts at x=65)
--    right border  15 px   VGA x 577 .. 606
--    blanking       8 px
--    hsync         26 px
--    blanking      24 px   (back porch, the 9918A color burst is here)
--
-- Vertically the raster lines with picture (border + active) are shown,
-- then 3 blank lines, a 3 line vsync and blank lines up to the end of the
-- frame (NTSC: 27 + 192 + 24 picture lines, PAL: 51 + 192 + 51).  Text modes
-- and the F18A 30-row mode use the same window.
--
-- Two ways to generate the output (generic OUT_HALFPX_CLKS):
--
--  0  The output is in the 100MHz clock domain; the half pixels are made
--     with a fractional step (684 every 8 * H15_TOTAL clocks), so they are
--     9 or 10 clocks wide.  Used by the stand-alone F18A.
--
--  N  The output is in the out_clk domain with exactly N out_clk cycles per
--     half pixel, e.g. N = 2 with the 21.477MHz MSX clock (1368 cycles per
--     line, like the V9938) for the OCM-PLD wrapper.  out_clk must be locked
--     to the core clocks so the line lengths match exactly (the F18A clocks
--     come from a PLL on out_clk): a free running line counter is aligned to
--     the line pairs once, through a synchronizer, and the line buffer is
--     read with its own clock.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use work.f18a_video_pkg.all;

entity f18a_video_15k is
   generic (
      H15_TOTAL      : integer := 796;          -- raster pixels per line (f18a_core)
      OUT_HALFPX_CLKS: integer := 0             -- 0 = 100MHz output, N = N out_clk cycles per half pixel
   );
   port (
      clk         : in  std_logic;              -- 100MHz
      vga_clk     : in  std_logic;              -- 25MHz, phase aligned
      out_clk     : in  std_logic;              -- output clock when OUT_HALFPX_CLKS > 0
      frame_pal   : in  std_logic;              -- PAL raster geometry in use
      raster_x    : in  unsigned(0 to 9);       -- VGA position, vga_clk domain
      raster_y    : in  unsigned(0 to 9);
      red_i       : in  std_logic_vector(0 to 3);  -- VGA pixel for raster_x/y
      grn_i       : in  std_logic_vector(0 to 3);
      blu_i       : in  std_logic_vector(0 to 3);

      -- Outputs, in the clk or out_clk domain.
      red_o       : out std_logic_vector(0 to 3);
      grn_o       : out std_logic_vector(0 to 3);
      blu_o       : out std_logic_vector(0 to 3);
      hsync_n_o   : out std_logic;
      vsync_n_o   : out std_logic;
      csync_n_o   : out std_logic;              -- composite sync for RGB / SCART
      blank_o     : out std_logic
   );
end f18a_video_15k;

architecture rtl of f18a_video_15k is

   -- Half pixels per 15KHz line.
   constant LINE_HALFPX : integer := 684;

   -- Horizontal layout in half pixels from the start of the left border.
   -- The line buffer is indexed by raster_x, and the VGA output pixel at
   -- x is the core color for raster_x = x + 1, so VGA x=39 is raster_x=40.
   constant X_FIRST     : integer := 40;     -- raster_x of the first pixel shown
   constant H_VISIBLE   : integer := 568;    -- (13 + 256 + 15) * 2
   constant H_SYNC_ON   : integer := 584;    -- + 8 * 2 blanking
   constant H_SYNC_OFF  : integer := 636;    -- + 26 * 2 sync

   -- Vertical layout in 15KHz lines.
   signal v_visible_s   : unsigned(0 to 8);
   signal v_sync_on_s   : unsigned(0 to 8);
   signal v_sync_off_s  : unsigned(0 to 8);

   -- Line buffer, 12-bit RGB per VGA pixel.
   type ram_t is array (0 to 1023) of std_logic_vector(0 to 11);
   signal ram : ram_t := (others => (others => '0'));

   -- Write side, vga_clk domain.
   signal wr_addr_r     : unsigned(0 to 9) := (others => '0');
   signal wr_data_r     : std_logic_vector(0 to 11) := (others => '0');
   signal wr_en_r       : std_logic := '0';
   signal wr_line_r     : unsigned(0 to 8) := (others => '0');
   signal pair_tgl_r    : std_logic := '0';    -- toggles at every line pair

   -- Output decode for a half pixel position and line.
   procedure decode (
      hpos, line        : in  unsigned;
      v_visible, v_on, v_off : in unsigned;
      signal visible_r  : out std_logic;
      signal hsync_r    : out std_logic;
      signal vsync_r    : out std_logic
   ) is
   begin
      -- Vertical sync starts and ends with the horizontal sync pulse.
      if hpos = H_SYNC_ON then
         if line >= v_on and line < v_off then
            vsync_r <= '1';
         else
            vsync_r <= '0';
         end if;
      end if;
      if hpos < H_VISIBLE and line < v_visible then
         visible_r <= '1';
      else
         visible_r <= '0';
      end if;
      if hpos >= H_SYNC_ON and hpos < H_SYNC_OFF then
         hsync_r <= '1';
      else
         hsync_r <= '0';
      end if;
   end procedure;

begin

   process (frame_pal)
      variable lines : integer;
   begin
      lines := video_geom('1', frame_pal).vsize / 2;
      v_visible_s  <= to_unsigned(lines, 9);
      v_sync_on_s  <= to_unsigned(lines + V15_BLANK_BEFORE_SYNC, 9);
      v_sync_off_s <= to_unsigned(lines + V15_BLANK_BEFORE_SYNC + V15_SYNC_LINES, 9);
   end process;

   -- Capture the first VGA line of each pair (even raster_y).  The
   -- registered address and data stay valid for four 100MHz clocks.
   process (vga_clk) begin if rising_edge(vga_clk) then
      wr_addr_r <= raster_x;
      wr_data_r <= red_i & grn_i & blu_i;
      wr_en_r   <= not raster_y(9);
      wr_line_r <= raster_y(0 to 8);
      if raster_x = 0 and raster_y(9) = '0' then
         pair_tgl_r <= not pair_tgl_r;
      end if;
   end if; end process;

   process (clk) begin if rising_edge(clk) then
      if wr_en_r = '1' then
         ram(to_integer(wr_addr_r)) <= wr_data_r;
      end if;
   end if; end process;


   --
   -- 100MHz output with a fractional half pixel step.
   --

   gen_dda : if OUT_HALFPX_CLKS = 0 generate

      -- Clocks of 100MHz per 15KHz line.
      constant LINE_CLKS : integer := 8 * H15_TOTAL;

      -- Delay from the start of the VGA line pair to the first output pixel.
      -- The output must not read a pixel before it was written: raster_x=40
      -- is written 1.6us into the line.
      constant T0        : integer := 200;

      signal pair_start_r  : std_logic := '0';
      signal clks_r        : unsigned(0 to 12) := (others => '0');
      signal acc_r         : unsigned(0 to 12) := (others => '0');
      signal hpos_r        : unsigned(0 to 9) := (others => '1');
      signal line_r        : unsigned(0 to 8) := (others => '0');
      signal vsync_r       : std_logic := '0';
      signal rd_data_r     : std_logic_vector(0 to 11) := (others => '0');
      signal visible_r     : std_logic := '0';
      signal hsync_r       : std_logic := '0';
      signal red_r, grn_r, blu_r : std_logic_vector(0 to 3) := (others => '0');
      signal hsync_n_r     : std_logic := '1';
      signal vsync_n_r     : std_logic := '1';
      signal csync_n_r     : std_logic := '1';
      signal blank_r       : std_logic := '1';

   begin

      process (clk)
         variable acc_v : unsigned(0 to 12);
      begin if rising_edge(clk) then

         rd_data_r <= ram(to_integer(X_FIRST + hpos_r));

         -- Line timing, restarted at the beginning of every VGA line pair.
         pair_start_r <= '0';
         if wr_en_r = '1' and wr_addr_r = 0 then
            pair_start_r <= '1';
         end if;

         if pair_start_r = '0' and wr_en_r = '1' and wr_addr_r = 0 then
            clks_r <= (others => '0');
         elsif clks_r /= LINE_CLKS then
            clks_r <= clks_r + 1;
         end if;

         if clks_r = T0 then
            -- First half pixel of the line.
            hpos_r <= (others => '0');
            acc_r  <= (others => '0');
            line_r <= wr_line_r;
         else
            -- Advance LINE_HALFPX half pixels every LINE_CLKS clocks.
            acc_v := acc_r + LINE_HALFPX;
            if acc_v >= LINE_CLKS then
               acc_r <= acc_v - LINE_CLKS;
               if hpos_r /= 1023 then
                  hpos_r <= hpos_r + 1;
               end if;
            else
               acc_r <= acc_v;
            end if;
         end if;

         -- Pipeline stage aligned with rd_data_r.
         decode(hpos_r, line_r, v_visible_s, v_sync_on_s, v_sync_off_s,
                visible_r, hsync_r, vsync_r);

         -- Outputs.
         if visible_r = '1' then
            red_r <= rd_data_r(0 to 3);
            grn_r <= rd_data_r(4 to 7);
            blu_r <= rd_data_r(8 to 11);
         else
            red_r <= (others => '0');
            grn_r <= (others => '0');
            blu_r <= (others => '0');
         end if;
         blank_r   <= not visible_r;
         hsync_n_r <= not hsync_r;
         vsync_n_r <= not vsync_r;
         -- Composite sync: hsync, inverted during vsync so the monitor keeps
         -- its horizontal lock.
         csync_n_r <= not (hsync_r xor vsync_r);

      end if; end process;

      red_o     <= red_r;
      grn_o     <= grn_r;
      blu_o     <= blu_r;
      hsync_n_o <= hsync_n_r;
      vsync_n_o <= vsync_n_r;
      csync_n_o <= csync_n_r;
      blank_o   <= blank_r;

   end generate;


   --
   -- out_clk output with an integer number of clocks per half pixel.
   --

   gen_div : if OUT_HALFPX_CLKS > 0 generate

      constant LINE_CLKS : integer := LINE_HALFPX * OUT_HALFPX_CLKS;

      -- First output pixel this many out_clk cycles after the synchronized
      -- start of the line pair (about 2.3us with 21.477MHz).
      constant T0        : integer := 24 * OUT_HALFPX_CLKS;

      signal pair_sync_r   : std_logic_vector(0 to 2) := "000";
      signal locked_r      : std_logic := '0';
      signal cnt_r         : integer range 0 to LINE_CLKS - 1 := 0;
      signal sub_r         : integer range 0 to OUT_HALFPX_CLKS - 1 := 0;
      signal hpos_r        : unsigned(0 to 9) := (others => '1');
      signal line_r        : unsigned(0 to 8) := (others => '0');
      signal v_visible_r   : unsigned(0 to 8) := (others => '0');
      signal v_sync_on_r   : unsigned(0 to 8) := (others => '0');
      signal v_sync_off_r  : unsigned(0 to 8) := (others => '0');
      signal vsync_r       : std_logic := '0';
      signal rd_data_r     : std_logic_vector(0 to 11) := (others => '0');
      signal visible_r     : std_logic := '0';
      signal hsync_r       : std_logic := '0';
      signal red_r, grn_r, blu_r : std_logic_vector(0 to 3) := (others => '0');
      signal hsync_n_r     : std_logic := '1';
      signal vsync_n_r     : std_logic := '1';
      signal csync_n_r     : std_logic := '1';
      signal blank_r       : std_logic := '1';

   begin

      process (out_clk)
         variable pair_edge_v : boolean;
      begin if rising_edge(out_clk) then

         rd_data_r <= ram(to_integer(X_FIRST + hpos_r));

         -- Line pair start, synchronized.  The line counter free runs once
         -- aligned, since a line pair is exactly LINE_CLKS out_clk cycles;
         -- it is realigned only when the error is more than one cycle
         -- (start up, or a change of the raster), so synchronizer
         -- uncertainty never moves the picture.
         pair_sync_r <= pair_tgl_r & pair_sync_r(0 to 1);
         pair_edge_v := pair_sync_r(1) /= pair_sync_r(2);

         if pair_edge_v and (locked_r = '0' or (cnt_r > 1 and cnt_r < LINE_CLKS - 1)) then
            cnt_r    <= 1;
            locked_r <= '1';
         elsif cnt_r = LINE_CLKS - 1 then
            cnt_r <= 0;
         else
            cnt_r <= cnt_r + 1;
         end if;

         if cnt_r = T0 then
            -- First half pixel of the line.  The line pair number and the
            -- PAL geometry change at the start of a pair, T0 cycles ago, so
            -- they are stable here.
            hpos_r <= (others => '0');
            sub_r  <= 0;
            line_r <= wr_line_r;
            v_visible_r  <= v_visible_s;
            v_sync_on_r  <= v_sync_on_s;
            v_sync_off_r <= v_sync_off_s;
         elsif sub_r = OUT_HALFPX_CLKS - 1 then
            sub_r <= 0;
            if hpos_r /= 1023 then
               hpos_r <= hpos_r + 1;
            end if;
         else
            sub_r <= sub_r + 1;
         end if;

         -- Pipeline stage aligned with rd_data_r.
         decode(hpos_r, line_r, v_visible_r, v_sync_on_r, v_sync_off_r,
                visible_r, hsync_r, vsync_r);

         -- Outputs.
         if visible_r = '1' then
            red_r <= rd_data_r(0 to 3);
            grn_r <= rd_data_r(4 to 7);
            blu_r <= rd_data_r(8 to 11);
         else
            red_r <= (others => '0');
            grn_r <= (others => '0');
            blu_r <= (others => '0');
         end if;
         blank_r   <= not visible_r;
         hsync_n_r <= not hsync_r;
         vsync_n_r <= not vsync_r;
         csync_n_r <= not (hsync_r xor vsync_r);

      end if; end process;

      red_o     <= red_r;
      grn_o     <= grn_r;
      blu_o     <= blu_r;
      hsync_n_o <= hsync_n_r;
      vsync_n_o <= vsync_n_r;
      csync_n_o <= csync_n_r;
      blank_o   <= blank_r;

   end generate;

end rtl;
