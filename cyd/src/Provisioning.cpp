#include "Provisioning.h"

#include <WiFi.h>

namespace {

constexpr std::uint32_t kWifiConnectTimeoutMs = 20000;

String messageFrom(const ApiResponse& response, const char* fallback) {
  if (response.error.length()) {
    return response.error;
  }
  JsonDocument document;
  if (!deserializeJson(document, response.body)
      && document["error"].is<const char*>()) {
    return document["error"] | fallback;
  }
  return fallback;
}

}  // namespace

Provisioning::Provisioning(
    BoardDisplay& display, CompanionConfigStore& config_store)
    : display_(display), config_store_(config_store) {}

void Provisioning::begin() {
  const std::uint64_t efuse = ESP.getEfuseMac();
  const String suffix = String(
      static_cast<std::uint32_t>(efuse & 0xFFFF), HEX);
  const String ssid = "WebDisplay-CYD-" + suffix;
  const String password = setupPassword();

  WiFi.persistent(false);
  WiFi.mode(WIFI_AP_STA);
  WiFi.softAP(ssid.c_str(), password.c_str());
  WiFi.setAutoReconnect(true);
  dns_.setTTL(60);
  dns_.start(53, "*", WiFi.softAPIP());

  server_.on("/", HTTP_GET, [this]() { handleRoot(); });
  server_.on("/probe", HTTP_POST, [this]() { handleProbe(); });
  server_.on("/enroll", HTTP_POST, [this]() { handleEnroll(); });
  server_.onNotFound([this]() { handleNotFound(); });
  server_.begin();
  display_.showSetup(
      ssid, password, "http://" + WiFi.softAPIP().toString());
  Serial.printf(
      "Provisioning AP ready: %s; setup page: http://%s\n",
      ssid.c_str(), WiFi.softAPIP().toString().c_str());
}

void Provisioning::loop() {
  if (complete_) {
    return;
  }
  dns_.processNextRequest();
  server_.handleClient();
}

bool Provisioning::enrolled(CompanionConfig& config) {
  if (!complete_) {
    return false;
  }
  config = enrolled_;
  dns_.stop();
  server_.stop();
  WiFi.softAPdisconnect(true);
  return true;
}

bool Provisioning::connectStation(const Pending& pending) {
  display_.showConnecting(pending.wifi_ssid);
  WiFi.disconnect(false, false);
  WiFi.begin(pending.wifi_ssid.c_str(), pending.wifi_password.c_str());
  const std::uint32_t started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < kWifiConnectTimeoutMs) {
    dns_.processNextRequest();
    delay(50);
  }
  if (!WiFi.isConnected()) {
    display_.showStatus("Wi-Fi failed", "Check SSID and password", false);
    return false;
  }
  Serial.printf(
      "Wi-Fi connected to %s at %s\n",
      pending.wifi_ssid.c_str(), WiFi.localIP().toString().c_str());
  return true;
}

void Provisioning::handleRoot() {
  String transport =
      "<input name='transport' type='hidden' value='https'>";
#ifdef CYD_ALLOW_INSECURE_HTTP
  transport =
      "<label>Transport<select name='transport'>"
      "<option value='https'>HTTPS pinned certificate</option>"
      "<option value='http'>HTTP development only</option></select></label>";
#endif
  String body =
      "<p>Connect this CYD to Wi-Fi and select the Pi.</p>"
      "<form method='post' action='/probe'>"
      "<label>Wi-Fi SSID<input name='ssid' required></label>"
      "<label>Wi-Fi password<input name='wifi_password' type='password'></label>"
      "<label>Display orientation<select name='orientation'>"
      "<option value='1'>USB on right (flipped)</option>"
      "<option value='3'>USB on left (original)</option>"
      "</select></label>"
      "<label><input name='ota_auto' type='checkbox' value='yes'>"
      " Automatically install verified CYD OTA firmware updates</label>"
      "<label>Pi hostname or IPv4 address<input name='host' "
      "placeholder='web-display-pi2.local' required></label>"
      "<label>HTTPS port<input name='port' type='number' min='1' max='65535' "
      "value='8443' required></label>";
  body += transport;
  body +=
      "<button type='submit'>Find Pi and certificate</button>"
      "</form>";
  respond("Web Display CYD setup", body);
}

void Provisioning::handleProbe() {
  pending_.orientation = server_.arg("orientation").toInt() == 3 ? 3 : 1;
  pending_.ota_auto = server_.arg("ota_auto") == "yes";
  display_.setOrientation(pending_.orientation);
  pending_.wifi_ssid = server_.arg("ssid");
  pending_.wifi_password = server_.arg("wifi_password");
  pending_.pi_host = server_.arg("host");
  pending_.pi_port = server_.arg("port").toInt();
  pending_.pi_host.trim();
  if (!pending_.wifi_ssid.length() || !pending_.pi_host.length()
      || !pending_.pi_port) {
    server_.send(400, "text/plain; charset=utf-8", "SSID, Pi address and port are required");
    return;
  }
  if (!connectStation(pending_)) {
    respond(
        "Wi-Fi connection failed",
        "<p>Check the SSID and password, then reload this page and try again.</p>");
    return;
  }

  WebDisplayApi api;
  pending_.use_https = server_.arg("transport") != "http";
  const ProbeResult probe = api.probe(
      pending_.pi_host, pending_.pi_port, pending_.use_https);
  if (!probe.ok) {
    respond(
        "Pi not found",
        "<p>" + escapeHtml(probe.error) + "</p><p>Confirm the hostname and port.</p>");
    return;
  }
  pending_.fingerprint = probe.fingerprint;
  display_.showPairing(hostForLog(), probe.fingerprint);

  String body = "<p>Pi version: ";
  body += escapeHtml(probe.version);
  body += "</p><p>Compare this SHA-256 certificate fingerprint with the Pi display:</p>";
  body += "<code>";
  body += escapeHtml(probe.fingerprint);
  body += "</code>";
  body += "<form method='post' action='/enroll'>";
  body += hiddenPendingFields();
  body += "<label><input name='confirmed' type='checkbox' value='yes' required>"
      " The fingerprints match</label>"
      "<label>Enrollment mode<select name='mode'>"
      "<option value='pair'>Pair an unconfigured Pi with its pairing code</option>"
      "<option value='login'>Log in to an already paired Pi</option>"
      "</select></label>"
      "<label>Pairing code (pair mode)<input name='pairing_code'></label>"
      "<label>New admin password (pair mode) or current admin password (login mode)"
      "<input name='admin_password' type='password' required></label>"
      "<button type='submit'>Enroll this CYD</button>"
      "</form>";
  respond("Confirm Pi certificate", body);
}

void Provisioning::handleEnroll() {
  if (server_.arg("confirmed") != "yes"
      || pending_.fingerprint != server_.arg("fingerprint")) {
    server_.send(400, "text/plain; charset=utf-8", "Certificate confirmation expired or did not match");
    return;
  }
  if (!WiFi.isConnected() && !connectStation(pending_)) {
    respond("Enrollment failed", "<p>Wi-Fi disconnected. Start setup again.</p>");
    return;
  }

  const String mode = server_.arg("mode");
  JsonDocument document;
  const char* endpoint = "/api/login";
  if (mode == "pair") {
    endpoint = "/api/pair";
    document["code"] = server_.arg("pairing_code");
  } else if (mode != "login") {
    server_.send(400, "text/plain; charset=utf-8", "Unknown enrollment mode");
    return;
  }
  document["password"] = server_.arg("admin_password");
  String login_payload;
  serializeJson(document, login_payload);

  WebDisplayApi api;
  display_.showStatus("Enrolling CYD", "Authenticating with Pi", true);
  const ApiResponse login = api.pair(
      pending_.pi_host, pending_.pi_port, pending_.use_https, pending_.fingerprint,
      endpoint, login_payload);
  if (login.status != 200 || !login.cookie.length()) {
    respond(
        "Enrollment failed",
        "<p>" + escapeHtml(messageFrom(login, "Pi rejected the supplied credentials"))
        + "</p><p>Go back and try again.</p>");
    return;
  }
  JsonDocument login_document;
  if (deserializeJson(login_document, login.body)) {
    respond("Enrollment failed", "<p>Pi returned an invalid session response.</p>");
    return;
  }
  const String csrf = login_document["csrf"] | "";
  String api_key;
  String key_error;
  if (!csrf.length() || !api.mintApiKey(
          pending_.pi_host, pending_.pi_port, pending_.use_https, pending_.fingerprint,
          login.cookie, csrf, api_key, key_error)) {
    respond(
        "Enrollment failed",
        "<p>" + escapeHtml(key_error.length() ? key_error : "Pi did not issue a session")
        + "</p>");
    return;
  }

  enrolled_.wifi_ssid = pending_.wifi_ssid;
  enrolled_.orientation = pending_.orientation;
  enrolled_.ota_auto = pending_.ota_auto;
  enrolled_.wifi_password = pending_.wifi_password;
  enrolled_.pi_host = pending_.pi_host;
  enrolled_.pi_port = pending_.pi_port;
  enrolled_.use_https = pending_.use_https;
  enrolled_.fingerprint = pending_.fingerprint;
  enrolled_.api_key = api_key;
  if (!config_store_.save(enrolled_)) {
    respond("Enrollment failed", "<p>Could not save CYD credentials in NVS.</p>");
    return;
  }
  pending_.wifi_password = "";
  pending_.fingerprint = "";
  complete_ = true;
  display_.showStatus("CYD enrolled", "Credentials saved locally", true);
  respond(
      "CYD enrolled",
      "<p>The CYD will now connect to "
      + escapeHtml(enrolled_.pi_host)
      + " using its dedicated API key. The admin password was not saved.</p>");
}

void Provisioning::handleNotFound() {
  String location = "http://";
  location += WiFi.softAPIP().toString();
  location += "/";
  server_.sendHeader("Location", location);
  server_.send(302, "text/plain", "");
}

void Provisioning::respond(const String& title, const String& body) {
  String page =
      "<!doctype html><html><head><meta name='viewport' "
      "content='width=device-width,initial-scale=1'>"
      "<title>Web Display CYD</title><style>"
      "body{font:16px system-ui;margin:0;background:#111;color:#eee}"
      "main{max-width:42rem;margin:auto;padding:1.25rem}"
      "h1{font-size:1.5rem}label{display:block;margin:1rem 0}"
      "input,select,button{box-sizing:border-box;width:100%;font:inherit;"
      "padding:.7rem;margin-top:.3rem}code{display:block;word-break:break-all;"
      "background:#222;padding:.8rem}button{background:#1677ff;color:#fff;border:0}"
      "input[type=checkbox]{width:auto;margin-right:.5rem}"
      "</style></head><body><main><h1>";
  page += escapeHtml(title);
  page += "</h1>";
  page += body;
  page += "</main></body></html>";
  server_.send(200, "text/html; charset=utf-8", page);
}

String Provisioning::escapeHtml(const String& input) const {
  String result;
  result.reserve(input.length());
  for (std::size_t index = 0; index < input.length(); ++index) {
    switch (input[index]) {
      case '&': result += "&amp;"; break;
      case '<': result += "&lt;"; break;
      case '>': result += "&gt;"; break;
      case '"': result += "&quot;"; break;
      case '\'': result += "&#39;"; break;
      default: result += input[index]; break;
    }
  }
  return result;
}

String Provisioning::hiddenPendingFields() const {
  String result = "<input type='hidden' name='fingerprint' value='";
  result += escapeHtml(pending_.fingerprint);
  result += "'>";
  return result;
}

String Provisioning::setupPassword() const {
  String mac = String(ESP.getEfuseMac(), HEX);
  const int start = mac.length() > 8 ? mac.length() - 8 : 0;
  return "cyd-" + mac.substring(start);
}

String Provisioning::hostForLog() const {
  return pending_.pi_host + ":" + String(pending_.pi_port);
}

