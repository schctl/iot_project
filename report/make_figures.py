"""System architecture block diagram -> figures/architecture.png"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

fig, ax = plt.subplots(figsize=(9, 3.6), dpi=200)
ax.set_xlim(0, 10); ax.set_ylim(0, 4); ax.axis("off")

def box(x, y, w, h, title, sub, fc):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.12", fc=fc, ec="#555", lw=1))
    ax.text(x + w/2, y + h*0.64, title, ha="center", va="center", fontsize=9.5, weight="bold")
    ax.text(x + w/2, y + h*0.3, sub, ha="center", va="center", fontsize=7.4, color="#333")

def arrow(x1, y1, x2, y2, label="", both=False):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="<->" if both else "->", color="#456", lw=1.2))
    if label:
        ax.text((x1+x2)/2, (y1+y2)/2 + 0.14, label, ha="center", fontsize=7, color="#345")

box(0.1, 2.55, 2.1, 1.1, "Sensors", "DHT22 (temp, RH)\nLDR module (lux)", "#e8eefb")
box(0.1, 0.35, 2.1, 1.1, "Local I/O", "AUTO/MAN button\nSSD1306 OLED", "#eef2f5")
box(3.3, 1.4, 2.2, 1.3, "ESP32", "sampling (2 s), lux conv.,\nthreshold + hysteresis", "#e2f3e8")
box(6.6, 2.55, 3.2, 1.1, "Actuators", "Relay 1 → fan, Relay 2 → light\nBuzzer (critical temp alarm)", "#fbeede")
box(6.6, 0.35, 1.45, 1.1, "MQTT broker", "broker.hivemq.com", "#e8eefb")
box(8.35, 0.35, 1.45, 1.1, "Dashboard", "dashboard.html\n(browser)", "#f2e6f7")

arrow(2.2, 3.1, 3.3, 2.35, "GPIO15 / ADC34")
arrow(2.2, 0.9, 3.3, 1.75, "GPIO14 / I²C", both=True)
arrow(5.5, 2.35, 6.6, 3.1, "GPIO19/18/25")
arrow(5.5, 1.75, 6.6, 0.9, "Wi-Fi, MQTT 1883", both=True)
arrow(8.05, 0.9, 8.35, 0.9, both=True)
ax.text(8.2, 0.12, "WSS 8884", ha="center", fontsize=7, color="#345")
plt.tight_layout()
plt.savefig("figures/architecture.png", bbox_inches="tight")
print("ok")
