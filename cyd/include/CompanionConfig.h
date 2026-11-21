#pragma once

#include <Arduino.h>
#include <cstdint>

struct CompanionConfig {
  std::uint8_t orientation = 1;
  String wifi_ssid;
  String wifi_password;
  String pi_host;
  std::uint16_t pi_port = 8443;
  String fingerprint;
  String api_key;
  bool use_https = true;
};

class CompanionConfigStore {
 public:
  bool load(CompanionConfig& config) const;
  bool save(const CompanionConfig& config) const;
  bool saveOrientation(std::uint8_t orientation) const;
  void clear() const;
  std::int32_t incrementBootCount() const;
  String fingerprintHint(const CompanionConfig& config) const;
};

