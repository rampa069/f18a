-- Simulation model of ocm/f18a_vdp_pll.v (altpll): core clock (x4) and
-- pixel clock (/2), phase locked to clk_21m.
--
-- All clocks are generated from one time unit so they never drift: clk_21m
-- must have a period of 168 units (the testbench uses 277ps, i.e.
-- 21.489MHz); clk_core has 42 units and clk_pix 336 units.

library ieee;
use ieee.std_logic_1164.all;

entity f18a_vdp_pll is
   generic (
      T_UNIT         : time := 277 ps
   );
   port (
      clk_21m        : in  std_logic;
      clk_core       : out std_logic;
      clk_pix        : out std_logic;
      locked         : out std_logic
   );
end f18a_vdp_pll;

architecture sim of f18a_vdp_pll is
   signal c_core : std_logic := '0';
   signal c_pix  : std_logic := '0';
begin

   process begin
      wait until rising_edge(clk_21m);
      loop
         c_core <= '1';
         wait for 21 * T_UNIT;
         c_core <= '0';
         wait for 21 * T_UNIT;
      end loop;
   end process;

   process begin
      wait until rising_edge(clk_21m);
      loop
         c_pix <= '1';
         wait for 168 * T_UNIT;
         c_pix <= '0';
         wait for 168 * T_UNIT;
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

   clk_core <= c_core;
   clk_pix  <= c_pix;

end sim;
