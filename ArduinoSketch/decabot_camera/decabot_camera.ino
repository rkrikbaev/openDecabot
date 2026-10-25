/*
 * openDecabot Camera (ESP32-CAM AI-Thinker)
 * https://github.com/triwaca/openDecabot
 *
 * A second board that gives the robot eyes. It joins the Wi-Fi network,
 * serves the camera over HTTP and announces itself to the Decabot server
 * (see /server), which then shows the video on the robot's dashboard card
 * and can process frames to drive the robot (see server/vision_example.py).
 *
 *   http://<camera ip>/           info page
 *   http://<camera ip>/capture    one JPEG frame
 *   http://<camera ip>/stream     MJPEG video (one viewer at a time)
 *   http://<camera ip>/led?on=1   flash LED on / off
 *   http://<camera ip>/status     JSON status
 *
 * Every CAMERA_ANNOUNCE_MS the board POSTs its URLs to
 *   http://SERVER_HOST:SERVER_PORT/api/robots/<ROBOT_NAME>/camera
 * so the server knows which camera belongs to which robot.
 *
 * To use:
 * - Copy config.example.h to config.h: Wi-Fi, server address and the name of
 *   the robot the camera sits on (the 4 letters on the dashboard, e.g. 51F2).
 * - Arduino IDE: board "AI Thinker ESP32-CAM" (esp32 core 3.x). The board
 *   has no USB: flash through an ESP32-CAM-MB base board or a USB-TTL
 *   adapter (IO0 to GND while powering on to enter flash mode).
 * - Power from the robot's 5 V rail (Decabot Mini Shield 5V and GND pins).
 *   The module draws up to 300 mA in bursts; a 470-1000 uF capacitor across
 *   5V/GND near the camera avoids brown-out resets.
 *
 * Mounting: the module takes the place of the LED matrix shield at the
 * front of the robot; set HAS_MATRIX false in the robot's config.h.
 */

#include "esp_camera.h"
#include <WiFi.h>
#include <WebServer.h>
#include <HTTPClient.h>
#include "config.h"
#ifndef WIFI_SSID
#error "Copy config.example.h to config.h (same folder) and fill in your Wi-Fi, server and robot name"
#endif
#ifndef CAMERA_ANNOUNCE_MS
#define CAMERA_ANNOUNCE_MS 10000
#endif
#ifndef CAMERA_FRAME_SIZE
#define CAMERA_FRAME_SIZE FRAMESIZE_VGA
#endif
#ifndef CAMERA_JPEG_QUALITY
#define CAMERA_JPEG_QUALITY 12
#endif
#ifndef CAMERA_VFLIP
#define CAMERA_VFLIP 0
#endif
#ifndef CAMERA_HMIRROR
#define CAMERA_HMIRROR 0
#endif

// AI-Thinker ESP32-CAM pins
#define PWDN_GPIO_NUM  32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM  0
#define SIOD_GPIO_NUM  26
#define SIOC_GPIO_NUM  27
#define Y9_GPIO_NUM    35
#define Y8_GPIO_NUM    34
#define Y7_GPIO_NUM    39
#define Y6_GPIO_NUM    36
#define Y5_GPIO_NUM    21
#define Y4_GPIO_NUM    19
#define Y3_GPIO_NUM    18
#define Y2_GPIO_NUM    5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM  23
#define PCLK_GPIO_NUM  22
#define FLASH_LED_PIN  4     // bright white LED
#define STATUS_LED_PIN 33    // small red LED, active LOW

const char *version = "cam-1.0.20261024";

WebServer server(80);
bool cameraOk = false;
bool wifiOk = false;
bool serverOk = false;
unsigned long lastAnnounce = 0;
unsigned long frames = 0;
unsigned long announceErrors = 0;

void setup() {
  Serial.begin(115200);
  Serial.println();
  Serial.println("openDecabot camera " + String(version));
  pinMode(FLASH_LED_PIN, OUTPUT);
  digitalWrite(FLASH_LED_PIN, LOW);
  pinMode(STATUS_LED_PIN, OUTPUT);
  digitalWrite(STATUS_LED_PIN, HIGH);

  cameraOk = initCamera();
  Serial.println(cameraOk ? "Camera ready" : "Camera init FAILED (check the ribbon cable)");

  WiFi.mode(WIFI_STA);
  WiFi.setHostname(("decabot-cam-" + String(ROBOT_NAME)).c_str());
  WiFi.setSleep(false);   // steadier streaming
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to Wi-Fi ");
  Serial.println(WIFI_SSID);

  server.on("/", handleRoot);
  server.on("/capture", handleCapture);
  server.on("/stream", handleStream);
  server.on("/led", handleLed);
  server.on("/status", handleStatus);
  server.onNotFound([]() { server.send(404, "text/plain", "not found"); });
  server.begin();
}

void loop() {
  bool connected = WiFi.status() == WL_CONNECTED;
  if (connected && !wifiOk) {
    Serial.println("Wi-Fi connected, IP " + WiFi.localIP().toString());
    lastAnnounce = 0;   // announce right away
  }
  wifiOk = connected;
  digitalWrite(STATUS_LED_PIN, (wifiOk && serverOk) ? LOW : HIGH);
  server.handleClient();
  if (wifiOk && millis() - lastAnnounce >= CAMERA_ANNOUNCE_MS) {
    lastAnnounce = millis();
    announce();
  }
}

/* ----------------------------------------------------------------- camera */

bool initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = CAMERA_FRAME_SIZE;
  config.jpeg_quality = CAMERA_JPEG_QUALITY;
  config.fb_count = psramFound() ? 2 : 1;
  config.fb_location = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  config.grab_mode = CAMERA_GRAB_LATEST;
  if (!psramFound()) {
    config.frame_size = FRAMESIZE_QVGA;   // no PSRAM: keep frames small
  }
  if (esp_camera_init(&config) != ESP_OK) return false;
  sensor_t *s = esp_camera_sensor_get();
  if (s) {
    s->set_vflip(s, CAMERA_VFLIP);
    s->set_hmirror(s, CAMERA_HMIRROR);
  }
  return true;
}

/* --------------------------------------------------------------- handlers */

String baseUrl() {
  return "http://" + WiFi.localIP().toString();
}

void handleRoot() {
  String html = "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Decabot camera " + String(ROBOT_NAME) + "</title>"
                "<style>body{font-family:Arial;background:#1a1a2e;color:#fff;text-align:center;padding:16px}img{max-width:100%;border-radius:12px}"
                "a{color:#9cf}</style></head><body><h2>openDecabot camera " + String(ROBOT_NAME) + "</h2>"
                "<img src='/stream'><p><a href='/capture'>capture</a> | <a href='/led?on=1'>flash on</a> | <a href='/led?on=0'>flash off</a> | <a href='/status'>status</a></p>"
                "</body></html>";
  server.send(200, "text/html", html);
}

void handleCapture() {
  if (!cameraOk) { server.send(503, "text/plain", "camera not initialised"); return; }
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) { server.send(503, "text/plain", "frame capture failed"); return; }
  frames++;
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.sendHeader("Cache-Control", "no-store");
  server.setContentLength(fb->len);
  server.send(200, "image/jpeg", "");
  server.client().write(fb->buf, fb->len);
  esp_camera_fb_return(fb);
}

// MJPEG: multipart stream, served until the viewer disconnects.
// WebServer handles one client at a time, so a second viewer waits.
void handleStream() {
  if (!cameraOk) { server.send(503, "text/plain", "camera not initialised"); return; }
  WiFiClient client = server.client();
  client.print("HTTP/1.1 200 OK\r\n"
               "Access-Control-Allow-Origin: *\r\n"
               "Cache-Control: no-store\r\n"
               "Content-Type: multipart/x-mixed-replace; boundary=frame\r\n\r\n");
  while (client.connected()) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) break;
    frames++;
    client.printf("--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n", fb->len);
    size_t written = client.write(fb->buf, fb->len);
    client.print("\r\n");
    esp_camera_fb_return(fb);
    if (written != fb->len) break;
    // keep announcing to the server while streaming
    if (millis() - lastAnnounce >= CAMERA_ANNOUNCE_MS) {
      lastAnnounce = millis();
      announce();
    }
    delay(1);
  }
  client.stop();
}

void handleLed() {
  bool on = server.hasArg("on") && server.arg("on") != "0";
  digitalWrite(FLASH_LED_PIN, on ? HIGH : LOW);
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(200, "text/plain", on ? "ON" : "OFF");
}

String statusJson() {
  return "{\"robot\":\"" + String(ROBOT_NAME) + "\",\"fw\":\"" + String(version) + "\","
         "\"stream\":\"" + baseUrl() + "/stream\",\"snapshot\":\"" + baseUrl() + "/capture\","
         "\"led\":\"" + baseUrl() + "/led\",\"ip\":\"" + WiFi.localIP().toString() + "\","
         "\"rssi\":" + String(WiFi.RSSI()) + ",\"camera\":" + String(cameraOk ? "true" : "false") + ","
         "\"psram\":" + String(psramFound() ? "true" : "false") + ",\"frames\":" + String(frames) + ","
         "\"uptime\":" + String(millis()) + ",\"heap\":" + String(ESP.getFreeHeap()) + "}";
}

void handleStatus() {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(200, "application/json", statusJson());
}

/* ---------------------------------------------------------------- server */

void announce() {
  HTTPClient http;
  WiFiClient wifiClient;
  String url = String("http://") + SERVER_HOST + ":" + String(SERVER_PORT) + "/api/robots/" + ROBOT_NAME + "/camera";
  http.begin(wifiClient, url);
  http.setTimeout(1500);
  http.addHeader("Content-Type", "application/json");
  if (strlen(SERVER_TOKEN) > 0) http.addHeader("Authorization", String("Bearer ") + SERVER_TOKEN);
  int code = http.POST(statusJson());
  if (code == 200) {
    if (!serverOk) Serial.println("Registered at " + url);
    serverOk = true;
  } else {
    announceErrors++;
    if (serverOk || announceErrors % 6 == 1) {
      Serial.print("Server error: ");
      Serial.println(code < 0 ? HTTPClient::errorToString(code) : String(code));
    }
    serverOk = false;
  }
  http.end();
}
