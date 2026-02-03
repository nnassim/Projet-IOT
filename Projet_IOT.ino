#include <WiFi.h>

const char* WIFI_SSID = "iPhone";
const char* WIFI_PASS = "nassim95";

String location = "Non_Definie";

void setup() {
  Serial.begin(115200);
  delay(3000);                 // Important pour la synchro PC
  Serial.println("ESP32 READY");

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) {
    delay(300);
  }
}

void loop() {
  if (!Serial.available()) return;

  String cmd = Serial.readStringUntil('\n');
  cmd.trim();

  if (cmd.startsWith("LOC ")) {
    location = cmd.substring(4);
    location.trim();
    Serial.println("[OK] LOC SET");
  }

  if (cmd == "SCAN") {
    scanWiFi();
  }
}

void scanWiFi() {
  int n = WiFi.scanNetworks(false, true);
  unsigned long ts = millis();

  Serial.println("#DATA_START");
  Serial.println("timestamp_ms,location,ssid,bssid,rssi,channel");

  for (int i = 0; i < n; i++) {
    Serial.printf(
      "%lu,%s,%s,%s,%d,%d\n",
      ts,
      location.c_str(),
      WiFi.SSID(i).c_str(),
      WiFi.BSSIDstr(i).c_str(),
      WiFi.RSSI(i),
      WiFi.channel(i)
    );
  }

  Serial.println("#DATA_END");
}
