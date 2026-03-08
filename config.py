"""
Configuration loader — reads from config.yaml with sensible defaults.
"""
import logging
import os
import yaml

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")

_DEFAULTS = {
    "mode": "screen",
    "target_fps": 25,
    "smoothing_factor": 0.15,
    "num_zones": 4,
    "monitor": 1,
    "min_brightness": 10,
    "white_balance": {"r": 1.0, "g": 1.0, "b": 1.0},
    "screen": {
        "color_mode": "vibrant",
        "bottom_weight": 0.7,
        "zone_overlap": 0.15,
        "capture_region": "full",
    },
    "audio": {
        "color_scheme": "spectrum",
        "sensitivity": 1.5,
        "base_brightness": 30,
    },
}

def _load():
    cfg = dict(_DEFAULTS)
    try:
        with open(_CONFIG_PATH, "r") as f:
            user_cfg = yaml.safe_load(f) or {}
        # Merge sub-dicts properly
        for key in ("audio", "screen", "white_balance"):
            if key in user_cfg:
                cfg[key] = {**cfg[key], **user_cfg.pop(key)}
        cfg.update(user_cfg)
    except FileNotFoundError:
        pass
    return cfg

_cfg = _load()

# Module-level constants
MODE             = _cfg["mode"]
TARGET_FPS       = _cfg["target_fps"]
SMOOTHING_FACTOR = _cfg["smoothing_factor"]
NUM_ZONES        = _cfg["num_zones"]
MONITOR          = _cfg["monitor"]
MIN_BRIGHTNESS   = _cfg["min_brightness"]
WHITE_BALANCE    = _cfg["white_balance"]
SCREEN           = _cfg["screen"]
AUDIO            = _cfg["audio"]

# Logging
LOG_LEVEL  = logging.INFO
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
