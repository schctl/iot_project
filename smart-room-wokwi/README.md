# IoT-Based Smart Room Monitoring and Automation (Option 1)

An ESP32 reads **temperature/humidity (DHT22)** and **light level (LDR module)**. It then
automatically switches a **fan** and a **light** through two relays, sounds a **buzzer** when the
temperature is critical, and sends all data over **Wi-Fi → MQTT** to a **web dashboard**. The dashboard
shows live values and charts, and lets you control the room manually.

```
 DHT22 ─┐                         ┌──► Relay 1 ──► Fan (blue LED)
 LDR  ──┼──► ESP32 (processing, ──┼──► Relay 2 ──► Light (yellow LED)
 Button ┘     thresholds)         ├──► Buzzer (alarm)
                │                 └──► OLED (local display)
                │ Wi-Fi, MQTT (TCP 1883)
                ▼
     broker.hivemq.com (public MQTT broker)
                │ MQTT over WebSockets (wss 8884)
                ▼
         dashboard.html (browser)
```

## Files

| File | Purpose |
|---|---|
| `sketch.ino` | ESP32 firmware (Arduino), fully commented |
| `diagram.json` | Wokwi circuit |
| `libraries.txt` | Libraries Wokwi installs automatically |
| `dashboard.html` | Web dashboard. Open it in any browser; no server needed |

## Run it on wokwi.com

1. Go to <https://wokwi.com/projects/new/esp32>.
2. **sketch.ino** tab: replace its contents with `sketch.ino`.
3. **diagram.json** tab: replace its contents with `diagram.json`.
4. Click the **▾** next to the tabs → **New file…** → name it `libraries.txt`, then paste `libraries.txt` into it.
5. Press **▶ Start**. The serial monitor should show `[WIFI] Connected` and then `[MQTT] ... connected`.
   (The first build on the free plan can take about a minute.)
6. Open `dashboard.html` in a browser. It connects on its own. The **Device ID** must match
   `DEVICE_ID` in the sketch (`sachin-room1`).
   Keep the Wokwi tab **visible** (for example, in a separate window): Wokwi pauses the simulation in a background tab.
7. Optional: **Save** the Wokwi project (needs a free login) to get a shareable link.

### Demo script (good for screenshots or a video)

| Action in Wokwi | Expected result |
|---|---|
| Click the DHT22 → set temperature **≥ 30 °C** | Fan relay clicks, blue LED on, dashboard shows Fan ON |
| Lower it to **≤ 29 °C** | Fan turns off (1 °C hysteresis) |
| Set temperature **≥ 40 °C** | Buzzer beeps, OLED shows `!! HIGH TEMP !!`, red alert banner and log entry on the dashboard, fan forced ON |
| Click the LDR → set lux **< 100** | Light relay on, yellow LED on |
| Raise lux **> 150** | Light turns off (50 lx hysteresis) |
| Press the **AUTO/MAN** button, or click MANUAL on the dashboard | Mode changes to MANUAL; the Fan/Light switches on the dashboard now control the relays |
| Change a threshold on the dashboard and click **Set** | The ESP32 applies it straight away (shown in serial log and telemetry) |
| Stop the simulation | After the keep-alive timeout the broker publishes the LWT, and the dashboard shows the device **offline** |

## Hardware / wiring

| Component | Pin(s) | ESP32 |
|---|---|---|
| DHT22 temperature + humidity | DATA / VCC / GND | GPIO15 / 3V3 / GND |
| LDR photoresistor module | AO / VCC / GND | GPIO34 (ADC1) / 3V3 / GND |
| Relay module 1 (Fan) | IN / VCC / GND | GPIO19 / VIN (5 V) / GND |
| Relay module 2 (Light) | IN / VCC / GND | GPIO18 / VIN (5 V) / GND |
| Relay contacts | COM → 3V3, NO → LED + 220 Ω → GND | stands in for the fan/lamp load |
| Buzzer | + / − | GPIO25 / GND |
| Push button (AUTO/MANUAL) | one side / other side | GPIO14 (internal pull-up) / GND |
| SSD1306 OLED 128×64 (I²C 0x3C) | SDA / SCL / VCC / GND | GPIO21 / GPIO22 / 3V3 / GND |

GPIO34 is an ADC1 pin. ADC2 pins can't be used while Wi-Fi is active.

## Sensors, actuators and protocol

- **DHT22**: digital single-wire sensor. Range −40 to 80 °C (±0.5 °C) and 0–100 % RH (±2 %). It can be read at most every 2 s, which sets the sampling interval.
- **LDR module**: the photoresistor forms a voltage divider with a 10 kΩ resistor. The ESP32's 12-bit ADC
  reads the divider ratio, the firmware converts it to resistance, and then to lux using the LDR's
  gamma curve (γ = 0.7, R₁₀ = 50 kΩ).
- **Relays**: active-HIGH relay modules. They isolate the ESP32 from the mains-side fan and lamp
  (simulated here with LEDs).
- **Buzzer**: driven by a PWM tone and beeps on and off (400 ms) while the alert is active.
- **MQTT**: a lightweight publish/subscribe protocol over TCP. The ESP32 uses `PubSubClient` on port 1883.
  The browser can't open raw TCP, so the dashboard uses MQTT over secure WebSockets (port 8884) on the
  same broker.

### MQTT topics (base `iot-a3/<DEVICE_ID>/`)

| Topic | Direction | Payload |
|---|---|---|
| `telemetry` | ESP32 → dashboard, every 2 s | `{"temp":26.0,"hum":55.0,"lux":500.0,"fan":false,"light":false,"mode":"auto","alert":false,"sensorOk":true,"thrTemp":30,"thrLux":100,"alertTemp":40,"uptime":42,"rssi":-60}` |
| `status` | ESP32 → dashboard (retained, LWT) | `online` / `offline` |
| `alert` | ESP32 → dashboard (event) | `{"type":"high_temp","msg":"Critical room temperature!","value":41.5,"uptime":120}` |
| `cmd` | dashboard → ESP32 | any of `{"mode":"auto"\|"manual"}`, `{"fan":true}`, `{"light":false}`, `{"thrTemp":28}`, `{"thrLux":200}`, `{"alertTemp":45}` |

You can also watch the traffic with any MQTT client, for example:
`mosquitto_sub -h broker.hivemq.com -t 'iot-a3/sachin-room1/#' -v`

### Decision logic (AUTO mode)

| Rule | ON condition | OFF condition |
|---|---|---|
| Fan | T ≥ thrTemp (30 °C) | T ≤ thrTemp − 1 °C |
| Light | lux < thrLux (100 lx) | lux > thrLux + 50 lx |
| Alarm | T ≥ alertTemp (40 °C) → buzzer, alert message, fan forced ON | T ≤ alertTemp − 1 °C |
| Sensor fault | DHT22 read fails → `sensor` alert | sensor recovers → `sensor_ok` |

In MANUAL mode the relays only follow dashboard commands. The safety alarm (buzzer and alert) still works.

## Running on a physical ESP32

Use the same `sketch.ino`. Change `WIFI_SSID` / `WIFI_PASS` (remove the channel argument if your AP is not on
channel 6). Install the six libraries in `libraries.txt` via the Arduino Library Manager and select
board **ESP32 Dev Module**. Power the relay modules from 5 V (VIN).

## Limitations and possible improvements

- The public broker has no authentication: anyone who knows the topic can read or send commands. Use a private
  broker (Mosquitto, HiveMQ Cloud, EMQX) with username/password and TLS (port 8883) for real deployments.
- The dashboard only shows live data (no history after a reload). You could add a database (InfluxDB + Grafana,
  ThingSpeak) for long-term trends.
- Thresholds go back to their defaults on reboot. You could store them in NVS with `Preferences`.
- The DHT22 is slow (0.5 Hz) and has limited accuracy. An SHT31/BME280 over I²C would be better.
- You could add a PIR sensor for occupancy-based control, and PWM fan speed control instead of on/off.
