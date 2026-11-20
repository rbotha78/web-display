#include "CompanionUi.h"

CompanionUi::CompanionUi(BoardDisplay& display) : display_(display) {}

void CompanionUi::render(
    const RemoteViewSnapshot& view,
    const String& error,
    bool connected) {
  CydDisplay& lcd = display_.gfx();
  lcd.fillScreen(TFT_BLACK);
  lcd.fillRect(0, 0, 320, 34, connected ? 0x0360 : 0xF800);
  lcd.setTextColor(TFT_WHITE);
  lcd.setTextSize(2);
  lcd.drawString(
      view.sleeping ? "Sleeping" : (connected ? view.mode : "Reconnect"), 8, 8);
  lcd.drawRightString(clockText(view.device_time), 312, 8);

  if (error.length() || !view.valid) {
    lcd.setTextColor(TFT_RED);
    lcd.setTextSize(2);
    lcd.drawString("Display offline", 10, 72);
    lcd.setTextColor(TFT_LIGHTGREY);
    lcd.setTextSize(1);
    lcd.drawString(error.length() ? error.substring(0, 52) : "No remote data", 10, 108);
    renderControls(view, false);
    return;
  }
  if (view.stale) {
    lcd.fillRect(0, 34, 320, 18, 0x8400);
    lcd.setTextColor(TFT_WHITE, 0x8400);
    lcd.setTextSize(1);
    lcd.drawString("Pi heartbeat is stale", 8, 39);
  }
  renderScene(view);
  renderControls(view, connected && !view.stale && !view.sleeping);
}

void CompanionUi::renderScene(const RemoteViewSnapshot& view) {
  if (view.scene_kind == "train") {
    renderTrain(view);
  } else if (view.scene_kind == "weather") {
    renderWeather(view);
  } else if (view.scene_kind == "music") {
    renderMusic(view);
  } else if (view.scene_kind == "dashboard" || view.scene_kind == "web"
      || view.scene_kind == "video") {
    renderGeneric(view);
  } else {
    renderClock(view);
  }
}

void CompanionUi::renderClock(const RemoteViewSnapshot& view) {
  CydDisplay& lcd = display_.gfx();
  lcd.setTextColor(TFT_WHITE, TFT_BLACK);
  lcd.setFont(&fonts::FreeSansBold24pt7b);
  lcd.drawCentreString(clockText(view.device_time), 160, 72);
  lcd.setFont(&fonts::Font0);
  lcd.setTextSize(1);
  lcd.setTextColor(TFT_CYAN, TFT_BLACK);
  lcd.drawCentreString(view.scene_title.length() ? view.scene_title : "Web Display", 160, 142);
}

void CompanionUi::renderWeather(const RemoteViewSnapshot& view) {
  CydDisplay& lcd = display_.gfx();
  lcd.setTextColor(TFT_WHITE, TFT_BLACK);
  lcd.setTextSize(2);
  lcd.drawString(
      view.weather_location.length() ? view.weather_location.substring(0, 24) : "Weather",
      10, 52);
  if (view.weather_error.length()) {
    lcd.setTextColor(TFT_RED);
    lcd.setTextSize(1);
    lcd.drawString(view.weather_error.substring(0, 60), 10, 84);
    return;
  }
  lcd.setTextSize(5);
  lcd.drawString(String(view.weather_temperature) + " C", 10, 82);
  lcd.setTextSize(2);
  lcd.setTextColor(TFT_CYAN);
  lcd.drawString(view.weather_description.substring(0, 24), 10, 132);
  lcd.setTextSize(1);
  lcd.setTextColor(TFT_LIGHTGREY);
  lcd.drawString("Humidity " + String(view.weather_humidity) + "%", 10, 164);
  lcd.drawString("Wind " + String(view.weather_wind_kmh) + " km/h", 150, 164);
  if (view.weather_stale) {
    lcd.setTextColor(TFT_ORANGE);
    lcd.drawString("Weather data is stale", 10, 178);
  }
}

void CompanionUi::renderTrain(const RemoteViewSnapshot& view) {
  CydDisplay& lcd = display_.gfx();
  lcd.setTextSize(1);
  lcd.setTextColor(TFT_CYAN);
  lcd.drawString(view.train_station, 8, 44);
  if (view.train_error.length()) {
    lcd.setTextColor(TFT_RED);
    lcd.drawString(view.train_error.substring(0, 58), 8, 64);
  }
  const std::int16_t start_y = 66;
  for (std::size_t index = 0; index < view.train_count; ++index) {
    const RemoteTrainRow& row = view.train_rows[index];
    const std::int16_t y = start_y + index * 29;
    lcd.fillRect(6, y, 308, 26, index % 2 ? 0x1082 : 0x0841);
    lcd.setTextColor(TFT_WHITE);
    lcd.drawString(row.scheduled, 10, y + 7);
    lcd.drawString(row.destination.substring(0, 23), 54, y + 7);
    lcd.drawRightString(row.platform, 252, y + 7);
    lcd.setTextColor(row.cancelled ? TFT_RED : (row.delayed ? TFT_ORANGE : TFT_GREEN));
    lcd.drawRightString(row.status.substring(0, 12), 310, y + 7);
  }
  if (!view.train_count) {
    lcd.setTextColor(TFT_LIGHTGREY);
    lcd.drawString("No train departures available", 8, 90);
  }
  if (view.train_stale) {
    lcd.setTextColor(TFT_ORANGE);
    lcd.drawString("Train data is stale", 8, 174);
  }
}

void CompanionUi::renderMusic(const RemoteViewSnapshot& view) {
  CydDisplay& lcd = display_.gfx();
  lcd.setTextColor(TFT_CYAN);
  lcd.setTextSize(1);
  lcd.drawString(String("Music: ") + view.music_state, 10, 50);
  lcd.setTextColor(TFT_WHITE);
  lcd.setTextSize(2);
  lcd.drawString(
      view.music_title.length() ? view.music_title.substring(0, 25) : "Nothing playing",
      10, 76);
  lcd.setTextColor(TFT_LIGHTGREY);
  lcd.setTextSize(1);
  lcd.drawString(view.music_artist.substring(0, 44), 10, 112);
  lcd.setTextColor(TFT_CYAN);
  lcd.drawString("Next:", 10, 142);
  lcd.setTextColor(TFT_WHITE);
  lcd.drawString(view.music_next.substring(0, 44), 44, 142);
}

void CompanionUi::renderGeneric(const RemoteViewSnapshot& view) {
  CydDisplay& lcd = display_.gfx();
  lcd.setTextColor(TFT_CYAN);
  lcd.setTextSize(1);
  lcd.drawString(view.scene_kind + " scene", 10, 48);
  lcd.setTextColor(TFT_WHITE);
  lcd.setTextSize(2);
  lcd.drawString(view.scene_title.substring(0, 27), 10, 72);
  lcd.setTextSize(1);
  lcd.setTextColor(TFT_LIGHTGREY);
  if (view.scene_text.length()) {
    lcd.drawString(view.scene_text.substring(0, 50), 10, 110);
    lcd.drawString(view.scene_text.substring(50, 100), 10, 126);
    lcd.drawString(view.scene_text.substring(100, 150), 10, 142);
  } else if (view.music_title.length()) {
    lcd.drawString("Music: " + view.music_title.substring(0, 42), 10, 120);
  } else {
    lcd.drawString(
        view.message.length() ? view.message.substring(0, 55) : "Scene controlled by the Pi",
        10, 120);
  }
}

void CompanionUi::renderControls(const RemoteViewSnapshot& view, bool enabled) {
  CydDisplay& lcd = display_.gfx();
  constexpr std::uint16_t width = 64;
  const char* music_toggle =
      view.music_state == "playing" ? "PAUSE"
      : (view.music_state == "paused" ? "RESUME" : "PLAY");
  const String labels[] = {
      view.carousel_paused ? "RESUME" : "PAUSE", "NEXT", music_toggle, "STOP", "SKIP"};
  const std::uint16_t colors[] = {TFT_BLUE, TFT_BLUE, TFT_GREEN, TFT_RED, TFT_PURPLE};
  for (std::size_t index = 0; index < 5; ++index) {
    const std::int16_t x = static_cast<std::int16_t>(index * width);
    lcd.fillRect(x + 1, 190, width - 2, 50, enabled ? colors[index] : TFT_DARKGREY);
    lcd.setTextColor(TFT_WHITE, enabled ? colors[index] : TFT_DARKGREY);
    lcd.setTextSize(1);
    lcd.drawCentreString(labels[index], x + width / 2, 211);
  }
  lcd.setTextColor(TFT_LIGHTGREY, TFT_BLACK);
  lcd.drawString("SCENE", 2, 180);
  lcd.drawString("MUSIC", 130, 180);
  (void)view;
}

TouchAction CompanionUi::takeAction(
    const RemoteViewSnapshot& view, bool connected) {
  std::uint16_t x = 0;
  std::uint16_t y = 0;
  if (!connected || view.stale || view.sleeping
      || !display_.takeTouch(x, y) || y < 190) {
    return TouchAction::kNone;
  }
  const std::uint8_t zone = x / 64;
  if (zone < 2 && view.mode != "carousel") {
    return TouchAction::kNone;
  }
  switch (zone) {
    case 0: return TouchAction::kCarouselPause;
    case 1: return TouchAction::kCarouselNext;
    case 2: return TouchAction::kMusicToggle;
    case 3: return TouchAction::kMusicStop;
    case 4: return TouchAction::kMusicSkip;
    default: return TouchAction::kNone;
  }
}

String CompanionUi::clockText(const String& device_time) const {
  const int time = device_time.indexOf('T');
  return time >= 0 ? device_time.substring(time + 1, time + 6) : "--:--";
}

