-- Simulation wrapper for the OCM-PLD VDP replacement (entity vdp in
-- ocm/f18a_vdp_ocm.vhd), driven by sim/tests/test_ocm.py.
--
-- Generates CLK21M (168 PLL model units, 21.489MHz) and, while capture_en_i
-- is set, dumps frames sampled on CLK21M like emsx_top does: one sample per
-- half pixel (every 2 cycles at 15KHz, every cycle at 31KHz) of the
-- non-blanked area to CAPTURE_DIR/ocm_<n>.ppm.  Color changes that do not
-- fall on a half pixel boundary are counted in phase_err_o.

library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;
use std.textio.all;

entity ocm_tb is
   generic (
      CAPTURE_DIR    : string := "."
   );
   port (
      clk21m_o       : out std_logic;
      reset_i        : in  std_logic;
      req_i          : in  std_logic;
      ack_o          : out std_logic;
      wrt_i          : in  std_logic;
      adr_i          : in  std_logic_vector(15 downto 0);
      dbi_o          : out std_logic_vector(7 downto 0);
      dbo_i          : in  std_logic_vector(7 downto 0);
      int_n_o        : out std_logic;
      pramoe_n_o     : out std_logic;
      pramwe_n_o     : out std_logic;
      pvideor_o      : out std_logic_vector(5 downto 0);
      pvideog_o      : out std_logic_vector(5 downto 0);
      pvideob_o      : out std_logic_vector(5 downto 0);
      pvideohs_n_o   : out std_logic;
      pvideovs_n_o   : out std_logic;
      pvideocs_n_o   : out std_logic;
      pvideodhclk_o  : out std_logic;
      pvideodlclk_o  : out std_logic;
      blank_o        : out std_logic;
      dispreso_i     : in  std_logic;
      ntsc_pal_type_i: in  std_logic;
      forced_v_mode_i: in  std_logic;

      capture_en_i   : in  std_logic;
      frames_o       : out integer;
      phase_err_o    : out integer
   );
end ocm_tb;

architecture sim of ocm_tb is

   constant T_UNIT : time := 277 ps;

   signal clk21m     : std_logic := '0';
   signal r, g, b    : std_logic_vector(5 downto 0);
   signal hs_n, vs_n : std_logic;
   signal blank      : std_logic;
   signal frames     : integer := 0;
   signal phase_err  : integer := 0;

begin

   clk21m <= not clk21m after 84 * T_UNIT;

   inst_vdp : entity work.vdp
   port map (
      clk21m            => clk21m,
      reset             => reset_i,
      req               => req_i,
      ack               => ack_o,
      wrt               => wrt_i,
      adr               => adr_i,
      dbi               => dbi_o,
      dbo               => dbo_i,
      int_n             => int_n_o,
      pramoe_n          => pramoe_n_o,
      pramwe_n          => pramwe_n_o,
      pramadr           => open,
      pramdbi           => (others => '0'),
      pramdbo           => open,
      vdpspeedmode      => '0',
      ratiomode         => "000",
      centeryjk_r25_n   => '1',
      pvideor           => r,
      pvideog           => g,
      pvideob           => b,
      pvideohs_n        => hs_n,
      pvideovs_n        => vs_n,
      pvideocs_n        => pvideocs_n_o,
      pvideodhclk       => pvideodhclk_o,
      pvideodlclk       => pvideodlclk_o,
      blank_o           => blank,
      interlacemode     => open,
      dispreso          => dispreso_i,
      ntsc_pal_type     => ntsc_pal_type_i,
      forced_v_mode     => forced_v_mode_i,
      legacy_vga        => '0',
      vga_int_field     => '0',
      spmaxspr          => '0',
      vdp_id            => "00001",
      offset_y          => (others => '0')
   );

   clk21m_o     <= clk21m;
   pvideor_o    <= r;
   pvideog_o    <= g;
   pvideob_o    <= b;
   pvideohs_n_o <= hs_n;
   pvideovs_n_o <= vs_n;
   blank_o      <= blank;
   frames_o     <= frames;
   phase_err_o  <= phase_err;

   capture : process (clk21m)
      file     f        : text;
      variable l        : line;
      variable open_v   : boolean := false;
      variable armed    : boolean := false;
      variable vs_d     : std_logic := '1';
      variable blank_d  : std_logic := '1';
      variable x        : integer := 0;
      variable rgb_d    : std_logic_vector(17 downto 0) := (others => '0');
      variable step     : integer;
   begin
      if rising_edge(clk21m) then
         if dispreso_i = '0' then step := 2; else step := 1; end if;

         if vs_d = '1' and vs_n = '0' then
            if open_v then
               file_close(f);
               open_v := false;
               frames <= frames + 1;
            end if;
            armed := capture_en_i = '1';
         end if;

         if blank = '0' then
            if blank_d = '1' then
               x := 0;
            end if;
            if armed and not open_v then
               file_open(f, CAPTURE_DIR & "/ocm_" & integer'image(frames) & ".ppm", write_mode);
               write(l, string'("P3"));
               writeline(f, l);
               if step = 2 then
                  write(l, string'("568 0"));
               else
                  write(l, string'("568 0"));
               end if;
               writeline(f, l);
               write(l, string'("63"));
               writeline(f, l);
               open_v := true;
               armed := false;
            end if;
            -- A new color must start on a half pixel boundary.
            if x mod step /= 0 and (r & g & b) /= rgb_d then
               phase_err <= phase_err + 1;
            end if;
            if open_v and x mod step = 0 then
               write(l, to_integer(unsigned(r)));
               write(l, ' ');
               write(l, to_integer(unsigned(g)));
               write(l, ' ');
               write(l, to_integer(unsigned(b)));
               write(l, ' ');
            end if;
            x := x + 1;
         elsif blank_d = '0' and open_v then
            writeline(f, l);
         end if;

         rgb_d   := r & g & b;
         vs_d    := vs_n;
         blank_d := blank;
      end if;
   end process;

end sim;
