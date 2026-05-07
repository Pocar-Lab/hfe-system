# Dissertation Figure Captions

## 01_control_telemetry_architecture: Control, Telemetry, and Logging Architecture

Combined system and firmware-loop view. UI commands pass from the lab computer to the Arduino over USB serial. Manual output commands are applied by the firmware, while stored auto settings are used in the scheduled 1 Hz measurement, control, and telemetry pass. Box shading separates the operator/computer side, Arduino firmware, physical hardware, and logged data.

- PDF: `generated/01_control_telemetry_architecture.pdf`

## 02_ln_valve_auto_logic: LN Valve Auto-Mode Logic

Automatic liquid-nitrogen valve control. Each scan reads the TMI and THI temperatures, closes on cold-limit conditions, and reopens only after both recovery criteria are satisfied.

- PDF: `generated/02_ln_valve_auto_logic.pdf`

## 03_key_measurements: Key Measurements and Interlocks

Experimental signals used for control, monitoring, and later analysis. The table keeps hardware context without listing implementation-level field names.

- PDF: `generated/03_key_measurements.pdf`

## 04_electrical_wiring_overview: Electrical Wiring Overview

Simplified cabinet wiring overview. Wire colors follow the cabinet convention: yellow data/signal, red 24 VDC, blue COM, green earth, black for 120 VAC or 5 VDC, and gray for the VFD three-phase feed and motor output.

- PDF: `generated/04_electrical_wiring_overview.pdf`
- Lucidchart import: `generated/04_electrical_wiring_overview.drawio`

## 05_tc_backplate_detail: Thermocouple Backplate Detail

Thermocouple backplate logic diagram shown in the installed orientation. The board is mounted upside down in the electrical box, so U9 is the top-left socket and U0 is the bottom-right socket. The connector feeds shared V_{in}, SDI/SDO, and SCK signals to the MAX31856 amplifier groups; GND is provided through the PCB area, and only the CS paths are separated by channel.

- PDF: `generated/05_tc_backplate_detail.pdf`
