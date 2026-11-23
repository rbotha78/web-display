#pragma once

#include <Arduino.h>
#include <DNSServer.h>
#include <WebServer.h>

#include "BoardDisplay.h"
#include "CompanionConfig.h"
#include "WebDisplayApi.h"

class Provisioning {
 public:
  Provisioning(BoardDisplay& display, CompanionConfigStore& config_store);
  void begin();
  void loop();
  bool enrolled(CompanionConfig& config);

 private:
  struct Pending {
    std::uint8_t orientation = 1;
    String wifi_ssid;
    String wifi_password;
    String pi_host;
    std::uint16_t pi_port = 8443;
    bool use_https = true;
    bool ota_auto = false;
    bool playback_controls = true;
    bool pi_paired = false;
    String fingerprint;
  };

  bool connectStation(const Pending& pending);
  void handleRoot();
  void handleProbe();
  void handleEnroll();
  void handleNotFound();
  void respond(const String& title, const String& body);
  String escapeHtml(const String& input) const;
  String hiddenPendingFields() const;
  String setupPassword() const;
  String hostForLog() const;

  BoardDisplay& display_;
  CompanionConfigStore& config_store_;
  DNSServer dns_;
  WebServer server_{80};
  Pending pending_;
  CompanionConfig enrolled_;
  bool complete_ = false;
  std::uint32_t last_display_ms_ = 0;
};

