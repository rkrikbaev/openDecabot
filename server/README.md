# openDecabot server

Lets an openDecabot talk to an external server over Wi-Fi. The robot sends
its telemetry, the server processes it and answers with control commands.
With this you can drive many robots from one web page, write autonomous
behaviour in Python instead of C, log telemetry, or connect the robot to any
other system (a game, a classroom dashboard, an AI agent...).

```
 ┌──────────────┐   POST /api/robots/<name>/sync  {"battery":87,"rssi":-60,...}   ┌──────────────────┐
 │  openDecabot │ ───────────────────────────────────────────────────────────────▶ │ decabot_server.py │
 │  (ESP8266 /  │                                                                   │                   │
 │   ESP32 C3)  │ ◀─────────────────────────────────────────────────────────────── │ dashboard + logic │
 └──────────────┘   200 text/plain  "f100,800\nn880,100\n"  (commands, one per line)└──────────────────┘
```

The robot is always the client: it works behind any home router, needs no
open ports and no fixed IP. Only plain HTTP and JSON are used, so the robot
side needs no extra libraries and the server can be rewritten in any language.

## Quick start

1. **Start the server** on a computer connected to the same Wi-Fi as the robot
   (Python 3.8+, no packages to install):

   ```bash
   python3 server/decabot_server.py
   ```

   Find the computer's IP (`ipconfig` on Windows, `ifconfig`/`ip a` on
   Linux and macOS), for example `192.168.1.100`.

2. **Configure the robot.**
   - Arduino: in `ArduinoSketch/decabot_serverClient` copy `config.example.h`
     to `config.h`, fill in `WIFI_SSID`, `WIFI_PASSWORD` and `SERVER_HOST`
     and upload to the Wemos D1 Mini. `config.h` is ignored by git, so your
     password stays on your computer.
     Needs the same libraries as `decabot_webControler` (Adafruit GFX,
     WEMOS Matrix GFX, ArduinoUniqueID). Note the robot now *joins* your Wi-Fi
     network instead of creating its own `decabot_XXXX` access point.
   - MicroPython (ESP32 C3): copy `microPython/esp32c3/server_client.py` to the
     board, edit the settings at the top and run it (or `import server_client`
     from `main.py`).

3. **Drive it.** Open `http://192.168.1.100:8000` in a browser. Each robot that
   contacts the server gets a card with its telemetry, a D-pad (hold to move,
   release to stop), a raw command box and an *Autopilot* switch that makes
   the server drive the robot in a square. The *Straight speed* and *Turn
   speed* sliders set the power the D-pad uses; move them and press *Upload*
   to apply. The values are kept on the server per robot, so every browser
   sees the same ones.

While the robot cannot reach the server, the LED matrix alternates the Wi-Fi
logo with the robot name; the eyes appear once the first answer arrives.

## Protocol

### Robot → server: telemetry

`POST http://<host>:<port>/api/robots/<robot name>/sync`
`Content-Type: application/json`, optional `Authorization: Bearer <token>`.

```json
{
  "id": "1234567890",
  "name": "AB12",
  "fw": "1.1.20261024",
  "uptime": 48213,
  "ip": "192.168.1.57",
  "rssi": -61,
  "heap": 38120,
  "battery": 87,
  "motors": {"esq": 0, "dir": 0},
  "moving": false,
  "interval": 200,
  "syncs": 240,
  "errors": 2,
  "commands": 11,
  "lastCommand": "f100,800",
  "sensors": {"a0": 812}
}
```

| field | meaning |
|---|---|
| `id`, `name` | chip id and the 4-letter name shown on the matrix (same as the captive-portal firmware) |
| `uptime` | milliseconds since boot |
| `rssi` | Wi-Fi signal in dBm (closer to 0 is better) |
| `heap` | free RAM in bytes |
| `battery` | 0..100 %, or `null` when `SHOW_BATTERY` is off |
| `motors.esq`, `motors.dir` | power currently applied to left / right servo, -100..100 |
| `interval` | current sync interval in ms |
| `syncs`, `errors`, `commands` | counters since boot |
| `sensors` | free-form object: add your own readings here (`buildTelemetry()` in the sketch) |

The default interval is 200 ms. One sync costs about 300 bytes up and a few
bytes down, so even 10 robots on a classroom Wi-Fi are no problem.

### Server → robot: commands

The response is `200 OK`, `text/plain`, one command per line. An empty body
means "nothing to do". Lines starting with `#` are ignored. The commands are
the same one-letter protocol the robot accepts on the Serial port, so you can
test them in the Arduino Serial Monitor first:

| command | effect |
|---|---|
| `f<power>` | forward, power 0..100 |
| `t<power>` | backwards |
| `d<power>` / `e<power>` | turn right / left (wheels in opposite directions) |
| `f<power>,<ms>` | any movement command may end with `,ms`: run for that long, then stop (`d60,450`) |
| `p` | stop |
| `y<-100..100>` then `x<-100..100>` | joystick: `y` sets speed, `x` sets direction and applies both |
| `i<ms>` | change the sync interval (minimum 100) |
| `n<hz>,<ms>` | play a note on the buzzer (`n494,200`) |
| `m<text>` | scroll text on the LED matrix (`mHELLO`), then back to eyes |
| `o` | stop scrolling, show the eyes |
| `l1` / `l0` | blue on-board LED on / off |
| `a` `b` `c` / `q` `r` `s` | servo calibration (left min/zero/max, right min/zero/max), see `decabot_webControler` |
| `?` | print robot info on Serial |

Commands are executed in order, immediately. Movement commands without a
duration keep running until the next movement command or `p`.

### Safety

- **Failsafe**: if the robot is moving and the server has not answered for
  `FAILSAFE_MS` (2 s by default) the motors stop. Set it to 0 to disable.
- **Token**: start the server with `--token secret` and put the same value in
  `SERVER_TOKEN`; syncs without it get `401`. Without a token anyone on the
  network can control the robots, which is fine for a classroom, not for the
  internet.
- The dashboard and the command API are not protected by the token; expose
  the server to the internet only behind a reverse proxy with authentication.

## HTTP API (for your own apps)

| method & path | body | answer |
|---|---|---|
| `GET /` | | dashboard |
| `GET /api/robots` | | list of robots with telemetry, queue and online state |
| `GET /api/robots/<name>` | | one robot |
| `POST /api/robots/<name>/commands` | `{"commands": ["f100", "p"]}` or plain text, one command per line | `{"queued": 2, "pending": 2}` |
| `POST /api/robots/<name>/clear` | | drops the pending queue |
| `POST /api/robots/<name>/auto` | `{"enabled": true}` | toggles the autopilot demo |
| `POST /api/robots/<name>/settings` | `{"straight": 80, "turn": 60}` | D-pad power (0..100) for straight moves and turns; stored per robot, returned in `GET /api/robots` |
| `POST /api/robots/<name>/sync` | telemetry JSON (used by the robot) | commands as text |

Queued commands are delivered on the robot's next sync, so with the default
interval a button press reaches the wheels within about 200 ms.

Example from a terminal:

```bash
curl -X POST http://192.168.1.100:8000/api/robots/AB12/commands -d $'f80,1000\nn880,100'
```

## Writing your own logic

Everything the server decides lives in one function in `decabot_server.py`:

```python
def process_telemetry(robot, telemetry):
    commands = []
    if telemetry.get("sensors", {}).get("a0", 1024) < 300:   # e.g. a line sensor
        commands.append("d60,300")
    return commands
```

It runs on every sync with the fresh telemetry and returns the commands to
send. Per-robot state (`robot.auto_step`, or any attribute you add to `Robot`)
survives between calls. Two examples are included: a low-battery rule that
stops the robot and shows `BAT`, and the autopilot that drives a square.

The protocol is plain HTTP, so the server can also be rewritten in Node, Go,
PHP or anything else: accept the POST, answer with lines of text.

## Tests

```bash
python3 server/test_server.py
```

## Options

```
python3 decabot_server.py --help
  --host 0.0.0.0     interface to listen on
  --port 8000
  --token SECRET     require Authorization: Bearer SECRET on /sync
  --verbose          log every sync and HTTP request
```
