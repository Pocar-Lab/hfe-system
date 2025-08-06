#include <Arduino.h>
#include <max6675.h>

// Thermocouple pins on Mega2560
const int CLK_PIN    = 52;
const int CS_PIN     = 53;
const int DO_PIN     = 50;

// Valve control pin (e.g. driving a MOSFET or relay)
const int VALVE_PIN  = 7;

// Control parameters
const float SETPOINT   = 25.0;  // °C
const float HYSTERESIS = 0.5;   // °C

// MAX6675 instance: arguments are (SCLK, CS, MISO)
MAX6675 tc(CLK_PIN, CS_PIN, DO_PIN);

unsigned long lastSample = 0;
enum ValveState { CLOSED = 0, OPEN = 1 };
ValveState valve = CLOSED;

void setup() {
  Serial.begin(115200);
  pinMode(VALVE_PIN, OUTPUT);
  digitalWrite(VALVE_PIN, LOW);

  // CSV header for PC-side parser/plotter
  Serial.println("time_s,temp_C,valve");
}

void loop() {
  unsigned long now = millis();
  if (now - lastSample < 1000) return;  // 1 Hz sampling
  lastSample = now;

  // --- 1) Read temperature
  float temp = tc.readCelsius();

  // --- 2) Simple hysteresis control
  if (valve == CLOSED && temp > SETPOINT + HYSTERESIS) {
    valve = OPEN;
    digitalWrite(VALVE_PIN, HIGH);
  }
  else if (valve == OPEN && temp < SETPOINT - HYSTERESIS) {
    valve = CLOSED;
    digitalWrite(VALVE_PIN, LOW);
  }

  // --- 3) Stream data as CSV: time [s], temp [°C], valve (0/1)
  float t_s = now / 1000.0;
  Serial.print(t_s, 3);
  Serial.print(',');
  Serial.print(temp, 2);
  Serial.print(',');
  Serial.println(valve);
}