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

class CompanionUi {
 public:
  explicit CompanionUi(BoardDisplay& display);
  void render(
      const RemoteViewSnapshot& view,
      const String& error,
      bool connected);
  TouchAction takeAction(const RemoteViewSnapshot& view, bool connected);

 private:
  void renderScene(const RemoteViewSnapshot& view);
  void renderClock(const RemoteViewSnapshot& view);
  void renderWeather(const RemoteViewSnapshot& view);
  void renderTrain(const RemoteViewSnapshot& view);
  void renderMusic(const RemoteViewSnapshot& view);
  void renderGeneric(const RemoteViewSnapshot& view);
  void renderControls(const RemoteViewSnapshot& view, bool enabled);
  String clockText(const String& device_time) const;

  BoardDisplay& display_;
};

