"""
Screen Capture Analyzer — captures screen regions and extracts per-zone colors.

Features:
  - Bottom-edge weighting (keyboard sits below screen)
  - Dominant color detection via histogram peaks
  - Zone overlap for smooth transitions
  - Custom capture region (full/bottom-half/custom)
  - Color modes: accurate, vibrant, hyper
"""

import logging
import time

import mss
import numpy as np

import config

logger = logging.getLogger(__name__)

# Color mode boost presets: (saturation_boost, brightness_scale)
COLOR_MODES = {
    "accurate": (1.0, 1.0),     # True to screen
    "vibrant":  (1.4, 1.15),    # Mild boost (default)
    "hyper":    (2.0, 1.4),     # Oversaturated, punchy (great for gaming)
}


def _dominant_color_bgr(pixels):
    """
    Find the most dominant (frequent) color in a pixel array using
    a fast histogram approach on quantized colors.
    Returns (B, G, R) as floats.
    """
    # Quantize to 32 levels per channel for binning (32^3 = 32768 bins)
    quant = (pixels // 8).astype(np.int32)
    # Pack into single int for histogram: B*1024 + G*32 + R
    keys = quant[:, :, 0] * 1024 + quant[:, :, 1] * 32 + quant[:, :, 2]
    flat_keys = keys.ravel()

    # Find the most frequent quantized color
    counts = np.bincount(flat_keys, minlength=32768)
    peak_key = np.argmax(counts)

    # Decode back to BGR (center of quantization bucket)
    qb = (peak_key // 1024) * 8 + 4
    qg = ((peak_key % 1024) // 32) * 8 + 4
    qr = (peak_key % 32) * 8 + 4

    return float(qb), float(qg), float(qr)


class ScreenCaptureAnalyzer:
    def __init__(self, num_zones=config.NUM_ZONES, smoothing=config.SMOOTHING_FACTOR):
        self.num_zones = num_zones
        self.smoothing = max(0.01, min(1.0, smoothing))
        self.sct = mss.mss()

        # Read screen config
        screen_cfg = config.SCREEN
        self.color_mode = screen_cfg.get("color_mode", "vibrant")
        self.bottom_weight = screen_cfg.get("bottom_weight", 0.7)
        self.zone_overlap = screen_cfg.get("zone_overlap", 0.15)
        self.capture_region = screen_cfg.get("capture_region", "full")

        # Resolve monitor
        full_mon = self.sct.monitors[config.MONITOR]
        self.monitor = self._resolve_region(full_mon)

        # Color mode boost factors
        sat_boost, bright_scale = COLOR_MODES.get(self.color_mode, COLOR_MODES["vibrant"])
        self.sat_boost = sat_boost
        self.bright_scale = bright_scale

        # EMA state (floats for sub-pixel precision)
        self.prev_colors = [(0.0, 0.0, 0.0) for _ in range(num_zones)]

        logger.info(f"Screen capture: mode={self.color_mode}, region={self.capture_region}, "
                    f"bottom_weight={self.bottom_weight}, overlap={self.zone_overlap}")

    def _resolve_region(self, mon):
        """Resolve capture area based on config."""
        if self.capture_region == "bottom":
            # Bottom half of screen
            half_h = mon["height"] // 2
            return {
                "left": mon["left"],
                "top": mon["top"] + half_h,
                "width": mon["width"],
                "height": half_h,
            }
        elif self.capture_region == "bottom_third":
            third_h = mon["height"] // 3
            return {
                "left": mon["left"],
                "top": mon["top"] + mon["height"] - third_h,
                "width": mon["width"],
                "height": third_h,
            }
        else:
            # Full screen
            return mon

    def get_zone_colors(self):
        """
        Captures the screen, divides into overlapping vertical zones,
        applies bottom-edge weighting and dominant color detection.
        Returns list of (R, G, B) tuples.
        """
        start_time = time.perf_counter()

        # Capture
        screenshot = self.sct.grab(self.monitor)
        img = np.array(screenshot)  # BGRA
        height, width, _ = img.shape

        # --- Build vertical weight mask (bottom rows weighted more) ---
        # Creates a weight array from top (low weight) to bottom (high weight)
        top_weight = 1.0 - self.bottom_weight
        row_weights = np.linspace(top_weight, 1.0, height).reshape(-1, 1, 1)

        # Apply weights to image
        weighted_img = img[:, :, :3].astype(np.float32) * row_weights

        zone_width = width // self.num_zones
        overlap_pixels = int(zone_width * self.zone_overlap)

        current_colors = []

        for i in range(self.num_zones):
            # --- Zone boundaries with overlap ---
            x_start = max(0, i * zone_width - overlap_pixels)
            x_end = min(width, (i + 1) * zone_width + overlap_pixels)
            if i == self.num_zones - 1:
                x_end = width

            zone_weighted = weighted_img[:, x_start:x_end, :]
            zone_raw = img[:, x_start:x_end, :3]

            # Downsample for speed
            sampled_w = zone_weighted[::4, ::4, :]
            sampled_raw = zone_raw[::4, ::4, :]

            # --- Dominant color (histogram peak) ---
            dom_b, dom_g, dom_r = _dominant_color_bgr(sampled_raw)

            # --- Weighted median (bottom-weighted) ---
            # Since we already applied row_weights, the mean of weighted pixels
            # naturally emphasizes the bottom of the screen
            wmean_bgr = np.mean(sampled_w, axis=(0, 1))
            # Normalize back by average weight
            avg_weight = np.mean(row_weights)
            wmean_bgr = wmean_bgr / avg_weight

            # --- Blend: 40% dominant + 60% weighted-average ---
            b = dom_b * 0.4 + wmean_bgr[0] * 0.6
            g = dom_g * 0.4 + wmean_bgr[1] * 0.6
            r = dom_r * 0.4 + wmean_bgr[2] * 0.6

            # --- Apply color mode boost ---
            r, g, b = self._apply_color_mode(r, g, b)

            # Minimum brightness
            r = max(config.MIN_BRIGHTNESS, r)
            g = max(config.MIN_BRIGHTNESS, g)
            b = max(config.MIN_BRIGHTNESS, b)

            # --- EMA smoothing ---
            prev_r, prev_g, prev_b = self.prev_colors[i]
            new_r = r * self.smoothing + prev_r * (1.0 - self.smoothing)
            new_g = g * self.smoothing + prev_g * (1.0 - self.smoothing)
            new_b = b * self.smoothing + prev_b * (1.0 - self.smoothing)

            new_r = max(0.0, min(255.0, new_r))
            new_g = max(0.0, min(255.0, new_g))
            new_b = max(0.0, min(255.0, new_b))

            self.prev_colors[i] = (new_r, new_g, new_b)
            current_colors.append((int(new_r), int(new_g), int(new_b)))

        elapsed = (time.perf_counter() - start_time) * 1000
        logger.debug(f"Capture & Process time: {elapsed:.1f}ms")

        return current_colors

    def _apply_color_mode(self, r, g, b):
        """Apply saturation boost and brightness scaling based on color mode."""
        max_val = max(r, g, b)
        min_val = min(r, g, b)

        if max_val > 10:
            # Saturation boost: expand channels away from midpoint
            sat_range = max_val - min_val
            if sat_range > 5 and self.sat_boost != 1.0:
                mid = (max_val + min_val) / 2.0
                r = mid + (r - mid) * self.sat_boost
                g = mid + (g - mid) * self.sat_boost
                b = mid + (b - mid) * self.sat_boost

            # Brightness scaling
            if self.bright_scale != 1.0:
                r *= self.bright_scale
                g *= self.bright_scale
                b *= self.bright_scale

        return max(0.0, min(255.0, r)), max(0.0, min(255.0, g)), max(0.0, min(255.0, b))

    def close(self):
        self.sct.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    analyzer = ScreenCaptureAnalyzer()
    try:
        colors = analyzer.get_zone_colors()
        logger.info(f"Captured Colors (L -> R): {colors}")
    finally:
        analyzer.close()
