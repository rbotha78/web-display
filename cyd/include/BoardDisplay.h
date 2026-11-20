#pragma once

#include <cstdint>

#include <Arduino.h>
#include <LovyanGFX.hpp>

class CydDisplay : public lgfx::LGFX_Device {
 public:
  CydDisplay();

 private:
  lgfx::Bus_SPI bus_;
  lgfx::Panel_ILI9341 panel_;
  lgfx::Touch_XPT2046 touch_;
  lgfx::Light_PWM backlight_;
};

class BoardDisplay {
 public:
  void begin();
  void loop();
  bool takeTouch(std::uint16_t& x, std::uint16_t& y);

  void showSetup(const String& ssid, const String& password, const String& address);
  void showPairing(const String& host, const String& fingerprint);
  void showStatus(const String& title, const String& line, bool ok);
  void showConnecting(const String& ssid);
  void showTouchPoint(std::int32_t x, std::int32_t y);
  CydDisplay& gfx();

 private:
  void header(const char* title, std::uint16_t color);
  void footer();

  CydDisplay display_;
  std::int32_t boot_count_ = 0;
  std::int32_t last_touch_x_ = -1;
  std::int32_t last_touch_y_ = -1;
  std::uint32_t last_touch_ms_ = 0;
  std::uint32_t last_taken_ms_ = 0;
  bool touch_latched_ = false;
};

