#include "RemoteView.h"

#include <cmath>

namespace {

String errorFrom(const ApiResponse& response, const char* fallback) {
  if (response.error.length()) {
    return response.error;
  }
  JsonDocument document;
  if (!deserializeJson(document, response.body)) {
    const String message = document["error"] | "";
    if (message.length()) {
      return message;
    }
  }
  return fallback;
}

String text(const JsonVariantConst& value, std::size_t limit = 160) {
  String result = value | "";
  if (result.length() > limit) {
    result = result.substring(0, limit);
  }
  return result;
}

}  // namespace

bool RemoteViewClient::fetch(
    const CompanionConfig& config,
    RemoteViewSnapshot& snapshot,
    String& error) {
  WebDisplayApi api;
  const ApiResponse response = api.request(config, "GET", "/api/remote-view");
  if (response.status != 200) {
    error = errorFrom(response, "Remote view unavailable");
    return false;
  }

  JsonDocument document;
  const DeserializationError json_error = deserializeJson(document, response.body);
  if (json_error) {
    error = String("Invalid remote view: ") + json_error.c_str();
    return false;
  }
  if (!(document["available"] | false)
      || !document["scene"].is<JsonVariantConst>()) {
    error = "Incomplete remote view";
    return false;
  }

  RemoteViewSnapshot next;
  next.valid = true;
  next.mode = text(document["mode"], 20);
  next.device_time = text(document["device_time"], 40);
  next.sleeping = document["sleep"]["sleeping"] | false;
  next.carousel_paused = document["carousel_paused"] | false;
  const double heartbeat_age = document["heartbeat_age_seconds"] | 0.0;
  next.stale = heartbeat_age > 10;
  next.message = text(document["message"], 100);

  if (!document["scene"].isNull()) {
    next.scene_kind = text(document["scene"]["kind"], 20);
    next.scene_title = text(document["scene"]["title"], 80);
    next.scene_text = text(document["scene"]["text"], 180);
    next.scene_layout = text(document["scene"]["layout"], 20);
    next.show_weather = document["scene"]["show_weather"] | false;
  }

  if (!document["weather"].isNull()) {
    const JsonVariantConst weather = document["weather"];
    next.weather_present = true;
    next.weather_location = text(weather["location"], 50);
    next.weather_description = text(weather["description"], 50);
    next.weather_error = text(weather["error"], 100);
    next.weather_icon = text(weather["icon"], 10);
    next.weather_temperature = static_cast<int>(
        std::lround(static_cast<double>(weather["temperature"] | 0.0)));
    next.weather_humidity = weather["humidity"] | 0;
    next.weather_wind_kmh = static_cast<float>(weather["wind_kmh"] | 0.0);
    next.weather_stale = weather["stale"] | false;
  }

  if (!document["train"].isNull()) {
    const JsonVariantConst train = document["train"];
    next.train_station = text(train["station"], 50);
    next.train_error = text(train["error"], 100);
    next.train_stale = train["stale"] | false;
    const JsonArrayConst rows = train["rows"].as<JsonArrayConst>();
    for (const JsonVariantConst row : rows) {
      if (next.train_count >= next.train_rows.size()) {
        break;
      }
      RemoteTrainRow& target = next.train_rows[next.train_count++];
      target.scheduled = text(row["scheduled"], 8);
      target.destination = text(row["destination"], 36);
      target.platform = text(row["platform"], 6);
      target.status = text(row["status"], 30);
      target.estimated = text(row["estimated"], 30);
      target.state = text(row["state"], 30);
      target.delayed = String(row["state"] | "") == "delayed";
      target.cancelled = row["cancelled"] | false;
    }
  }

  if (!document["music"].isNull()) {
    const JsonVariantConst music = document["music"];
    next.music_state = text(music["state"], 20);
    next.music_title = text(music["title"], 100);
    next.music_artist = text(music["artist"], 100);
    next.music_next = text(music["next_title"], 80);
  }

  snapshot = next;
  error = "";
  return true;
}

bool RemoteViewClient::controlCarousel(
    const CompanionConfig& config, const String& action, String& error) {
  JsonDocument body;
  body["action"] = action;
  String payload;
  serializeJson(body, payload);
  WebDisplayApi api;
  const ApiResponse response = api.request(
      config, "POST", "/api/playlist/control", payload);
  if (response.status == 200) {
    error = "";
    return true;
  }
  error = errorFrom(response, "Carousel command failed");
  return false;
}

bool RemoteViewClient::controlMusic(
    const CompanionConfig& config, const String& action, String& error) {
  JsonDocument body;
  body["action"] = action;
  String payload;
  serializeJson(body, payload);
  WebDisplayApi api;
  const ApiResponse response = api.request(
      config, "POST", "/api/plex/music/control", payload);
  if (response.status == 200) {
    error = "";
    return true;
  }
  error = errorFrom(response, "Music command failed");
  return false;
}

