-- Simulation model of ocm/f18a_vdp_pll.v (altpll): 100.23MHz and 25.06MHz,
-- phase locked to clk_21m.
--
-- All three clocks are generated from one time unit so they never drift:
-- clk_21m must have a period of 84 units (the testbench uses 554ps, i.e.
-- 21.487MHz); clk_100m has 18 units (14/3) and clk_25m 72 units (7/6).

library ieee;
use ieee.std_logic_1164.all;

entity f18a_vdp_pll is
   generic (
      T_UNIT         : time := 554 ps
   );
   port (
      clk_21m        : in  std_logic;
      clk_100m       : out std_logic;
      clk_25m        : out std_logic;
      locked         : out std_logic
   );
end f18a_vdp_pll;

architecture sim of f18a_vdp_pll is
   signal c100 : std_logic := '0';
   signal c25  : std_logic := '0';
begin

   process begin
      wait until rising_edge(clk_21m);
      loop
         c100 <= '1';
         wait for 9 * T_UNIT;
         c100 <= '0';
         wait for 9 * T_UNIT;
      end loop;
   end process;

   process begin
      wait until rising_edge(clk_21m);
      loop
         c25 <= '1';
         wait for 36 * T_UNIT;
         c25 <= '0';
         wait for 36 * T_UNIT;
      end loop;
   end process;

   process begin
      locked <= '0';
      for i in 1 to 16 loop
         wait until rising_edge(clk_21m);
      end loop;
      locked <= '1';
      wait;
   end process;

   clk_100m <= c100;
   clk_25m  <= c25;

end sim;
