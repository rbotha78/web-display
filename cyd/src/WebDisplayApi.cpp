#include "WebDisplayApi.h"

#include <NetworkClientSecure.h>
#include <NetworkClient.h>
#include <ESPmDNS.h>
#include <WiFi.h>

#include <cctype>

namespace {

constexpr std::uint32_t kConnectTimeoutMs = 10000;
constexpr std::uint32_t kReadTimeoutMs = 15000;
constexpr std::size_t kMaxResponseBytes = 96 * 1024;

String fingerprintText(const std::uint8_t digest[32]) {
  static constexpr char hex[] = "0123456789ABCDEF";
  String result;
  result.reserve(95);
  for (std::size_t index = 0; index < 32; ++index) {
    if (index) {
      result += ':';
    }
    result += hex[digest[index] >> 4];
    result += hex[digest[index] & 0x0F];
  }
  return result;
}

String headerValue(const String& headers, const char* name) {
  const String lower = String(name);
  int start = 0;
  while (start < headers.length()) {
    const int end = headers.indexOf('\n', start);
    const String line = headers.substring(start, end >= 0 ? end : headers.length());
    const int colon = line.indexOf(':');
    if (colon > 0 && line.substring(0, colon).equalsIgnoreCase(lower)) {
      String value = line.substring(colon + 1);
      value.trim();
      return value;
    }
    if (end < 0) {
      break;
    }
    start = end + 1;
  }
  return "";
}

String cookieOnly(const String& value) {
  const int semicolon = value.indexOf(';');
  return semicolon >= 0 ? value.substring(0, semicolon) : value;
}

bool contentComplete(
    const String& response, std::size_t& body_length, std::size_t header_length) {
  const String header = response.substring(0, header_length);
  const String length = headerValue(header, "Content-Length");
  if (!length.length()) {
    return false;
  }
  body_length = static_cast<std::size_t>(length.toInt());
  return response.length() - header_length >= body_length;
}

ApiResponse parseResponse(const String& raw) {
  ApiResponse response;
  const int status_end = raw.indexOf("\r\n");
  if (status_end < 0 || !raw.startsWith("HTTP/")) {
    response.error = "Invalid response from Pi";
    return response;
  }
  const String status_line = raw.substring(0, status_end);
  const int first_space = status_line.indexOf(' ');
  const int second_space = status_line.indexOf(' ', first_space + 1);
  response.status = status_line.substring(
      first_space + 1, second_space > 0 ? second_space : status_line.length()).toInt();

  const int header_end = raw.indexOf("\r\n\r\n");
  if (header_end < 0) {
    response.error = "Incomplete response from Pi";
    return response;
  }
  const String headers = raw.substring(status_end + 2, header_end);
  response.cookie = cookieOnly(headerValue(headers, "Set-Cookie"));
  std::size_t body_length = 0;
  contentComplete(raw, body_length, static_cast<std::size_t>(header_end + 4));
  response.body = raw.substring(header_end + 4, header_end + 4 + body_length);
  if (!body_length && raw.length() > header_end + 4) {
    response.body = raw.substring(header_end + 4);
  }
  return response;
}

}  // namespace

ProbeResult WebDisplayApi::probe(
    const String& host, std::uint16_t port, bool use_https) const {
  ProbeResult result;
  String actual_fingerprint;
  const ApiResponse response = exchange(
      host, port, use_https, "", true, "GET", "/health", "", "", "", "",
      &actual_fingerprint);
  result.fingerprint = actual_fingerprint;
  if (response.status != 200) {
    result.error = response.error.length()
        ? response.error : "Pi health request failed";
    return result;
  }

  JsonDocument document;
  const DeserializationError error = deserializeJson(document, response.body);
  if (error) {
    result.error = String("Invalid Pi health response: ") + error.c_str();
    return result;
  }
  result.ok = document["status"] == "ok";
  result.version = document["version"] | "";
  if (!result.ok) {
    result.error = "Pi reports unhealthy status";
  }
  return result;
}

ApiResponse WebDisplayApi::request(
    const CompanionConfig& config,
    const char* method,
    const String& path,
    const String& body,
    const String& cookie,
    const String& csrf) const {
  return exchange(
      config.pi_host, config.pi_port, config.use_https, config.fingerprint, false, method, path,
      body, config.api_key, cookie, csrf);
}

ApiResponse WebDisplayApi::pair(
    const String& host,
    std::uint16_t port,
    bool use_https,
    const String& fingerprint,
    const char* endpoint,
    const String& credential) const {
  return exchange(
      host, port, use_https, fingerprint, false, "POST", endpoint, credential,
      "", "", "");
}

bool WebDisplayApi::mintApiKey(
    const String& host,
    std::uint16_t port,
    bool use_https,
    const String& fingerprint,
    const String& cookie,
    const String& csrf,
    String& api_key,
    String& error) const {
  const ApiResponse response = exchange(
      host, port, use_https, fingerprint, false, "POST", "/api/keys",
      "{\"name\":\"ESP32 CYD\"}", "", cookie, csrf);
  if (response.status != 201) {
    error = response.error.length() ? response.error : "Could not create CYD API key";
    return false;
  }
  JsonDocument document;
  const DeserializationError json_error = deserializeJson(document, response.body);
  if (json_error) {
    error = "Invalid key response from Pi";
    return false;
  }
  api_key = document["key"] | "";
  if (!api_key.length()) {
    error = "Pi did not return an API key";
    return false;
  }
  return true;
}

bool WebDisplayApi::download(
    const CompanionConfig& config,
    const String& path,
    std::size_t expected_size,
    const std::function<bool(const std::uint8_t*, std::size_t)>& sink,
    String& error) const {
  if (!WiFi.isConnected()) {
    error = "Wi-Fi is not connected";
    return false;
  }
  if (!config.pi_host.length() || config.pi_port == 0) {
    error = "Pi address is missing";
    return false;
  }
  if (!config.use_https) {
    error = "OTA requires HTTPS";
    return false;
  }

  IPAddress target_address;
  bool resolved = WiFi.hostByName(config.pi_host.c_str(), target_address);
  resolved = resolved && static_cast<std::uint32_t>(target_address) != 0;
  if (!resolved && config.pi_host.endsWith(".local")) {
    const String mdns_name = config.pi_host.substring(0, config.pi_host.length() - 6);
    MDNS.begin("web-display-cyd");
    target_address = MDNS.queryHost(mdns_name, 2500);
    resolved = static_cast<std::uint32_t>(target_address) != 0;
  }

  NetworkClientSecure client;
  client.setInsecure();
  client.setTimeout(kReadTimeoutMs);
  client.setHandshakeTimeout(kReadTimeoutMs);
  const bool connected = resolved
      ? client.connect(target_address, config.pi_port, kConnectTimeoutMs)
      : client.connect(config.pi_host.c_str(), config.pi_port, kConnectTimeoutMs);
  if (!connected) {
    char tls_error[96] = {};
    client.lastError(tls_error, sizeof(tls_error));
    error = tls_error[0] ? String(tls_error) : "Could not connect to Pi";
    return false;
  }

  std::uint8_t digest[32] = {};
  if (!client.getFingerprintSHA256(digest)) {
    error = "Could not read Pi certificate";
    client.stop();
    return false;
  }
  if (!config.fingerprint.equalsIgnoreCase(fingerprintText(digest))) {
    error = "Pi certificate changed; remove and re-enroll this CYD";
    client.stop();
    return false;
  }

  String request;
  request.reserve(512);
  request += "GET ";
  request += path;
  request += " HTTP/1.1\r\nHost: ";
  request += config.pi_host;
  request += "\r\nConnection: close\r\nAccept: application/octet-stream\r\nAuthorization: Bearer ";
  request += config.api_key;
  request += "\r\n\r\n";
  if (client.write(
          reinterpret_cast<const std::uint8_t*>(request.c_str()), request.length())
      != request.length()) {
    error = "OTA request was interrupted";
    client.stop();
    return false;
  }

  String headers;
  headers.reserve(512);
  bool header_complete = false;
  int status = 0;
  std::size_t content_length = 0;
  std::size_t received = 0;
  const std::uint32_t started = millis();
  while (millis() - started < kReadTimeoutMs * 4) {
    while (client.available()) {
      const int value = client.read();
      if (value < 0) {
        break;
      }
      if (!header_complete) {
        headers += static_cast<char>(value);
        const int header_end = headers.indexOf("\r\n\r\n");
        if (header_end >= 0) {
          const String status_line = headers.substring(0, headers.indexOf('\r'));
          const int first = status_line.indexOf(' ');
          const int second = status_line.indexOf(' ', first + 1);
          status = status_line.substring(first + 1, second > 0 ? second : status_line.length()).toInt();
          if (status != 200) {
            error = "Pi firmware download was rejected";
            client.stop();
            return false;
          }
          const String length = headerValue(headers, "Content-Length");
          content_length = length.length() ? static_cast<std::size_t>(length.toInt()) : 0;
          if (content_length != expected_size) {
            error = "CYD firmware size mismatch";
            client.stop();
            return false;
          }
          header_complete = true;
          break;
        }
      }
    }
    if (header_complete) {
      break;
    }
    delay(1);
  }
  if (!header_complete) {
    error = "Pi firmware download did not start";
    client.stop();
    return false;
  }

  while (received < content_length && millis() - started < kReadTimeoutMs * 20) {
    if (!client.available()) {
      delay(1);
      continue;
    }
    std::uint8_t buffer[4096];
    const std::size_t wanted = content_length - received;
    const std::size_t count = client.read(buffer, wanted < sizeof(buffer) ? wanted : sizeof(buffer));
    if (!count) {
      break;
    }
    received += count;
    if (!sink(buffer, count)) {
      error = "CYD firmware write failed";
      client.stop();
      return false;
    }
  }
  client.stop();
  if (received != expected_size) {
    error = "CYD firmware download ended early";
    return false;
  }
  return true;
}

ApiResponse WebDisplayApi::exchange(
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
    String* actual_fingerprint) const {
  ApiResponse response;
  if (!WiFi.isConnected()) {
    response.error = "Wi-Fi is not connected";
    return response;
  }
  if (host.length() == 0 || port == 0) {
    response.error = "Pi address is missing";
    return response;
  }

  IPAddress target_address;
  bool resolved = WiFi.hostByName(host.c_str(), target_address);
  resolved = resolved && static_cast<std::uint32_t>(target_address) != 0;
  if (!resolved && host.endsWith(".local")) {
    const String mdns_name = host.substring(0, host.length() - 6);
    MDNS.begin("web-display-cyd");
    target_address = MDNS.queryHost(mdns_name, 2500);
    resolved = static_cast<std::uint32_t>(target_address) != 0;
    if (!resolved) {
      const int services = MDNS.queryService("web-display", "tcp");
      for (int index = 0; index < services && !resolved; ++index) {
        String discovered_host = MDNS.hostname(index);
        discovered_host.toLowerCase();
        if (discovered_host.endsWith(".local")) {
          discovered_host.remove(discovered_host.length() - 6);
        }
        if (discovered_host == mdns_name) {
          target_address = MDNS.address(index);
          resolved = static_cast<std::uint32_t>(target_address) != 0;
        }
      }
    }
  }
  NetworkClientSecure tls_client;
  NetworkClient plain_client;
  NetworkClient* client = &plain_client;
  if (use_https) {
#ifdef CYD_ALLOW_INSECURE_HTTP
    (void)plain_client;
#endif
    tls_client.setInsecure();
    tls_client.setTimeout(kReadTimeoutMs);
    tls_client.setHandshakeTimeout(kReadTimeoutMs);
    client = &tls_client;
    const bool connected = resolved
        ? tls_client.connect(target_address, port, kConnectTimeoutMs)
        : tls_client.connect(host.c_str(), port, kConnectTimeoutMs);
    if (!connected) {
      char tls_error[96] = {};
      tls_client.lastError(tls_error, sizeof(tls_error));
      Serial.printf(
          "TLS target %s resolved=%s address=%s heap=%lu largest=%lu; ",
          host.c_str(), resolved ? "yes" : "no",
          resolved ? target_address.toString().c_str() : "none",
          ESP.getFreeHeap(), ESP.getMaxAllocHeap());
      response.error = tls_error[0] ? String(tls_error) : "Could not connect to Pi";
      Serial.println(response.error);
      return response;
    }
    std::uint8_t digest[32] = {};
    if (!tls_client.getFingerprintSHA256(digest)) {
      response.error = "Could not read Pi certificate";
      tls_client.stop();
      return response;
    }
    const String observed = fingerprintText(digest);
    if (actual_fingerprint) {
      *actual_fingerprint = observed;
    }
    if (!allow_unpinned && !fingerprint.equalsIgnoreCase(observed)) {
      response.error = "Pi certificate changed; remove and re-enroll this CYD";
      tls_client.stop();
      return response;
    }
  } else {
#ifndef CYD_ALLOW_INSECURE_HTTP
    response.error = "HTTP development mode is disabled";
    return response;
#else
    plain_client.setTimeout(kReadTimeoutMs);
    const bool connected = resolved
        ? plain_client.connect(target_address, port, kConnectTimeoutMs)
        : plain_client.connect(host.c_str(), port, kConnectTimeoutMs);
    if (!connected) {
      response.error = "Could not connect to development Pi";
      return response;
    }
#endif
  }

  String request;
  request.reserve(512 + body.length());
  request += method;
  request += ' ';
  request += path;
  request += " HTTP/1.1\r\nHost: ";
  request += host;
  request += "\r\nConnection: close\r\nAccept: application/json\r\n";
  if (authorization.length()) {
    request += "Authorization: Bearer ";
    request += authorization;
    request += "\r\n";
  }
  if (cookie.length()) {
    request += "Cookie: ";
    request += cookie;
    request += "\r\n";
  }
  if (csrf.length()) {
    request += "X-CSRF-Token: ";
    request += csrf;
    request += "\r\n";
  }
  if (body.length()) {
    request += "Content-Type: application/json\r\nContent-Length: ";
    request += body.length();
    request += "\r\n";
  }
  request += "\r\n";
  request += body;

  const std::size_t written = client->write(
      reinterpret_cast<const std::uint8_t*>(request.c_str()), request.length());
  if (written != request.length()) {
    response.error = "Pi request was interrupted";
    client->stop();
    return response;
  }

  String raw;
  raw.reserve(4096);
  const std::uint32_t started = millis();
  while ((client->connected() || client->available()) && millis() - started < kReadTimeoutMs) {
    while (client->available()) {
      const int value = client->read();
      if (value < 0) {
        break;
      }
      raw += static_cast<char>(value);
      if (raw.length() > kMaxResponseBytes) {
        response.error = "Pi response is too large";
        client->stop();
        return response;
      }
      const int header_end = raw.indexOf("\r\n\r\n");
      if (header_end > 0) {
        std::size_t body_length = 0;
        if (contentComplete(raw, body_length, header_end + 4)) {
          client->stop();
          return parseResponse(raw);
        }
      }
    }
    delay(1);
  }
  client->stop();
  if (raw.length()) {
    return parseResponse(raw);
  }
  response.error = "Pi did not respond";
  return response;
}

