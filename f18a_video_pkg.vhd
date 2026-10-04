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

-- Video timing geometry of every supported output standard, in one place.
--
-- The core always renders with a double scan raster: every VDP line takes
-- two raster lines of 794..796 pixels at 25MHz (31.8us).  The VGA output
-- shows it directly; for 15KHz output each pair of raster lines becomes one
-- 15KHz line (f18a_video_15k), so the 15KHz modes need an even number of
-- raster lines and use the 9918A / 9929A vertical layout:
--
--                    VGA          NTSC 15KHz       PAL 15KHz
--   lines            525          262 (524)        313 (626)
--   top border       48 (24*2)    27 (54)          51 (102)
--   active           192 (384)    192 (384)        192 (384)
--   bottom border    48           24 (48)          51 (102)
--   blank+sync       45           3 + 3 + 13       3 + 3 + 13
--   frame rate       59.97Hz      59.94Hz          50.17Hz
--
-- (raster lines in parentheses).  The 15KHz horizontal layout is fixed in
-- f18a_video_15k.

library ieee;
use ieee.std_logic_1164.all;

package f18a_video_pkg is

   type video_geom_t is record
      hmax     : integer;  -- last raster x (line is hmax + 1 pixels)
      vmax     : integer;  -- last raster y (frame is vmax + 1 lines)
      vsize    : integer;  -- raster lines with picture (border + active)
      vfp      : integer;  -- VGA vsync start line
      vsp      : integer;  -- VGA vsync end line
      ystart   : integer;  -- first raster line of the 192-line VDP area
      ystart30 : integer;  -- first raster line of the 240-line (30 row) area
   end record;

   constant GEOM_VGA  : video_geom_t := (
      hmax => 793, vmax => 524, vsize => 480, vfp => 490, vsp => 492,
      ystart => 48, ystart30 => 0);

   constant GEOM_NTSC : video_geom_t := (
      hmax => 795, vmax => 523, vsize => 486, vfp => 496, vsp => 498,
      ystart => 54, ystart30 => 2);

   constant GEOM_PAL  : video_geom_t := (
      hmax => 795, vmax => 625, vsize => 588, vfp => 598, vsp => 600,
      ystart => 102, ystart30 => 54);

   -- 15KHz vertical layout derived from the raster geometry.
   constant V15_BLANK_BEFORE_SYNC : integer := 3;
   constant V15_SYNC_LINES        : integer := 3;

   function video_geom(video_15k : std_logic; pal : std_logic) return video_geom_t;

end package;

package body f18a_video_pkg is

   function video_geom(video_15k : std_logic; pal : std_logic) return video_geom_t is
   begin
      if video_15k = '0' then
         return GEOM_VGA;
      elsif pal = '1' then
         return GEOM_PAL;
      else
         return GEOM_NTSC;
      end if;
   end function;

end package body;
