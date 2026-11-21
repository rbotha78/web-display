#include "BoardDisplay.h"

#include <Preferences.h>

#include "BoardPins.h"

CydDisplay::CydDisplay() {
  {
    auto config = bus_.config();
    config.spi_host = HSPI_HOST;
    config.spi_mode = 0;
    config.freq_write = 40000000;
    config.freq_read = 16000000;
    config.spi_3wire = false;
    config.use_lock = true;
    config.dma_channel = SPI_DMA_CH_AUTO;
    config.pin_sclk = board_pins::kTftClock;
    config.pin_mosi = board_pins::kTftMosi;
    config.pin_miso = board_pins::kTftMiso;
    config.pin_dc = board_pins::kTftDc;
    bus_.config(config);
    panel_.setBus(&bus_);
  }
  {
    auto config = panel_.config();
    config.pin_cs = board_pins::kTftCs;
    config.pin_rst = -1;
    config.pin_busy = -1;
    config.memory_width = 240;
    config.memory_height = 320;
    config.panel_width = 240;
    config.panel_height = 320;
    config.dummy_read_pixel = 8;
    config.dummy_read_bits = 1;
    config.readable = true;
    config.invert = false;
    config.rgb_order = false;
    config.dlen_16bit = false;
    config.bus_shared = true;
    panel_.config(config);
  }
  setPanel(&panel_);
  {
    auto config = touch_.config();
    config.spi_host = VSPI_HOST;
    config.pin_sclk = board_pins::kTouchClock;
    config.pin_mosi = board_pins::kTouchMosi;
    config.pin_miso = board_pins::kTouchMiso;
    config.pin_cs = board_pins::kTouchCs;
    config.pin_int = -1;
    config.pin_rst = -1;
    config.freq = 1000000;
    config.x_min = 200;
    config.x_max = 3900;
    config.y_min = 200;
    config.y_max = 3900;
    config.offset_rotation = 2;
    config.bus_shared = true;
    touch_.config(config);
    panel_.setTouch(&touch_);
  }
  {
    auto config = backlight_.config();
    config.pin_bl = board_pins::kBacklight;
    config.invert = false;
    config.freq = 12000;
    config.pwm_channel = 7;
    backlight_.config(config);
    panel_.setLight(&backlight_);
  }
}

void BoardDisplay::begin(std::uint8_t orientation) {
  display_.begin();
  setOrientation(orientation);
  display_.setBrightness(220);
  display_.setTextWrap(false);

  Preferences preferences;
  preferences.begin("wd-cyd", false);
  boot_count_ = preferences.getLong("boots", 0) + 1;
  preferences.putLong("boots", boot_count_);
  preferences.end();
}

void BoardDisplay::setOrientation(std::uint8_t orientation) {
  display_.setRotation(orientation == 1 ? 1 : 3);
}

void BoardDisplay::loop() {
  std::uint16_t x = 0;
  std::uint16_t y = 0;
  if (display_.getTouch(&x, &y)) {
    if (millis() - last_touch_ms_ > 80 || x != last_touch_x_ || y != last_touch_y_) {
      last_touch_ms_ = millis();
      last_touch_x_ = x;
      last_touch_y_ = y;
      Serial.printf("Touch: %u, %u\n", x, y);
    }
  }
}

bool BoardDisplay::takeTouch(std::uint16_t& x, std::uint16_t& y) {
  if (!display_.getTouch(&x, &y)) {
    touch_latched_ = false;
    return false;
  }
  y = display_.height() - 1 - y;
  if (touch_latched_ || millis() - last_taken_ms_ < 300) {
    return false;
  }
  touch_latched_ = true;
  last_taken_ms_ = millis();
  return true;
}

void BoardDisplay::header(const char* title, std::uint16_t color) {
  display_.fillScreen(TFT_BLACK);
  display_.fillRect(0, 0, 320, 38, color);
  display_.setTextColor(TFT_WHITE, color);
  display_.setFont(&fonts::FreeSansBold12pt7b);
  display_.drawString(title, 8, 8);
  display_.setFont(&fonts::Font0);
}

void BoardDisplay::footer() {
  char text[80];
  snprintf(
      text, sizeof(text), "Boot %ld  Heap %lu  %s",
      static_cast<long>(boot_count_), ESP.getFreeHeap(),
      psramFound() ? "PSRAM" : "no PSRAM");
  display_.fillRect(0, 220, 320, 20, TFT_DARKGREY);
  display_.setTextColor(TFT_WHITE, TFT_DARKGREY);
  display_.drawRightString(text, 316, 225, &fonts::Font0);
}

void BoardDisplay::showSetup(const String& ssid, const String& password, const String& address) {
  header("CYD setup", 0x001F);
  display_.setTextColor(TFT_WHITE, TFT_BLACK);
  display_.setTextSize(2);
  display_.drawString("1. Join Wi-Fi:", 10, 52);
  display_.setTextColor(TFT_CYAN, TFT_BLACK);
  display_.drawString(ssid, 10, 78);
  display_.setTextColor(TFT_WHITE, TFT_BLACK);
  display_.drawString("Password:", 10, 108);
  display_.setTextColor(TFT_CYAN, TFT_BLACK);
  display_.drawString(password, 10, 134);
  display_.setTextColor(TFT_WHITE, TFT_BLACK);
  display_.drawString("2. Open:", 10, 166);
  display_.setTextColor(TFT_CYAN, TFT_BLACK);
  display_.drawString(address, 10, 192);
  display_.setTextSize(1);
  footer();
}

void BoardDisplay::showPairing(const String& host, const String& fingerprint) {
  header("Confirm Pi certificate", 0x001F);
  display_.setTextColor(TFT_WHITE, TFT_BLACK);
  display_.setTextSize(2);
  display_.drawString(host, 8, 48);
  display_.setTextSize(1);
  display_.setTextColor(TFT_CYAN, TFT_BLACK);
  display_.drawString("SHA-256:", 8, 78);
  const int line_length = 47;
  for (int offset = 0; offset < fingerprint.length(); offset += line_length) {
    display_.drawString(
        fingerprint.substring(offset, offset + line_length),
        8, 94 + (offset / line_length) * 14);
  }
  display_.setTextColor(TFT_WHITE, TFT_BLACK);
  display_.drawString(
      "Compare this fingerprint with the Pi setup screen.", 8, 156);
  display_.drawString(
      "Continue only when both values match.", 8, 172);
  footer();
}

void BoardDisplay::showConnecting(const String& ssid) {
  showStatus("Connecting", ssid, true);
}

void BoardDisplay::showStatus(const String& title, const String& line, bool ok) {
  header(title.c_str(), ok ? 0x0380 : 0xF800);
  display_.setTextSize(2);
  display_.setTextColor(ok ? TFT_GREEN : TFT_RED, TFT_BLACK);
  display_.drawString(line, 10, 78);
  display_.setTextSize(1);
  footer();
}

void BoardDisplay::showTouchPoint(std::int32_t x, std::int32_t y) {
  last_touch_x_ = x;
  last_touch_y_ = y;
}

CydDisplay& BoardDisplay::gfx() {
  return display_;
}

