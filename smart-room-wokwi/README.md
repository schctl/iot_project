# Smart Room Monitoring and Automation (Option 1)

An ESP32 measures temperature, humidity and light in a room.

- It turns a **fan** on when the room gets hot.
- It turns a **light** on when the room gets dark.
- It **beeps an alarm** if the room gets far too hot.
- It sends all readings to a **web dashboard**, where you can watch them and switch things by hand.

```
 DHT22 ─┐                     ┌──► Relay 1 ──► Fan (blue LED)
 LDR  ──┼──► ESP32 ───────────┼──► Relay 2 ──► Light (yellow LED)
 Button ┘      │              ├──► Buzzer
               │              └──► OLED screen
               │ Wi-Fi + MQTT
               ▼
     broker.hivemq.com (free public MQTT server)
               │
               ▼
     dashboard.html (in your browser)
```

## Files

| File | What it is |
|---|---|
| `sketch.ino` | The ESP32 code |
| `diagram.json` | The circuit for Wokwi |
| `libraries.txt` | The libraries Wokwi needs |
| `dashboard.html` | The dashboard. Just open it in a browser |

## How to run it

1. Open <https://wokwi.com/projects/new/esp32>.
2. Paste `sketch.ino` into the **sketch.ino** tab.
3. Paste `diagram.json` into the **diagram.json** tab.
4. Click **▾** next to the tabs, choose **New file…**, name it `libraries.txt`, and paste `libraries.txt` into it.
5. Press **▶ Start**. Wait for `[WIFI] Connected` and `[MQTT] ... connected` in the serial monitor. The first build can take about a minute.
6. Open `dashboard.html` in your browser. It connects by itself.

Keep the Wokwi tab visible, for example in its own window. Wokwi pauses when its tab is hidden.

## Things to try

Click a sensor in Wokwi to change its value.

| Do this | You should see |
|---|---|
| Set temperature to 30 °C or more | Fan turns on (blue LED) |
| Set it back to 29 °C or less | Fan turns off |
| Set temperature to 40 °C or more | Buzzer beeps, OLED shows `!! HIGH TEMP !!`, red alert on the dashboard |
| Set light below 100 lux | Light turns on (yellow LED) |
| Set light above 150 lux | Light turns off |
| Press the **AUTO/MAN** button, or click **MANUAL** on the dashboard | You can now switch the fan and light from the dashboard |
| Change a threshold on the dashboard and click **Set** | The ESP32 uses the new value right away |
| Stop the simulation | The dashboard shows the device as **offline** after a short wait |

The off points are a little away from the on points, so the relays don't keep flicking on and off.

## Wiring

| Part | Connects to |
|---|---|
| DHT22 (temperature + humidity) | DATA → GPIO15, VCC → 3V3, GND → GND |
| LDR light sensor | AO → GPIO34, VCC → 3V3, GND → GND |
| Relay 1 (fan) | IN → GPIO19, VCC → VIN (5 V), GND → GND |
| Relay 2 (light) | IN → GPIO18, VCC → VIN (5 V), GND → GND |
| Fan / light (LEDs) | Relay COM → 3V3, relay NO → LED → 220 Ω → GND |
| Buzzer | + → GPIO25, − → GND |
| AUTO/MAN button | GPIO14 and GND |
| OLED screen (SSD1306) | SDA → GPIO21, SCL → GPIO22, VCC → 3V3, GND → GND |

## How the data is sent

The ESP32 and the dashboard talk through MQTT. Each side sends messages to named "topics" on the broker, and
the broker passes them on to whoever is listening. All topics start with `iot-a3/sachin-room1/`.

| Topic | Sent by | What's in it |
|---|---|---|
| `telemetry` | ESP32, every 2 s | Readings, relay states, mode and thresholds |
| `status` | ESP32 | `online`, or `offline` if it disconnects |
| `alert` | ESP32 | Alarm messages, e.g. high temperature |
| `cmd` | Dashboard | Commands, e.g. `{"mode":"manual"}`, `{"fan":true}`, `{"thrTemp":28}` |

## Using a real ESP32

The same `sketch.ino` works on a real board.

1. Change `WIFI_SSID` and `WIFI_PASS` to your own network.
2. Install the libraries listed in `libraries.txt`.
3. Select the **ESP32 Dev Module** board.
4. Power the relays from 5 V (VIN).

## Limitations

- The public broker has no password, so anyone who knows the topic names can read or send messages.
- The dashboard only shows live data. Nothing is saved.
- Thresholds go back to their defaults when the ESP32 restarts.
- The DHT22 is slow (one reading every 2 s).
