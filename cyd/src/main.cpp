#include <Arduino.h>
#include <WiFi.h>

#include "BoardDisplay.h"
#include "CompanionConfig.h"
#include "CompanionUi.h"
#include "Provisioning.h"
#include "RemoteView.h"

namespace {

enum class AppState {
  kUnconfigured,
  kConnecting,
  kReady,
};

#ifndef CYD_VERSION
#define CYD_VERSION "development"
#endif

BoardDisplay display;
CompanionConfigStore config_store;
Provisioning provisioning(display, config_store);
RemoteViewClient remote_view;
RemoteViewSnapshot snapshot;
CompanionUi ui(display);
CompanionConfig config;
AppState state = AppState::kUnconfigured;
std::uint32_t reconnect_at = 0;
std::uint32_t heartbeat_at = 0;
String poll_error;

void checkFactoryReset() {
  pinMode(0, INPUT_PULLUP);
  if (digitalRead(0) != LOW) {
    return;
  }
  const std::uint32_t started = millis();
  display.showStatus("Factory reset", "Keep BOOT held for 5 seconds", false);
  while (millis() - started < 5000 && digitalRead(0) == LOW) {
    delay(25);
  }
  if (digitalRead(0) == LOW) {
    config_store.clear();
    display.showStatus("Factory reset", "CYD enrollment cleared", true);
    delay(800);
    ESP.restart();
  }
}
bool has_snapshot = false;

bool connectSavedWifi() {
  if (WiFi.isConnected()) {
    return true;
  }
  if (!config.wifi_ssid.length()) {
    return false;
  }
  display.showConnecting(config.wifi_ssid);
  WiFi.mode(WIFI_STA);
  WiFi.begin(config.wifi_ssid.c_str(), config.wifi_password.c_str());
  const std::uint32_t started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < 15000) {
    delay(50);
  }
  return WiFi.isConnected();
}

void pollPi() {
  if (remote_view.fetch(config, snapshot, poll_error)) {
    has_snapshot = true;
  } else {
    Serial.printf("Remote view failed: %s\n", poll_error.c_str());
  }
  ui.render(snapshot, poll_error, WiFi.isConnected());
}

void handleTouch() {
  const TouchAction action = ui.takeAction(
      snapshot, has_snapshot && !poll_error.length() && WiFi.isConnected());
  if (action == TouchAction::kNone) {
    return;
  }
  String command_error;
  bool ok = false;
  if (action == TouchAction::kCarouselPause) {
    ok = remote_view.controlCarousel(
        config, snapshot.carousel_paused ? "resume" : "pause", command_error);
  } else if (action == TouchAction::kCarouselNext) {
    ok = remote_view.controlCarousel(config, "next", command_error);
  } else if (action == TouchAction::kMusicToggle) {
    const String music_action =
        snapshot.music_state == "playing"
            ? "pause"
            : (snapshot.music_state == "paused" ? "resume" : "play");
    ok = remote_view.controlMusic(config, music_action, command_error);
  } else if (action == TouchAction::kMusicStop) {
    ok = remote_view.controlMusic(config, "stop", command_error);
  } else if (action == TouchAction::kMusicSkip) {
    ok = remote_view.controlMusic(config, "skip", command_error);
  }
  if (!ok) {
    poll_error = command_error;
  } else {
    poll_error = "";
    heartbeat_at = 0;
  }
  ui.render(snapshot, poll_error, has_snapshot && WiFi.isConnected());
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(150);
  Serial.printf("\nWeb Display CYD %s\n", CYD_VERSION);
  display.begin();

  checkFactoryReset();
  WiFi.persistent(false);
  if (config_store.load(config)) {
    state = AppState::kConnecting;
    Serial.printf("Stored enrollment found for %s:%u\n",
        config.pi_host.c_str(), config.pi_port);
  } else {
    Serial.println("No enrollment; starting captive-portal provisioning");
    provisioning.begin();
    state = AppState::kUnconfigured;
  }
}

void loop() {
  display.loop();

  if (state == AppState::kUnconfigured) {
    provisioning.loop();
    if (provisioning.enrolled(config)) {
      state = AppState::kConnecting;
      reconnect_at = 0;
    }
    delay(4);
    return;
  }

  if (state == AppState::kConnecting) {
    if (millis() < reconnect_at) {
      delay(50);
      return;
    }
    if (connectSavedWifi()) {
      state = AppState::kReady;
      heartbeat_at = 0;
      display.showStatus("CYD connected", WiFi.localIP().toString(), true);
    } else {
      display.showStatus("Wi-Fi offline", "Retrying saved network", false);
      reconnect_at = millis() + 15000;
    }
  } else if (millis() >= heartbeat_at) {
    heartbeat_at = millis() + 5000;
    pollPi();
    if (!WiFi.isConnected()) {
      state = AppState::kConnecting;
      reconnect_at = 0;
    }
  } else {
    handleTouch();
  }
  delay(10);
}

