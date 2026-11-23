#include "CompanionConfig.h"

#include <Preferences.h>

namespace {
constexpr const char* kNamespace = "wd-cyd";
}

bool CompanionConfigStore::load(CompanionConfig& config) const {
  Preferences preferences;
  if (!preferences.begin(kNamespace, false)) {
    return false;
  }
  const bool migrate_orientation =
      preferences.getUChar("orientation_version", 0) < 1;
  config.orientation = migrate_orientation
      ? 1 : preferences.getUChar("orientation", 1);
  if (migrate_orientation) {
    preferences.putUChar("orientation", config.orientation);
    preferences.putUChar("orientation_version", 1);
  }
  config.wifi_ssid = preferences.getString("wifi_ssid", "");
  config.wifi_password = preferences.getString("wifi_pass", "");
  config.pi_host = preferences.getString("pi_host", "");
  config.pi_port = preferences.getUShort("pi_port", 8443);
  config.fingerprint = preferences.getString("fingerprint", "");
  config.api_key = preferences.getString("api_key", "");
  config.use_https = preferences.getBool("https", true);
  config.ota_auto = preferences.getBool("ota_auto", false);
  preferences.end();
  return config.wifi_ssid.length() && config.pi_host.length()
      && (!config.use_https || config.fingerprint.length()) && config.api_key.length();
}

bool CompanionConfigStore::save(const CompanionConfig& config) const {
  if (!config.wifi_ssid.length() || !config.pi_host.length()
      || (config.use_https && !config.fingerprint.length()) || !config.api_key.length()) {
    return false;
  }
  Preferences preferences;
  if (!preferences.begin(kNamespace, false)) {
    return false;
  }
  bool ok = true;
  ok = preferences.putUChar("orientation", config.orientation) > 0 && ok;
  ok = preferences.putUChar("orientation_version", 1) > 0 && ok;
  ok = preferences.putString("wifi_ssid", config.wifi_ssid) > 0 && ok;
  ok = preferences.putString("wifi_pass", config.wifi_password) > 0 && ok;
  ok = preferences.putString("pi_host", config.pi_host) > 0 && ok;
  ok = preferences.putUShort("pi_port", config.pi_port) > 0 && ok;
  ok = preferences.putString("fingerprint", config.fingerprint) > 0 && ok;
  ok = preferences.putString("api_key", config.api_key) > 0 && ok;
  ok = preferences.putBool("https", config.use_https) > 0 && ok;
  ok = preferences.putBool("ota_auto", config.ota_auto) > 0 && ok;
  preferences.end();
  return ok;
}

bool CompanionConfigStore::saveOrientation(std::uint8_t orientation) const {
  Preferences preferences;
  if (!preferences.begin(kNamespace, false)) {
    return false;
  }
  const bool ok = preferences.putUChar("orientation", orientation) > 0;
  const bool version_ok = preferences.putUChar("orientation_version", 1) > 0;
  preferences.end();
  return ok && version_ok;
}

bool CompanionConfigStore::saveOtaAuto(bool enabled) const {
  Preferences preferences;
  if (!preferences.begin(kNamespace, false)) {
    return false;
  }
  const bool ok = preferences.putBool("ota_auto", enabled) > 0;
  preferences.end();
  return ok;
}

void CompanionConfigStore::clear() const {
  Preferences preferences;
  if (preferences.begin(kNamespace, false)) {
    preferences.remove("wifi_ssid");
    preferences.remove("wifi_pass");
    preferences.remove("pi_host");
    preferences.remove("pi_port");
    preferences.remove("fingerprint");
    preferences.remove("api_key");
    preferences.remove("https");
    preferences.remove("ota_auto");
    preferences.remove("orientation");
    preferences.remove("orientation_version");
    preferences.end();
  }
}

std::int32_t CompanionConfigStore::incrementBootCount() const {
  Preferences preferences;
  preferences.begin(kNamespace, false);
  const std::int32_t value = preferences.getLong("boots", 0) + 1;
  preferences.putLong("boots", value);
  preferences.end();
  return value;
}

String CompanionConfigStore::fingerprintHint(const CompanionConfig& config) const {
  return config.fingerprint.substring(0, 23);
}

