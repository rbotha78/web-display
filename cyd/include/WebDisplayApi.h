#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>
#include <cstdint>
#include <functional>

#include "CompanionConfig.h"

struct ApiResponse {
  int status = 0;
  String body;
  String cookie;
  String error;
};

struct ProbeResult {
  bool ok = false;
  bool paired = false;
  String fingerprint;
  String version;
  String error;
};

class WebDisplayApi {
 public:
  ProbeResult probe(const String& host, std::uint16_t port, bool use_https = true) const;
  ApiResponse request(
      const CompanionConfig& config,
      const char* method,
      const String& path,
      const String& body = "",
      const String& cookie = "",
      const String& csrf = "") const;
  ApiResponse pair(
      const String& host,
      std::uint16_t port,
      bool use_https,
      const String& fingerprint,
      const char* endpoint,
      const String& credential) const;
  bool mintApiKey(
      const String& host,
      std::uint16_t port,
      bool use_https,
      const String& fingerprint,
      const String& cookie,
      const String& csrf,
      String& api_key,
      String& error) const;
  bool download(
      const CompanionConfig& config,
      const String& path,
      std::size_t expected_size,
      const std::function<bool(const std::uint8_t*, std::size_t)>& sink,
      String& error) const;

 private:
  ApiResponse exchange(
      const String& host,
      std::uint16_t port,
      bool use_https,
      const String& fingerprint,
      bool allow_unpinned,
      const char* method,
      const String& path,
      const String& body,
      const String& authorization,
      const String& cookie,
      const String& csrf,
      String* actual_fingerprint = nullptr) const;
};

