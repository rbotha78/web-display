#include "CompanionUi.h"

#include <algorithm>
#include <cmath>

CompanionUi::CompanionUi(BoardDisplay& display) : display_(display) {}

void CompanionUi::setPlaybackControls(bool enabled) {
  playback_controls_ = enabled;
  invalidate();
}

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
    lcd.drawRightString(clockText(), 312, 8);
  }

  if (error.length() || !view.valid) {
    lcd.setTextColor(TFT_RED);
    lcd.setTextSize(2);
    lcd.drawString("Display offline", 10, playback_controls_ ? 72 : 88);
    lcd.setTextColor(TFT_LIGHTGREY);
    lcd.setTextSize(1);
    lcd.drawString(error.length() ? error.substring(0, 52) : "No remote data", 10, 108);
    if (playback_controls_) {
      renderControls(view, false);
    }
    return;
  }
  if (view.stale) {
    lcd.fillRect(0, 34, 320, 18, 0x8400);
    lcd.setTextColor(TFT_WHITE, 0x8400);
    lcd.setTextSize(1);
    lcd.drawString("Pi heartbeat is stale", 8, 39);
  }
  renderScene(view);
  if (playback_controls_) {
    renderControls(view, connected && !view.stale && !view.sleeping);
  }
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
  lcd.drawCentreString(
      clockText(), 160, playback_controls_ ? 72 : 82);
  lcd.setFont(&fonts::Font0);
  lcd.setTextSize(1);
  lcd.setTextColor(TFT_CYAN, TFT_BLACK);
  lcd.drawCentreString(
      view.scene_title.length() ? view.scene_title : "Web Display",
      160, playback_controls_ ? 142 : 180);
}

void CompanionUi::renderDashboard(const RemoteViewSnapshot& view) {
  CydDisplay& lcd = display_.gfx();
  const bool show_clock = view.scene_layout != "notice";
  const bool show_notice = view.scene_layout != "clock";

  if (show_clock) {
    lcd.setTextColor(TFT_WHITE, TFT_BLACK);
    lcd.setFont(&fonts::FreeSansBold24pt7b);
    lcd.drawCentreString(clockText(), 160, 42);
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
    const std::int16_t weather_y = playback_controls_ ? 142 : 188;
    const std::size_t line_count =
        (view.show_weather
            ? static_cast<std::size_t>((weather_y - notice_y) / 12)
            : static_cast<std::size_t>(((playback_controls_ ? 176 : 232) - notice_y) / 12));
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
    const std::int16_t y = playback_controls_ ? 142 : 188;
    const std::uint16_t height = playback_controls_ ? 36 : 50;
    lcd.fillRect(0, y, 320, height, TFT_DARKGREY);
    lcd.setTextSize(1);
    lcd.setTextColor(TFT_WHITE, TFT_DARKGREY);
    drawWeatherIcon(4, y + 2, playback_controls_ ? 32 : 46, view.weather_icon, TFT_DARKGREY);
    String location = view.weather_location.length()
        ? view.weather_location.substring(0, 10) : "Weather";
    String temperature = String(view.weather_temperature) + "C";
    lcd.setTextSize(2);
    const std::int16_t text_x = playback_controls_ ? 42 : 58;
    lcd.drawString(location, text_x, y + 6);
    lcd.drawRightString(temperature, 312, y + 6);

    String forecast = view.weather_description.length()
        ? view.weather_description.substring(0, 12) : "Weather";
    String measurements = "H";
    measurements += String(view.weather_humidity);
    measurements += "% W";
    measurements += String(view.weather_wind_kmh, 1);
    measurements += "";
    lcd.drawString(forecast, text_x, y + 25);
    lcd.drawRightString(measurements, 312, y + 25);
  }
}

void CompanionUi::renderWeather(const RemoteViewSnapshot& view) {
  CydDisplay& lcd = display_.gfx();
  drawWeatherIcon(
      playback_controls_ ? 220 : 238,
      playback_controls_ ? 76 : 58,
      playback_controls_ ? 48 : 72,
      view.weather_icon, TFT_BLACK);
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
  const std::int16_t details_y = playback_controls_ ? 164 : 186;
  lcd.drawString("Humidity " + String(view.weather_humidity) + "%", 10, details_y);
  lcd.drawString("Wind " + String(view.weather_wind_kmh, 1) + " km/h", 150, details_y);
  if (view.weather_stale) {
    lcd.setTextColor(TFT_ORANGE);
    lcd.drawString("Weather data is stale", 10, playback_controls_ ? 178 : 208);
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
  const std::int16_t row_height = playback_controls_ ? 29 : 40;
  const std::int16_t row_box_height = playback_controls_ ? 26 : 36;
  for (std::size_t index = 0; index < view.train_count; ++index) {
    const RemoteTrainRow& row = view.train_rows[index];
    const std::int16_t y = start_y + index * row_height;
    lcd.fillRect(6, y, 308, row_box_height, index % 2 ? 0x1082 : 0x0841);
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
    lcd.drawString(
        "No train departures available", 8, playback_controls_ ? 90 : 120);
  }
  if (view.train_stale) {
    lcd.setTextColor(TFT_ORANGE);
    lcd.drawString("Train data is stale", 8, playback_controls_ ? 174 : 228);
  }
}

void CompanionUi::renderMusic(const RemoteViewSnapshot& view) {
  CydDisplay& lcd = display_.gfx();
  lcd.setTextColor(TFT_CYAN);
  lcd.setTextSize(1);
  lcd.drawString(String("Music: ") + view.music_state, 10, playback_controls_ ? 50 : 54);
  lcd.setTextColor(TFT_WHITE);
  lcd.setTextSize(2);
  lcd.drawString(
      view.music_title.length() ? view.music_title.substring(0, 25) : "Nothing playing",
      10, playback_controls_ ? 76 : 88);
  lcd.setTextColor(TFT_LIGHTGREY);
  lcd.setTextSize(1);
  lcd.drawString(
      view.music_artist.substring(0, 44), 10, playback_controls_ ? 112 : 138);
  lcd.setTextColor(TFT_CYAN);
  lcd.drawString("Next:", 10, playback_controls_ ? 142 : 190);
  lcd.setTextColor(TFT_WHITE);
  lcd.drawString(
      view.music_next.substring(0, 44), 44, playback_controls_ ? 142 : 190);
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
    const std::size_t line_count = playback_controls_ ? 3 : 7;
    for (std::size_t line = 0; line < line_count; ++line) {
      const std::size_t offset = line * 50;
      if (offset >= view.scene_text.length()) {
        break;
      }
      lcd.drawString(
          view.scene_text.substring(offset, offset + 50), 10, 110 + line * 16);
    }
  } else if (view.music_title.length()) {
    lcd.drawString("Music: " + view.music_title.substring(0, 42), 10, 120);
  } else {
    lcd.drawString(
        view.message.length() ? view.message.substring(0, 55) : "Scene controlled by the Pi",
        10, 120);
  }
}

void CompanionUi::renderControls(const RemoteViewSnapshot& view, bool enabled) {
  if (!playback_controls_) {
    return;
  }
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
    std::uint16_t color = enabled ? colors[index] : TFT_DARKGREY;
    if (feedback_zone_ == static_cast<std::int8_t>(index) && feedback_ == TouchFeedback::kPressed) {
      color = TFT_WHITE;
    } else if (feedback_zone_ == static_cast<std::int8_t>(index) && feedback_ == TouchFeedback::kWaiting) {
      color = 0xFFE0;
    } else if (feedback_zone_ == static_cast<std::int8_t>(index) && feedback_ == TouchFeedback::kSuccess) {
      color = 0x07E0;
    } else if (feedback_zone_ == static_cast<std::int8_t>(index) && feedback_ == TouchFeedback::kFailure) {
      color = TFT_RED;
    }
    lcd.fillRect(x + 1, 190, width - 2, 50, color);
    lcd.drawRect(x, 190, width, 50, TFT_WHITE);
    lcd.setTextColor(
        feedback_zone_ == static_cast<std::int8_t>(index) && feedback_ == TouchFeedback::kPressed
            ? TFT_BLACK : TFT_WHITE,
        color);
    lcd.setTextSize(1);
    String label = labels[index];
    if (feedback_zone_ == static_cast<std::int8_t>(index)) {
      if (feedback_ == TouchFeedback::kWaiting) label = "WAIT";
      if (feedback_ == TouchFeedback::kSuccess) label = "OK";
      if (feedback_ == TouchFeedback::kFailure) label = "FAIL";
    }
    lcd.drawCentreString(label, x + width / 2, 211);
  }
  lcd.setTextColor(TFT_LIGHTGREY, TFT_BLACK);
  lcd.drawString("SCENE", 2, 180);
  lcd.drawString("MUSIC", 130, 180);
  (void)view;
}

TouchAction CompanionUi::takeAction(
    const RemoteViewSnapshot& view, bool connected) {
  if (!playback_controls_) {
    pressed_zone_ = -1;
    pressed_armed_ = false;
    return TouchAction::kNone;
  }
  std::uint16_t x = 0;
  std::uint16_t y = 0;
  const bool touched = display_.pollTouch(x, y);

  if (feedback_ != TouchFeedback::kNone
      && millis() >= feedback_until_) {
    feedback_ = TouchFeedback::kNone;
    feedback_zone_ = -1;
    renderControls(view, connected && !view.stale && !view.sleeping);
  }

  if (!connected || view.stale || view.sleeping) {
    if (!touched) {
      pressed_zone_ = -1;
    }
    return TouchAction::kNone;
  }

  if (!touched) {
    if (pressed_zone_ >= 0) {
      const std::int8_t released_zone = pressed_zone_;
      const bool armed = pressed_armed_;
      pressed_zone_ = -1;
      pressed_since_ = 0;
      pressed_armed_ = false;
      if (!armed || millis() - last_action_ms_ < 250) {
        feedback_zone_ = -1;
        feedback_ = TouchFeedback::kNone;
        return TouchAction::kNone;
      }
      if (released_zone < 2 && view.mode != "carousel") {
        return TouchAction::kNone;
      }
      last_action_ms_ = millis();
      feedback_zone_ = released_zone;
      feedback_ = TouchFeedback::kWaiting;
      feedback_until_ = millis() + 1500;
      renderControls(view, true);
      switch (released_zone) {
        case 0: return TouchAction::kCarouselPause;
        case 1: return TouchAction::kCarouselNext;
        case 2: return TouchAction::kMusicToggle;
        case 3: return TouchAction::kMusicStop;
        case 4: return TouchAction::kMusicSkip;
        default: return TouchAction::kNone;
      }
    }
    return TouchAction::kNone;
  }

  if (y < 190 || y >= 240 || x >= 320) {
    pressed_zone_ = -1;
    pressed_since_ = 0;
    pressed_armed_ = false;
    return TouchAction::kNone;
  }

  const std::int8_t zone = static_cast<std::int8_t>(x / 64);
  if (pressed_zone_ != zone) {
    pressed_zone_ = zone;
    pressed_since_ = millis();
    pressed_armed_ = false;
    feedback_zone_ = zone;
    feedback_ = TouchFeedback::kPressed;
    feedback_until_ = millis() + 1500;
    renderControls(view, true);
  }
  if (!pressed_armed_
      && pressed_since_ != 0
      && millis() - pressed_since_ >= 40) {
    pressed_armed_ = true;
  }
  return TouchAction::kNone;
}

void CompanionUi::showActionResult(
    TouchAction action,
    bool success,
    const RemoteViewSnapshot& view,
    bool enabled) {
  if (!playback_controls_) {
    return;
  }
  std::int8_t zone = -1;
  switch (action) {
    case TouchAction::kCarouselPause: zone = 0; break;
    case TouchAction::kCarouselNext: zone = 1; break;
    case TouchAction::kMusicToggle: zone = 2; break;
    case TouchAction::kMusicStop: zone = 3; break;
    case TouchAction::kMusicSkip: zone = 4; break;
    default: break;
  }
  feedback_zone_ = zone;
  feedback_ = success ? TouchFeedback::kSuccess : TouchFeedback::kFailure;
  feedback_until_ = millis() + 900;
  renderControls(view, enabled);
}

void CompanionUi::invalidate() {
  last_signature_ = "";
}

String CompanionUi::clockText() const {
  time_t now = time(nullptr);
  if (now == 0) return "--:--";
  struct tm* tm_info = localtime(&now);
  char buf[6];
  snprintf(buf, sizeof(buf), "%02d:%02d", tm_info->tm_hour, tm_info->tm_min);
  return String(buf);
}

String CompanionUi::dateText(const String& device_time) const {
  return device_time.indexOf('T') >= 0
      ? device_time.substring(0, device_time.indexOf('T'))
      : "";
}

void CompanionUi::drawWeatherIcon(
    std::int16_t x, std::int16_t y, std::uint16_t size,
    const String& icon, std::uint16_t background) const {
  CydDisplay& lcd = display_.gfx();
  const float scale = size / 24.0f;
  const auto sx = [scale](int value) -> int { return static_cast<int>(value * scale); };
  const auto point_x = [x, &sx](int value) -> int { return x + sx(value); };
  const auto point_y = [y, &sx](int value) -> int { return y + sx(value); };
  const auto circle = [&](int cx, int cy, int radius, std::uint16_t color) {
    lcd.fillSmoothCircle(point_x(cx), point_y(cy), sx(radius), color);
  };
  const auto wide_line = [&](
      int x0, int y0, int x1, int y1, float radius, std::uint16_t color) {
    lcd.drawWideLine(
        point_x(x0), point_y(y0), point_x(x1), point_y(y1),
        std::max(0.5f, radius * scale), color);
  };
  const auto cloud = [&](int ox, int oy, std::uint16_t color) {
    lcd.fillSmoothRoundRect(
        point_x(3 + ox), point_y(10 + oy), sx(18), sx(8), sx(4), color);
    circle(8 + ox, 10 + oy, 5, color);
    circle(15 + ox, 9 + oy, 6, color);
  };
  const auto sun = [&](int cx, int cy) {
    circle(cx, cy, 5, TFT_ORANGE);
    circle(cx, cy, 4, TFT_YELLOW);
    for (int index = 0; index < 8; ++index) {
      const float angle = static_cast<float>(index) * 0.785398163f;
      const int x0 = cx + static_cast<int>(std::cos(angle) * 7.0f);
      const int y0 = cy + static_cast<int>(std::sin(angle) * 7.0f);
      const int x1 = cx + static_cast<int>(std::cos(angle) * 10.0f);
      const int y1 = cy + static_cast<int>(std::sin(angle) * 10.0f);
      wide_line(x0, y0, x1, y1, 0.9f, TFT_YELLOW);
    }
  };
  const auto moon = [&](int cx, int cy) {
    circle(cx, cy, 6, 0x9E7F);
    circle(cx + 3, cy - 2, 6, background);
  };
  const String code = icon.length() >= 2 ? icon.substring(0, 2) : "";
  const bool night = icon.endsWith("n");

  if (code == "01") {
    if (night) {
      moon(12, 12);
      circle(19, 5, 1, TFT_WHITE);
      circle(5, 18, 1, TFT_WHITE);
    } else {
      sun(12, 12);
    }
  } else if (code == "02") {
    if (night) {
      moon(7, 7);
    } else {
      sun(7, 7);
    }
    cloud(0, 2, 0xCE59);
    cloud(0, 3, TFT_LIGHTGREY);
  } else if (code == "03") {
    cloud(0, 2, 0xCE59);
    cloud(0, 0, TFT_LIGHTGREY);
  } else if (code == "04") {
    cloud(-1, -2, 0x8430);
    cloud(2, 3, 0xC618);
  } else if (code == "09" || code == "10") {
    cloud(0, -1, 0xA514);
    for (int drop = 0; drop < 3; ++drop) {
      wide_line(
          6 + drop * 6, 16, 4 + drop * 6, 21,
          1.1f, drop == 1 ? 0x07FF : TFT_CYAN);
    }
  } else if (code == "11") {
    cloud(0, -2, 0x8430);
    lcd.fillTriangle(
        point_x(13), point_y(11), point_x(8), point_y(23),
        point_x(13), point_y(18), TFT_ORANGE);
    lcd.fillTriangle(
        point_x(15), point_y(15), point_x(12), point_y(23),
        point_x(18), point_y(15), TFT_YELLOW);
  } else if (code == "13") {
    circle(12, 12, 2, TFT_WHITE);
    for (int index = 0; index < 6; ++index) {
      const float angle = static_cast<float>(index) * 1.047197551f;
      const int dx = static_cast<int>(std::cos(angle) * 10.0f);
      const int dy = static_cast<int>(std::sin(angle) * 10.0f);
      wide_line(12, 12, 12 + dx, 12 + dy, 0.9f, TFT_WHITE);
      wide_line(
          12 + dx * 3 / 4, 12 + dy * 3 / 4,
          12 + dx * 3 / 4 - dy / 5, 12 + dy * 3 / 4 + dx / 5,
          0.65f, TFT_CYAN);
    }
  } else if (code == "50") {
    wide_line(2, 6, 21, 6, 1.1f, 0xCE59);
    wide_line(5, 12, 23, 12, 1.1f, TFT_LIGHTGREY);
    wide_line(1, 18, 19, 18, 1.1f, 0xA514);
  } else {
    cloud(0, 0, TFT_LIGHTGREY);
    lcd.setFont(&fonts::FreeSansBold18pt7b);
    lcd.setTextColor(TFT_DARKGREY, TFT_BLACK);
    lcd.drawCentreString("?", point_x(12), point_y(3));
    lcd.setFont(&fonts::Font0);
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
  signature += playback_controls_ ? "k|" : "n|";
  signature += view.valid ? "1|" : "0|";
  signature += connected ? "c|" : "d|";
  signature += error;
  signature += '|';
  signature += view.sleeping ? "s|" : "-|";
  signature += view.stale ? "x|" : "-|";
  signature += view.carousel_paused ? "p|" : "-|";
  signature += view.mode;
  signature += '|';
  signature += clockText();
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

