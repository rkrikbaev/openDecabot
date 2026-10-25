#!/usr/bin/env python3
"""
openDecabot server
https://github.com/triwaca/openDecabot

Receives telemetry from robots running the decabot_serverClient sketch,
decides what they should do and answers with control commands.
Python 3.8+, standard library only.

Run:
    python3 decabot_server.py                 # listens on 0.0.0.0:8000
    python3 decabot_server.py --port 9000 --token mysecret --verbose

Open http://<this computer ip>:8000 in a browser to see the robots and
drive them. Protocol and HTTP API: see README.md in this folder.

The place to add your own logic is process_telemetry() below: it is called
on every robot sync with the fresh telemetry and returns the commands the
robot must execute.
"""

import argparse
import json
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

MAX_QUEUE = 50          # commands waiting per robot
OFFLINE_AFTER = 3.0     # seconds without sync -> robot shown offline
# Per-robot settings edited on the dashboard (value -> (min, max)).
#   straight/turn: D-pad power in %
#   ramp: milliseconds to accelerate from 0 to the commanded power (0 = off)
#   morse_hz / morse_unit: speaker tone and dot length for Morse messages
#   turn_ms_360: milliseconds of a full turn in place at the turn power (calibration
#   for the +-15 degree buttons); angle_step: degrees per button press
DEFAULT_SETTINGS = {"straight": 100, "turn": 100, "ramp": 2000, "morse_hz": 880, "morse_unit": 80,
                    "turn_ms_360": 1500, "angle_step": 15}
SETTING_LIMITS = {"straight": (0, 100), "turn": (0, 100), "ramp": (0, 10000),
                  "morse_hz": (100, 4000), "morse_unit": (30, 300),
                  "turn_ms_360": (200, 20000), "angle_step": (1, 180)}
# Settings the robot itself must know: (setting, telemetry field, command letter).
# The server re-sends the command whenever the robot reports another value.
ROBOT_SYNCED = [("ramp", "ramp", "g"), ("turn_ms_360", "turn360", "k")]
RESEND_SECONDS = 1.0        # how often to push a value the robot does not report yet
CAMERA_OFFLINE_AFTER = 35.0 # seconds without an announce -> camera shown offline

# Quick Morse phrases shown as buttons on the dashboard
MORSE_PHRASES = ["ПРИВЕТ", "ПОКА", "КАК ДЕЛА"]

MORSE = {
    # Russian (ITU / GOST) alphabet
    "А": ".-", "Б": "-...", "В": ".--", "Г": "--.", "Д": "-..", "Е": ".", "Ж": "...-",
    "З": "--..", "И": "..", "Й": ".---", "К": "-.-", "Л": ".-..", "М": "--", "Н": "-.",
    "О": "---", "П": ".--.", "Р": ".-.", "С": "...", "Т": "-", "У": "..-", "Ф": "..-.",
    "Х": "....", "Ц": "-.-.", "Ч": "---.", "Ш": "----", "Щ": "--.-", "Ъ": "--.--",
    "Ы": "-.--", "Ь": "-..-", "Э": "..-..", "Ю": "..--", "Я": ".-.-",
    # Latin
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.", "G": "--.",
    "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..", "M": "--", "N": "-.",
    "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.", "S": "...", "T": "-", "U": "..-",
    "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--", "Z": "--..",
    # digits and a little punctuation
    "1": ".----", "2": "..---", "3": "...--", "4": "....-", "5": ".....", "6": "-....",
    "7": "--...", "8": "---..", "9": "----.", "0": "-----",
    ".": "......", ",": ".-.-.-", "?": "..--..", "!": "--..--",
}


# The LED matrix font is Latin only: Cyrillic is transliterated for the m command
TRANSLIT = {
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "E", "Ж": "ZH", "З": "Z",
    "И": "I", "Й": "Y", "К": "K", "Л": "L", "М": "M", "Н": "N", "О": "O", "П": "P", "Р": "R",
    "С": "S", "Т": "T", "У": "U", "Ф": "F", "Х": "H", "Ц": "TS", "Ч": "CH", "Ш": "SH",
    "Щ": "SCH", "Ъ": "", "Ы": "Y", "Ь": "", "Э": "E", "Ю": "YU", "Я": "YA",
}
MATRIX_MAX_CHARS = 40


def matrix_text(text):
    """Text for the LED matrix: Cyrillic transliterated, other non-ASCII dropped."""
    out = []
    for ch in str(text):
        upper = ch.upper()
        if upper in TRANSLIT:
            latin = TRANSLIT[upper]
            out.append(latin if ch.isupper() else latin.lower())
        elif 32 <= ord(ch) < 127:
            out.append(ch)
    return " ".join("".join(out).split())[:MATRIX_MAX_CHARS]


def encode_morse(text):
    """'КАК ДЕЛА' -> '-.- .- -.-/-.. . .-.. .-'  (space between letters, / between words).

    Unknown characters are dropped; Ё is read as Е.
    """
    words = []
    for word in str(text).upper().replace("Ё", "Е").split():
        letters = [MORSE[ch] for ch in word if ch in MORSE]
        if letters:
            words.append(" ".join(letters))
    return "/".join(words)

# Autopilot demo: (command, seconds before the next step). Drives a square.
AUTO_PATTERN = [("f60,900", 1.3), ("d60,450", 0.9)]


# --------------------------------------------------------------------------- #
#  Decision logic: edit here                                                   #
# --------------------------------------------------------------------------- #

def process_telemetry(robot, telemetry):
    """Called on every sync. Return a list of command strings for the robot.

    The commands queued by the dashboard / API are sent anyway; whatever is
    returned here is appended after them. Keep it fast: the robot waits for
    the answer.
    """
    commands = []

    # Example 1: protect the batteries
    battery = telemetry.get("battery")
    if isinstance(battery, (int, float)):
        if battery < 10 and not robot.low_battery:
            robot.low_battery = True
            commands += ["p", "n330,400", "mBAT"]
        elif battery >= 15:
            robot.low_battery = False

    # Example 2: autonomous driving decided by the server
    if robot.auto:
        commands += autopilot(robot)

    # Keep the robot-side settings (ramp, turn calibration) equal to the dashboard.
    # Also covers a reboot: the robot comes back with its config.h defaults.
    now = time.monotonic()
    for setting, field, letter in ROBOT_SYNCED:
        reported = telemetry.get(field)
        wanted = robot.settings[setting]
        if isinstance(reported, (int, float)) and int(reported) != wanted \
                and now - robot.sent_at.get(setting, 0.0) >= RESEND_SECONDS:
            robot.sent_at[setting] = now
            commands.append(f"{letter}{wanted}")

    return commands


def autopilot(robot):
    now = time.monotonic()
    if now < robot.auto_next:
        return []
    command, wait = AUTO_PATTERN[robot.auto_step % len(AUTO_PATTERN)]
    robot.auto_step += 1
    robot.auto_next = now + wait
    return [command]


# --------------------------------------------------------------------------- #
#  Robot registry                                                              #
# --------------------------------------------------------------------------- #

class Robot:
    def __init__(self, robot_id):
        self.id = robot_id
        self.telemetry = {}
        self.first_seen = time.time()
        self.last_seen = 0.0
        self.syncs = 0
        self.queue = deque()
        self.sent = deque(maxlen=20)
        self.auto = False
        self.auto_step = 0
        self.auto_next = 0.0
        self.low_battery = False
        self.settings = dict(DEFAULT_SETTINGS)
        self.sent_at = {}           # setting -> when it was last pushed to the robot
        self.camera = None          # last announce from the ESP32-CAM, see /camera
        self.camera_seen = 0.0

    def update_settings(self, data):
        """Accepts a subset of DEFAULT_SETTINGS keys; values are clamped to their limits."""
        for key, (low, high) in SETTING_LIMITS.items():
            if key in data:
                value = data[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(key)
                self.settings[key] = max(low, min(high, int(value)))
        for setting, _, _ in ROBOT_SYNCED:
            if setting in data:
                self.sent_at[setting] = 0.0  # push the new value on the next sync
        return self.settings

    def enqueue(self, commands):
        added = 0
        for command in commands:
            command = str(command).strip()
            if not command or len(self.queue) >= MAX_QUEUE:
                continue
            self.queue.append(command)
            added += 1
        return added

    def drain(self):
        commands = list(self.queue)
        self.queue.clear()
        return commands

    def online(self):
        return self.last_seen and time.time() - self.last_seen < OFFLINE_AFTER

    def camera_info(self):
        if not self.camera:
            return None
        info = dict(self.camera)
        info["online"] = time.time() - self.camera_seen < CAMERA_OFFLINE_AFTER
        info["ageSeconds"] = round(time.time() - self.camera_seen, 1)
        return info

    def to_dict(self):
        return {
            "id": self.id,
            "online": self.online(),
            "lastSeen": self.last_seen,
            "ageSeconds": round(time.time() - self.last_seen, 1) if self.last_seen else None,
            "syncs": self.syncs,
            "queued": list(self.queue),
            "sent": list(self.sent),
            "auto": self.auto,
            "settings": self.settings,
            "camera": self.camera_info(),
            "telemetry": self.telemetry,
        }


class Registry:
    def __init__(self):
        self.lock = threading.Lock()
        self.robots = {}

    def get(self, robot_id, create=False):
        with self.lock:
            robot = self.robots.get(robot_id)
            if robot is None and create:
                robot = Robot(robot_id)
                self.robots[robot_id] = robot
                log(f"new robot: {robot_id}")
            return robot

    def all(self):
        with self.lock:
            return list(self.robots.values())


REGISTRY = Registry()
VERBOSE = False
TOKEN = ""


def log(message):
    print(time.strftime("%H:%M:%S"), message, flush=True)


# --------------------------------------------------------------------------- #
#  HTTP                                                                        #
# --------------------------------------------------------------------------- #

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"   # keep-alive: the robot reuses the socket
    server_version = "openDecabot/1.1"

    def log_message(self, fmt, *args):  # silence the default access log
        if VERBOSE:
            super().log_message(fmt, *args)

    # -- helpers ----------------------------------------------------------

    def send(self, status, body, content_type="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def parse_body(self):
        """Body as text lines or JSON {"commands": [...]} -> list of commands."""
        raw = self.read_body().decode("utf-8", "replace").strip()
        if not raw:
            return []
        if raw.startswith("{") or raw.startswith("["):
            data = json.loads(raw)
            if isinstance(data, dict):
                data = data.get("commands", [])
            return [str(c) for c in data]
        return [line for line in raw.splitlines() if line.strip()]

    def authorized(self):
        if not TOKEN:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {TOKEN}"

    def route(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        parts = path.split("/")
        # /api/robots/<id>/<action>
        if len(parts) >= 4 and parts[1] == "api" and parts[2] == "robots":
            return path, parts[3], parts[4] if len(parts) > 4 else ""
        return path, None, None

    # -- verbs --------------------------------------------------------------

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path, robot_id, action = self.route()
        if path == "/":
            return self.send(200, DASHBOARD_HTML.replace("__PHRASES__", json.dumps(MORSE_PHRASES)), "text/html")
        if path == "/api/phrases":
            return self.send(200, MORSE_PHRASES)
        if path == "/api/robots":
            return self.send(200, [r.to_dict() for r in REGISTRY.all()])
        if robot_id and not action:
            robot = REGISTRY.get(robot_id)
            if robot is None:
                return self.send(404, {"error": "unknown robot"})
            return self.send(200, robot.to_dict())
        self.send(404, {"error": "not found"})

    def do_POST(self):
        path, robot_id, action = self.route()
        if robot_id is None:
            self.read_body()
            return self.send(404, {"error": "not found"})

        if action == "sync":
            return self.handle_sync(robot_id)
        if action == "camera":
            return self.handle_camera(robot_id)

        robot = REGISTRY.get(robot_id, create=True)
        if action == "commands":
            try:
                commands = self.parse_body()
            except (ValueError, TypeError):
                return self.send(400, {"error": "bad body"})
            added = robot.enqueue(commands)
            log(f"{robot_id}: queued {commands}")
            return self.send(200, {"queued": added, "pending": len(robot.queue)})
        if action == "auto":
            try:
                data = json.loads(self.read_body() or b"{}")
            except ValueError:
                return self.send(400, {"error": "bad json"})
            robot.auto = bool(data.get("enabled", not robot.auto))
            robot.auto_step = 0
            robot.auto_next = 0.0
            if not robot.auto:
                robot.enqueue(["p"])
            log(f"{robot_id}: autopilot {'on' if robot.auto else 'off'}")
            return self.send(200, {"auto": robot.auto})
        if action == "clear":
            self.read_body()
            dropped = len(robot.drain())
            return self.send(200, {"dropped": dropped})
        if action == "say":
            try:
                data = json.loads(self.read_body() or b"{}")
                text = str(data.get("text", "")) if isinstance(data, dict) else ""
            except ValueError:
                return self.send(400, {"error": "bad json"})
            pattern = encode_morse(text)
            if not pattern:
                return self.send(400, {"error": "nothing to send: no Morse-encodable characters"})
            commands = []
            shown = matrix_text(text) if data.get("show", True) else ""
            if shown:
                commands.append("m" + shown)       # scroll the text while the Morse plays
            commands.append(f"w{robot.settings['morse_hz']},{robot.settings['morse_unit']},{pattern}")
            robot.enqueue(commands)
            log(f"{robot_id}: say {text!r} -> {pattern}" + (f" / matrix {shown!r}" if shown else ""))
            return self.send(200, {"text": text, "pattern": pattern, "matrix": shown, "pending": len(robot.queue)})
        if action == "show":
            try:
                data = json.loads(self.read_body() or b"{}")
                text = str(data.get("text", "")) if isinstance(data, dict) else ""
            except ValueError:
                return self.send(400, {"error": "bad json"})
            shown = matrix_text(text)
            if not shown:
                return self.send(400, {"error": "nothing to show: only Latin letters, digits and punctuation fit the matrix"})
            robot.enqueue(["m" + shown])
            log(f"{robot_id}: show {shown!r}")
            return self.send(200, {"text": text, "matrix": shown, "pending": len(robot.queue)})
        if action == "settings":
            try:
                data = json.loads(self.read_body() or b"{}")
                if not isinstance(data, dict):
                    raise ValueError
                settings = robot.update_settings(data)
            except (ValueError, TypeError):
                return self.send(400, {"error": "settings must be numbers 0..100"})
            log(f"{robot_id}: settings {settings}")
            return self.send(200, {"settings": settings})
        self.read_body()
        self.send(404, {"error": "not found"})

    def handle_camera(self, robot_id):
        """Announce from the ESP32-CAM: {"stream": url, "snapshot": url, ...}."""
        raw = self.read_body()
        if not self.authorized():
            return self.send(401, {"error": "unauthorized"})
        try:
            data = json.loads(raw.decode("utf-8", "replace") or "{}")
            if not isinstance(data, dict) or not str(data.get("stream", "")).startswith("http"):
                raise ValueError
        except ValueError:
            return self.send(400, {"error": "camera announce needs at least a stream url"})
        robot = REGISTRY.get(robot_id, create=True)
        if robot.camera is None or robot.camera.get("stream") != data.get("stream"):
            log(f"{robot_id}: camera at {data.get('stream')}")
        robot.camera = data
        robot.camera_seen = time.time()
        return self.send(200, {"ok": True, "robot": robot_id})

    def handle_sync(self, robot_id):
        raw = self.read_body()
        if not self.authorized():
            return self.send(401, "# unauthorized\n", "text/plain")
        try:
            telemetry = json.loads(raw.decode("utf-8", "replace") or "{}")
            if not isinstance(telemetry, dict):
                raise ValueError
        except ValueError:
            return self.send(400, "# bad telemetry json\n", "text/plain")

        robot = REGISTRY.get(robot_id, create=True)
        robot.telemetry = telemetry
        robot.last_seen = time.time()
        robot.syncs += 1

        commands = robot.drain()
        try:
            commands += process_telemetry(robot, telemetry) or []
        except Exception as exc:  # never let user logic take the robot down
            log(f"{robot_id}: process_telemetry failed: {exc!r}")

        if commands:
            robot.sent.extend(commands)
            log(f"{robot_id}: -> {commands}")
        elif VERBOSE:
            log(f"{robot_id}: sync {telemetry}")

        body = "".join(c + "\n" for c in commands)
        self.send(200, body, "text/plain")


# --------------------------------------------------------------------------- #
#  Dashboard                                                                   #
# --------------------------------------------------------------------------- #

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>openDecabot server</title>
<style>
  :root{--bg:#1a1a2e;--card:#16213e;--border:#0f3460;--txt:#fff;--ok:#2ecc71;--bad:#e74c3c;--muted:#9aa5c4}
  *{box-sizing:border-box}
  body{margin:0;padding:16px;background:var(--bg);color:var(--txt);font-family:Arial,Helvetica,sans-serif}
  h1{font-size:20px;margin:0 0 12px}
  .cards{display:flex;flex-wrap:wrap;gap:16px}
  .card{background:var(--card);border:2px solid var(--border);border-radius:16px;padding:14px;width:340px}
  .card h2{margin:0 0 8px;font-size:18px;display:flex;align-items:center;gap:8px}
  .dot{width:10px;height:10px;border-radius:50%;background:var(--bad);display:inline-block}
  .dot.on{background:var(--ok)}
  table{width:100%;font-size:13px;border-collapse:collapse;margin-bottom:10px}
  td{padding:2px 0;color:var(--muted)}td+td{color:var(--txt);text-align:right;font-family:monospace}
  .pad{display:grid;grid-template-columns:repeat(3,64px);grid-template-rows:repeat(3,64px);gap:8px;justify-content:center;margin:10px 0;touch-action:none}
  button{background:var(--border);color:var(--txt);border:0;border-radius:12px;font-size:20px;cursor:pointer;-webkit-tap-highlight-color:transparent}
  button:active{background:#1f5fa8}
  .pad .f{grid-column:2;grid-row:1}.pad .e{grid-column:1;grid-row:2}.pad .p{grid-column:2;grid-row:2;background:#8e2a2a}
  .pad .ccw{grid-column:1;grid-row:1}.pad .cw{grid-column:3;grid-row:1}
  .pad .ccw,.pad .cw{font-size:13px;line-height:1.1;background:#12305e}
  .pad .ccw b,.pad .cw b{display:block;font-size:18px}
  .speeds .cal{width:100%;margin-top:6px;padding:7px;font-size:13px;background:#12305e}
  .pad .d{grid-column:3;grid-row:2}.pad .t{grid-column:2;grid-row:3}
  .row{display:flex;gap:6px;margin-top:6px}
  .row input{flex:1;padding:8px;border-radius:8px;border:1px solid var(--border);background:#0d1630;color:var(--txt);font-family:monospace}
  .row button{padding:8px 12px;font-size:14px}
  .small{font-size:12px;color:var(--muted);margin-top:8px;word-break:break-all;font-family:monospace}
  label{font-size:14px;display:flex;align-items:center;gap:6px;margin-top:8px}
  .video{margin:8px 0 4px}
  .video img{width:100%;border-radius:12px;background:#000;display:block;aspect-ratio:4/3;object-fit:contain}
  .video .bar{display:flex;gap:6px;align-items:center;font-size:12px;color:var(--muted);margin-top:4px}
  .video .bar button{padding:5px 10px;font-size:12px}
  .morse{border-top:1px solid var(--border);margin-top:10px;padding-top:4px}
  .morse .phrases button{flex:1;padding:8px 6px;font-size:14px}
  .speeds{border-top:1px solid var(--border);margin-top:10px;padding-top:8px}
  .speeds .line{display:grid;grid-template-columns:92px 1fr 52px;align-items:center;gap:8px;font-size:13px;color:var(--muted);margin:4px 0}
  .speeds input[type=range]{width:100%;accent-color:#1f5fa8}
  .speeds .val{text-align:right;font-family:monospace;color:var(--txt)}
  .speeds .upload{width:100%;margin-top:6px;padding:8px;font-size:14px}
  .speeds .upload.dirty{background:#1f5fa8}
  .speeds .upload:disabled{opacity:.5;cursor:default}
  .empty{color:var(--muted)}
</style>
</head>
<body>
<h1>openDecabot server</h1>
<div id="cards" class="cards"><p class="empty">Waiting for robots... Upload decabot_serverClient with this computer's IP in config.h.</p></div>
<script>
async function api(path, body){
  const r = await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  return r.json();
}
function cmd(id, c){ return api(`/api/robots/${id}/commands`,{commands:[c]}); }
function hold(btn, id, c){
  // c is a function so the power is read from the uploaded settings at press time
  let pressed = false;
  const down = e => { e.preventDefault(); pressed = true; cmd(id, c()); };
  const up = e => { e.preventDefault(); if(pressed){ pressed = false; cmd(id,'p'); } };
  btn.onpointerdown = down; btn.onpointerup = up; btn.onpointerleave = up; btn.onpointercancel = up;
}
function row(k,v){ return `<tr><td>${k}</td><td>${v}</td></tr>`; }
// Robot settings shown as sliders; stored on the server, applied on Upload
const SETTINGS = [
  {key:'straight', label:'Straight speed', min:0, max:100,  step:5,   fmt:v => v + ' %'},
  {key:'turn',     label:'Turn speed',     min:0, max:100,  step:5,   fmt:v => v + ' %'},
  {key:'ramp',     label:'Acceleration',   min:0, max:5000, step:100, fmt:v => v == 0 ? 'off' : (v/1000).toFixed(1) + ' s'},
  {key:'morse_hz',   label:'Morse tone',   min:200, max:2000, step:20, fmt:v => v + ' Hz'},
  {key:'morse_unit', label:'Morse dot',    min:30,  max:300,  step:10, fmt:v => v + ' ms'},
  {key:'turn_ms_360', label:'Full turn',   min:200, max:6000, step:50, fmt:v => (v/1000).toFixed(2) + ' s'},
  {key:'angle_step',  label:'Angle step',  min:1,   max:90,   step:1,  fmt:v => v + '°'},
];
const PHRASES = __PHRASES__;
const fmtOf = Object.fromEntries(SETTINGS.map(s => [s.key, s.fmt]));
function render(robots){
  const root = document.getElementById('cards');
  if(!robots.length){ return; }
  const have = new Set();
  for(const r of robots){
    have.add(r.id);
    let card = document.getElementById('r-'+r.id);
    const t = r.telemetry || {};
    if(!card){
      card = document.createElement('div'); card.className='card'; card.id='r-'+r.id;
      card.innerHTML = `
        <h2><span class="dot"></span>${r.id}</h2>
        <div class="video" hidden><img alt="camera"><div class="bar"><button class="live">Live</button><button class="snap">Snapshot</button><button class="flash">Flash</button><span class="caminfo"></span></div></div>
        <table class="tele"></table>
        <div class="pad">
          <button class="ccw"><b>&#8634;</b><span class="deg"></span></button><button class="cw"><b>&#8635;</b><span class="deg"></span></button>
          <button class="f">&#9650;</button><button class="e">&#9664;</button><button class="p">&#9632;</button>
          <button class="d">&#9654;</button><button class="t">&#9660;</button>
        </div>
        <div class="row"><input placeholder="raw command, e.g. f60,800 or mHELLO"><button class="send">Send</button></div>
        <div class="row"><button class="beep">Beep</button><button class="hello">Say hi</button><button class="eyes">Eyes</button><button class="clear">Clear queue</button></div>
        <div class="morse">
          <div class="row phrases">${PHRASES.map(p => `<button data-say="${p}">${p}</button>`).join('')}</div>
          <div class="row"><input class="say-text" placeholder="any text in Morse (RU / EN)"><button class="say">Say</button></div>
          <div class="row"><input class="show-text" placeholder="text for the LED matrix"><button class="show">Show</button></div>
          <div class="small pattern"></div>
        </div>
        <label><input type="checkbox" class="auto"> Autopilot (server drives a square)</label>
        <div class="speeds">
          ${SETTINGS.map(s => `<div class="line"><span>${s.label}</span><input type="range" class="${s.key}" min="${s.min}" max="${s.max}" step="${s.step}"><span class="val ${s.key}-val"></span></div>`).join('')}
          <button class="upload" disabled>Upload</button>
          <button class="cal" title="Turns a full circle at the turn speed: adjust Full turn until it is exactly 360°">Test full turn (360°)</button>
        </div>
        <div class="small sent"></div>`;
      if(root.querySelector('.empty')) root.innerHTML='';
      root.appendChild(card);
      card.settings = {...r.settings};
      hold(card.querySelector('.f'), r.id, () => 'f'+card.settings.straight);
      hold(card.querySelector('.t'), r.id, () => 't'+card.settings.straight);
      hold(card.querySelector('.e'), r.id, () => 'e'+card.settings.turn);
      hold(card.querySelector('.d'), r.id, () => 'd'+card.settings.turn);
      const sliders = Object.fromEntries(SETTINGS.map(s => [s.key, card.querySelector('.' + s.key)]));
      const upload = card.querySelector('.upload');
      const showSliders = () => {
        for(const k in sliders){ sliders[k].value = card.settings[k]; card.querySelector('.'+k+'-val').textContent = fmtOf[k](card.settings[k]); }
        upload.disabled = true; upload.classList.remove('dirty'); upload.textContent = 'Upload';
        for(const el of card.querySelectorAll('.deg')) el.textContent = card.settings.angle_step + '°';
      };
      const onSlide = () => {
        for(const k in sliders) card.querySelector('.'+k+'-val').textContent = fmtOf[k](+sliders[k].value);
        const dirty = Object.keys(sliders).some(k => +sliders[k].value !== card.settings[k]);
        upload.disabled = !dirty; upload.classList.toggle('dirty', dirty); upload.textContent = 'Upload';
      };
      for(const k in sliders) sliders[k].oninput = onSlide;
      upload.onclick = async () => {
        const body = Object.fromEntries(SETTINGS.map(s => [s.key, +sliders[s.key].value]));
        const res = await api(`/api/robots/${r.id}/settings`, body);
        if(res.settings){ card.settings = res.settings; showSliders(); upload.textContent = 'Uploaded'; }
      };
      card.querySelector('.cal').onclick = () => cmd(r.id, `v360,${card.settings.turn}`);
      card.showSliders = showSliders;
      showSliders();
      // camera: snapshots by default, Live switches to the MJPEG stream (one viewer at a time)
      const video = card.querySelector('.video'), img = video.querySelector('img');
      card.live = false; card.flash = false;
      const refresh = () => { if(card.camera && !card.live) img.src = card.camera.snapshot + '?t=' + Date.now(); };
      video.querySelector('.live').onclick = e => {
        card.live = !card.live;
        e.target.textContent = card.live ? 'Stop' : 'Live';
        img.src = card.live ? card.camera.stream : card.camera.snapshot + '?t=' + Date.now();
      };
      video.querySelector('.snap').onclick = () => { if(card.live) video.querySelector('.live').click(); else refresh(); };
      video.querySelector('.flash').onclick = e => {
        card.flash = !card.flash;
        e.target.textContent = card.flash ? 'Flash off' : 'Flash';
        fetch(card.camera.led + '?on=' + (card.flash ? 1 : 0), {mode:'no-cors'});
      };
      card.refreshCamera = refresh;
      setInterval(() => { if(card.camera && !card.live && document.visibilityState === 'visible') refresh(); }, 5000);
      card.querySelector('.p').onclick = () => cmd(r.id,'p');
      // turn by angle: timed from the "Full turn" calibration, executed by the robot (v<deg>,<power>)
      card.querySelector('.ccw').onclick = () => cmd(r.id, `v-${card.settings.angle_step},${card.settings.turn}`);
      card.querySelector('.cw').onclick  = () => cmd(r.id, `v${card.settings.angle_step},${card.settings.turn}`);
      const input = card.querySelector('input[placeholder]');
      const send = () => { if(input.value.trim()){ cmd(r.id, input.value.trim()); input.value=''; } };
      card.querySelector('.send').onclick = send;
      input.onkeydown = e => { if(e.key==='Enter') send(); };
      card.querySelector('.beep').onclick = () => cmd(r.id,'n880,150');
      card.querySelector('.hello').onclick = () => cmd(r.id,'mHELLO');
      card.querySelector('.eyes').onclick = () => cmd(r.id,'o');
      card.querySelector('.clear').onclick = () => api(`/api/robots/${r.id}/clear`,{});
      const say = async text => {
        const res = await api(`/api/robots/${r.id}/say`, {text});
        card.querySelector('.pattern').textContent = res.pattern ? `${text}: ${res.pattern}` + (res.matrix ? ` | matrix: ${res.matrix}` : '') : (res.error || '');
      };
      const show = async text => {
        const res = await api(`/api/robots/${r.id}/show`, {text});
        card.querySelector('.pattern').textContent = res.matrix ? `matrix: ${res.matrix}` : (res.error || '');
      };
      const showInput = card.querySelector('.show-text');
      const showSend = () => { if(showInput.value.trim()){ show(showInput.value.trim()); showInput.value=''; } };
      card.querySelector('.show').onclick = showSend;
      showInput.onkeydown = e => { if(e.key==='Enter') showSend(); };
      for(const b of card.querySelectorAll('[data-say]')) b.onclick = () => say(b.dataset.say);
      const sayInput = card.querySelector('.say-text');
      const saySend = () => { if(sayInput.value.trim()){ say(sayInput.value.trim()); sayInput.value=''; } };
      card.querySelector('.say').onclick = saySend;
      sayInput.onkeydown = e => { if(e.key==='Enter') saySend(); };
      card.querySelector('.auto').onchange = e => api(`/api/robots/${r.id}/auto`,{enabled:e.target.checked});
    }
    card.querySelector('.dot').className = 'dot' + (r.online ? ' on' : '');
    const cam = r.camera, video = card.querySelector('.video');
    if(cam && cam.stream){
      const first = !card.camera;
      card.camera = cam;
      video.hidden = false;
      video.querySelector('.caminfo').textContent = (cam.online ? 'camera online' : `camera offline (${cam.ageSeconds} s)`) + (cam.rssi != null ? ` · ${cam.rssi} dBm` : '');
      if(first) card.refreshCamera();
    } else { video.hidden = true; card.camera = null; }
    const hasMatrix = r.telemetry?.matrix !== false;
    card.querySelector('.show-text').parentElement.hidden = !hasMatrix;
    card.querySelector('.tele').innerHTML =
      row('status', r.online ? 'online' : `offline (${r.ageSeconds ?? '-'} s)`) +
      row('ip / rssi', `${t.ip ?? '-'} / ${t.rssi ?? '-'} dBm`) +
      row('battery', t.battery == null ? 'n/a' : t.battery + ' %') +
      row('motors L / R', `${t.motors?.esq ?? '-'} / ${t.motors?.dir ?? '-'}` + (t.targets && (t.targets.esq !== t.motors?.esq || t.targets.dir !== t.motors?.dir) ? ` → ${t.targets.esq} / ${t.targets.dir}` : '')) +
      row('acceleration', t.ramp == null ? '-' : (t.ramp == 0 ? 'off' : t.ramp + ' ms')) +
      row('full turn', t.turn360 == null ? '-' : t.turn360 + ' ms') +
      row('speaker', t.speaking ? 'playing' : 'quiet') +
      row('uptime', t.uptime != null ? Math.round(t.uptime/1000)+' s' : '-') +
      row('syncs / errors', `${r.syncs} / ${t.errors ?? '-'}`) +
      row('interval', (t.interval ?? '-') + ' ms') +
      row('free heap', t.heap != null ? t.heap + ' B' : '-') +
      row('last command', t.lastCommand ? (t.lastCommand.length > 28 ? t.lastCommand.slice(0,27) + '…' : t.lastCommand) : '-') +
      row('firmware', t.fw ?? '-');
    if(document.activeElement !== card.querySelector('.auto')) card.querySelector('.auto').checked = r.auto;
    // settings changed from another browser: refresh sliders unless this one has unsaved edits
    if(JSON.stringify(r.settings) !== JSON.stringify(card.settings) && card.querySelector('.upload').disabled){
      card.settings = {...r.settings}; card.showSliders();
    }
    card.querySelector('.sent').textContent = 'sent: ' + (r.sent.slice(-8).join(' ') || '-') + (r.queued.length ? ' | queued: ' + r.queued.join(' ') : '');
  }
  for(const card of [...root.querySelectorAll('.card')]) if(!have.has(card.id.slice(2))) card.remove();
}
async function poll(){
  try{ render(await (await fetch('/api/robots')).json()); }catch(e){}
  setTimeout(poll, 1000);
}
poll();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
#  Entry point                                                                 #
# --------------------------------------------------------------------------- #

def make_server(host="0.0.0.0", port=8000):
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main(argv=None):
    global VERBOSE, TOKEN
    parser = argparse.ArgumentParser(description="openDecabot control server")
    parser.add_argument("--host", default="0.0.0.0", help="interface to listen on (default: all)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--token", default="", help="shared secret robots must send (SERVER_TOKEN in config.h)")
    parser.add_argument("--verbose", action="store_true", help="log every sync and HTTP request")
    args = parser.parse_args(argv)
    VERBOSE = args.verbose
    TOKEN = args.token

    server = make_server(args.host, args.port)
    log(f"openDecabot server listening on http://{args.host}:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
