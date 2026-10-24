#!/usr/bin/env python3
"""Self-test for decabot_server.py. Run:  python3 test_server.py"""

import json
import threading
import unittest
import urllib.error
import urllib.request

import decabot_server as srv


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = srv.make_server("127.0.0.1", 0)
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        srv.REGISTRY.robots.clear()
        srv.TOKEN = ""

    def call(self, method, path, body=None, headers=None):
        data = None
        if body is not None:
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as err:
            return err.code, err.read().decode()

    def sync(self, robot="AB12", **telemetry):
        body = {"id": "1", "name": robot, "battery": None, "motors": {"esq": 0, "dir": 0}}
        body.update(telemetry)
        return self.call("POST", f"/api/robots/{robot}/sync", body, {"Content-Type": "application/json"})

    def test_sync_registers_robot_and_returns_empty_body(self):
        status, body = self.sync(rssi=-55)
        self.assertEqual(status, 200)
        self.assertEqual(body, "")
        status, body = self.call("GET", "/api/robots")
        robots = json.loads(body)
        self.assertEqual([r["id"] for r in robots], ["AB12"])
        self.assertTrue(robots[0]["online"])
        self.assertEqual(robots[0]["telemetry"]["rssi"], -55)

    def test_queued_commands_are_delivered_once_in_order(self):
        status, body = self.call("POST", "/api/robots/AB12/commands", {"commands": ["f100", "p"]})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["queued"], 2)
        # text body is accepted too
        self.call("POST", "/api/robots/AB12/commands", "n440,100\n")
        _, body = self.sync()
        self.assertEqual(body, "f100\np\nn440,100\n")
        _, body = self.sync()
        self.assertEqual(body, "")

    def test_low_battery_rule_fires_once(self):
        _, body = self.sync(battery=5)
        self.assertEqual(body.splitlines(), ["p", "n330,400", "mBAT"])
        _, body = self.sync(battery=5)
        self.assertEqual(body, "")
        self.sync(battery=50)      # recovers
        _, body = self.sync(battery=5)
        self.assertEqual(body.splitlines()[0], "p")

    def test_autopilot_sends_pattern_steps(self):
        status, body = self.call("POST", "/api/robots/AB12/auto", {"enabled": True})
        self.assertEqual(json.loads(body)["auto"], True)
        _, body = self.sync()
        self.assertEqual(body.strip(), srv.AUTO_PATTERN[0][0])
        _, body = self.sync()
        self.assertEqual(body, "")       # waits before the next step
        _, body = self.call("POST", "/api/robots/AB12/auto", {"enabled": False})
        _, body = self.sync()
        self.assertEqual(body.strip(), "p")

    def test_token_is_enforced_on_sync(self):
        srv.TOKEN = "secret"
        status, _ = self.sync()
        self.assertEqual(status, 401)
        status, _ = self.call("POST", "/api/robots/AB12/sync", {"name": "AB12"},
                              {"Authorization": "Bearer secret"})
        self.assertEqual(status, 200)

    def test_bad_json_and_unknown_paths(self):
        status, _ = self.call("POST", "/api/robots/AB12/sync", "not json")
        self.assertEqual(status, 400)
        status, _ = self.call("GET", "/api/robots/NOPE")
        self.assertEqual(status, 404)
        status, _ = self.call("GET", "/nothing")
        self.assertEqual(status, 404)
        status, body = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("openDecabot server", body)

    def test_settings_are_stored_clamped_and_validated(self):
        self.sync(ramp=2000)
        _, body = self.call("GET", "/api/robots/AB12")
        self.assertEqual(json.loads(body)["settings"], srv.DEFAULT_SETTINGS)
        status, body = self.call("POST", "/api/robots/AB12/settings", {"straight": 60, "turn": 140})
        self.assertEqual(status, 200)
        settings = json.loads(body)["settings"]
        self.assertEqual((settings["straight"], settings["turn"]), (60, 100))
        status, body = self.call("POST", "/api/robots/AB12/settings", {"turn": 35, "morse_unit": 5})
        settings = json.loads(body)["settings"]
        self.assertEqual((settings["straight"], settings["turn"], settings["morse_unit"]), (60, 35, 30))
        status, _ = self.call("POST", "/api/robots/AB12/settings", {"turn": "fast"})
        self.assertEqual(status, 400)
        _, body = self.call("GET", "/api/robots/AB12")
        self.assertEqual(json.loads(body)["settings"]["turn"], 35)

    def test_ramp_setting_is_pushed_until_the_robot_reports_it(self):
        self.sync(ramp=2000)
        _, body = self.sync(ramp=2000)
        self.assertEqual(body, "")                       # already in sync
        self.call("POST", "/api/robots/AB12/settings", {"ramp": 1000})
        _, body = self.sync(ramp=2000)
        self.assertEqual(body, "g1000\n")
        _, body = self.sync(ramp=2000)                   # robot still reports old: not spammed within 1 s
        self.assertEqual(body, "")
        _, body = self.sync(ramp=1000)                   # robot applied it
        self.assertEqual(body, "")
        _, body = self.sync()                            # robot without ramp support: nothing sent
        self.assertEqual(body, "")

    def test_morse_encoding(self):
        self.assertEqual(srv.encode_morse("ПРИВЕТ"), ".--. .-. .. .-- . -")
        self.assertEqual(srv.encode_morse("ПОКА"), ".--. --- -.- .-")
        self.assertEqual(srv.encode_morse("КАК ДЕЛА"), "-.- .- -.-/-.. . .-.. .-")
        self.assertEqual(srv.encode_morse("sos"), "... --- ...")
        self.assertEqual(srv.encode_morse("ёж  1"), ". ...-/.----")
        self.assertEqual(srv.encode_morse("@#$"), "")

    def test_say_queues_a_morse_command_with_the_robot_settings(self):
        self.call("POST", "/api/robots/AB12/settings", {"morse_hz": 1000, "morse_unit": 60})
        status, body = self.call("POST", "/api/robots/AB12/say", {"text": "Пока"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["pattern"], ".--. --- -.- .-")
        _, body = self.sync()
        self.assertEqual(body, "w1000,60,.--. --- -.- .-\n")
        status, _ = self.call("POST", "/api/robots/AB12/say", {"text": "***"})
        self.assertEqual(status, 400)
        status, body = self.call("GET", "/api/phrases")
        self.assertEqual(json.loads(body), srv.MORSE_PHRASES)

    def test_clear_drops_queue(self):
        self.call("POST", "/api/robots/AB12/commands", {"commands": ["f100", "f100"]})
        _, body = self.call("POST", "/api/robots/AB12/clear", {})
        self.assertEqual(json.loads(body)["dropped"], 2)
        _, body = self.sync()
        self.assertEqual(body, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
