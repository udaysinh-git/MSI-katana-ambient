"""
MSI Mystic Light MS-1565 Keyboard LED Controller via HID.

Protocol reverse-engineered by OpenRGB (MR !2619).
Requires Administrator privileges on Windows.
Device: VID 0x1462, PID 0x1601, Usage Page 0x00FF, Usage 0x01

The device uses 64-byte HID feature reports with report ID 0x02.
Two-step protocol:
  1. Zone select:  [0x02, 0x01, zone_mask, ...pad to 64]
  2. Color data:   FeaturePacket_MS1565 struct (see below)

Zone masks:
  Zone 1 = 0x01, Zone 2 = 0x02, Zone 3 = 0x04, Zone 4 = 0x08, All = 0x0F
"""

import hid
import logging
import time

import config

logger = logging.getLogger(__name__)

VID = 0x1462
PID = 0x1601

# Modes
MODE_OFF       = 0
MODE_STEADY    = 1
MODE_BREATHING = 2
MODE_CYCLE     = 3
MODE_WAVE      = 4

# Zone masks (bitmask)
ZONE_1   = 0x01
ZONE_2   = 0x02
ZONE_3   = 0x04
ZONE_4   = 0x08
ZONE_ALL = 0x0F

ZONE_MASKS = [ZONE_1, ZONE_2, ZONE_3, ZONE_4]

# Max keyframes for color animation
MAX_KEYFRAMES = 10


class MSIKeyboardHID:
    """Controls MSI MS-1565 keyboard LEDs via HID feature reports."""

    def __init__(self):
        self.dev = None
        # Track last-sent colors to avoid redundant HID writes (reduces flicker)
        self._last_sent = [(0, 0, 0)] * 4
        self._color_threshold = 5  # Min per-channel change to trigger an update
        # White balance from config
        wb = config.WHITE_BALANCE
        self._wb_r = wb.get("r", 1.0)
        self._wb_g = wb.get("g", 1.0)
        self._wb_b = wb.get("b", 1.0)

    def initialize(self):
        """Open the HID device."""
        try:
            self.dev = hid.device()
            self.dev.open(VID, PID)
            name = self.dev.get_product_string()
            logger.info(f"Opened HID device: {name}")
            return True
        except Exception as e:
            logger.error(f"Failed to open HID device (VID:0x{VID:04X} PID:0x{PID:04X}): {e}")
            logger.error("Make sure you're running as Administrator!")
            return False

    def _send_zone_select(self, zone_mask):
        """
        Step 1: Select which zone(s) the next command applies to.
        Sends: [report_id=0x02, 0x01, zone_mask, ...zeros] (64 bytes)
        """
        buf = [0x00] * 64
        buf[0] = 0x02   # report ID
        buf[1] = 0x01   # zone select command
        buf[2] = zone_mask
        return self.dev.send_feature_report(buf)

    def _build_color_packet(self, mode, r, g, b, speed_ms=750):
        """
        Step 2: Build the 64-byte FeaturePacket_MS1565.

        Struct layout (from OpenRGB):
          byte 0:  report_id  = 0x02
          byte 1:  packet_id  = 0x02
          byte 2:  mode
          byte 3:  speed2     (low byte of speed in 1/100s)
          byte 4:  speed1     (high byte of speed in 1/100s)
          byte 5:  unused     = 0x00
          byte 6:  unused     = 0x00
          byte 7:  unused     = 0x0F
          byte 8:  unused     = 0x01
          byte 9:  wave_dir   = 0x00
          bytes 10..49: color_keyframes (10 x 4 bytes each: time_frame, R, G, B)
          bytes 50..63: padding zeros
        """
        buf = [0x00] * 64
        buf[0] = 0x02  # report ID
        buf[1] = 0x02  # packet ID (color data)
        buf[2] = mode

        # Speed in 1/100 seconds (e.g., 750ms = 75 centiseconds)
        speed_cs = speed_ms // 10
        buf[3] = speed_cs & 0xFF         # speed2 (low byte)
        buf[4] = (speed_cs >> 8) & 0xFF  # speed1 (high byte)

        buf[5] = 0x00  # unused
        buf[6] = 0x00  # unused
        buf[7] = 0x0F  # unused (constant from OpenRGB)
        buf[8] = 0x01  # unused (constant from OpenRGB)
        buf[9] = 0x00  # wave_dir

        # Color keyframe 0: time_frame=0, R, G, B
        buf[10] = 0x00  # time_frame
        buf[11] = r
        buf[12] = g
        buf[13] = b

        # Color keyframe 1 (wrap-around): time_frame=100, same color
        buf[14] = 100   # time_frame
        buf[15] = r
        buf[16] = g
        buf[17] = b

        return buf

    def set_zone_color(self, zone_index, r, g, b):
        """
        Set a single zone to a static color.
        zone_index: 0-3
        """
        if not self.dev:
            return False

        if zone_index < 0 or zone_index >= 4:
            logger.warning(f"Invalid zone index: {zone_index}")
            return False

        zone_mask = ZONE_MASKS[zone_index]

        # Apply white balance correction
        r = int(min(255, r * self._wb_r))
        g = int(min(255, g * self._wb_g))
        b = int(min(255, b * self._wb_b))

        # Step 1: Select the zone
        res1 = self._send_zone_select(zone_mask)
        if res1 == -1:
            logger.warning(f"Zone select failed for zone {zone_index}")
            return False

        # Small delay so the controller can process the zone select
        time.sleep(0.002)

        # Step 2: Send static color
        packet = self._build_color_packet(MODE_STEADY, r, g, b)
        res2 = self.dev.send_feature_report(packet)
        if res2 == -1:
            logger.warning(f"Color packet failed for zone {zone_index}")
            return False

        # Small delay between zones
        time.sleep(0.002)
        return True

    def set_all_zones(self, colors):
        """
        Set all 4 zones at once.
        Skips zones where color hasn't changed enough to reduce flicker.
        colors: list of 4 (R, G, B) tuples
        """
        if not self.dev:
            return False

        success = True
        for i, (r, g, b) in enumerate(colors):
            # Skip if color hasn't changed enough
            lr, lg, lb = self._last_sent[i]
            if (abs(r - lr) < self._color_threshold and
                abs(g - lg) < self._color_threshold and
                abs(b - lb) < self._color_threshold):
                continue

            if self.set_zone_color(i, r, g, b):
                self._last_sent[i] = (r, g, b)
            else:
                success = False
        return success

    def set_all_zones_single_color(self, r, g, b):
        """Set ALL zones to the same color in one operation."""
        if not self.dev:
            return False

        res1 = self._send_zone_select(ZONE_ALL)
        packet = self._build_color_packet(MODE_STEADY, r, g, b)
        res2 = self.dev.send_feature_report(packet)
        return res1 != -1 and res2 != -1

    def turn_off(self):
        """Turn off all keyboard LEDs."""
        if not self.dev:
            return False

        self._send_zone_select(ZONE_ALL)
        packet = self._build_color_packet(MODE_OFF, 0, 0, 0)
        return self.dev.send_feature_report(packet) != -1

    def close(self):
        """Close the HID device."""
        if self.dev:
            self.dev.close()
            self.dev = None


if __name__ == "__main__":
    import time

    logging.basicConfig(level=logging.INFO)

    kb = MSIKeyboardHID()
    if not kb.initialize():
        exit(1)

    print("Setting zones to: Red, Green, Blue, Yellow")
    kb.set_all_zones([
        (255, 0, 0),     # Zone 1: Red
        (0, 255, 0),     # Zone 2: Green
        (0, 0, 255),     # Zone 3: Blue
        (255, 255, 0),   # Zone 4: Yellow
    ])
    print("Done! Check your keyboard.")
    time.sleep(3)

    print("Setting all to white...")
    kb.set_all_zones_single_color(255, 255, 255)
    time.sleep(3)

    print("Turning off...")
    kb.turn_off()

    kb.close()
