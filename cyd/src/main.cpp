#include <Arduino.h>
#include <WiFi.h>
#include <time.h>

#include "BoardDisplay.h"
#include "CompanionConfig.h"
#include "CompanionUi.h"
#include "Provisioning.h"
#include "RemoteView.h"
#include "CydOta.h"

size_t getArduinoLoopTaskStackSize() {
  return 16 * 1024;
}

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
CydOta ota;
AppState state = AppState::kUnconfigured;
std::uint32_t reconnect_at = 0;
std::uint32_t heartbeat_at = 0;
String poll_error;
bool boot_pressed = false;
std::uint32_t boot_pressed_at = 0;
std::uint32_t last_orientation_change_at = 0;

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
bool ota_checked = false;
std::uint32_t ota_pending_started_at = 0;
std::uint32_t last_diagnostic_at = 0;

void handleBootButton() {
  const bool pressed = digitalRead(0) == LOW;
  if (pressed && !boot_pressed) {
    boot_pressed = true;
    boot_pressed_at = millis();
    return;
  }
  if (pressed) {
    if (millis() - boot_pressed_at >= 5000) {
      config_store.clear();
      display.showStatus("Factory reset", "CYD enrollment cleared", true);
      delay(500);
      ESP.restart();
    }
    return;
  }
  if (!boot_pressed) {
    return;
  }
  boot_pressed = false;
  if (millis() - boot_pressed_at < 100) {
    return;
  }
  const std::uint32_t held_for = millis() - boot_pressed_at;
  if (held_for >= 2000) {
    config.ota_auto = !config.ota_auto;
    if (!config_store.saveOtaAuto(config.ota_auto)) {
      config.ota_auto = !config.ota_auto;
      display.showStatus("Automatic OTA", "Could not save setting", false);
      return;
    }
    display.showStatus(
        "Automatic OTA",
        config.ota_auto ? "Enabled" : "Disabled",
        true);
    Serial.printf("Automatic CYD OTA %s\n", config.ota_auto ? "enabled" : "disabled");
    heartbeat_at = millis() + 2000;
    return;
  }
  if (millis() - last_orientation_change_at < 500) {
    return;
  }
  last_orientation_change_at = millis();
  config.orientation = config.orientation == 1 ? 3 : 1;
  if (!config_store.saveOrientation(config.orientation)) {
    display.showStatus("Orientation", "Could not save rotation", false);
    return;
  }
  display.setOrientation(config.orientation);
  heartbeat_at = 0;
  Serial.printf("Display orientation changed to %u\n", config.orientation);
}

bool connectSavedWifi() {
  if (WiFi.isConnected()) {
    return true;
  }
  if (!config.wifi_ssid.length()) {
    return false;
  }
  display.showConnecting(config.wifi_ssid, config.ota_auto);
  WiFi.mode(WIFI_STA);
  WiFi.begin(config.wifi_ssid.c_str(), config.wifi_password.c_str());
  const std::uint32_t started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < 15000) {
    delay(50);
  }
  return WiFi.isConnected();
}

bool pollPi() {
  if (remote_view.fetch(config, snapshot, poll_error)) {
    has_snapshot = true;
  } else {
    Serial.printf("Remote view failed: %s\n", poll_error.c_str());
  }
  ui.render(snapshot, poll_error, WiFi.isConnected());
  return poll_error.length() == 0;
}

void handleTouch() {
  const TouchAction action = ui.takeAction(
      snapshot, has_snapshot && !poll_error.length() && WiFi.isConnected());
  if (action == TouchAction::kNone) {
    return;
  }
  RemoteViewSnapshot before = snapshot;
  String command_error;
  ControlResult control_result;
  bool ok = false;
  if (action == TouchAction::kCarouselPause) {
    ok = remote_view.controlCarousel(
        config, snapshot.carousel_paused ? "resume" : "pause",
        control_result, command_error);
  } else if (action == TouchAction::kCarouselNext) {
    ok = remote_view.controlCarousel(
        config, "next", control_result, command_error);
  } else if (action == TouchAction::kMusicToggle) {
    const String music_action =
        snapshot.music_state == "playing"
            ? "pause"
            : (snapshot.music_state == "paused" ? "resume" : "play");
    ok = remote_view.controlMusic(
        config, music_action, control_result, command_error);
  } else if (action == TouchAction::kMusicStop) {
    ok = remote_view.controlMusic(
        config, "stop", control_result, command_error);
  } else if (action == TouchAction::kMusicSkip) {
    ok = remote_view.controlMusic(
        config, "skip", control_result, command_error);
  }
  if (!ok) {
    poll_error = command_error;
  } else {
    poll_error = "";
  }

  bool state_changed = false;
  if (ok) {
    if (control_result.has_carousel_paused) {
      snapshot.carousel_paused = control_result.carousel_paused;
    }
    if (control_result.music_state.length()) {
      snapshot.music_state = control_result.music_state;
      snapshot.music_title = control_result.music_title;
      snapshot.music_artist = control_result.music_artist;
    }

    String refresh_error;
    RemoteViewSnapshot refreshed = snapshot;
    if (remote_view.fetch(config, refreshed, refresh_error)) {
      snapshot = refreshed;
      has_snapshot = true;
      poll_error = "";
      state_changed = action == TouchAction::kCarouselPause
          ? snapshot.carousel_paused != before.carousel_paused
          : action == TouchAction::kCarouselNext
              ? (snapshot.scene_kind != before.scene_kind
                 || snapshot.scene_title != before.scene_title
                 || snapshot.scene_text != before.scene_text)
              : action == TouchAction::kMusicToggle
                  ? snapshot.music_state != before.music_state
                  : action == TouchAction::kMusicStop
                      ? snapshot.music_state != before.music_state
                      : snapshot.music_title != before.music_title
                        || snapshot.music_artist != before.music_artist;
    } else {
      poll_error = "";
    }
  }

  if (state_changed) {
    ui.invalidate();
  }
  const bool success = ok;
  ui.showActionResult(
      action,
      success,
      snapshot,
      has_snapshot && !poll_error.length() && WiFi.isConnected());
  ui.render(snapshot, poll_error, has_snapshot && WiFi.isConnected());
  heartbeat_at = 0;
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(150);
  Serial.printf("\nWeb Display CYD %s\n", CYD_VERSION);
  const bool enrolled = config_store.load(config);
  ui.setPlaybackControls(config.playback_controls);
  display.begin(config.orientation);
  pinMode(0, INPUT_PULLUP);
  if (ota.pending()) {
    ota_pending_started_at = millis();
  }

  checkFactoryReset();
  WiFi.persistent(false);
  if (enrolled) {
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
  handleBootButton();

#ifdef CYD_OTA_TEST_COMMAND
  const int ota_test_command = Serial.available() ? Serial.read() : -1;
  if (ota_test_command == 'O') {
    String ota_error;
    CydOtaStatus ota_status = ota.check(config, CYD_VERSION, ota_error);
    if (!ota_status.available) {
      Serial.printf("OTA test: no update: %s\n", ota_error.c_str());
      return;
    }
    Serial.printf("OTA test: installing %s\n", ota_status.latest.c_str());
    if (!ota.install(config, ota_status, ota_error)) {
      Serial.printf("OTA test failed: %s\n", ota_error.c_str());
    }
  }
  if (ota_test_command == 'R') {
    String ota_error;
    Serial.println("OTA test: forcing rollback");
    ota.rollbackAfterTimeout(0, ota_error);
    if (ota_error.length()) {
      Serial.println(ota_error);
    }
  }
#endif

  if (millis() - last_diagnostic_at >= 10000) {
    last_diagnostic_at = millis();
    Serial.printf(
        "CYD diag version=%s state=%u wifi=%u heap=%lu largest=%lu"
        " snapshot=%u scene=%s remote_error=%s ota_pending=%u ota_auto=%u"
        " controls=%u\n",
        CYD_VERSION,
        static_cast<unsigned>(state),
        WiFi.isConnected() ? 1u : 0u,
        ESP.getFreeHeap(),
        ESP.getMaxAllocHeap(),
        has_snapshot ? 1u : 0u,
        snapshot.scene_kind.c_str(),
        poll_error.c_str(),
        ota.pending() ? 1u : 0u,
        config.ota_auto ? 1u : 0u,
        config.playback_controls ? 1u : 0u);
  }

  if (state == AppState::kUnconfigured) {
    provisioning.loop();
    if (provisioning.enrolled(config)) {
      ui.setPlaybackControls(config.playback_controls);
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
      configTime(0, 0, "pool.ntp.org", "time.google.com");
    } else {
      display.showStatus("Wi-Fi offline", "Retrying saved network", false);
      reconnect_at = millis() + 15000;
    }
  } else if (millis() >= heartbeat_at) {
    heartbeat_at = millis() + 5000;
    if (pollPi()) {
      if (ota.pending()
#ifdef CYD_OTA_TEST_SKIP_CONFIRM
          && false
#endif
      ) {
        String ota_error;
        ota.confirmAfterHealth(ota_error);
        if (ota_error.length()) {
          Serial.println(ota_error);
        }
      }
      if (config.ota_auto && !ota_checked) {
        ota_checked = true;
        String ota_error;
        CydOtaStatus ota_status = ota.check(config, CYD_VERSION, ota_error);
        if (ota_status.available) {
          ui.render(snapshot, "OTA update available - installing", WiFi.isConnected());
          if (!ota.install(config, ota_status, ota_error)) {
            Serial.printf("CYD OTA failed: %s\n", ota_error.c_str());
          }
        } else if (ota_error.length()) {
          Serial.printf("CYD OTA check failed: %s\n", ota_error.c_str());
        }
      }
    } else if (ota.pending() && ota_pending_started_at == 0) {
      ota_pending_started_at = millis();
    }
    if (ota.pending() && ota_pending_started_at != 0
        && millis() - ota_pending_started_at >= 60000) {
      String ota_error;
      ota.rollbackAfterTimeout(millis() - ota_pending_started_at, ota_error);
    }
    if (!WiFi.isConnected()) {
      state = AppState::kConnecting;
      reconnect_at = 0;
    }
  } else {
    handleTouch();
  }
  delay(10);
}

