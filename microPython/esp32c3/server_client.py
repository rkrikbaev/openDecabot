# openDecabot server client for MicroPython (ESP32 C3)
# https://github.com/triwaca/openDecabot
#
# Same protocol as ArduinoSketch/decabot_serverClient: the robot joins a
# Wi-Fi network, POSTs its telemetry as JSON to the server every
# SYNC_INTERVAL_MS and executes the commands returned, one per line
# (f100, t50, d100, e100, p, f60,800, i500, n494,200, ...).
# Protocol description: /server/README.md
#
# Copy this file to the board, edit the settings below and run it, or add
#   import server_client
# to main.py to start it on boot.

import json
import time

import machine
import network

try:
    import requests  # bundled in MicroPython >= 1.21 ESP32 builds
except ImportError:
    import urequests as requests

# ----------------------------------------------------------------- settings
WIFI_SSID = "YourNetwork"
WIFI_PASSWORD = "YourPassword"
SERVER_URL = "http://192.168.1.100:8000"   # computer running server/decabot_server.py
SERVER_TOKEN = ""                           # same as --token on the server, "" = none
ROBOT_NAME = ""                             # "" = derived from the chip id
SYNC_INTERVAL_MS = 300
FAILSAFE_MS = 2000                          # stop motors when the server is silent, 0 = off

PIN_SERVO_DIR = 6                           # see boot.py pinout
PIN_SERVO_ESQ = 7
PIN_BUZZER = 3

# servo calibration: pulse width in microseconds for full backwards / stop / full forward
SERVO_MIN_US, SERVO_ZERO_US, SERVO_MAX_US = 1000, 1500, 2000
# ----------------------------------------------------------------------------


def chip_name():
    import ubinascii
    uid = ubinascii.hexlify(machine.unique_id()).decode().upper()
    return uid[-4:]


class Motors:
    def __init__(self):
        self.dir = machine.PWM(machine.Pin(PIN_SERVO_DIR), freq=50)
        self.esq = machine.PWM(machine.Pin(PIN_SERVO_ESQ), freq=50)
        self.power_dir = 0
        self.power_esq = 0
        self.stop()

    @staticmethod
    def _us_to_duty(us):
        return int(us * 65535 // 20000)   # 50 Hz -> 20 ms period

    def _write(self, servo, power, invert):
        power = max(-100, min(100, power))
        if invert:
            power = -power
        if power >= 0:
            us = SERVO_ZERO_US + (SERVO_MAX_US - SERVO_ZERO_US) * power // 100
        else:
            us = SERVO_ZERO_US + (SERVO_ZERO_US - SERVO_MIN_US) * power // 100
        servo.duty_u16(self._us_to_duty(us))

    def set(self, esq, dir_):
        self.power_esq = max(-100, min(100, esq))
        self.power_dir = max(-100, min(100, dir_))
        self._write(self.esq, self.power_esq, False)
        self._write(self.dir, self.power_dir, True)   # right servo is mirrored

    def stop(self):
        self.set(0, 0)

    def moving(self):
        return self.power_esq != 0 or self.power_dir != 0


class Robot:
    def __init__(self):
        self.name = ROBOT_NAME or chip_name()
        self.motors = Motors()
        self.buzzer = None
        self.interval = SYNC_INTERVAL_MS
        self.motion_end = None
        self.last_contact = None
        self.failsafe_done = False
        self.syncs = 0
        self.errors = 0
        self.commands = 0
        self.last_command = ""
        self.speed = 0
        self.direction = 0
        self.wlan = network.WLAN(network.STA_IF)
        self.url = "%s/api/robots/%s/sync" % (SERVER_URL, self.name)
        self.headers = {"Content-Type": "application/json"}
        if SERVER_TOKEN:
            self.headers["Authorization"] = "Bearer " + SERVER_TOKEN

    # -- hardware ------------------------------------------------------------

    def beep(self, freq, ms):
        try:
            if self.buzzer is None:
                self.buzzer = machine.PWM(machine.Pin(PIN_BUZZER))
            self.buzzer.freq(max(31, int(freq)))
            self.buzzer.duty_u16(32768)
            time.sleep_ms(min(int(ms), 1000))
            self.buzzer.duty_u16(0)
        except Exception as exc:
            print("buzzer:", exc)

    # -- network -------------------------------------------------------------

    def connect_wifi(self):
        self.wlan.active(True)
        if not self.wlan.isconnected():
            print("Connecting to Wi-Fi", WIFI_SSID)
            self.wlan.connect(WIFI_SSID, WIFI_PASSWORD)
            for _ in range(200):
                if self.wlan.isconnected():
                    break
                time.sleep_ms(100)
        if self.wlan.isconnected():
            print("Wi-Fi ok, IP", self.wlan.ifconfig()[0])
            return True
        print("Wi-Fi failed")
        return False

    def telemetry(self):
        import gc
        return {
            "id": chip_name(),
            "name": self.name,
            "fw": "mpy-1.1",
            "uptime": time.ticks_ms(),
            "ip": self.wlan.ifconfig()[0],
            "rssi": self.wlan.status("rssi") if self.wlan.isconnected() else None,
            "heap": gc.mem_free(),
            "battery": None,
            "motors": {"esq": self.motors.power_esq, "dir": self.motors.power_dir},
            "moving": self.motors.moving(),
            "interval": self.interval,
            "syncs": self.syncs,
            "errors": self.errors,
            "commands": self.commands,
            "lastCommand": self.last_command,
            "sensors": {},
        }

    def sync(self):
        self.syncs += 1
        response = None
        try:
            response = requests.post(self.url, data=json.dumps(self.telemetry()), headers=self.headers)
            if response.status_code == 200:
                self.last_contact = time.ticks_ms()
                self.failsafe_done = False
                for line in response.text.split("\n"):
                    line = line.strip()
                    if line and not line.startswith("#"):
                        self.execute(line)
            else:
                self.errors += 1
                print("Server error", response.status_code)
        except Exception as exc:
            self.errors += 1
            if self.errors % 10 == 1:
                print("Server unreachable:", exc)
        finally:
            if response is not None:
                response.close()

    # -- commands ------------------------------------------------------------

    def execute(self, text):
        cmd, param = text[0], text[1:]
        extra = ""
        if "," in param:
            param, extra = param.split(",", 1)
        try:
            value = int(param)
        except ValueError:
            value = 0
        try:
            duration = int(extra)
        except ValueError:
            duration = 0
        self.commands += 1
        self.last_command = text
        print("Command", cmd, param, extra)

        if cmd == "f":
            self.motors.set(value, value)
            self.timed(duration)
        elif cmd == "t":
            self.motors.set(-value, -value)
            self.timed(duration)
        elif cmd == "d":
            self.motors.set(value, -value)
            self.timed(duration)
        elif cmd == "e":
            self.motors.set(-value, value)
            self.timed(duration)
        elif cmd == "p":
            self.motors.stop()
            self.motion_end = None
        elif cmd == "y":
            self.speed = value
        elif cmd == "x":
            self.direction = value
            self.motors.set(self.speed + value // 2, self.speed - value // 2)
            self.motion_end = None
        elif cmd == "i":
            self.interval = max(100, value)
        elif cmd == "n":
            self.beep(value, duration or 200)
        elif cmd == "l":
            pass   # no on-board LED mapped on the C3 build
        elif cmd in ("m", "o"):
            print("Display:", param)   # add your display driver here
        elif cmd == "?":
            print("Decabot", self.name, self.url)
        else:
            print("Unknown command", text)
            self.commands -= 1

    def timed(self, duration):
        self.motion_end = time.ticks_add(time.ticks_ms(), duration) if duration > 0 else None

    def housekeeping(self):
        now = time.ticks_ms()
        if self.motion_end is not None and time.ticks_diff(now, self.motion_end) >= 0:
            self.motion_end = None
            self.motors.stop()
        if (FAILSAFE_MS and not self.failsafe_done and self.motors.moving()
                and self.last_contact is not None
                and time.ticks_diff(now, self.last_contact) > FAILSAFE_MS):
            self.failsafe_done = True
            self.motors.stop()
            print("Failsafe: server silent, motors stopped")

    # -- main loop -----------------------------------------------------------

    def run(self):
        print("openDecabot", self.name, "->", self.url)
        while not self.connect_wifi():
            time.sleep(2)
        self.beep(880, 100)
        next_sync = time.ticks_ms()
        while True:
            if not self.wlan.isconnected():
                self.connect_wifi()
            elif time.ticks_diff(time.ticks_ms(), next_sync) >= 0:
                next_sync = time.ticks_add(time.ticks_ms(), self.interval)
                self.sync()
            self.housekeeping()
            time.sleep_ms(10)


if __name__ == "__main__":
    Robot().run()
