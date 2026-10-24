#!/usr/bin/env python3
"""
openDecabot vision example: the server-side "eyes" of the robot.

Pulls snapshots from the ESP32-CAM announced on the Decabot server, looks for
the biggest blob of one colour (a red ball, a piece of coloured paper...) and
steers the robot towards it through the normal command API. Runs next to
decabot_server.py, on the same computer or any other.

Needs OpenCV:   pip install opencv-python-headless numpy

Run:
    python3 vision_example.py --server http://127.0.0.1:8000 --robot 51F2
    python3 vision_example.py --robot 51F2 --color blue --dry-run   # look, don't drive
    python3 vision_example.py --self-test                           # no camera needed

Tune COLORS and the thresholds below for your lighting. Use --show to open a
window with the detection (needs the non-headless opencv-python).
"""

import argparse
import json
import sys
import time
import urllib.request

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover - reported to the user
    cv2 = None
    np = None

# HSV ranges (OpenCV scale: H 0..179, S 0..255, V 0..255). Red wraps around 0.
COLORS = {
    "red":    [((0, 120, 70), (10, 255, 255)), ((170, 120, 70), (179, 255, 255))],
    "green":  [((40, 80, 60), (85, 255, 255))],
    "blue":   [((95, 100, 60), (130, 255, 255))],
    "yellow": [((20, 120, 100), (35, 255, 255))],
}

MIN_AREA_FRACTION = 0.002   # ignore blobs smaller than 0.2 % of the frame
NEAR_AREA_FRACTION = 0.12   # stop when the target fills this much: we have arrived
CENTER_DEADBAND = 0.12      # |x offset| below this (fraction of half width) = straight ahead


def find_target(frame_bgr, color="red"):
    """Returns (x_offset, area_fraction) or None.

    x_offset is -1 (far left) .. +1 (far right) of the frame centre.
    """
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mask = None
    for low, high in COLORS[color]:
        part = cv2.inRange(hsv, np.array(low, dtype=np.uint8), np.array(high, dtype=np.uint8))
        mask = part if mask is None else cv2.bitwise_or(mask, part)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    biggest = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(biggest)
    h, w = mask.shape
    if area < MIN_AREA_FRACTION * w * h:
        return None
    x, _, bw, _ = cv2.boundingRect(biggest)
    center = x + bw / 2
    return (center - w / 2) / (w / 2), area / (w * h)


def decide(target, speed=50, turn=35):
    """Turns a detection into one robot command (timed, so a lost camera stops the robot)."""
    if target is None:
        return "p"
    x_offset, area = target
    if area >= NEAR_AREA_FRACTION:
        return "p"                               # arrived
    if x_offset > CENTER_DEADBAND:
        return f"d{turn},300"                    # target on the right: turn right a bit
    if x_offset < -CENTER_DEADBAND:
        return f"e{turn},300"
    return f"f{speed},600"


# --------------------------------------------------------------------------- #

def http_json(url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.load(resp)


def fetch_frame(snapshot_url):
    with urllib.request.urlopen(snapshot_url, timeout=5) as resp:
        raw = np.frombuffer(resp.read(), dtype=np.uint8)
    return cv2.imdecode(raw, cv2.IMREAD_COLOR)


def run(server, robot, color, dry_run, show, period):
    info = http_json(f"{server}/api/robots/{robot}")
    camera = info.get("camera")
    if not camera or not camera.get("snapshot"):
        sys.exit(f"robot {robot} has no camera announced on {server}")
    print(f"camera {camera['snapshot']}  color {color}  {'DRY RUN' if dry_run else 'driving'}")
    last_command = None
    while True:
        started = time.time()
        try:
            frame = fetch_frame(camera["snapshot"])
            target = find_target(frame, color)
            command = decide(target)
        except Exception as exc:
            print("frame error:", exc)
            target, command = None, "p"
        if target:
            print(f"target x={target[0]:+.2f} area={target[1]*100:.1f}%  ->  {command}")
        elif command != last_command:
            print("no target  ->  p")
        if not dry_run and (command != "p" or last_command != "p"):
            http_json(f"{server}/api/robots/{robot}/commands", {"commands": [command]})
        last_command = command
        if show and target is not None:
            cv2.imshow("decabot vision", frame)
            cv2.waitKey(1)
        time.sleep(max(0.0, period - (time.time() - started)))


def self_test():
    """Synthetic frames: a red square left, right, centre, big; and no target."""
    def frame_with_square(x, size):
        img = np.full((240, 320, 3), (200, 200, 200), dtype=np.uint8)   # grey background
        cv2.rectangle(img, (x, 100), (x + size, 100 + size), (0, 0, 255), -1)  # BGR red
        return img

    cases = [
        (frame_with_square(20, 40), "e35,300", "left"),
        (frame_with_square(260, 40), "d35,300", "right"),
        (frame_with_square(140, 40), "f50,600", "centre"),
        (frame_with_square(60, 200), "p", "very close"),
        (np.full((240, 320, 3), (200, 200, 200), dtype=np.uint8), "p", "nothing"),
    ]
    ok = True
    for frame, expected, name in cases:
        got = decide(find_target(frame, "red"))
        print(f"{name:11s} expected {expected:9s} got {got}")
        ok &= got == expected
    print("vision self-test", "OK" if ok else "FAILED")
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(description="Drive an openDecabot towards a coloured object seen by its camera")
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--robot", default="")
    parser.add_argument("--color", default="red", choices=sorted(COLORS))
    parser.add_argument("--period", type=float, default=0.4, help="seconds between frames")
    parser.add_argument("--dry-run", action="store_true", help="print decisions, send nothing")
    parser.add_argument("--show", action="store_true", help="open a window with the frames")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if cv2 is None:
        sys.exit("OpenCV missing: pip install opencv-python-headless numpy")
    if args.self_test:
        return self_test()
    if not args.robot:
        sys.exit("--robot NAME is required (the 4 letters on the dashboard)")
    try:
        run(args.server, args.robot, args.color, args.dry_run, args.show, args.period)
    except KeyboardInterrupt:
        if not args.dry_run:
            http_json(f"{args.server}/api/robots/{args.robot}/commands", {"commands": ["p"]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
