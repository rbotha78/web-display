#pragma once

#include <cstdint>

namespace board_pins {

constexpr std::uint8_t kTftCs = 15;
constexpr std::uint8_t kTftDc = 2;
constexpr std::uint8_t kTftMosi = 13;
constexpr std::uint8_t kTftMiso = 12;
constexpr std::uint8_t kTftClock = 14;
constexpr std::uint8_t kBacklight = 21;

constexpr std::uint8_t kTouchCs = 33;
constexpr std::uint8_t kTouchMosi = 32;
constexpr std::uint8_t kTouchMiso = 39;
constexpr std::uint8_t kTouchClock = 25;
constexpr std::uint8_t kTouchInterrupt = 36;

}  // namespace board_pins

