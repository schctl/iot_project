# IoT Project: Smart Room Monitoring and Automation

Internet of Things, Assignment 3 (Option 1). An ESP32 reads a DHT22 (temperature, humidity) and an LDR (light). It switches a
fan and a light through relays using thresholds with hysteresis, raises a buzzer and an MQTT alert at critical temperature,
and publishes everything over Wi-Fi/MQTT to a browser dashboard that can also control the device.

| Path | Contents |
|---|---|
| [`smart-room-wokwi/`](smart-room-wokwi/) | Wokwi project (`sketch.ino`, `diagram.json`, `libraries.txt`), `dashboard.html`, run instructions |
| [`report/`](report/) | Report ([`report.pdf`](report/report.pdf)), its Markdown source, figures and build scripts |

Quick start: open <https://wokwi.com/projects/new/esp32>, paste the three files from `smart-room-wokwi/`, press Start,
then open `smart-room-wokwi/dashboard.html` in a browser. Full steps: [`smart-room-wokwi/README.md`](smart-room-wokwi/README.md).
