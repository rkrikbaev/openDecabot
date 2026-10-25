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
PIN_BUZZER = 3                              # speaker
RAMP_MS = 2000                              # 0 -> commanded power in this time, 0 = off
TURN_MS_PER_360 = 1500                      # full turn in place at TURN_POWER (command k)
TURN_POWER = 60
MORSE_HZ = 880
MORSE_UNIT_MS = 80

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
        self.power_dir = 0.0
        self.power_esq = 0.0
        self.target_dir = 0
        self.target_esq = 0
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

    def _apply(self):
        self._write(self.esq, int(round(self.power_esq)), False)
        self._write(self.dir, int(round(self.power_dir)), True)   # right servo is mirrored

    def set(self, esq, dir_, ramp_ms=0):
        """Commanded power. With ramp_ms > 0 the power climbs there gradually (see ramp.h)."""
        self.target_esq = max(-100, min(100, esq))
        self.target_dir = max(-100, min(100, dir_))
        self.power_esq = self._start(self.power_esq, self.target_esq, ramp_ms)
        self.power_dir = self._start(self.power_dir, self.target_dir, ramp_ms)
        self._apply()

    @staticmethod
    def _start(current, target, ramp_ms):
        if ramp_ms == 0 or target == 0:
            return target                       # ramp off, or stop: immediate
        if (target >= 0) != (current >= 0):
            return 0                            # reversing: stop now, ramp from zero
        if abs(target) < abs(current):
            return target                       # slowing down: immediate
        return current

    def step(self, dt_ms, ramp_ms):
        """Called periodically; moves the applied power towards the targets."""
        changed = False
        for name in ("esq", "dir"):
            current = getattr(self, "power_" + name)
            target = getattr(self, "target_" + name)
            if current == target:
                continue
            delta = abs(target) * dt_ms / ramp_ms if ramp_ms else abs(target - current)
            current = min(target, current + delta) if current < target else max(target, current - delta)
            if abs(current - target) < 0.01:
                current = target
            setattr(self, "power_" + name, current)
            changed = True
        if changed:
            self._apply()

    def stop(self):
        self.set(0, 0)

    def moving(self):
        return self.power_esq != 0 or self.power_dir != 0 or self.target_esq != 0 or self.target_dir != 0


class Robot:
    def __init__(self):
        self.name = ROBOT_NAME or chip_name()
        self.motors = Motors()
        self.buzzer = None
        self.interval = SYNC_INTERVAL_MS
        self.ramp_ms = RAMP_MS
        self.turn_ms_360 = TURN_MS_PER_360
        self.ramp_tick = time.ticks_ms()
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

    def _tone(self, freq, on):
        if self.buzzer is None:
            self.buzzer = machine.PWM(machine.Pin(PIN_BUZZER))
        if on:
            self.buzzer.freq(max(31, int(freq)))
            self.buzzer.duty_u16(32768)
        else:
            self.buzzer.duty_u16(0)

    def beep(self, freq, ms):
        try:
            self._tone(freq, True)
            time.sleep_ms(min(int(ms), 1000))
            self._tone(freq, False)
        except Exception as exc:
            print("buzzer:", exc)

    def morse(self, hz, unit, pattern):
        """Plays a dot/dash pattern. Blocking: a short phrase takes 1-3 s."""
        hz = hz or MORSE_HZ
        unit = unit or MORSE_UNIT_MS
        print("Morse:", pattern)
        try:
            for ch in pattern:
                if ch in ".-":
                    self._tone(hz, True)
                    time.sleep_ms(unit if ch == "." else 3 * unit)
                    self._tone(hz, False)
                    time.sleep_ms(unit)
                elif ch == " ":
                    time.sleep_ms(2 * unit)
                elif ch == "/":
                    time.sleep_ms(6 * unit)
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
            "motors": {"esq": int(round(self.motors.power_esq)), "dir": int(round(self.motors.power_dir))},
            "moving": self.motors.moving(),
            "targets": {"esq": self.motors.target_esq, "dir": self.motors.target_dir},
            "interval": self.interval,
            "ramp": self.ramp_ms,
            "turn360": self.turn_ms_360,
            "speaking": False,
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
            self.motors.set(value, value, self.ramp_ms)
            self.timed(duration)
        elif cmd == "t":
            self.motors.set(-value, -value, self.ramp_ms)
            self.timed(duration)
        elif cmd == "d":
            self.motors.set(value, -value, self.ramp_ms)
            self.timed(duration)
        elif cmd == "e":
            self.motors.set(-value, value, self.ramp_ms)
            self.timed(duration)
        elif cmd == "p":
            self.motors.stop()
            self.motion_end = None
        elif cmd == "y":
            self.speed = value
        elif cmd == "x":
            self.direction = value
            self.motors.set(self.speed + value // 2, self.speed - value // 2, self.ramp_ms)
            self.motion_end = None
        elif cmd == "i":
            self.interval = max(100, value)
        elif cmd == "g":
            self.ramp_ms = max(0, min(10000, value))
        elif cmd == "k":
            self.turn_ms_360 = max(200, min(20000, value))
        elif cmd == "v":
            # v<degrees>,<power>: timed turn in place, no ramp
            power = max(1, min(100, duration or TURN_POWER))
            ms = abs(value) * self.turn_ms_360 // 360
            if value == 0 or ms == 0:
                self.motors.stop()
            else:
                self.motors.set(power if value > 0 else -power, -power if value > 0 else power, 0)
                self.timed(ms)
        elif cmd == "w":
            # w<hz>,<dot ms>,<pattern>
            unit, _, pattern = extra.partition(",")
            try:
                unit = int(unit)
            except ValueError:
                unit = 0
            self.morse(value, unit, pattern)
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
        dt = time.ticks_diff(now, self.ramp_tick)
        if dt >= 20:
            self.ramp_tick = now
            self.motors.step(dt, self.ramp_ms)
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
