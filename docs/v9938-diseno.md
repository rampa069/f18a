# Diseño: compatibilidad V9938 en el F18A

Estado: aprobado el 2026-10-04 (beads f18a-5pv.1.1).

## 1. Objetivo y alcance

Hacer que el core pueda comportarse como un **V9938** (MSX2) además de como
el TMS9918A + F18A actual, para usarlo en cores MSX2 (OCM-PLD y similares)
sobre placas tipo MiST. Después vendrá el V9958 (f18a-5pv.2), que es un
superconjunto, así que todo lo de aquí se diseña pensando en él.

Referencias de comportamiento, por orden de confianza:

1. openMSX (`src/video/VDP*.cc`, `SpriteChecker.cc`, `VDPCmdEngine.cc`):
   es la emulación más contrastada con hardware real, incluido el timing.
2. *V9938 Technical Data Book* (Yamaha / ASCII).
3. El VDP de OCM-PLD (ESE-VDP) solo como referencia de cómo lo resolvieron
   en FPGA, nunca copiando código (licencia no comercial).

## 2. Plataformas

| Placa | FPGA | RAM interna | VRAM de 128 KB |
|---|---|---|---|
| Poseidon | Cyclone IV GX EP4CGX150 | ~720 M9K (~810 KB) | En BRAM (128 M9K) |
| SiDi | Cyclone IV E EP4CE22 | 66 M9K (~74 KB) | No cabe: SDRAM |

Plantilla de top, pines y SDRAM: el core FP-1100
(`~/fp1100_build/rtl/fp1100_top.sv`, `poseidon/*.qsf`, `rtl/fp1100_sdram.v`,
`common/mist-modules`).

**Decisión:** la VRAM se abstrae detrás de una interfaz con
petición/aceptación/dato válido (latencia variable), con dos
implementaciones:

- **BRAM** (Poseidon): latencia fija de 1-2 ciclos. Es la primera que se
  hace y la que valida todo el diseño.
- **SDRAM** (SiDi y similares): el controlador de la FP-1100 detrás de la
  misma interfaz. Se hace después, como tarea propia, sin tocar los motores.

Los motores de render actuales del F18A asumen BRAM con latencia de un
ciclo. Pasarlos a la interfaz con latencia variable es parte del trabajo de
la tarea base (f18a-5pv.1.3). El presupuesto con SDRAM es holgado: una línea
dura 5472 ciclos a 85,9 MHz y el peor caso (G7 + 8 sprites modo 2) son unos
256 + 32 + 56 bytes, es decir, ~200 accesos de 16 bits.

## 3. Modo de chip y registros

Nueva entrada estática `chip_i` en `f18a_core`:

| `chip_i` | Comportamiento |
|---|---|
| `TMS9918` | El F18A actual: 16 KB, registros VR0-VR7 enmascarados a 3 bits (o ignorados con `vr8_ignore_i`), extensiones F18A tras desbloquear por VR57 |
| `V9938` | 128 KB, registros R0-R46 del V9938, status S#0-S#9 |
| `V9958` | (más adelante) V9938 + R25-R27, YJK, ID 2 en S#1 |

### Choque con las extensiones F18A

El F18A usa VR8-VR63 para sus extensiones. En modo V9938 estos números son
los registros del V9938:

| Registros | V9938 | F18A |
|---|---|---|
| R8-R14 | modo, tablas, página de VRAM | VR10/VR11 segunda capa de tiles |
| R15 | puntero de status | VR15 contador / selector de status (parecido) |
| R16-R23 | paleta, indirecto, colores, ajuste, línea de interrupción, scroll | VR19 línea de interrupción (parecido) |
| R32-R46 | motor de comandos | VR24-VR36 paletas, scroll, bitmap layer |
| — | — | VR47-VR63: DPM, incremento, GPU, desbloqueo VR57 |

**Decisión:** en modo V9938, R0-R46 son siempre del V9938 (todos los
gráficos del V9938 están disponibles) y las *extensiones gráficas propias
del F18A* configuradas en VR8-VR46 (tiles ECM de 64 colores, segunda capa de
tiles, capa bitmap superpuesta, scroll suave y paletas extra del F18A) no
están disponibles: ningún software MSX las usa, y el de TI-99 / ColecoVision
que sí las usa sigue funcionando en modo TMS9918. Lo que está por encima
de R46 (VR47-VR63: desbloqueo, GPU, incremento, DPM) sigue funcionando
igual, porque el V9938 no tiene esos registros. Así la GPU del F18A sigue
disponible en modo V9938.

Más adelante, si interesa, se puede añadir un banco alternativo para las
extensiones gráficas del F18A en modo V9938; no hace falta para la
compatibilidad.

## 4. VRAM

- 128 KB, dirección de 17 bits: R14 (bits 16-14) y el contador de 14 bits
  de siempre, con el arrastre a R14 del V9938.
- 64 KB de expansión: no se implementan (casi ningún MSX2 la usa); R45 MXC
  y MXS/MXD apuntarían a VRAM inexistente, lectura FFh.
- **G6 y G7 (y G7 con YJK en el V9958) usan VRAM entrelazada:** el V9938
  lee dos bancos de 64 KB a la vez. Igual que openMSX, se guarda en orden
  físico y se rota la dirección lógica en esos modos:
  `física = ((lógica << 16) | (lógica >> 1)) & 0x1FFFF`. Afecta al acceso de
  la CPU y del motor de comandos; el render lee directamente en orden
  físico.
- Dos puertos:
  - **A**: CPU (con el read-ahead de siempre), GPU del F18A y motor de
    comandos, con arbitraje (CPU primero).
  - **B**: render (tiles, bitmap, sprites), secuencial dentro de cada línea
    como ahora.

En Poseidon son 128 M9K, que caben de sobra junto al resto del core.

## 5. Raster y geometría

Se amplía `f18a_video_pkg` y `f18a_raster` (ya nativo a 15 kHz) con los
datos de openMSX:

- **192 / 212 líneas** (R9 LN). En el V9938 la línea 0 del área activa cae
  27 / 17 líneas después del reset del contador en NTSC y 54 / 44 en PAL: el
  modo de 212 crece 10 líneas por arriba y 10 por abajo. Los bordes quedan:
  NTSC 27-192-24 o 17-212-14; PAL 51-192-51 o 41-212-41 (a verificar contra
  openMSX con el modelo).
- **R18 (ajuste)**: desplaza la imagen ±7/8 píxeles en horizontal (4 ciclos
  de 21 MHz por paso) y ±7/8 líneas en vertical (mueve el reset del
  contador de líneas).
- **R23 (scroll vertical)**: se suma a la línea VDP en todos los modos
  (envuelve en 256).
- **R19 + IE1 (interrupción de línea)**: compara con la línea de display;
  flag FH en S#1. HR y VR en S#2 con las posiciones de openMSX.
- **Entrelazado (R9 IL/EO)**: 262/263 o 312/313 líneas alternas, flag EO en
  S#2, páginas pares/impares en G4-G7. El scandoubler de la placa (o el del
  wrapper) tiene que aceptarlo; se hace al final de la tarea de geometría.
- **Modo texto**: openMSX lo coloca 6 píxeles a la derecha en el TMS9918 y 9
  en el V99x8. **El F18A actual lo pone a 8**, así que hoy está 2 píxeles
  desplazado respecto a un TMS9918 real. Se corrige con el mismo cambio
  (6 en modo TMS9918, 9 en V9938) y se actualiza el modelo.

## 6. Render

Línea a línea, como ahora: durante la línea N se prepara la N+1 en un
buffer de línea y se muestra el otro. Se mantiene el reparto actual:

| Bloque | Modos | Plan |
|---|---|---|
| Motor de tiles F18A | T1, T2, G1, G2, G3, MC | Reutilizar. Añadir 212 líneas, R23, direcciones de 17 bits, parpadeo de T2 (R12/R13), máscaras de tabla del V9938 (R2-R4, R10, R11) |
| Motor bitmap nuevo | G4, G5, G6, G7 (y YJK en V9958) | Nuevo: lectura lineal de la página (R2), 4/2/4/8 bpp, entrelazado en G6/G7 |
| Sprites F18A | Modo 1 (G1, G2, MC) | Reutilizar como está |
| Sprites modo 2 nuevos | G3-G7 | Nuevo: 8 por línea, color por línea, CC/IC/EC, colisiones con coordenadas (S#3-S#6) |
| Color / paleta | todos | Ampliar: paleta del V9938 (16 entradas de 9 bits, R16 + 9Ah), G7 de 256 colores fijos (3-3-2), TP (R8). Salida a 4 bits por canal como ahora (3 bits del V9938 replicados) |

El buffer de línea sigue en 8 bits por píxel (PIX, PRI, índice de 6 bits):
en G7 la entrada es directamente el color GGGRRRBB, porque G7 no tiene
transparencia ni prioridad, y `f18a_color` lo convierte a RGB sin pasar
por la paleta (azul de 2 bits a los niveles 0, 2, 4, 7, como openMSX). Los
sprites de G7 usan los 16 colores fijos y el borde es R#7 entero. Las placas MiST tienen DAC de 6 bits por canal; la salida puede
crecer a 6 bits más adelante sin cambiar el resto.

## 7. Motor de comandos

Módulo nuevo en el puerto A de la VRAM, con prioridad por debajo de la CPU.
Registros R32-R46, comandos HMMC, YMMM, HMMM, HMMV, LMMC, LMCM, LMMM, LMMV,
LINE, SRCH, PSET, POINT y STOP, operaciones lógicas (IMP, AND, OR, EOR,
NOT y sus variantes T), flags CE/TR/BD en S#2 y S#7-S#9.

**Implementado** (f18a-5pv.1.11) en `f18a_v9938_cmd.vhd`, dentro de
`f18a_cpu`: accede al puerto A cuando la CPU no lo usa (pausa la GPU del
F18A como la CPU). Sigue la semántica de openMSX actual (git master), que
difiere de openMSX 20.0 en BD tras SRCH / lectura de S#9 y en LINE por
encima de la línea 0; el modelo `sim/v9938_cmd.py` se valida contra un
openMSX compilado de master (imagen `openmsx-master`). Los comandos en modos
no bitmap (V9958, R#25 CMD) quedan para f18a-5pv.2.

**Velocidad:** primero funcionalmente correcto y tan rápido como deje la
BRAM (mucho más rápido que el V9938). Algunos juegos dependen de la
velocidad real; el modelo de tiempos de openMSX (`VDPAccessSlots`) se
estudia aparte, como opción (equivalente al VDPSPEEDMODE de OCM).

## 8. Validación

- **Modelo de referencia** `sim/v9938_model.py`, que crece con cada
  subtarea: registros, VRAM con entrelazado, paleta, todos los modos,
  sprites modo 1 y 2, comandos.
- **openMSX como oráculo**: un script que arranca openMSX en modo
  `-machine` MSX2 sin interfaz, ejecuta un programa de prueba, y vuelca
  VRAM, registros y una captura (`vram`, `vdpregs`, `screenshot -raw`). El
  modelo se valida contra esas capturas, y el hardware simulado contra el
  modelo, como hasta ahora.
- Cada subtarea añade escenas en `sim/scenes.py` y tests en `sim/tests/`.

## 9. Recursos (Poseidon)

| | Ahora | Estimado V9938 |
|---|---|---|
| VRAM | 16 M9K | 128 M9K |
| Resto (buffers, paleta, GPU) | ~8 M9K | ~14 M9K |
| Lógica | ~4.500 LEs | ~10-14 kLEs (comandos, bitmap, sprites modo 2) |

El EP4CGX150 tiene ~150 kLEs y ~720 M9K: no es un problema.

## 10. Decisiones (confirmadas)

1. **Extensiones del F18A en modo V9938**: solo VR47-VR63 (GPU, desbloqueo,
   incremento, DPM); las extensiones gráficas propias del F18A quedan para
   el modo TMS9918. Un banco alternativo para tenerlas también en modo
   V9938 queda como mejora opcional.
2. **Velocidad del motor de comandos**: rápido primero, timing exacto del
   V9938 como opción posterior.
3. **VRAM de expansión (64 KB extra)**: no se implementa.
4. **Posición del modo texto**: 6 píxeles (TMS9918) / 9 (V9938) como el
   hardware real.
5. **Primera placa**: Poseidon con VRAM en BRAM; la SDRAM (SiDi) después.

## 11. Cambios en el plan de beads

- f18a-5pv.1.3 (base) incluye la interfaz de VRAM con latencia variable y
  el modo de chip `chip_i`.
- Nueva tarea: **backend de VRAM en SDRAM** (SiDi), después de la base.
- Nueva tarea: **posición del modo texto** (6 / 9 píxeles), puede hacerse
  ya en modo TMS9918.
- f18a-hf5 (hardware): top MiST para Poseidon, que pasa a ser la primera
  plataforma de pruebas reales.
