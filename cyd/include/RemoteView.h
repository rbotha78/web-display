#pragma once

#include <array>

#include <Arduino.h>

#include "CompanionConfig.h"
#include "WebDisplayApi.h"

struct RemoteTrainRow {
  String scheduled;
  String destination;
  String platform;
  String status;
  String estimated;
  String state;
  bool delayed = false;
  bool cancelled = false;
};

struct RemoteViewSnapshot {
  bool valid = false;
  bool sleeping = false;
  bool stale = false;
  bool carousel_paused = false;
  String mode;
  String device_time;
  String timezone;
  String scene_kind;
  String scene_title;
  String scene_text;
  String scene_layout;
  bool show_weather = false;
  String weather_location;
  String weather_description;
  String weather_error;
  String weather_icon;
  int weather_temperature = 0;
  int weather_humidity = 0;
  float weather_wind_kmh = 0.0f;
  bool weather_present = false;
  bool weather_stale = false;
  String train_station;
  String train_error;
  bool train_stale = false;
  std::array<RemoteTrainRow, 4> train_rows;
  std::size_t train_count = 0;
  String music_state;
  String music_title;
  String music_artist;
  String music_next;
  String message;
};

struct ControlResult {
  bool ok = false;
  bool carousel_paused = false;
  bool has_carousel_paused = false;
  String music_state;
  String music_title;
  String music_artist;
  String music_next;
};

class RemoteViewClient {
 public:
  bool fetch(
      const CompanionConfig& config,
      RemoteViewSnapshot& snapshot,
      String& error);

  bool controlCarousel(
      const CompanionConfig& config, const String& action,
      ControlResult& result, String& error);
  bool controlMusic(
      const CompanionConfig& config, const String& action,
      ControlResult& result, String& error);
};

