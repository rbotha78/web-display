#pragma once

#include <Arduino.h>

#include "BoardDisplay.h"
#include "RemoteView.h"

enum class TouchAction {
  kNone,
  kCarouselPause,
  kCarouselNext,
  kMusicToggle,
  kMusicStop,
  kMusicSkip,
};

enum class TouchFeedback {
  kNone,
  kPressed,
  kWaiting,
  kSuccess,
  kFailure,
};

class CompanionUi {
 public:
  explicit CompanionUi(BoardDisplay& display);
  void render(
      const RemoteViewSnapshot& view,
      const String& error,
      bool connected);
  TouchAction takeAction(const RemoteViewSnapshot& view, bool connected);
  void setPlaybackControls(bool enabled);
  void showActionResult(
      TouchAction action,
      bool success,
      const RemoteViewSnapshot& view,
      bool enabled);
  void invalidate();

 private:
  String renderSignature(
      const RemoteViewSnapshot& view,
      const String& error,
      bool connected) const;
  String headingText(const RemoteViewSnapshot& view) const;
  void renderScene(const RemoteViewSnapshot& view);
  void renderClock(const RemoteViewSnapshot& view);
  void renderDashboard(const RemoteViewSnapshot& view);
  void drawWeatherIcon(
      std::int16_t x, std::int16_t y, std::uint16_t size,
      const String& icon, std::uint16_t background) const;
  String dateText(const String& device_time) const;
  void renderWeather(const RemoteViewSnapshot& view);
  void renderTrain(const RemoteViewSnapshot& view);
  void renderMusic(const RemoteViewSnapshot& view);
  void renderGeneric(const RemoteViewSnapshot& view);
  void renderControls(const RemoteViewSnapshot& view, bool enabled);
  void renderControlFeedback(
      const RemoteViewSnapshot& view,
      bool enabled);
  String clockText(const String& device_time) const;

  BoardDisplay& display_;
  String last_signature_;
  std::int8_t pressed_zone_ = -1;
  std::int8_t feedback_zone_ = -1;
  TouchFeedback feedback_ = TouchFeedback::kNone;
  std::uint32_t feedback_until_ = 0;
  std::uint32_t pressed_since_ = 0;
  std::uint32_t last_action_ms_ = 0;
  bool pressed_armed_ = false;
  bool playback_controls_ = true;
};

