#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_MAX31856.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

// ── Shared software-SPI pins ─────────────────────────────────────────────
constexpr int SCK_PIN  = 8;   // CLK
constexpr int MOSI_PIN = 2;   // DI  (MCU -> MAX31856)
constexpr int MISO_PIN = 22;  // DO  (MAX31856 -> MCU)

// ── CS pins for U0..U9 (U9 on D48) ───────────────────────────────────────
constexpr uint8_t CS_PINS[] = { 9, 3, 23, 31, 39, 47, 30, 38, 46, 48 };
// Installed probes are T-type everywhere except the HX probes on U8/U9.
// Leave unused channels on the default K-type unless/until wiring is assigned.
constexpr max31856_thermocoupletype_t TC_TYPES[] = {
  MAX31856_TCTYPE_K, // U0 (unused)
  MAX31856_TCTYPE_K, // U1 (unused)
  MAX31856_TCTYPE_T, // U2 / TTEST
  MAX31856_TCTYPE_T, // U3 / TFO
  MAX31856_TCTYPE_T, // U4 / TTI
  MAX31856_TCTYPE_K, // U5 (unused)
  MAX31856_TCTYPE_T, // U6 / TTO
  MAX31856_TCTYPE_T, // U7 / TMI
  MAX31856_TCTYPE_K, // U8 / THI
  MAX31856_TCTYPE_K, // U9 / THM
};
constexpr size_t  NUM_TCS   = sizeof(CS_PINS) / sizeof(CS_PINS[0]);

// ── Always emit 10 columns: temp0_C .. temp9_C ───────────────────────────
constexpr size_t MAX_TCS_OUT = 10;

// ── Valve output ─────────────────────────────────────────────────────────
constexpr int VALVE_PIN = 7;

// ── Heater relays ────────────────────────────────────────────────────────
constexpr int HEATER_BOTTOM_PIN = 11;  // tank bottom heater relay
constexpr int HEATER_EXHAUST_PIN = 5;  // LN exhaust heater relay

// ── Pump / VFD (Fuji FRENIC-Mini) ────────────────────────────────────────
constexpr uint8_t  PWM_PIN           = 6;       // OC4A on Arduino Mega
constexpr uint16_t PWM_TOP           = 999;     // 2 kHz with prescaler 8
constexpr float    PUMP_CMD_MAX_PCT  = 100.0f;  // clamp analog command to 0–100 % of full scale
constexpr float    PUMP_MAX_FREQ_HZ  = 71.7f;   // 100% -> 71.7 Hz (≈2150 rpm, ≈4.0 L/min HFE)
constexpr float    VFD_RATED_CURRENT_A = 3.4f;  // motor nameplate FLA at 230 V
constexpr float    VFD_RATED_OUTPUT_POWER_W = 746.0f; // 1 HP motor output
constexpr float    MOTOR_NAMEPLATE_EFFICIENCY = 0.855f;
constexpr float    VFD_EST_RATED_INPUT_POWER_W =
    VFD_RATED_OUTPUT_POWER_W / MOTOR_NAMEPLATE_EFFICIENCY;
constexpr float    VFD_BASE_VOLTAGE  = 230.0f;  // nominal output voltage for % display
constexpr float    MOTOR_EST_RPM_PER_HZ = 30.0f; // 4-pole motor estimate; actual shaft RPM is slightly lower under load
constexpr uint8_t  VFD_SLAVE_ADDR    = 1;       // y01
constexpr uint32_t VFD_BAUD          = 9600;    // y04
constexpr unsigned long VFD_POLL_MS  = 1000UL;  // poll VFD monitor registers once per second

// ── Flow meter (KROHNE MFC400 via DFR0845 on Serial2) ───────────────────
// MFC400 Modbus supplement (ER 1.0) maps 30000..30008 as five float input registers:
// Flow Velocity, Volume Flow, Mass Flow, Temperature, Density.
constexpr uint8_t  FLOW_SLAVE_ADDR   = 1;
constexpr uint32_t FLOW_BAUD         = 19200;
constexpr unsigned long FLOW_POLL_MS = 1000UL;
constexpr uint16_t FLOW_REG_START    = 30000;   // 30000..30008, five floats
constexpr uint8_t  FLOW_REG_COUNT    = 10;      // 10 x 16-bit regs = 5 x float32
constexpr float    FLUID_CONC_PCT    = 100.0f;
constexpr char     FLUID_NAME[]      = "HFE-7200";
// ── Pressure sensors (0–5.013 V = 10 bar gauge) ─────────────────────────
// IMPORTANT: these must be analog-capable pins (A0–A15 on the Mega). If you move the wiring,
// update these constants to the matching Ax (or 54..69) numbers.
constexpr uint8_t PRESSURE_PIN_BEFORE = A8;  // before pump
constexpr uint8_t PRESSURE_PIN_AFTER  = A0;  // after pump
constexpr uint8_t PRESSURE_PIN_TANK   = A1;  // tank
constexpr float   PRESSURE_FSO_V      = 5.013f;   // full-scale output voltage
constexpr float   PRESSURE_FSO_BAR    = 10.0f;    // full-scale in bar (gauge)
constexpr float   PRESSURE_ERR_BAR    = 0.05f;    // sensor accuracy (±)
constexpr float   ADC_REF_V           = 5.0f;     // default analog reference (5 V)
constexpr float   PSI_PER_BAR         = 14.5037738f;
constexpr float   ATMOSPHERE_BAR      = 1.01325f; // add for absolute pressure display
constexpr float   PRESSURE_AFTER_ZERO_V = 0.029f; // 1 atm output for after-pump sensor
constexpr float   PUMP_DELTA_P_ESTOP_BAR = 8.0f;  // emergency-stop threshold for after-before pressure delta

// Modbus group M registers (Fuji FRENIC-Mini)
constexpr uint16_t REG_M09 = 0x0809;  // output frequency (0.01 Hz)
constexpr uint8_t  N_M_REG = 4;       // M09–M12 inclusive

// Modbus group W registers (Fuji FRENIC-Mini Monitor 2)
constexpr uint16_t REG_W05 = 0x0F05;  // output current (RTU format [19], engineering units)
constexpr uint16_t REG_W21 = 0x0F15;  // input power   (RTU format [24], engineering units)
constexpr uint8_t  N_W_DRIVE_REG = 2; // W05–W06 inclusive

// ── Control parameters ───────────────────────────────────────────────────
constexpr float DEFAULT_HFE_GOAL_C           = -110.0f; // °C, LXe reference temperature
constexpr float DEFAULT_HX_LIMIT_C           = -120.0f; // °C, HFE icing guard at THI
constexpr float DEFAULT_LN_AUTO_HYSTERESIS_C = 0.5f;    // °C, HFE goal reopen margin
constexpr float DEFAULT_HX_APPROACH_C        = 10.0f;   // °C, THI reopen margin below TMI
constexpr size_t HFE_AUTO_SENSOR_INDEX       = 7;       // U7 = TMI
constexpr size_t THI_SENSOR_INDEX            = 8;       // U8 = THI

// ── Valve/override state ─────────────────────────────────────────────────
enum ValveState   : uint8_t { CLOSED = 0, OPEN = 1 };
enum OverrideMode : uint8_t { AUTO = 0, FORCE_OPEN = 1, FORCE_CLOSE = 2 };

constexpr OverrideMode DEFAULT_VALVE_MODE = FORCE_CLOSE;

static ValveState   g_valve = CLOSED;
static OverrideMode g_mode  = DEFAULT_VALVE_MODE;
static float        g_hfe_goal_c = DEFAULT_HFE_GOAL_C;
static float        g_hx_limit_c = DEFAULT_HX_LIMIT_C;
static float        g_ln_auto_hysteresis_c = DEFAULT_LN_AUTO_HYSTERESIS_C;
static float        g_hx_approach_c = DEFAULT_HX_APPROACH_C;
static bool         g_auto_close_latched = false;
static bool         g_auto_status_sampled = false;
static bool         g_heater_bottom_on = false;
static bool         g_heater_exhaust_on = false;

// ── Sensor objects (software SPI: (CS, DI, DO, CLK)) ─────────────────────
static Adafruit_MAX31856* tc[NUM_TCS] = { nullptr };

// ── Timing ───────────────────────────────────────────────────────────────
static unsigned long lastSample = 0;
static unsigned long lastVfdPoll = 0;
static unsigned long lastFlowPoll = 0;
constexpr unsigned long SAMPLE_INTERVAL_MS = 1000UL;

// ── Pump / VFD state ─────────────────────────────────────────────────────
HardwareSerial &VFD = Serial3;
HardwareSerial &FLOW = Serial2;

struct VfdSnapshot {
  bool   valid;
  float  freqHz;
  float  inputPowerPct;
  float  outputCurrentPct;
  float  rotationSpeedRpm;
  float  inputPowerKw;
  float  inputPowerW;
  float  outputCurrentA;
  float  outputVoltageV;
  unsigned long lastPollMs;
};

static VfdSnapshot g_vfd = { false, NAN, NAN, NAN, NAN, NAN, NAN, NAN, NAN, 0 };
static float       g_pump_cmd_pct = 0.0f;

struct FlowSnapshot {
  bool   valid;
  float  flowVelocityMps;
  float  volumeFlowM3s;
  float  massFlowKgS;
  float  temperatureRaw;
  float  densityKgM3;
  unsigned long lastPollMs;
};

static FlowSnapshot g_flow = { false, NAN, NAN, NAN, NAN, NAN, 0 };

enum AutoCloseReason : uint8_t {
  AUTO_CLOSE_NONE = 0,
  AUTO_CLOSE_MISSING_THI,
  AUTO_CLOSE_MISSING_HFE_TEMP,
  AUTO_CLOSE_THI_LIMIT,
  AUTO_CLOSE_HFE_GOAL,
};

struct AutoValveStatus {
  bool thiValid;
  bool hfeValid;
  bool closeRequested;
  bool readyToOpen;
  AutoCloseReason reason;
  float thiTempC;
  float hfeTempC;
  float thiCloseThresholdC;
  float hfeCloseThresholdC;
  float thiReopenThresholdC;
  float hfeReopenThresholdC;
};

static AutoValveStatus g_auto_status = {
  false,
  false,
  true,
  false,
  AUTO_CLOSE_MISSING_THI,
  NAN,
  NAN,
  DEFAULT_HX_LIMIT_C,
  DEFAULT_HFE_GOAL_C,
  NAN,  // thiReopenThresholdC resolves from TMI at runtime
  DEFAULT_HFE_GOAL_C + DEFAULT_LN_AUTO_HYSTERESIS_C,
};

enum SafetyLawIndex : uint8_t {
  SAFETY_LAW_PUMP_DELTA_P_HIGH = 0,
};

struct SafetyLawState {
  const char* key;
  const char* label;
  bool  enabled;
  bool  active;
  bool  tripped;
  float limitBar;
  float valueBar;
};

static SafetyLawState g_safety_laws[] = {
  { "pump_delta_p_high", "Pump delta P high", true, false, false, PUMP_DELTA_P_ESTOP_BAR, NAN },
};

static bool          g_emergency_stop_latched = false;
static unsigned long g_emergency_stop_ms = 0;

// ── Helpers ──────────────────────────────────────────────────────────────
static float readPressureVolts(uint8_t pin) {
  int raw = analogRead(pin);
  if (raw < 0 || raw > 1023) return NAN;
  return raw * (ADC_REF_V / 1023.0f);
}

static float voltsToBar(float volts) {
  if (!isfinite(volts)) return NAN;
  float bar = volts * (PRESSURE_FSO_BAR / PRESSURE_FSO_V);
  if (!isfinite(bar)) return NAN;
  if (bar < 0.02f) bar = 0.0f; // clamp small offsets/noise
  return bar;
}

static float voltsToBarAfter(float volts) {
  if (!isfinite(volts)) return NAN;
  const float slope = PRESSURE_FSO_BAR / (PRESSURE_FSO_V - PRESSURE_AFTER_ZERO_V);
  float bar = (volts - PRESSURE_AFTER_ZERO_V) * slope;
  if (!isfinite(bar)) return NAN;
  if (fabs(bar) < 0.02f) bar = 0.0f; // deadband around atmospheric for noise
  return bar;
}

static void applyValve(ValveState v) {
  g_valve = v;
  digitalWrite(VALVE_PIN, v == OPEN ? HIGH : LOW);
}

static void applyHeaterBottom(bool on) {
  g_heater_bottom_on = on;
  digitalWrite(HEATER_BOTTOM_PIN, on ? HIGH : LOW);
}

static void applyHeaterExhaust(bool on) {
  g_heater_exhaust_on = on;
  digitalWrite(HEATER_EXHAUST_PIN, on ? HIGH : LOW);
}

static void setupPwm2kHz() {
  pinMode(PWM_PIN, OUTPUT);

  // Fast PWM, TOP = ICR4 (mode 14), non-inverting on OC4A, prescaler = 8
  TCCR4A = _BV(COM4A1) | _BV(WGM41);
  TCCR4B = _BV(WGM43)  | _BV(WGM42) | _BV(CS41);

  ICR4  = PWM_TOP;  // TOP -> 2 kHz
  OCR4A = 0;        // start at 0 %
}

static void setDuty(float frac) {
  if (!isfinite(frac)) frac = 0.0f;
  if (frac < 0.0f) frac = 0.0f;
  if (frac > 1.0f) frac = 1.0f;
  OCR4A = static_cast<uint16_t>(frac * PWM_TOP + 0.5f);
}

static float setPumpCommandPct(float pct) {
  if (!isfinite(pct)) pct = 0.0f;
  if (pct < 0.0f) pct = 0.0f;
  if (pct > PUMP_CMD_MAX_PCT) pct = PUMP_CMD_MAX_PCT;
  g_pump_cmd_pct = pct;
  setDuty(pct / 100.0f);
  return g_pump_cmd_pct;
}

static size_t safetyLawCount() {
  return sizeof(g_safety_laws) / sizeof(g_safety_laws[0]);
}

static int firstSafetyLawIndexByState(bool wantActive) {
  for (size_t i = 0; i < safetyLawCount(); ++i) {
    const bool match = wantActive ? g_safety_laws[i].active : g_safety_laws[i].tripped;
    if (match) return static_cast<int>(i);
  }
  return -1;
}

static bool canResetEmergencyStop() {
  for (size_t i = 0; i < safetyLawCount(); ++i) {
    if (g_safety_laws[i].enabled && g_safety_laws[i].active) return false;
  }
  return true;
}

static void triggerEmergencyStop(size_t idx, unsigned long nowMs) {
  if (idx >= safetyLawCount()) return;
  SafetyLawState &law = g_safety_laws[idx];
  if (!law.enabled) return;

  setPumpCommandPct(0.0f);
  g_emergency_stop_latched = true;
  g_emergency_stop_ms = nowMs;

  if (law.tripped) return;

  law.tripped = true;
  Serial.print(F("# Emergency stop tripped: "));
  Serial.print(law.key);
  if (isfinite(law.valueBar)) {
    Serial.print(F(" ("));
    Serial.print(law.valueBar, 3);
    Serial.print(F(" bar > "));
    Serial.print(law.limitBar, 3);
    Serial.print(F(" bar)"));
  }
  Serial.println();
}

static void updatePumpDeltaPSafety(float pressureBeforeBar, float pressureAfterBar, unsigned long nowMs) {
  SafetyLawState &law = g_safety_laws[SAFETY_LAW_PUMP_DELTA_P_HIGH];
  const float deltaPBar =
    (isfinite(pressureBeforeBar) && isfinite(pressureAfterBar))
      ? (pressureAfterBar - pressureBeforeBar)
      : NAN;

  law.valueBar = deltaPBar;
  law.active = law.enabled && isfinite(deltaPBar) && (deltaPBar > law.limitBar);

  if (law.active) {
    triggerEmergencyStop(SAFETY_LAW_PUMP_DELTA_P_HIGH, nowMs);
  }
}

static void resetEmergencyStopIfSafe() {
  if (!g_emergency_stop_latched) {
    Serial.println(F("# Emergency stop already cleared"));
    return;
  }

  if (!canResetEmergencyStop()) {
    const int idx = firstSafetyLawIndexByState(true);
    Serial.print(F("# Emergency stop reset blocked"));
    if (idx >= 0) {
      const SafetyLawState &law = g_safety_laws[idx];
      Serial.print(F(": "));
      Serial.print(law.key);
      if (isfinite(law.valueBar)) {
        Serial.print(F(" still at "));
        Serial.print(law.valueBar, 3);
        Serial.print(F(" bar"));
      }
    }
    Serial.println();
    return;
  }

  for (size_t i = 0; i < safetyLawCount(); ++i) {
    g_safety_laws[i].tripped = false;
  }
  g_emergency_stop_latched = false;
  g_emergency_stop_ms = 0;
  Serial.println(F("# Emergency stop reset"));
}

// Modbus RTU CRC16
static uint16_t modbusCRC(const uint8_t *data, size_t len) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < len; ++i) {
    crc ^= data[i];
    for (uint8_t b = 0; b < 8; ++b) {
      if (crc & 0x0001) {
        crc >>= 1;
        crc ^= 0xA001;
      } else {
        crc >>= 1;
      }
    }
  }
  return crc;
}

static float regsToFloatBE(const uint16_t *regs) {
  // MFC400 C6.8.4 defaults to Big Endian for multi-register values.
  union {
    uint32_t u32;
    float f32;
  } value;
  value.u32 = (static_cast<uint32_t>(regs[0]) << 16) | regs[1];
  return value.f32;
}

// Read N_M_REG contiguous registers starting at M09 (FC=0x03)
static bool vfdReadHoldingRegs(uint16_t startReg, uint8_t regCount, uint16_t *vals) {
  uint8_t frame[8];

  frame[0] = VFD_SLAVE_ADDR;
  frame[1] = 0x03;             // Read Holding Registers
  frame[2] = startReg >> 8;
  frame[3] = startReg & 0xFF;
  frame[4] = 0x00;
  frame[5] = regCount;

  uint16_t crc = modbusCRC(frame, 6);
  frame[6] = crc & 0xFF;
  frame[7] = crc >> 8;

  while (VFD.available()) VFD.read(); // clear stale bytes

  VFD.write(frame, 8);
  VFD.flush();

  // Expected reply: addr, func, byteCount (=2*N), data(2*N), CRC(2)
  const uint8_t expectedLen = 3 + 2 * regCount + 2;
  uint8_t buf[32];
  uint8_t len = 0;
  unsigned long start = millis();

  while ((millis() - start) < 200 && len < expectedLen) {
    if (VFD.available()) {
      buf[len++] = static_cast<uint8_t>(VFD.read());
    }
  }

  if (len != expectedLen) {
    return false;
  }

  uint16_t crcResp = (uint16_t)buf[len - 1] << 8 | buf[len - 2];
  uint16_t crcCalc = modbusCRC(buf, len - 2);
  if (crcResp != crcCalc) {
    return false;
  }

  if (buf[0] != VFD_SLAVE_ADDR || buf[1] != 0x03) {
    return false;
  }

  uint8_t byteCount = buf[2];
  if (byteCount != 2 * regCount) {
    return false;
  }

  for (uint8_t i = 0; i < regCount; ++i) {
    uint8_t hi = buf[3 + 2 * i];
    uint8_t lo = buf[4 + 2 * i];
    vals[i] = ((uint16_t)hi << 8) | lo;
  }

  return true;
}

// Read N_M_REG contiguous registers starting at M09 (FC=0x03)
static bool vfdReadM09toM12(uint16_t *vals) {
  return vfdReadHoldingRegs(REG_M09, N_M_REG, vals);
}

// RTU data format [19]: current value in engineering units.
// For the small FRENIC-Mini used here, the minimum step is 0.01 A.
static float vfdDecodeFormat19CurrentA(uint16_t raw) {
  return raw / 100.0f;
}

// RTU data format [24]: 2-bit exponent + 14-bit mantissa floating point.
// Exponent 0..3 maps to decimal shifts of 10^-2, 10^-1, 10^0, 10^1.
static float vfdDecodeFormat24(uint16_t raw) {
  const uint16_t exponent = (raw >> 14) & 0x0003;
  const uint16_t mantissa = raw & 0x3FFF;
  if (mantissa == 0) return 0.0f;

  float value = static_cast<float>(mantissa);
  switch (exponent) {
    case 0: return value * 0.01f;
    case 1: return value * 0.1f;
    case 2: return value;
    default: return value * 10.0f;
  }
}

static bool pollVfd() {
  uint16_t mVals[N_M_REG];
  uint16_t wDriveVals[N_W_DRIVE_REG];
  uint16_t wPowerVal[1];

  const bool okM = vfdReadM09toM12(mVals);
  const bool okWDrive = vfdReadHoldingRegs(REG_W05, N_W_DRIVE_REG, wDriveVals);
  const bool okWPower = vfdReadHoldingRegs(REG_W21, 1, wPowerVal);

  g_vfd.lastPollMs = millis();

  g_vfd.valid = okM || okWDrive || okWPower;
  g_vfd.freqHz = NAN;
  g_vfd.inputPowerPct = NAN;
  g_vfd.outputCurrentPct = NAN;
  g_vfd.rotationSpeedRpm = NAN;
  g_vfd.inputPowerKw = NAN;
  g_vfd.inputPowerW = NAN;
  g_vfd.outputCurrentA = NAN;
  g_vfd.outputVoltageV = NAN;

  if (!g_vfd.valid) {
    return false;
  }

  if (okM) {
    g_vfd.freqHz           = mVals[0] / 100.0f;  // M09, 0.01 Hz units
    g_vfd.inputPowerPct    = mVals[1] / 100.0f;  // M10, 0.01 % of nominal applicable motor output
    g_vfd.outputCurrentPct = mVals[2] / 100.0f;  // M11, 0.01 % of inverter rated current
    if (!okWDrive) {
      g_vfd.outputVoltageV = mVals[3] * 0.1f;    // M12, 0.1 V representation
    }
    if (MOTOR_EST_RPM_PER_HZ > 0.0f) {
      g_vfd.rotationSpeedRpm = g_vfd.freqHz * MOTOR_EST_RPM_PER_HZ;
    }
  }

  if (okWDrive) {
    g_vfd.outputCurrentA = vfdDecodeFormat19CurrentA(wDriveVals[0]); // W05
    g_vfd.outputVoltageV = wDriveVals[1] * 0.1f;                     // W06, format [3]
    if (!okM && VFD_RATED_CURRENT_A > 0.0f) {
      g_vfd.outputCurrentPct = g_vfd.outputCurrentA / VFD_RATED_CURRENT_A * 100.0f;
    }
  } else if (okM && VFD_RATED_CURRENT_A > 0.0f) {
    g_vfd.outputCurrentA = g_vfd.outputCurrentPct * 0.01f * VFD_RATED_CURRENT_A;
  }

  if (okWPower) {
    g_vfd.inputPowerKw = vfdDecodeFormat24(wPowerVal[0]);    // W21
    g_vfd.inputPowerW = g_vfd.inputPowerKw * 1000.0f;
    if (!okM && VFD_EST_RATED_INPUT_POWER_W > 0.0f) {
      g_vfd.inputPowerPct = g_vfd.inputPowerW / VFD_EST_RATED_INPUT_POWER_W * 100.0f;
    }
  } else if (okM && VFD_RATED_OUTPUT_POWER_W > 0.0f) {
    g_vfd.inputPowerW = g_vfd.inputPowerPct * 0.01f * VFD_RATED_OUTPUT_POWER_W;
    g_vfd.inputPowerKw = g_vfd.inputPowerW / 1000.0f;
  }

  return true;
}

static bool flowReadMeasurements(uint16_t *vals) {
  uint8_t frame[8];

  frame[0] = FLOW_SLAVE_ADDR;
  frame[1] = 0x04;                  // Read Input Registers
  frame[2] = FLOW_REG_START >> 8;
  frame[3] = FLOW_REG_START & 0xFF;
  frame[4] = 0x00;
  frame[5] = FLOW_REG_COUNT;

  uint16_t crc = modbusCRC(frame, 6);
  frame[6] = crc & 0xFF;
  frame[7] = crc >> 8;

  while (FLOW.available()) FLOW.read();

  FLOW.write(frame, 8);
  FLOW.flush();

  const uint8_t expectedLen = 3 + 2 * FLOW_REG_COUNT + 2;
  uint8_t buf[32];
  uint8_t len = 0;
  unsigned long start = millis();

  while ((millis() - start) < 250 && len < expectedLen) {
    if (FLOW.available()) {
      buf[len++] = static_cast<uint8_t>(FLOW.read());
    }
  }

  if (len != expectedLen) {
    return false;
  }

  uint16_t crcResp = (uint16_t)buf[len - 1] << 8 | buf[len - 2];
  uint16_t crcCalc = modbusCRC(buf, len - 2);
  if (crcResp != crcCalc) {
    return false;
  }

  if (buf[0] != FLOW_SLAVE_ADDR || buf[1] != 0x04) {
    return false;
  }

  if (buf[2] != 2 * FLOW_REG_COUNT) {
    return false;
  }

  for (uint8_t i = 0; i < FLOW_REG_COUNT; ++i) {
    vals[i] = ((uint16_t)buf[3 + 2 * i] << 8) | buf[4 + 2 * i];
  }

  return true;
}

static bool pollFlowMeter() {
  uint16_t regs[FLOW_REG_COUNT];
  const bool ok = flowReadMeasurements(regs);
  g_flow.lastPollMs = millis();
  if (!ok) {
    g_flow.valid = false;
    g_flow.flowVelocityMps = NAN;
    g_flow.volumeFlowM3s = NAN;
    g_flow.massFlowKgS = NAN;
    g_flow.temperatureRaw = NAN;
    g_flow.densityKgM3 = NAN;
    return false;
  }

  g_flow.valid = true;
  g_flow.flowVelocityMps = regsToFloatBE(&regs[0]);
  g_flow.volumeFlowM3s   = regsToFloatBE(&regs[2]);
  g_flow.massFlowKgS     = regsToFloatBE(&regs[4]);
  g_flow.temperatureRaw  = regsToFloatBE(&regs[6]);
  g_flow.densityKgM3     = regsToFloatBE(&regs[8]);
  return true;
}

static bool tryParseFloat(const String& text, float *out) {
  if (!out) return false;
  String trimmed = text;
  trimmed.trim();
  if (!trimmed.length()) return false;
  if (trimmed.length() >= 32) return false;

  char buf[32];
  trimmed.toCharArray(buf, sizeof(buf));

  char *endPtr = nullptr;
  const double value = strtod(buf, &endPtr);
  if (endPtr == buf || (endPtr && *endPtr != '\0') || !isfinite(value)) return false;

  *out = static_cast<float>(value);
  return true;
}

static bool parseFloatSuffix(const String& cmd, size_t prefixLen, float *out) {
  if (!out) return false;
  String rest = cmd.substring(prefixLen);
  rest.trim();
  return tryParseFloat(rest, out);
}

static bool parseFloatArgs(const String& cmd, size_t prefixLen, float values[], size_t count) {
  if (!values || count == 0) return false;
  String rest = cmd.substring(prefixLen);
  rest.trim();
  if (!rest.length() || rest.length() >= 80) return false;

  char buf[80];
  rest.toCharArray(buf, sizeof(buf));
  char *cursor = buf;

  for (size_t i = 0; i < count; ++i) {
    while (*cursor == ' ' || *cursor == '\t' || *cursor == ',') ++cursor;
    if (*cursor == '\0') return false;

    char *endPtr = nullptr;
    const double value = strtod(cursor, &endPtr);
    if (endPtr == cursor || !isfinite(value)) return false;
    values[i] = static_cast<float>(value);
    cursor = endPtr;
  }

  while (*cursor == ' ' || *cursor == '\t' || *cursor == ',') ++cursor;
  return *cursor == '\0';
}

static const char* autoCloseReasonKey(AutoCloseReason reason) {
  switch (reason) {
    case AUTO_CLOSE_MISSING_THI: return "missing_thi";
    case AUTO_CLOSE_MISSING_HFE_TEMP: return "missing_hfe_temp";
    case AUTO_CLOSE_THI_LIMIT: return "thi_limit";
    case AUTO_CLOSE_HFE_GOAL: return "hfe_goal";
    default: return "none";
  }
}

static void updateAutoValveStatusFromValues(float thiTemp, float hfeTempC) {
  g_auto_status.thiTempC = thiTemp;
  g_auto_status.hfeTempC = hfeTempC;
  g_auto_status.thiValid = isfinite(thiTemp);
  g_auto_status.hfeValid = isfinite(hfeTempC);
  g_auto_status.thiCloseThresholdC = g_hx_limit_c;
  g_auto_status.hfeCloseThresholdC = g_hfe_goal_c;
  // THI reopens when it approaches TMI within g_hx_approach_c; HFE reopen keeps hysteresis.
  g_auto_status.thiReopenThresholdC = isfinite(hfeTempC) ? (hfeTempC - g_hx_approach_c) : NAN;
  g_auto_status.hfeReopenThresholdC = g_hfe_goal_c + g_ln_auto_hysteresis_c;
  g_auto_status.closeRequested = false;
  g_auto_status.readyToOpen = false;
  g_auto_status.reason = AUTO_CLOSE_NONE;

  if (!g_auto_status.thiValid) {
    g_auto_status.closeRequested = true;
    g_auto_status.reason = AUTO_CLOSE_MISSING_THI;
    return;
  }

  if (!g_auto_status.hfeValid) {
    g_auto_status.closeRequested = true;
    g_auto_status.reason = AUTO_CLOSE_MISSING_HFE_TEMP;
    return;
  }

  if (thiTemp <= g_hx_limit_c) {
    g_auto_status.closeRequested = true;
    g_auto_status.reason = AUTO_CLOSE_THI_LIMIT;
    return;
  }

  if (hfeTempC <= g_hfe_goal_c) {
    g_auto_status.closeRequested = true;
    g_auto_status.reason = AUTO_CLOSE_HFE_GOAL;
    return;
  }

  g_auto_status.readyToOpen =
    isfinite(g_auto_status.thiReopenThresholdC) &&
    thiTemp >= g_auto_status.thiReopenThresholdC &&
    hfeTempC >= g_auto_status.hfeReopenThresholdC;
}

static void updateAutoValveStatus(const float temps[], size_t count) {
  const float thiTemp =
    (temps && count > THI_SENSOR_INDEX && isfinite(temps[THI_SENSOR_INDEX]))
      ? temps[THI_SENSOR_INDEX]
      : NAN;
  const float hfeTempC =
    (temps && count > HFE_AUTO_SENSOR_INDEX && isfinite(temps[HFE_AUTO_SENSOR_INDEX]))
      ? temps[HFE_AUTO_SENSOR_INDEX]
      : NAN;

  updateAutoValveStatusFromValues(thiTemp, hfeTempC);
  g_auto_status_sampled = true;
}

static void runAutoValveControl() {
  if (g_auto_status.closeRequested) {
    g_auto_close_latched = true;
    applyValve(CLOSED);
    return;
  }

  if (g_auto_close_latched && !g_auto_status.readyToOpen) {
    applyValve(CLOSED);
    return;
  }

  g_auto_close_latched = false;
  applyValve(OPEN);
}

static void refreshAutoStatusAfterTargetChange() {
  updateAutoValveStatusFromValues(g_auto_status.thiTempC, g_auto_status.hfeTempC);
  if (g_mode == AUTO && g_auto_status_sampled) {
    runAutoValveControl();
  }
}

static bool setAutoTargets(float hfeGoalC, float hxLimitC, float hxApproachC, float hysteresisC) {
  if (!isfinite(hfeGoalC) || !isfinite(hxLimitC) ||
      !isfinite(hxApproachC) || !isfinite(hysteresisC) ||
      hxApproachC < 0.0f || hysteresisC < 0.0f) {
    return false;
  }

  g_hfe_goal_c = hfeGoalC;
  g_hx_limit_c = hxLimitC;
  g_hx_approach_c = hxApproachC;
  g_ln_auto_hysteresis_c = hysteresisC;
  refreshAutoStatusAfterTargetChange();
  return true;
}

static void handleCommand(const String& s) {
  String cmd = s; cmd.trim();
  if (!cmd.length()) return;

  String upper = cmd; upper.toUpperCase();
  if (upper == "ESTOP RESET" || upper == "EMERGENCY STOP RESET" || upper == "SAFETY RESET") {
    resetEmergencyStopIfSafe();
  }
  else if (upper == "VALVE OPEN")       { g_mode = FORCE_OPEN;  applyValve(OPEN);   }
  else if (upper == "VALVE CLOSE") { g_mode = FORCE_CLOSE; applyValve(CLOSED); }
  else if (upper == "VALVE AUTO")  {
    if (g_mode != AUTO) {
      g_auto_close_latched = false;
    }
    g_mode = AUTO;
    if (g_auto_status_sampled) {
      runAutoValveControl();
    }
  }
  else if (upper.startsWith("AUTO TARGETS")) {
    float values[4] = { NAN, NAN, NAN, NAN };
    if (!parseFloatArgs(cmd, 12, values, 4) ||
        !setAutoTargets(values[0], values[1], values[2], values[3])) {
      Serial.println(F("# Invalid AUTO TARGETS command"));
      return;
    }

    Serial.print(F("# Auto targets set: HFE goal "));
    Serial.print(g_hfe_goal_c, 2);
    Serial.print(F(" C, HX limit "));
    Serial.print(g_hx_limit_c, 2);
    Serial.print(F(" C, HX approach "));
    Serial.print(g_hx_approach_c, 2);
    Serial.print(F(" C, hysteresis "));
    Serial.print(g_ln_auto_hysteresis_c, 2);
    Serial.println(F(" C"));
  }
  else if (upper.startsWith("SETPOINT")) {
    float nextGoal = NAN;
    if (!parseFloatSuffix(cmd, 8, &nextGoal)) {
      Serial.println(F("# Invalid SETPOINT command"));
      return;
    }

    g_hfe_goal_c = nextGoal;
    refreshAutoStatusAfterTargetChange();
    Serial.print(F("# HFE goal set to "));
    Serial.print(g_hfe_goal_c, 2);
    Serial.println(F(" C"));
  }
  else if (upper.startsWith("HFE GOAL")) {
    float nextGoal = NAN;
    if (!parseFloatSuffix(cmd, 8, &nextGoal)) {
      Serial.println(F("# Invalid HFE GOAL command"));
      return;
    }

    g_hfe_goal_c = nextGoal;
    refreshAutoStatusAfterTargetChange();
    Serial.print(F("# HFE goal set to "));
    Serial.print(g_hfe_goal_c, 2);
    Serial.println(F(" C"));
  }
  else if (upper.startsWith("HX APPROACH")) {
    float nextApproach = NAN;
    if (!parseFloatSuffix(cmd, 11, &nextApproach) || nextApproach < 0.0f) {
      Serial.println(F("# Invalid HX APPROACH command"));
      return;
    }

    g_hx_approach_c = nextApproach;
    refreshAutoStatusAfterTargetChange();
    Serial.print(F("# HX approach set to "));
    Serial.print(g_hx_approach_c, 2);
    Serial.println(F(" C"));
  }
  else if (upper.startsWith("HX LIMIT")) {
    float nextHxLimit = NAN;
    if (!parseFloatSuffix(cmd, 8, &nextHxLimit)) {
      Serial.println(F("# Invalid HX LIMIT command"));
      return;
    }

    g_hx_limit_c = nextHxLimit;
    refreshAutoStatusAfterTargetChange();
    Serial.print(F("# HX limit set to "));
    Serial.print(g_hx_limit_c, 2);
    Serial.println(F(" C"));
  }
  else if (upper.startsWith("THI LIMIT")) {
    float nextHxLimit = NAN;
    if (!parseFloatSuffix(cmd, 9, &nextHxLimit)) {
      Serial.println(F("# Invalid THI LIMIT command"));
      return;
    }

    g_hx_limit_c = nextHxLimit;
    refreshAutoStatusAfterTargetChange();
    Serial.print(F("# HX limit set to "));
    Serial.print(g_hx_limit_c, 2);
    Serial.println(F(" C"));
  }
  else if (upper.startsWith("HYSTERESIS")) {
    float nextHysteresis = NAN;
    if (!parseFloatSuffix(cmd, 10, &nextHysteresis) || nextHysteresis < 0.0f) {
      Serial.println(F("# Invalid HYSTERESIS command"));
      return;
    }

    g_ln_auto_hysteresis_c = nextHysteresis;
    refreshAutoStatusAfterTargetChange();
    Serial.print(F("# Hysteresis set to "));
    Serial.print(g_ln_auto_hysteresis_c, 2);
    Serial.println(F(" C"));
  }
  else if (upper == "HEATER BOTTOM ON")    { applyHeaterBottom(true); }
  else if (upper == "HEATER BOTTOM OFF")   { applyHeaterBottom(false); }
  else if (upper == "HEATER EXHAUST ON")   { applyHeaterExhaust(true); }
  else if (upper == "HEATER EXHAUST OFF")  { applyHeaterExhaust(false); }
  else if (upper.startsWith("PUMP")) {
    String rest = cmd.substring(4);
    rest.trim();
    String restUpper = rest; restUpper.toUpperCase();

    float pct = NAN;
    if (restUpper.startsWith("HZ")) {
      rest = rest.substring(2); rest.trim();
      float hz = rest.toFloat();
      if (isfinite(hz) && PUMP_MAX_FREQ_HZ > 0.0f) {
        pct = (hz / PUMP_MAX_FREQ_HZ) * 100.0f;
      }
    } else {
      if (rest.endsWith("%")) rest.remove(rest.length() - 1);
      pct = rest.toFloat();
    }

    if (isfinite(pct)) {
      if (g_emergency_stop_latched && pct > 0.0f) {
        Serial.println(F("# Pump command blocked by emergency stop; send ESTOP RESET once safe"));
        return;
      }
      float applied = setPumpCommandPct(pct);
      Serial.print(F("# Pump cmd set to "));
      Serial.print(applied, 3);
      Serial.println(F(" % of full-scale (analog)"));
    }
  }
}

// Returns NAN if faulted/missing; otherwise °C
static float safeReadCelsius(Adafruit_MAX31856* dev) {
  if (!dev) return NAN;
  float t = dev->readThermocoupleTemperature();
  uint8_t f = dev->readFault();
  if (f) return NAN; // OPEN/other faults
  if (!isfinite(t) || t < -200.0f || t > 1370.0f) return NAN; // sanity
  return t;
}

static void emitTelemetry(const float temps[], size_t count, unsigned long nowMs,
                          float pressureBeforeBar, float pressureAfterBar, float pressureTankBar,
                          float pressureAfterVolts) {
  const float t_s = nowMs / 1000.0f;
  const char modeChar = (g_mode == AUTO) ? 'A' : (g_mode == FORCE_OPEN ? 'O' : 'C');
  const int trippedLawIdx = firstSafetyLawIndexByState(false);

  Serial.print(F("{\"type\":\"telemetry\""));
  Serial.print(F(",\"t\":"));
  Serial.print(t_s, 3);

  Serial.print(F(",\"temps\":["));
  for (size_t i = 0; i < count; ++i) {
    const float v = (temps && isfinite(temps[i])) ? temps[i] : NAN;
    if (isfinite(v)) Serial.print(v, 2);
    else             Serial.print(F("null"));
    if (i + 1 < count) Serial.print(',');
  }
  Serial.print(']');

  Serial.print(F(",\"valve\":"));
  Serial.print((int)g_valve);

  Serial.print(F(",\"mode\":\""));
  Serial.print(modeChar);
  Serial.print('"');

  Serial.print(F(",\"pump\":{"));
  const float cmdPct  = g_pump_cmd_pct;
  const float cmdFrac = cmdPct / 100.0f;
  const float tgtHz   = PUMP_MAX_FREQ_HZ * cmdFrac;

  Serial.print(F("\"cmd_pct\":"));
  Serial.print(cmdPct, 3);
  Serial.print(F(",\"cmd_frac\":"));
  Serial.print(cmdFrac, 5);
  Serial.print(F(",\"cmd_hz\":"));
  Serial.print(tgtHz, 3);
  Serial.print(F(",\"max_freq_hz\":"));
  Serial.print(PUMP_MAX_FREQ_HZ, 1);
  Serial.print(F(",\"poll_ms\":"));
  Serial.print(g_vfd.lastPollMs);

  if (g_vfd.valid) {
    Serial.print(F(",\"freq_hz\":"));
    if (isfinite(g_vfd.freqHz)) Serial.print(g_vfd.freqHz, 2); else Serial.print(F("null"));

    Serial.print(F(",\"freq_pct\":"));
    float freqPct = (PUMP_MAX_FREQ_HZ > 0.0f) ? (g_vfd.freqHz / PUMP_MAX_FREQ_HZ * 100.0f) : NAN;
    if (isfinite(freqPct)) Serial.print(freqPct, 2); else Serial.print(F("null"));

    Serial.print(F(",\"input_power_pct\":"));
    if (isfinite(g_vfd.inputPowerPct)) Serial.print(g_vfd.inputPowerPct, 2); else Serial.print(F("null"));
    Serial.print(F(",\"input_power_kw\":"));
    if (isfinite(g_vfd.inputPowerKw)) Serial.print(g_vfd.inputPowerKw, 2); else Serial.print(F("null"));
    Serial.print(F(",\"input_power_w\":"));
    if (isfinite(g_vfd.inputPowerW)) Serial.print(g_vfd.inputPowerW, 0); else Serial.print(F("null"));

    Serial.print(F(",\"output_current_pct\":"));
    if (isfinite(g_vfd.outputCurrentPct)) Serial.print(g_vfd.outputCurrentPct, 2); else Serial.print(F("null"));
    Serial.print(F(",\"output_current_a\":"));
    if (isfinite(g_vfd.outputCurrentA)) Serial.print(g_vfd.outputCurrentA, 2); else Serial.print(F("null"));

    Serial.print(F(",\"output_voltage_v\":"));
    if (isfinite(g_vfd.outputVoltageV)) Serial.print(g_vfd.outputVoltageV, 1); else Serial.print(F("null"));
    if (VFD_BASE_VOLTAGE > 0.0f) {
      Serial.print(F(",\"output_voltage_pct\":"));
      float outputVoltagePct = isfinite(g_vfd.outputVoltageV)
        ? (g_vfd.outputVoltageV / VFD_BASE_VOLTAGE * 100.0f)
        : NAN;
      if (isfinite(outputVoltagePct)) Serial.print(outputVoltagePct, 1); else Serial.print(F("null"));
    }

    Serial.print(F(",\"rotation_speed_rpm\":"));
    if (isfinite(g_vfd.rotationSpeedRpm)) Serial.print(g_vfd.rotationSpeedRpm, 0); else Serial.print(F("null"));
  }

  Serial.print(F(",\"pressure_before_bar\":"));
  if (isfinite(pressureBeforeBar)) Serial.print(pressureBeforeBar, 3); else Serial.print(F("null"));
  Serial.print(F(",\"pressure_after_bar\":"));
  if (isfinite(pressureAfterBar)) Serial.print(pressureAfterBar, 3); else Serial.print(F("null"));
  Serial.print(F(",\"pressure_tank_bar\":"));
  if (isfinite(pressureTankBar)) Serial.print(pressureTankBar, 3); else Serial.print(F("null"));

  Serial.print(F(",\"pressure_before_bar_abs\":"));
  if (isfinite(pressureBeforeBar)) Serial.print(pressureBeforeBar + ATMOSPHERE_BAR, 3); else Serial.print(F("null"));
  Serial.print(F(",\"pressure_after_bar_abs\":"));
  if (isfinite(pressureAfterBar)) Serial.print(pressureAfterBar + ATMOSPHERE_BAR, 3); else Serial.print(F("null"));
  Serial.print(F(",\"pressure_tank_bar_abs\":"));
  if (isfinite(pressureTankBar)) Serial.print(pressureTankBar + ATMOSPHERE_BAR, 3); else Serial.print(F("null"));

  Serial.print(F(",\"pressure_after_v\":"));
  if (isfinite(pressureAfterVolts)) Serial.print(pressureAfterVolts, 3); else Serial.print(F("null"));

  Serial.print(F(",\"pressure_before_psi\":"));
  if (isfinite(pressureBeforeBar)) Serial.print(pressureBeforeBar * PSI_PER_BAR, 3); else Serial.print(F("null"));
  Serial.print(F(",\"pressure_after_psi\":"));
  if (isfinite(pressureAfterBar)) Serial.print(pressureAfterBar * PSI_PER_BAR, 3); else Serial.print(F("null"));
  Serial.print(F(",\"pressure_tank_psi\":"));
  if (isfinite(pressureTankBar)) Serial.print(pressureTankBar * PSI_PER_BAR, 3); else Serial.print(F("null"));

  Serial.print(F(",\"pressure_error_bar\":"));
  Serial.print(PRESSURE_ERR_BAR, 3);
  Serial.print('}');
  Serial.print(F(",\"safety\":{"));
  Serial.print(F("\"emergency_stop\":"));
  Serial.print(g_emergency_stop_latched ? F("true") : F("false"));
  Serial.print(F(",\"reset_required\":"));
  Serial.print(g_emergency_stop_latched ? F("true") : F("false"));
  Serial.print(F(",\"tripped_ms\":"));
  if (g_emergency_stop_latched) Serial.print(g_emergency_stop_ms);
  else                          Serial.print(F("null"));
  Serial.print(F(",\"active_reason\":"));
  if (trippedLawIdx >= 0) {
    Serial.print('"');
    Serial.print(g_safety_laws[trippedLawIdx].key);
    Serial.print('"');
  } else {
    Serial.print(F("null"));
  }
  Serial.print(F(",\"message\":"));
  if (trippedLawIdx >= 0) {
    Serial.print(F("\"Emergency stop: "));
    Serial.print(g_safety_laws[trippedLawIdx].label);
    Serial.print('"');
  } else {
    Serial.print(F("null"));
  }
  Serial.print(F(",\"laws\":{"));
  for (size_t i = 0; i < safetyLawCount(); ++i) {
    const SafetyLawState &law = g_safety_laws[i];
    Serial.print('"');
    Serial.print(law.key);
    Serial.print(F("\":{"));
    Serial.print(F("\"label\":\""));
    Serial.print(law.label);
    Serial.print(F("\",\"enabled\":"));
    Serial.print(law.enabled ? F("true") : F("false"));
    Serial.print(F(",\"active\":"));
    Serial.print(law.active ? F("true") : F("false"));
    Serial.print(F(",\"tripped\":"));
    Serial.print(law.tripped ? F("true") : F("false"));
    Serial.print(F(",\"limit_bar\":"));
    Serial.print(law.limitBar, 3);
    Serial.print(F(",\"value_bar\":"));
    if (isfinite(law.valueBar)) Serial.print(law.valueBar, 3);
    else                        Serial.print(F("null"));
    Serial.print(F(",\"units\":\"bar\"}"));
    if (i + 1 < safetyLawCount()) Serial.print(',');
  }
  Serial.print(F("}"));
  Serial.print('}');
  Serial.print(F(",\"fluid\":{"));
  Serial.print(F("\"name\":\""));
  Serial.print(FLUID_NAME);
  Serial.print(F("\",\"concentration_pct\":"));
  Serial.print(FLUID_CONC_PCT, 1);
  Serial.print(F(",\"meter_valid\":"));
  Serial.print(g_flow.valid ? 1 : 0);
  Serial.print(F(",\"meter_poll_ms\":"));
  Serial.print(g_flow.lastPollMs);

  if (g_flow.valid) {
    Serial.print(F(",\"flow_velocity_mps\":"));
    Serial.print(g_flow.flowVelocityMps, 6);
    Serial.print(F(",\"volume_flow_m3s\":"));
    Serial.print(g_flow.volumeFlowM3s, 9);
    Serial.print(F(",\"mass_flow_kgs\":"));
    Serial.print(g_flow.massFlowKgS, 9);
    Serial.print(F(",\"temperature_raw\":"));
    Serial.print(g_flow.temperatureRaw, 6);
    Serial.print(F(",\"density_kg_m3\":"));
    Serial.print(g_flow.densityKgM3, 6);
  }
  Serial.print('}');
  Serial.print(F(",\"control\":{"));
  Serial.print(F("\"hfe_goal_c\":"));
  Serial.print(g_hfe_goal_c, 2);
  Serial.print(F(",\"setpoint_c\":"));
  Serial.print(g_hfe_goal_c, 2);
  Serial.print(F(",\"hx_limit_c\":"));
  Serial.print(g_hx_limit_c, 2);
  Serial.print(F(",\"thi_limit_c\":"));
  Serial.print(g_hx_limit_c, 2);
  Serial.print(F(",\"ln_hysteresis_c\":"));
  Serial.print(g_ln_auto_hysteresis_c, 2);
  Serial.print(F(",\"hx_approach_c\":"));
  Serial.print(g_hx_approach_c, 2);
  Serial.print(F(",\"thi_temp_c\":"));
  if (g_auto_status.thiValid) Serial.print(g_auto_status.thiTempC, 2); else Serial.print(F("null"));
  Serial.print(F(",\"hfe_temp_c\":"));
  if (g_auto_status.hfeValid) Serial.print(g_auto_status.hfeTempC, 2); else Serial.print(F("null"));
  Serial.print(F(",\"tmi_temp_c\":"));
  if (g_auto_status.hfeValid) Serial.print(g_auto_status.hfeTempC, 2); else Serial.print(F("null"));
  Serial.print(F(",\"flow_temp_c\":"));
  if (g_auto_status.hfeValid) Serial.print(g_auto_status.hfeTempC, 2); else Serial.print(F("null"));
  Serial.print(F(",\"thi_valid\":"));
  Serial.print(g_auto_status.thiValid ? F("true") : F("false"));
  Serial.print(F(",\"hfe_valid\":"));
  Serial.print(g_auto_status.hfeValid ? F("true") : F("false"));
  Serial.print(F(",\"tmi_valid\":"));
  Serial.print(g_auto_status.hfeValid ? F("true") : F("false"));
  Serial.print(F(",\"flow_valid\":"));
  Serial.print(g_auto_status.hfeValid ? F("true") : F("false"));
  Serial.print(F(",\"thi_reopen_c\":"));
  if (isfinite(g_auto_status.thiReopenThresholdC)) Serial.print(g_auto_status.thiReopenThresholdC, 2);
  else Serial.print(F("null"));
  Serial.print(F(",\"hfe_reopen_c\":"));
  Serial.print(g_auto_status.hfeReopenThresholdC, 2);
  Serial.print(F(",\"tmi_reopen_c\":"));
  Serial.print(g_auto_status.hfeReopenThresholdC, 2);
  Serial.print(F(",\"flow_reopen_c\":"));
  Serial.print(g_auto_status.hfeReopenThresholdC, 2);
  Serial.print(F(",\"close_requested\":"));
  Serial.print(g_auto_status.closeRequested ? F("true") : F("false"));
  Serial.print(F(",\"ready_to_open\":"));
  Serial.print(g_auto_status.readyToOpen ? F("true") : F("false"));
  Serial.print(F(",\"auto_close_latched\":"));
  Serial.print(g_auto_close_latched ? F("true") : F("false"));
  Serial.print(F(",\"within_hysteresis_band\":"));
  Serial.print((g_auto_close_latched && !g_auto_status.closeRequested && !g_auto_status.readyToOpen) ? F("true") : F("false"));
  Serial.print(F(",\"auto_close_reason\":\""));
  Serial.print(autoCloseReasonKey(g_auto_status.reason));
  Serial.print('"');
  Serial.print(F(",\"telemetry_interval_ms\":"));
  Serial.print(SAMPLE_INTERVAL_MS);
  Serial.print('}');
  Serial.print(F(",\"heaters\":{"));
  Serial.print(F("\"bottom\":"));
  Serial.print(g_heater_bottom_on ? 1 : 0);
  Serial.print(F(",\"exhaust\":"));
  Serial.print(g_heater_exhaust_on ? 1 : 0);
  Serial.print('}');
  Serial.println('}');
}

void setup() {
  Serial.begin(115200);
  VFD.begin(VFD_BAUD, SERIAL_8E1);
  FLOW.begin(FLOW_BAUD, SERIAL_8E1);
  analogReference(DEFAULT);

  setupPwm2kHz();
  setPumpCommandPct(0.0f);  // start at 0% analog

  g_mode = DEFAULT_VALVE_MODE;
  digitalWrite(VALVE_PIN, LOW);
  pinMode(VALVE_PIN, OUTPUT);
  applyValve(CLOSED);
  pinMode(HEATER_BOTTOM_PIN, OUTPUT);
  pinMode(HEATER_EXHAUST_PIN, OUTPUT);
  applyHeaterBottom(false);
  applyHeaterExhaust(false);

  pinMode(PRESSURE_PIN_BEFORE, INPUT);
  pinMode(PRESSURE_PIN_AFTER, INPUT);
  pinMode(PRESSURE_PIN_TANK, INPUT);

  pinMode(SCK_PIN,  OUTPUT);
  pinMode(MOSI_PIN, OUTPUT);
  pinMode(MISO_PIN, INPUT);

  for (size_t i = 0; i < NUM_TCS; ++i) {
    pinMode(CS_PINS[i], OUTPUT);
    digitalWrite(CS_PINS[i], HIGH); // deselect
    tc[i] = new Adafruit_MAX31856(CS_PINS[i], MOSI_PIN, MISO_PIN, SCK_PIN);
    tc[i]->begin();
    tc[i]->setThermocoupleType(TC_TYPES[i]);
    tc[i]->setNoiseFilter(MAX31856_NOISE_FILTER_60HZ); // correct enum
  }

  // JSON line telemetry: temps[0..9] (°C), valve (0/1), mode (A/O/C), pump{}, safety{}, fluid{}, control{}, heaters{}
  Serial.println(F("# Telemetry keys: temps[0..9] (°C), valve (0/1), mode (A/O/C), pump{} (VFD + pressures), safety{} (latched interlocks), fluid{} (MFC400), control{} (HFE goal + HX limit + hysteresis + HX approach + LN auto status), heaters{bottom,exhaust}"));
}

void loop() {
  // ── Serial command parser (non-blocking) ───────────────────────────────
  static String line;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') { if (line.length()) handleCommand(line); line = ""; }
    else { line += c; if (line.length() > 64) line = ""; }
  }

  unsigned long now = millis();

  // ── Poll VFD (non-blocking 200 ms timeout inside) ──────────────────────
  if (now - lastVfdPoll >= VFD_POLL_MS) {
    lastVfdPoll = now;
    pollVfd();
  }

  if (now - lastFlowPoll >= FLOW_POLL_MS) {
    lastFlowPoll = now;
    pollFlowMeter();
  }

  // ── 1 Hz sampling ──────────────────────────────────────────────────────
  if (now - lastSample >= SAMPLE_INTERVAL_MS) {
    lastSample = now;

    // Read sensors into a fixed-size array
    float temps_out[MAX_TCS_OUT];
    for (size_t i = 0; i < MAX_TCS_OUT; ++i) {
      temps_out[i] = (i < NUM_TCS) ? safeReadCelsius(tc[i]) : NAN;
    }

    updateAutoValveStatus(temps_out, MAX_TCS_OUT);

    // Control: LN auto closes on THI/TMI cold limits and reopens once both recover by hysteresis.
    if (g_mode == AUTO) {
      runAutoValveControl();
    } else if (g_mode == FORCE_OPEN)  applyValve(OPEN);
    else if (g_mode == FORCE_CLOSE)   applyValve(CLOSED);

    float pressureBeforeVolts = readPressureVolts(PRESSURE_PIN_BEFORE);
    float pressureAfterVolts  = readPressureVolts(PRESSURE_PIN_AFTER);
    float pressureTankVolts   = readPressureVolts(PRESSURE_PIN_TANK);

    float pressureBeforeBar = voltsToBar(pressureBeforeVolts);
    float pressureAfterBar  = voltsToBarAfter(pressureAfterVolts);
    float pressureTankBar   = voltsToBar(pressureTankVolts);

    updatePumpDeltaPSafety(pressureBeforeBar, pressureAfterBar, now);

    emitTelemetry(temps_out, MAX_TCS_OUT, now,
                  pressureBeforeBar, pressureAfterBar, pressureTankBar,
                  pressureAfterVolts);
  }
}
