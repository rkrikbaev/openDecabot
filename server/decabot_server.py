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
            return self.send(200, DASHBOARD_HTML, "text/html")
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
        self.read_body()
        self.send(404, {"error": "not found"})

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
  .pad .d{grid-column:3;grid-row:2}.pad .t{grid-column:2;grid-row:3}
  .row{display:flex;gap:6px;margin-top:6px}
  .row input{flex:1;padding:8px;border-radius:8px;border:1px solid var(--border);background:#0d1630;color:var(--txt);font-family:monospace}
  .row button{padding:8px 12px;font-size:14px}
  .small{font-size:12px;color:var(--muted);margin-top:8px;word-break:break-all;font-family:monospace}
  label{font-size:14px;display:flex;align-items:center;gap:6px;margin-top:8px}
  .empty{color:var(--muted)}
</style>
</head>
<body>
<h1>openDecabot server</h1>
<div id="cards" class="cards"><p class="empty">Waiting for robots... Upload decabot_serverClient with this computer's IP in config.h.</p></div>
<script>
const speed = 100;
async function api(path, body){
  const r = await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  return r.json();
}
function cmd(id, c){ return api(`/api/robots/${id}/commands`,{commands:[c]}); }
function hold(btn, id, c){
  const down = e => { e.preventDefault(); cmd(id,c); };
  const up = e => { e.preventDefault(); cmd(id,'p'); };
  btn.onpointerdown = down; btn.onpointerup = up; btn.onpointerleave = up; btn.onpointercancel = up;
}
function row(k,v){ return `<tr><td>${k}</td><td>${v}</td></tr>`; }
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
        <table class="tele"></table>
        <div class="pad">
          <button class="f">&#9650;</button><button class="e">&#9664;</button><button class="p">&#9632;</button>
          <button class="d">&#9654;</button><button class="t">&#9660;</button>
        </div>
        <div class="row"><input placeholder="raw command, e.g. f60,800 or mHELLO"><button class="send">Send</button></div>
        <div class="row"><button class="beep">Beep</button><button class="hello">Say hi</button><button class="eyes">Eyes</button><button class="clear">Clear queue</button></div>
        <label><input type="checkbox" class="auto"> Autopilot (server drives a square)</label>
        <div class="small sent"></div>`;
      if(root.querySelector('.empty')) root.innerHTML='';
      root.appendChild(card);
      hold(card.querySelector('.f'), r.id, 'f'+speed);
      hold(card.querySelector('.t'), r.id, 't'+Math.round(speed/2));
      hold(card.querySelector('.e'), r.id, 'e'+speed);
      hold(card.querySelector('.d'), r.id, 'd'+speed);
      card.querySelector('.p').onclick = () => cmd(r.id,'p');
      const input = card.querySelector('input[placeholder]');
      const send = () => { if(input.value.trim()){ cmd(r.id, input.value.trim()); input.value=''; } };
      card.querySelector('.send').onclick = send;
      input.onkeydown = e => { if(e.key==='Enter') send(); };
      card.querySelector('.beep').onclick = () => cmd(r.id,'n880,150');
      card.querySelector('.hello').onclick = () => cmd(r.id,'mHELLO');
      card.querySelector('.eyes').onclick = () => cmd(r.id,'o');
      card.querySelector('.clear').onclick = () => api(`/api/robots/${r.id}/clear`,{});
      card.querySelector('.auto').onchange = e => api(`/api/robots/${r.id}/auto`,{enabled:e.target.checked});
    }
    card.querySelector('.dot').className = 'dot' + (r.online ? ' on' : '');
    card.querySelector('.tele').innerHTML =
      row('status', r.online ? 'online' : `offline (${r.ageSeconds ?? '-'} s)`) +
      row('ip / rssi', `${t.ip ?? '-'} / ${t.rssi ?? '-'} dBm`) +
      row('battery', t.battery == null ? 'n/a' : t.battery + ' %') +
      row('motors L / R', `${t.motors?.esq ?? '-'} / ${t.motors?.dir ?? '-'}`) +
      row('uptime', t.uptime != null ? Math.round(t.uptime/1000)+' s' : '-') +
      row('syncs / errors', `${r.syncs} / ${t.errors ?? '-'}`) +
      row('interval', (t.interval ?? '-') + ' ms') +
      row('free heap', t.heap != null ? t.heap + ' B' : '-') +
      row('last command', t.lastCommand || '-') +
      row('firmware', t.fw ?? '-');
    if(document.activeElement !== card.querySelector('.auto')) card.querySelector('.auto').checked = r.auto;
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
