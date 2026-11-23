#include "CydOta.h"

#include <ArduinoJson.h>
#include <Preferences.h>
#include <Update.h>
#include <esp_ota_ops.h>
#include <esp_heap_caps.h>
#include <mbedtls/sha256.h>
#include <sodium.h>

#include "WebDisplayApi.h"

namespace {

constexpr const char* kPreferencesNamespace = "wd-cyd";
constexpr const char* kPendingVersion = "ota_pending";
constexpr const char* kPendingAt = "ota_pending_at";
constexpr const char* kPreviousSubtype = "ota_previous";
constexpr std::size_t kMaxManifestBytes = 64 * 1024;
constexpr std::size_t kMaxFirmwareBytes = 2 * 1024 * 1024;

// Raw 32-byte Ed25519 public key for the existing Web Display update signer.
const std::uint8_t kCydPublicKey[32] = {
#ifdef CYD_OTA_TEST_PUBLIC_KEY_0
    CYD_OTA_TEST_PUBLIC_KEY_0,
    CYD_OTA_TEST_PUBLIC_KEY_1,
    CYD_OTA_TEST_PUBLIC_KEY_2,
    CYD_OTA_TEST_PUBLIC_KEY_3,
    CYD_OTA_TEST_PUBLIC_KEY_4,
    CYD_OTA_TEST_PUBLIC_KEY_5,
    CYD_OTA_TEST_PUBLIC_KEY_6,
    CYD_OTA_TEST_PUBLIC_KEY_7,
    CYD_OTA_TEST_PUBLIC_KEY_8,
    CYD_OTA_TEST_PUBLIC_KEY_9,
    CYD_OTA_TEST_PUBLIC_KEY_10,
    CYD_OTA_TEST_PUBLIC_KEY_11,
    CYD_OTA_TEST_PUBLIC_KEY_12,
    CYD_OTA_TEST_PUBLIC_KEY_13,
    CYD_OTA_TEST_PUBLIC_KEY_14,
    CYD_OTA_TEST_PUBLIC_KEY_15,
    CYD_OTA_TEST_PUBLIC_KEY_16,
    CYD_OTA_TEST_PUBLIC_KEY_17,
    CYD_OTA_TEST_PUBLIC_KEY_18,
    CYD_OTA_TEST_PUBLIC_KEY_19,
    CYD_OTA_TEST_PUBLIC_KEY_20,
    CYD_OTA_TEST_PUBLIC_KEY_21,
    CYD_OTA_TEST_PUBLIC_KEY_22,
    CYD_OTA_TEST_PUBLIC_KEY_23,
    CYD_OTA_TEST_PUBLIC_KEY_24,
    CYD_OTA_TEST_PUBLIC_KEY_25,
    CYD_OTA_TEST_PUBLIC_KEY_26,
    CYD_OTA_TEST_PUBLIC_KEY_27,
    CYD_OTA_TEST_PUBLIC_KEY_28,
    CYD_OTA_TEST_PUBLIC_KEY_29,
    CYD_OTA_TEST_PUBLIC_KEY_30,
    CYD_OTA_TEST_PUBLIC_KEY_31,
#else
    0xd5, 0x3b, 0x1a, 0x71, 0x16, 0xef, 0xe3, 0x0c,
    0x91, 0xab, 0xf1, 0x98, 0x2e, 0x50, 0x4a, 0x0c,
    0x2e, 0x4c, 0x4a, 0x0c, 0xfc, 0xc3, 0x29, 0xc3,
    0xcf, 0x2c, 0xdb, 0x5d, 0xa4, 0xa9, 0x1a, 0x64,
#endif
};

std::size_t base64Value(char value) {
  if (value >= 'A' && value <= 'Z') return value - 'A';
  if (value >= 'a' && value <= 'z') return value - 'a' + 26;
  if (value >= '0' && value <= '9') return value - '0' + 52;
  if (value == '+') return 62;
  if (value == '/') return 63;
  return 64;
}

std::size_t base64Decode(const String& input, std::uint8_t* output, std::size_t output_size) {
  std::size_t output_count = 0;
  std::size_t quartet = 0;
  std::uint32_t buffer = 0;
  for (std::size_t index = 0; index < input.length(); ++index) {
    const char value = input[index];
    if (value == '=') break;
    const std::size_t bits = base64Value(value);
    if (bits == 64) continue;
    buffer = (buffer << 6) | bits;
    if (++quartet == 4) {
      if (output_count < output_size) output[output_count++] = (buffer >> 16) & 0xff;
      if (output_count < output_size) output[output_count++] = (buffer >> 8) & 0xff;
      if (output_count < output_size) output[output_count++] = buffer & 0xff;
      quartet = 0;
      buffer = 0;
    }
  }
  if (quartet == 2 && output_count < output_size) {
    output[output_count++] = (buffer >> 4) & 0xff;
  } else if (quartet == 3 && output_count < output_size) {
    output[output_count++] = (buffer >> 10) & 0xff;
  }
  return output_count;
}

String sha256Hex(const std::uint8_t digest[32]) {
  static const char hex[] = "0123456789abcdef";
  String output;
  output.reserve(64);
  for (std::size_t index = 0; index < 32; ++index) {
    output += hex[digest[index] >> 4];
    output += hex[digest[index] & 0x0f];
  }
  return output;
}

void clearPendingOta() {
  Preferences preferences;
  preferences.begin(kPreferencesNamespace, false);
  preferences.remove(kPendingVersion);
  preferences.remove(kPendingAt);
  preferences.remove(kPreviousSubtype);
  preferences.end();
}

}  // namespace

CydOtaStatus CydOta::check(
    const CompanionConfig& config,
    const String& current_version,
    String& error) {
  CydOtaStatus status;
  status.current = current_version;
  status.checking = true;
  WebDisplayApi api;
  String path = "/api/cyd/firmware/status?version=";
  path += current_version;
  const ApiResponse response = api.request(config, "GET", path);
  if (response.status != 200) {
    error = response.error.length() ? response.error : "Could not check CYD firmware";
    status.checking = false;
    return status;
  }
  JsonDocument document;
  if (deserializeJson(document, response.body)) {
    error = "Invalid CYD firmware status";
    status.checking = false;
    return status;
  }
  status.available = document["available"] | false;
  status.latest = document["latest"]["version"] | "";
  status.size = document["latest"]["size"] | 0;
  status.checking = false;
  return status;
}

bool versionGreater(const String& candidate, const String& current) {
  unsigned int candidate_major = 0;
  unsigned int candidate_minor = 0;
  unsigned int candidate_patch = 0;
  unsigned int current_major = 0;
  unsigned int current_minor = 0;
  unsigned int current_patch = 0;
  if (sscanf(candidate.c_str(), "%u.%u.%u",
             &candidate_major, &candidate_minor, &candidate_patch) != 3
      || sscanf(current.c_str(), "%u.%u.%u",
                &current_major, &current_minor, &current_patch) != 3) {
    return false;
  }
  return candidate_major * 1000000u + candidate_minor * 1000u + candidate_patch
      > current_major * 1000000u + current_minor * 1000u + current_patch;
}

bool CydOta::pending() const {
  Preferences preferences;
  preferences.begin(kPreferencesNamespace, true);
  const String version = preferences.getString(kPendingVersion, "");
  preferences.end();
  return version.length() > 0;
}

bool CydOta::confirmAfterHealth(String& error) {
  Preferences preferences;
  preferences.begin(kPreferencesNamespace, false);
  const String version = preferences.getString(kPendingVersion, "");
  if (!version.length()) {
    preferences.end();
    return true;
  }
  const esp_err_t result = esp_ota_mark_app_valid_cancel_rollback();
  preferences.end();
  clearPendingOta();
  if (result != ESP_OK) {
    error = "Could not confirm CYD OTA";
    return false;
  }
  return true;
}

bool CydOta::rollbackAfterTimeout(std::uint32_t pending_millis, String& error) {
  (void)pending_millis;
  Preferences preferences;
  preferences.begin(kPreferencesNamespace, true);
  const int previous_subtype = preferences.getInt(kPreviousSubtype, -1);
  preferences.end();
  if (previous_subtype < ESP_PARTITION_SUBTYPE_APP_OTA_MIN
      || previous_subtype > ESP_PARTITION_SUBTYPE_APP_OTA_MAX) {
    error = "Could not identify previous CYD OTA partition";
    return false;
  }
  const esp_partition_t* previous = esp_partition_find_first(
      ESP_PARTITION_TYPE_APP,
      static_cast<esp_partition_subtype_t>(previous_subtype),
      nullptr);
  if (!previous) {
    error = "Previous CYD OTA partition is missing";
    return false;
  }
  const esp_err_t result = esp_ota_set_boot_partition(previous);
  if (result != ESP_OK) {
    error = "Could not select previous CYD OTA partition";
    return false;
  }
  esp_restart();
  return true;
}

bool CydOta::install(
    const CompanionConfig& config,
    const CydOtaStatus& status,
    String& error) {
  if (!status.available || !status.latest.length() || status.size == 0
      || status.size > kMaxFirmwareBytes
      || !versionGreater(status.latest, status.current)) {
    error = "CYD firmware update is unavailable";
    return false;
  }

  if (sodium_init() < 0) {
    error = "Could not initialize CYD OTA signature verification";
    return false;
  }

  WebDisplayApi api;
  const ApiResponse manifest_response =
      api.request(config, "GET", "/api/cyd/firmware/manifest");
  if (manifest_response.status != 200) {
    error = manifest_response.error.length()
        ? manifest_response.error : "Could not download CYD OTA manifest";
    return false;
  }
  JsonDocument envelope;
  if (deserializeJson(envelope, manifest_response.body)) {
    error = "Invalid CYD OTA manifest response";
    return false;
  }
  const String manifest_b64 = envelope["manifest"] | "";
  const String signature_b64 = envelope["signature"] | "";
  if (!manifest_b64.length() || !signature_b64.length()) {
    error = "CYD OTA manifest or signature is missing";
    return false;
  }

  std::uint8_t* manifest = static_cast<std::uint8_t*>(
      heap_caps_malloc(kMaxManifestBytes, MALLOC_CAP_8BIT));
  if (!manifest) {
    error = "Not enough memory to verify CYD OTA manifest";
    return false;
  }
  std::size_t manifest_size = base64Decode(
      manifest_b64, manifest, kMaxManifestBytes);
  std::uint8_t signature[64];
  const std::size_t signature_size = base64Decode(
      signature_b64, signature, sizeof(signature));
  if (manifest_size == 0 || manifest_size > kMaxManifestBytes
      || signature_size != sizeof(signature)) {
    heap_caps_free(manifest);
    error = "CYD OTA signature data is invalid";
    return false;
  }
  if (crypto_sign_ed25519_verify_detached(
          signature, manifest, manifest_size, kCydPublicKey) != 0) {
    heap_caps_free(manifest);
    error = "CYD OTA signature verification failed";
    return false;
  }

  JsonDocument manifest_document;
  if (deserializeJson(manifest_document, manifest, manifest_size)
      || manifest_document["format"] != 1
      || manifest_document["product"] != "web-display-cyd"
      || manifest_document["board"] != "esp32dev"
      || manifest_document["partition_layout"] != "min_spiffs") {
    heap_caps_free(manifest);
    error = "CYD OTA manifest is invalid";
    return false;
  }
  const String version = manifest_document["version"] | "";
  const String payload = manifest_document["payload"] | "";
  const String digest = manifest_document["sha256"] | "";
  const std::size_t size = manifest_document["size"] | 0;
  heap_caps_free(manifest);
  if (version != status.latest
      || size == 0
      || size > kMaxFirmwareBytes
      || size != status.size
      || payload != "web-display-cyd-" + version + ".bin"
      || digest.length() != 64) {
    error = "CYD OTA manifest does not match the available firmware";
    return false;
  }

  Preferences preferences;
  const esp_partition_t* running = esp_ota_get_running_partition();
  if (!running || running->type != ESP_PARTITION_TYPE_APP
      || running->subtype < ESP_PARTITION_SUBTYPE_APP_OTA_MIN
      || running->subtype > ESP_PARTITION_SUBTYPE_APP_OTA_MAX) {
    error = "Could not identify running CYD OTA partition";
    return false;
  }
  preferences.begin(kPreferencesNamespace, false);
  preferences.putString(kPendingVersion, version);
  preferences.putULong(kPendingAt, millis());
  preferences.putInt(kPreviousSubtype, running->subtype);
  preferences.end();

  if (!Update.begin(size, U_FLASH)) {
    clearPendingOta();
    error = "Could not start CYD OTA partition write";
    return false;
  }
  mbedtls_sha256_context sha;
  mbedtls_sha256_init(&sha);
  mbedtls_sha256_starts(&sha, 0);
  std::size_t received = 0;

  String path = "/api/cyd/firmware/download?version=";
  path += version;
  path += "&sha256=";
  path += digest;
  const bool downloaded = api.download(
      config, path, size,
      [&](const std::uint8_t* data, std::size_t count) {
        if (received + count > size) return false;
        mbedtls_sha256_update(&sha, data, count);
        if (Update.write(const_cast<std::uint8_t*>(data), count) != count) {
          return false;
        }
        received += count;
        if (received % (256 * 1024) < count) {
          Serial.printf(
              "CYD OTA progress %lu/%lu\n",
              static_cast<unsigned long>(received),
              static_cast<unsigned long>(size));
        }
        delay(1);
        return true;
      },
      error);
  if (!downloaded) {
    Update.abort();
    mbedtls_sha256_free(&sha);
    clearPendingOta();
    return false;
  }

  std::uint8_t actual_digest[32];
  mbedtls_sha256_finish(&sha, actual_digest);
  mbedtls_sha256_free(&sha);
  if (sha256Hex(actual_digest) != digest) {
    Update.abort();
    clearPendingOta();
    error = "CYD OTA SHA-256 mismatch";
    return false;
  }
  if (!Update.end(true)) {
    clearPendingOta();
    error = "Could not finalize CYD OTA";
    return false;
  }
  esp_restart();
  return true;
}
