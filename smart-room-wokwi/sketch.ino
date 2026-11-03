/*
 * ============================================================================
 *  IoT-Based Smart Room Monitoring and Automation  (Assignment 3 - Option 1)
 * ============================================================================
 *  Board      : ESP32 DevKit V1
 *  Sensors    : DHT22  (temperature + humidity)      -> GPIO15
 *               LDR photoresistor module (light)      -> GPIO34 (ADC1)
 *  Actuators  : Relay 1 -> Fan   (blue LED as load)   -> GPIO19
 *               Relay 2 -> Light (yellow LED as load) -> GPIO18
 *               Buzzer  -> high-temperature alarm     -> GPIO25
 *  Inputs     : Push button -> AUTO / MANUAL toggle   -> GPIO14
 *  Display    : SSD1306 128x64 OLED (I2C)             -> SDA GPIO21, SCL GPIO22
 *  Network    : Wi-Fi  ->  MQTT (public HiveMQ broker)
 *
 *  Data flow
 *    sensors --(2 s)--> ESP32 --(processing + thresholds)--> relays / buzzer
 *                         |
 *                         +--MQTT publish--> iot-a3/<DEVICE_ID>/telemetry
 *                         +--MQTT publish--> iot-a3/<DEVICE_ID>/alert
 *                         +--MQTT publish--> iot-a3/<DEVICE_ID>/status (retained, LWT)
 *                         +<-MQTT subscribe- iot-a3/<DEVICE_ID>/cmd  (dashboard control)
 *
 *  The same sketch runs on a physical ESP32: only change WIFI_SSID / WIFI_PASS.
 * ============================================================================
 */

#include <WiFi.h>
#include <PubSubClient.h>      // MQTT client
#include <ArduinoJson.h>       // JSON encode / decode (v7 API)
#include <DHT.h>               // DHT22 driver
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>  // OLED driver

// ----------------------------------------------------------------------------
//  CONFIGURATION  (edit these)
// ----------------------------------------------------------------------------
// Wokwi provides a virtual open access point called "Wokwi-GUEST".
const char* WIFI_SSID    = "Wokwi-GUEST";
const char* WIFI_PASS    = "";
const int   WIFI_CHANNEL = 6;          // Wokwi-GUEST is on channel 6 (faster connect)

// Public MQTT broker (no account needed). The dashboard uses the same broker
// over secure WebSockets (wss://broker.hivemq.com:8884/mqtt).
const char* MQTT_HOST = "broker.hivemq.com";
const int   MQTT_PORT = 1883;

// Unique ID so our topics don't collide with other users of the public broker.
// MUST match the "Device ID" field in dashboard.html.
const char* DEVICE_ID = "sachin-room1";

// Default thresholds (can be changed at runtime from the dashboard)
float thrTempC   = 30.0;   // fan turns ON at/above this temperature (°C)
float thrLux     = 100.0;  // light turns ON below this light level (lux)
float alertTempC = 40.0;   // critical temperature -> buzzer + alert message

// Hysteresis bands (prevent relays from chattering around a threshold)
const float TEMP_HYST = 1.0;   // fan OFF when temp <= thrTempC - 1.0
const float LUX_HYST  = 50.0;  // light OFF when lux >  thrLux + 50

// Timing (milliseconds)
const unsigned long SENSOR_INTERVAL    = 2000;  // DHT22 needs >= 2 s between reads
const unsigned long TELEMETRY_INTERVAL = 2000;  // publish rate
const unsigned long OLED_INTERVAL      = 500;   // display refresh
const unsigned long MQTT_RETRY_MS      = 3000;  // reconnect back-off
const unsigned long WIFI_RETRY_MS      = 30000;
const unsigned long DEBOUNCE_MS        = 30;
const unsigned long BEEP_PERIOD_MS     = 400;   // buzzer on/off period during alert

// ----------------------------------------------------------------------------
//  PIN MAP
// ----------------------------------------------------------------------------
#define PIN_DHT         15
#define PIN_LDR         34   // ADC1 channel: ADC2 pins can't be used while Wi-Fi is on
#define PIN_RELAY_FAN   19
#define PIN_RELAY_LIGHT 18
#define PIN_BUZZER      25
#define PIN_BUTTON      14

#define DHT_TYPE  DHT22
#define OLED_W    128
#define OLED_H    64
#define OLED_ADDR 0x3C

// LDR conversion constants (from the Wokwi photoresistor documentation)
const float LDR_GAMMA = 0.7;
const float LDR_RL10  = 50;       // LDR resistance at 10 lux, in kΩ
const float LDR_FIXED_R = 10000;  // fixed divider resistor on the module, in Ω
const float ADC_MAX   = 4095.0;   // ESP32 ADC is 12-bit

// ----------------------------------------------------------------------------
//  GLOBAL OBJECTS & STATE
// ----------------------------------------------------------------------------
DHT              dht(PIN_DHT, DHT_TYPE);
Adafruit_SSD1306 oled(OLED_W, OLED_H, &Wire, -1);
WiFiClient       netClient;
PubSubClient     mqtt(netClient);

// MQTT topics (built in setup() from DEVICE_ID)
String tTelemetry, tStatus, tAlert, tCmd;

// Latest sensor values
float temperature = NAN;
float humidity    = NAN;
float lux         = 0;
bool  sensorOk    = false;

// Actuator / mode state
bool autoMode  = true;    // true = AUTO (thresholds), false = MANUAL (dashboard)
bool fanOn     = false;
bool lightOn   = false;
bool alertOn   = false;   // high-temperature alert currently active
bool sensorErrReported = false;

bool oledOk = false;

// Scheduling timestamps
unsigned long lastSensor = 0, lastTelemetry = 0, lastOled = 0;
unsigned long lastMqttTry = 0, lastWifiTry = 0, lastBeep = 0;
bool beepState = false;

// Button debounce state
int  btnStable = HIGH, btnLastRead = HIGH;
unsigned long btnChangedAt = 0;

// ----------------------------------------------------------------------------
//  ACTUATOR HELPERS
// ----------------------------------------------------------------------------
// Relay modules used here are active-HIGH (IN = HIGH energises the coil).
void setFan(bool on) {
  if (on != fanOn) Serial.printf("[ACT] Fan   -> %s\n", on ? "ON" : "OFF");
  fanOn = on;
  digitalWrite(PIN_RELAY_FAN, on ? HIGH : LOW);
}

void setLight(bool on) {
  if (on != lightOn) Serial.printf("[ACT] Light -> %s\n", on ? "ON" : "OFF");
  lightOn = on;
  digitalWrite(PIN_RELAY_LIGHT, on ? HIGH : LOW);
}

void setMode(bool automatic) {
  if (automatic != autoMode) Serial.printf("[MODE] -> %s\n", automatic ? "AUTO" : "MANUAL");
  autoMode = automatic;
}

// ----------------------------------------------------------------------------
//  SENSOR ACQUISITION & PROCESSING
// ----------------------------------------------------------------------------
// Convert the LDR module's analog output to lux.
// The module is a voltage divider: LDR (to GND) + 10 kΩ fixed resistor (to VCC).
// More light -> lower LDR resistance -> lower AO voltage.
//   ratio = Vout / Vcc = R_ldr / (R_ldr + 10k)  ->  R_ldr = 10k * ratio / (1 - ratio)
// Using the ratio makes the result independent of the supply voltage (3.3 V / 5 V).
float readLux() {
  int raw = analogRead(PIN_LDR);
  float ratio = raw / ADC_MAX;
  ratio = constrain(ratio, 0.0005, 0.9995);          // avoid divide-by-zero at the rails
  float resistance = LDR_FIXED_R * ratio / (1.0 - ratio);
  float l = pow(LDR_RL10 * 1e3 * pow(10, LDR_GAMMA) / resistance, 1.0 / LDR_GAMMA);
  return constrain(l, 0.0, 100000.0);
}

void readSensors() {
  float t = dht.readTemperature();
  float h = dht.readHumidity();
  sensorOk = !(isnan(t) || isnan(h));
  if (sensorOk) {
    temperature = t;
    humidity    = h;
  }
  lux = readLux();

  if (sensorOk) {
    Serial.printf("[SENSE] T=%.1f C  H=%.1f %%  L=%.1f lx\n", temperature, humidity, lux);
  } else {
    Serial.printf("[SENSE] DHT22 read FAILED  L=%.1f lx\n", lux);
  }
}

// ----------------------------------------------------------------------------
//  DECISION LOGIC  (threshold-based automation + alerts)
// ----------------------------------------------------------------------------
void publishAlert(const char* type, const char* msg, float value) {
  Serial.printf("[ALERT] %s: %s (%.1f)\n", type, msg, value);
  if (!mqtt.connected()) return;
  JsonDocument doc;
  doc["type"]   = type;
  doc["msg"]    = msg;
  doc["value"]  = value;
  doc["uptime"] = millis() / 1000;
  char buf[192];
  size_t n = serializeJson(doc, buf);
  mqtt.publish(tAlert.c_str(), (const uint8_t*)buf, n, false);
}

void evaluateRules() {
  // --- Sensor health alert (reported once per failure episode) ---
  if (!sensorOk && !sensorErrReported) {
    publishAlert("sensor", "DHT22 read failure", 0);
    sensorErrReported = true;
  } else if (sensorOk && sensorErrReported) {
    publishAlert("sensor_ok", "DHT22 recovered", temperature);
    sensorErrReported = false;
  }

  // --- Critical temperature alert (edge-triggered, with hysteresis) ---
  if (sensorOk) {
    if (!alertOn && temperature >= alertTempC) {
      alertOn = true;
      publishAlert("high_temp", "Critical room temperature!", temperature);
    } else if (alertOn && temperature <= alertTempC - TEMP_HYST) {
      alertOn = false;
      publishAlert("high_temp_clear", "Temperature back to normal", temperature);
    }
  }

  if (!autoMode) return;   // MANUAL: relays only follow dashboard commands

  // --- Fan control with hysteresis ---
  if (sensorOk) {
    if (temperature >= thrTempC)                 setFan(true);
    else if (temperature <= thrTempC - TEMP_HYST) setFan(false);
    if (alertOn) setFan(true);                    // safety: always cool when critical
  }

  // --- Light control with hysteresis ---
  if (lux < thrLux)                 setLight(true);
  else if (lux > thrLux + LUX_HYST) setLight(false);
}

// Non-blocking buzzer pattern while an alert is active
void updateBuzzer() {
  if (!alertOn) {
    if (beepState) { noTone(PIN_BUZZER); beepState = false; }
    return;
  }
  if (millis() - lastBeep >= BEEP_PERIOD_MS) {
    lastBeep  = millis();
    beepState = !beepState;
    if (beepState) tone(PIN_BUZZER, 2000);
    else           noTone(PIN_BUZZER);
  }
}

// ----------------------------------------------------------------------------
//  MQTT
// ----------------------------------------------------------------------------
void publishTelemetry() {
  if (!mqtt.connected()) return;
  JsonDocument doc;
  if (sensorOk) {
    doc["temp"] = round(temperature * 10) / 10.0;
    doc["hum"]  = round(humidity * 10) / 10.0;
  } else {
    doc["temp"] = nullptr;
    doc["hum"]  = nullptr;
  }
  doc["lux"]       = round(lux * 10) / 10.0;
  doc["fan"]       = fanOn;
  doc["light"]     = lightOn;
  doc["mode"]      = autoMode ? "auto" : "manual";
  doc["alert"]     = alertOn;
  doc["sensorOk"]  = sensorOk;
  doc["thrTemp"]   = thrTempC;
  doc["thrLux"]    = thrLux;
  doc["alertTemp"] = alertTempC;
  doc["uptime"]    = millis() / 1000;
  doc["rssi"]      = WiFi.RSSI();

  char buf[384];
  size_t n = serializeJson(doc, buf);
  mqtt.publish(tTelemetry.c_str(), (const uint8_t*)buf, n, false);
}

// Handle commands from the dashboard, e.g.
//   {"mode":"manual"}  {"fan":true}  {"light":false}  {"thrTemp":28}  {"thrLux":200}
void onMqttMessage(char* topic, byte* payload, unsigned int len) {
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, payload, len);
  if (err) {
    Serial.printf("[MQTT] Bad command JSON: %s\n", err.c_str());
    return;
  }
  Serial.printf("[MQTT] Command: %.*s\n", (int)len, (const char*)payload);

  if (doc["mode"].is<const char*>()) {
    setMode(strcmp(doc["mode"].as<const char*>(), "auto") == 0);
  }
  // Direct relay commands imply MANUAL mode
  if (doc["fan"].is<bool>())   { setMode(false); setFan(doc["fan"].as<bool>()); }
  if (doc["light"].is<bool>()) { setMode(false); setLight(doc["light"].as<bool>()); }

  if (doc["thrTemp"].is<float>()) {
    float v = doc["thrTemp"].as<float>();
    if (v >= 0 && v <= 80) { thrTempC = v; Serial.printf("[CFG] thrTemp = %.1f\n", v); }
  }
  if (doc["thrLux"].is<float>()) {
    float v = doc["thrLux"].as<float>();
    if (v >= 0 && v <= 100000) { thrLux = v; Serial.printf("[CFG] thrLux = %.1f\n", v); }
  }
  if (doc["alertTemp"].is<float>()) {
    float v = doc["alertTemp"].as<float>();
    if (v >= 0 && v <= 80) { alertTempC = v; Serial.printf("[CFG] alertTemp = %.1f\n", v); }
  }

  evaluateRules();      // apply new settings right away
  publishTelemetry();   // give the dashboard instant feedback
}

// Non-blocking reconnect: one attempt every MQTT_RETRY_MS
void maintainMqtt() {
  if (WiFi.status() != WL_CONNECTED || mqtt.connected()) return;
  if (millis() - lastMqttTry < MQTT_RETRY_MS) return;
  lastMqttTry = millis();

  String clientId = String("smartroom-") + DEVICE_ID + "-" + String((uint32_t)esp_random(), HEX);
  Serial.printf("[MQTT] Connecting to %s as %s ... ", MQTT_HOST, clientId.c_str());

  // Last Will: broker publishes retained "offline" if we drop off unexpectedly
  if (mqtt.connect(clientId.c_str(), nullptr, nullptr, tStatus.c_str(), 1, true, "offline")) {
    Serial.println("connected");
    mqtt.publish(tStatus.c_str(), "online", true);
    mqtt.subscribe(tCmd.c_str());
    publishTelemetry();
  } else {
    Serial.printf("failed (state %d), retrying\n", mqtt.state());
  }
}

// The ESP32 Wi-Fi stack reconnects by itself (setAutoReconnect). As a fallback,
// restart the connection only if we've been offline for a long time.
void maintainWifi() {
  if (WiFi.status() == WL_CONNECTED) { lastWifiTry = millis(); return; }
  if (millis() - lastWifiTry < WIFI_RETRY_MS) return;
  lastWifiTry = millis();
  Serial.println("[WIFI] Still offline, restarting connection ...");
  WiFi.begin(WIFI_SSID, WIFI_PASS, WIFI_CHANNEL);
}

// ----------------------------------------------------------------------------
//  BUTTON (debounced, toggles AUTO / MANUAL)
// ----------------------------------------------------------------------------
void handleButton() {
  int reading = digitalRead(PIN_BUTTON);
  if (reading != btnLastRead) { btnChangedAt = millis(); btnLastRead = reading; }
  if (millis() - btnChangedAt > DEBOUNCE_MS && reading != btnStable) {
    btnStable = reading;
    if (btnStable == LOW) {          // pressed (INPUT_PULLUP -> active LOW)
      setMode(!autoMode);
      evaluateRules();
      publishTelemetry();
    }
  }
}

// ----------------------------------------------------------------------------
//  OLED
// ----------------------------------------------------------------------------
void drawOled() {
  if (!oledOk) return;
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextSize(1);

  // Header bar
  oled.fillRect(0, 0, OLED_W, 10, SSD1306_WHITE);
  oled.setTextColor(SSD1306_BLACK);
  oled.setCursor(2, 1);
  oled.print("SMART ROOM");
  oled.setCursor(OLED_W - 6 * 6 - 2, 1);
  oled.print(autoMode ? "  AUTO" : "MANUAL");
  oled.setTextColor(SSD1306_WHITE);

  // Readings
  oled.setCursor(0, 14);
  if (sensorOk) oled.printf("Temp : %5.1f C", temperature);
  else          oled.print("Temp : --- (err)");
  oled.setCursor(0, 24);
  if (sensorOk) oled.printf("Hum  : %5.1f %%", humidity);
  else          oled.print("Hum  : ---");
  oled.setCursor(0, 34);
  oled.printf("Light: %6.0f lx", lux);

  // Actuators
  oled.setCursor(0, 44);
  oled.printf("Fan:%-3s  Lamp:%-3s", fanOn ? "ON" : "OFF", lightOn ? "ON" : "OFF");

  // Bottom line: alert takes priority over connectivity status
  oled.setCursor(0, 55);
  if (alertOn) {
    oled.fillRect(0, 54, OLED_W, 10, SSD1306_WHITE);
    oled.setTextColor(SSD1306_BLACK);
    oled.setCursor(10, 55);
    oled.print("!! HIGH TEMP !!");
    oled.setTextColor(SSD1306_WHITE);
  } else {
    oled.printf("WiFi:%s MQTT:%s",
                WiFi.status() == WL_CONNECTED ? "OK" : "--",
                mqtt.connected() ? "OK" : "--");
  }
  oled.display();
}

// ----------------------------------------------------------------------------
//  SETUP & LOOP
// ----------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  Serial.println("\n=== Smart Room Monitoring & Automation ===");

  pinMode(PIN_RELAY_FAN, OUTPUT);
  pinMode(PIN_RELAY_LIGHT, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  setFan(false);
  setLight(false);

  analogReadResolution(12);
  dht.begin();

  Wire.begin(21, 22);
  oledOk = oled.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR);
  if (!oledOk) Serial.println("[OLED] not found, continuing without display");
  drawOled();

  // Build topic names
  String base = String("iot-a3/") + DEVICE_ID + "/";
  tTelemetry = base + "telemetry";
  tStatus    = base + "status";
  tAlert     = base + "alert";
  tCmd       = base + "cmd";
  Serial.printf("[MQTT] Topic base: %s\n", base.c_str());

  // Start Wi-Fi (non-blocking; loop() keeps working while it connects)
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS, WIFI_CHANNEL);
  lastWifiTry = millis();
  Serial.printf("[WIFI] Connecting to %s ...\n", WIFI_SSID);

  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMqttMessage);
  mqtt.setBufferSize(512);
  mqtt.setKeepAlive(60);   // generous: the simulator can run slower than real time
}

void loop() {
  unsigned long now = millis();

  // Connectivity
  static bool wasWifi = false;
  bool isWifi = WiFi.status() == WL_CONNECTED;
  if (isWifi && !wasWifi) Serial.printf("[WIFI] Connected, IP %s\n", WiFi.localIP().toString().c_str());
  wasWifi = isWifi;
  maintainWifi();
  maintainMqtt();
  mqtt.loop();

  // Local inputs / outputs
  handleButton();
  updateBuzzer();

  // Periodic tasks
  if (now - lastSensor >= SENSOR_INTERVAL) {
    lastSensor = now;
    readSensors();
    evaluateRules();
  }
  if (now - lastTelemetry >= TELEMETRY_INTERVAL) {
    lastTelemetry = now;
    publishTelemetry();
  }
  if (now - lastOled >= OLED_INTERVAL) {
    lastOled = now;
    drawOled();
  }
}
