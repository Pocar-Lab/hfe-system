#include <Arduino.h>
#include <max6675.h>

// SPI pins on Mega2560
const int CLK_PIN = 52;
const int DO_PIN  = 50;

// Chip Select (CS) pins for each thermocouple
const int CS_PINS[4] = {53, 51, 49, 48};  // Assign unique CS pins

// Valve control pin
const int VALVE_PIN = 7;

// Control parameters
const float SETPOINT   = 25.0;  // °C
const float HYSTERESIS = 0.5;   // °C

// Create MAX6675 instances for 4 sensors
MAX6675 tc[4] = {
  MAX6675(CLK_PIN, CS_PINS[0], DO_PIN),
  MAX6675(CLK_PIN, CS_PINS[1], DO_PIN),
  MAX6675(CLK_PIN, CS_PINS[2], DO_PIN),
  MAX6675(CLK_PIN, CS_PINS[3], DO_PIN)
};

unsigned long lastSample = 0;
enum ValveState { CLOSED = 0, OPEN = 1 };
ValveState valve = CLOSED;

void setup() {
  Serial.begin(115200);
  pinMode(VALVE_PIN, OUTPUT);
  digitalWrite(VALVE_PIN, LOW);

  // Set all CS pins as output
  for (int i = 0; i < 4; i++) {
    pinMode(CS_PINS[i], OUTPUT);
    digitalWrite(CS_PINS[i], HIGH);  // Deselect all sensors
  }

  // CSV header
  Serial.println("time_s,temp1_C,temp2_C,temp3_C,temp4_C,valve");
}

void loop() {
  unsigned long now = millis();
  if (now - lastSample < 1000) return;  // 1 Hz
  lastSample = now;

  // --- 1) Read temperatures
  float temps[4];
  for (int i = 0; i < 4; i++) {
    temps[i] = tc[i].readCelsius();
  }

  // --- 2) Hysteresis control based on average temperature
  float avgTemp = 0;
  for (int i = 0; i < 4; i++) {
    avgTemp += temps[i];
  }
  avgTemp /= 4.0;

  if (valve == CLOSED && avgTemp > SETPOINT + HYSTERESIS) {
    valve = OPEN;
    digitalWrite(VALVE_PIN, HIGH);
  }
  else if (valve == OPEN && avgTemp < SETPOINT - HYSTERESIS) {
    valve = CLOSED;
    digitalWrite(VALVE_PIN, LOW);
  }

  // --- 3) Print CSV: time, 4 temps, valve
  float t_s = now / 1000.0;
  Serial.print(t_s, 3);
  for (int i = 0; i < 4; i++) {
    Serial.print(',');
    Serial.print(temps[i], 2);
  }
  Serial.print(',');
  Serial.println(valve);
}
