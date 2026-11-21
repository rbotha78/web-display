#include "CompanionUi.h"

CompanionUi::CompanionUi(BoardDisplay& display) : display_(display) {}

namespace {

void splitStatusText(const String& status, String& first, String& second) {
  constexpr std::size_t kLineLimit = 20;
  first = status.substring(0, kLineLimit);
  second = "";
  if (status.length() <= kLineLimit) {
    return;
  }
  const int last_space = status.substring(0, kLineLimit + 1).lastIndexOf(' ');
  if (last_space > 0) {
    first = status.substring(0, last_space);
    second = status.substring(last_space + 1);
  } else {
    first = status.substring(0, kLineLimit);
    second = status.substring(kLineLimit);
  }
  if (second.length() > kLineLimit) {
    second = second.substring(0, kLineLimit);
  }
  first.trim();
  second.trim();
}

}  // namespace

void CompanionUi::render(
    const RemoteViewSnapshot& view,
    const String& error,
    bool connected) {
  const String signature = renderSignature(view, error, connected);
  if (signature == last_signature_) {
    return;
  }
  last_signature_ = signature;
  CydDisplay& lcd = display_.gfx();
  lcd.fillScreen(TFT_BLACK);
  lcd.fillRect(0, 0, 320, 34, connected ? 0x0360 : 0xF800);
  lcd.setTextColor(TFT_WHITE);
  lcd.setTextSize(1);
  lcd.drawString(headingText(view), 8, 10);
  const bool dashboard_clock =
      view.scene_kind == "dashboard" && view.scene_layout != "notice";
  if (!dashboard_clock) {
    lcd.setTextSize(2);
    lcd.drawRightString(clockText(view.device_time), 312, 8);
  }

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
  } else if (view.scene_kind == "dashboard") {
    renderDashboard(view);
  } else if (view.scene_kind == "web" || view.scene_kind == "video") {
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

void CompanionUi::renderDashboard(const RemoteViewSnapshot& view) {
  CydDisplay& lcd = display_.gfx();
  const bool show_clock = view.scene_layout != "notice";
  const bool show_notice = view.scene_layout != "clock";

  if (show_clock) {
    lcd.setTextColor(TFT_WHITE, TFT_BLACK);
    lcd.setFont(&fonts::FreeSansBold24pt7b);
    lcd.drawCentreString(clockText(view.device_time), 160, 42);
    lcd.setFont(&fonts::Font0);
    lcd.setTextSize(1);
    lcd.setTextColor(TFT_CYAN, TFT_BLACK);
    lcd.setTextSize(2);
    lcd.drawCentreString(dateText(view.device_time), 160, 108);
  }

  if (show_notice && view.scene_text.length()) {
    lcd.setTextSize(1);
    lcd.setTextColor(TFT_WHITE, TFT_BLACK);
    String notice = view.scene_text;
    const std::int16_t notice_y = show_clock ? 124 : 54;
    const std::size_t line_limit = 48;
    const std::size_t line_count = view.show_weather ? 2 : 3;
    for (std::size_t offset = 0, line = 0;
         offset < notice.length() && line < line_count;
         line++) {
      String part = notice.substring(offset, offset + line_limit);
      int break_at = part.lastIndexOf(' ');
      if (offset + line_limit < notice.length() && break_at > 12) {
        part = part.substring(0, break_at);
        offset += break_at + 1;
      } else {
        offset += part.length();
      }
      part.trim();
      lcd.drawCentreString(part, 160, notice_y + line * 12);
    }
  }

  if (view.show_weather && view.weather_present) {
    lcd.fillRect(0, 142, 320, 36, TFT_DARKGREY);
    lcd.setTextSize(1);
    lcd.setTextColor(TFT_WHITE, TFT_DARKGREY);
    drawWeatherIcon(4, 144, 32, view.weather_icon);
    String location = view.weather_location.length()
        ? view.weather_location.substring(0, 10) : "Weather";
    String temperature = String(view.weather_temperature) + "C";
    lcd.setTextSize(2);
    lcd.drawString(location, 42, 148);
    lcd.drawRightString(temperature, 312, 148);

    String forecast = view.weather_description.length()
        ? view.weather_description.substring(0, 12) : "Weather";
    String measurements = "H";
    measurements += String(view.weather_humidity);
    measurements += "% W";
    measurements += String(view.weather_wind_kmh, 1);
    measurements += "";
    lcd.drawString(forecast, 42, 164);
    lcd.drawRightString(measurements, 312, 164);
  }
}

void CompanionUi::renderWeather(const RemoteViewSnapshot& view) {
  CydDisplay& lcd = display_.gfx();
  drawWeatherIcon(220, 76, 48, view.weather_icon);
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
  lcd.drawString("Wind " + String(view.weather_wind_kmh, 1) + " km/h", 150, 164);
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
    lcd.drawString(row.destination.substring(0, 22), 54, y + 7);
    lcd.drawRightString(row.platform, 196, y + 7);

    String status = row.status;
    if (!status.length()) {
      status = row.estimated;
    }
    if (!status.length()) {
      status = row.cancelled ? "Cancelled"
          : (row.delayed ? "Delayed" : (row.state.length() ? row.state : "On time"));
    }
    lcd.setTextColor(row.cancelled ? TFT_RED : (row.delayed ? TFT_ORANGE : TFT_GREEN));
    if (status.length() <= 18) {
      lcd.drawString(status, 204, y + 7);
    } else {
      String first;
      String second;
      splitStatusText(status, first, second);
      lcd.drawString(first, 204, y + 3);
      if (second.length()) {
        lcd.drawString(second, 204, y + 13);
      }
    }
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

String CompanionUi::dateText(const String& device_time) const {
  return device_time.indexOf('T') >= 0
      ? device_time.substring(0, device_time.indexOf('T'))
      : "";
}

void CompanionUi::drawWeatherIcon(
    std::int16_t x, std::int16_t y, std::uint16_t size, const String& icon) const {
  CydDisplay& lcd = display_.gfx();
  const float scale = size / 24.0f;
  const auto sx = [scale](int value) -> int { return static_cast<int>(value * scale); };
  const String code = icon.length() >= 2 ? icon.substring(0, 2) : "";
  const bool night = icon.endsWith("n");

  if (code == "01") {
    lcd.fillCircle(x + sx(12), y + sx(12), sx(6), TFT_YELLOW);
    lcd.drawLine(x + sx(12), y, x + sx(12), y + sx(3), TFT_YELLOW);
    lcd.drawLine(x + sx(12), y + sx(21), x + sx(12), y + sx(24), TFT_YELLOW);
    lcd.drawLine(x, y + sx(12), x + sx(3), y + sx(12), TFT_YELLOW);
    lcd.drawLine(x + sx(21), y + sx(12), x + sx(24), y + sx(12), TFT_YELLOW);
  } else if (code == "02" || code == "03" || code == "04") {
    lcd.fillCircle(x + sx(8), y + sx(10), sx(6), TFT_LIGHTGREY);
    lcd.fillCircle(x + sx(15), y + sx(9), sx(7), TFT_LIGHTGREY);
    lcd.fillRect(x + sx(3), y + sx(10), sx(18), sx(8), TFT_LIGHTGREY);
    if (night) {
      lcd.fillCircle(x + sx(7), y + sx(7), sx(4), TFT_CYAN);
      lcd.fillCircle(x + sx(10), y + sx(5), sx(4), TFT_DARKGREY);
    }
  } else if (code == "09" || code == "10") {
    lcd.fillCircle(x + sx(8), y + sx(7), sx(5), TFT_LIGHTGREY);
    lcd.fillCircle(x + sx(14), y + sx(6), sx(6), TFT_LIGHTGREY);
    lcd.fillRect(x + sx(3), y + sx(7), sx(17), sx(7), TFT_LIGHTGREY);
    lcd.drawLine(x + sx(6), y + sx(16), x + sx(4), y + sx(21), TFT_CYAN);
    lcd.drawLine(x + sx(12), y + sx(16), x + sx(10), y + sx(22), TFT_CYAN);
    lcd.drawLine(x + sx(18), y + sx(16), x + sx(16), y + sx(21), TFT_CYAN);
  } else if (code == "11") {
    lcd.fillCircle(x + sx(8), y + sx(7), sx(5), TFT_LIGHTGREY);
    lcd.fillCircle(x + sx(15), y + sx(6), sx(6), TFT_LIGHTGREY);
    lcd.fillRect(x + sx(3), y + sx(7), sx(18), sx(7), TFT_LIGHTGREY);
    lcd.fillTriangle(x + sx(13), y + sx(12), x + sx(8), y + sx(22), x + sx(13), y + sx(18), TFT_YELLOW);
  } else if (code == "13") {
    lcd.fillCircle(x + sx(12), y + sx(12), sx(2), TFT_WHITE);
    lcd.drawLine(x + sx(12), y + sx(2), x + sx(12), y + sx(22), TFT_WHITE);
    lcd.drawLine(x + sx(2), y + sx(12), x + sx(22), y + sx(12), TFT_WHITE);
    lcd.drawLine(x + sx(5), y + sx(5), x + sx(19), y + sx(19), TFT_WHITE);
    lcd.drawLine(x + sx(19), y + sx(5), x + sx(5), y + sx(19), TFT_WHITE);
  } else if (code == "50") {
    lcd.drawLine(x + sx(2), y + sx(7), x + sx(22), y + sx(7), TFT_LIGHTGREY);
    lcd.drawLine(x + sx(5), y + sx(12), x + sx(19), y + sx(12), TFT_LIGHTGREY);
    lcd.drawLine(x + sx(2), y + sx(17), x + sx(22), y + sx(17), TFT_LIGHTGREY);
  } else {
    lcd.drawRect(x + sx(4), y + sx(4), sx(16), sx(16), TFT_LIGHTGREY);
  }
}

String CompanionUi::headingText(const RemoteViewSnapshot& view) const {
  if (view.sleeping) {
    return "Sleeping";
  }
  if (view.scene_kind == "music") {
    return "";
  }
  if (view.music_state == "playing" || view.music_state == "paused"
      || view.music_state == "loading") {
    String heading = "Now: ";
    heading += view.music_title.length() ? view.music_title : "Music";
    if (view.music_artist.length() && heading.length() < 24) {
      heading += " - ";
      heading += view.music_artist;
    }
    return heading.substring(0, 34);
  }
  if (view.scene_title.length()) {
    return view.scene_title.substring(0, 34);
  }
  if (view.scene_kind == "train") {
    return "Train departures";
  }
  if (view.scene_kind == "weather") {
    return "Weather";
  }
  return view.mode.length() ? view.mode : "Reconnect";
}

String CompanionUi::renderSignature(
    const RemoteViewSnapshot& view,
    const String& error,
    bool connected) const {
  String signature;
  signature.reserve(180);
  signature += view.valid ? "1|" : "0|";
  signature += connected ? "c|" : "d|";
  signature += error;
  signature += '|';
  signature += view.sleeping ? "s|" : "-|";
  signature += view.stale ? "x|" : "-|";
  signature += view.carousel_paused ? "p|" : "-|";
  signature += view.mode;
  signature += '|';
  signature += clockText(view.device_time);
  signature += '|';
  signature += headingText(view);
  signature += '|';
  signature += view.scene_kind;
  signature += '|';
  signature += view.scene_title;
  signature += '|';
  signature += view.scene_text;
  signature += '|';
  signature += view.scene_layout;
  signature += '|';
  signature += view.show_weather ? "w|" : "-|";
  signature += view.train_station;
  signature += '|';
  signature += view.train_error;
  signature += '|';
  for (std::size_t index = 0; index < view.train_count; ++index) {
    const RemoteTrainRow& row = view.train_rows[index];
    signature += row.scheduled;
    signature += '|';
    signature += row.destination;
    signature += '|';
    signature += row.platform;
    signature += '|';
    signature += row.status;
    signature += '|';
    signature += row.estimated;
    signature += '|';
    signature += row.state;
  }
  signature += '|';
  signature += view.weather_location;
  signature += '|';
  signature += view.weather_description;
  signature += '|';
  signature += view.weather_error;
  signature += '|';
  signature += view.weather_icon;
  signature += '|';
  signature += String(view.weather_temperature);
  signature += '|';
  signature += String(view.weather_humidity);
  signature += '|';
  signature += String(view.weather_wind_kmh, 1);
  signature += '|';
  signature += view.music_state;
  signature += '|';
  signature += view.music_title;
  signature += '|';
  signature += view.music_artist;
  signature += '|';
  signature += view.music_next;
  signature += '|';
  signature += view.message;
  return signature;
}

