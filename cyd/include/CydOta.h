#pragma once

#include <Arduino.h>

#include "CompanionConfig.h"

struct CydOtaStatus {
  bool available = false;
  bool checking = false;
  String current;
  String latest;
  std::size_t size = 0;
};

class CydOta {
 public:
  CydOtaStatus check(
      const CompanionConfig& config,
      const String& current_version,
      String& error);

  bool install(
      const CompanionConfig& config,
      const CydOtaStatus& status,
      String& error);

  bool pending() const;
  bool confirmAfterHealth(String& error);
  bool rollbackAfterTimeout(std::uint32_t pending_millis, String& error);
};
