"""
Audio Reactive Analyzer — captures system audio and maps it to keyboard zones.

Uses sounddevice to capture system audio (via Stereo Mix or configured device).
Performs FFT to split audio into 4 frequency bands:
  Zone 1 (Left)  = Bass       (20-250 Hz)
  Zone 2 (Mid-L) = Low-Mid    (250-1000 Hz)
  Zone 3 (Mid-R) = High-Mid   (1000-4000 Hz)
  Zone 4 (Right) = Treble     (4000-16000 Hz)

Features:
  - Beat detection with flash on kicks
  - Running normalization (adapts to volume over time)
  - Peak hold with smooth decay
  - Dynamic color shifting based on frequency content
"""

import logging
import threading
import numpy as np
import sounddevice as sd

import config

logger = logging.getLogger(__name__)

# Frequency band edges in Hz (4 bands for 4 keyboard zones)
BAND_EDGES = [20, 250, 1000, 4000, 16000]

# Spectrum color palette per zone — base hues
SPECTRUM_HUES = [
    (255, 20,  50),   # Zone 1 (Bass):     Deep Red
    (255, 120, 0),    # Zone 2 (Low-Mid):  Warm Orange
    (0,   180, 255),  # Zone 3 (High-Mid): Electric Cyan
    (130, 0,   255),  # Zone 4 (Treble):   Vivid Purple
]

# "Energy" color palette — shifts from warm to cool with overall energy
ENERGY_GRADIENT = [
    (255, 40,  0),    # Low energy:  deep red/orange
    (255, 200, 0),    # Medium:      gold
    (0,   255, 100),  # High:        green
    (0,   150, 255),  # Very high:   electric blue
]


def _lerp_color(c1, c2, t):
    """Linearly interpolate between two RGB colors. t in [0, 1]."""
    t = max(0.0, min(1.0, t))
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def _energy_to_gradient_color(energy, gradient):
    """Map energy [0..1] to a color along a gradient list."""
    n = len(gradient) - 1
    idx = energy * n
    lo = int(idx)
    hi = min(lo + 1, n)
    frac = idx - lo
    return _lerp_color(gradient[lo], gradient[hi], frac)


class AudioAnalyzer:
    """Captures system audio and returns per-zone RGB colors with beat-reactive effects."""

    def __init__(self):
        audio_cfg = config.AUDIO
        self.sensitivity = audio_cfg.get("sensitivity", 1.5)
        self.base_brightness = audio_cfg.get("base_brightness", 30)
        self.color_scheme = audio_cfg.get("color_scheme", "spectrum")
        self.num_zones = config.NUM_ZONES

        # Audio parameters
        self.sample_rate = 44100
        self.block_size = 2048
        self.channels = 2

        # Shared stereo buffers (written by callback thread, read by main)
        self._lock = threading.Lock()
        self._left_buffer = np.zeros(self.block_size, dtype=np.float32)
        self._right_buffer = np.zeros(self.block_size, dtype=np.float32)

        # Stereo weights per zone: how much left vs right channel affects each zone
        # Zone 1 (far left) = 100% left, Zone 4 (far right) = 100% right
        self._stereo_weights = [
            (1.0,  0.0),   # Zone 1: 100% left
            (0.7,  0.3),   # Zone 2: 70% left, 30% right
            (0.3,  0.7),   # Zone 3: 30% left, 70% right
            (0.0,  1.0),   # Zone 4: 100% right
        ]
        self._stream = None

        # --- Visualization state ---
        # Smoothed energy per zone (EMA)
        self._smooth_energy = [0.0] * self.num_zones
        # Peak hold per zone (decays slowly for a trailing glow)
        self._peak_energy = [0.0] * self.num_zones
        # Running max for normalization (adapts over time)
        self._running_max = 0.001
        # Beat detection: track previous bass energy for onset detection
        self._prev_bass = 0.0
        self._beat_flash = 0.0  # 1.0 on beat, decays to 0

    def _find_loopback_device(self):
        """Find a device that captures system audio (what you hear)."""
        audio_cfg = config.AUDIO

        # If user specified a device ID in config, use that directly
        device_id = audio_cfg.get("device_id", None)
        if device_id is not None:
            dev = sd.query_devices(device_id)
            logger.info(f"Using configured audio device [{device_id}]: {dev['name']}")
            return device_id

        devices = sd.query_devices()

        # Priority 1: "Stereo Mix" — captures system audio on Realtek
        for i, dev in enumerate(devices):
            name = dev['name'].lower()
            if 'stereo mix' in name and dev['max_input_channels'] > 0:
                logger.info(f"Found Stereo Mix [{i}]: {dev['name']}")
                return i

        # Priority 2: Any device with "loopback" in the name
        for i, dev in enumerate(devices):
            name = dev['name'].lower()
            if 'loopback' in name and dev['max_input_channels'] > 0:
                logger.info(f"Found loopback device [{i}]: {dev['name']}")
                return i

        # Priority 3: "What U Hear" (some sound cards use this name)
        for i, dev in enumerate(devices):
            name = dev['name'].lower()
            if 'what u hear' in name and dev['max_input_channels'] > 0:
                logger.info(f"Found What U Hear [{i}]: {dev['name']}")
                return i

        logger.warning("No system audio capture device found. "
                        "Run 'python list_audio.py' and set audio.device_id in config.yaml")
        return None

    def initialize(self):
        """Start capturing system audio via WASAPI loopback."""
        device_id = self._find_loopback_device()

        try:
            if device_id is not None:
                dev_info = sd.query_devices(device_id)
                self.sample_rate = int(dev_info['default_samplerate'])
                out_ch = dev_info.get('max_output_channels', 0)
                in_ch = dev_info.get('max_input_channels', 0)
                self.channels = min(2, max(out_ch, in_ch, 1))
                logger.info(f"Device channels: in={in_ch}, out={out_ch}, using={self.channels}")

            # Build configs: try without extra_settings first (Stereo Mix is WDM-KS),
            # then with WASAPI settings for WASAPI loopback devices
            wasapi_settings = None
            try:
                wasapi_settings = sd.WasapiSettings(exclusive=False)
            except (AttributeError, TypeError):
                pass

            configs = []
            for ch in [self.channels, 2, 1]:
                configs.append((ch, None))
                if wasapi_settings:
                    configs.append((ch, wasapi_settings))

            for ch, extra in configs:
                try:
                    self._stream = sd.InputStream(
                        device=device_id,
                        channels=ch,
                        samplerate=self.sample_rate,
                        blocksize=self.block_size,
                        callback=self._audio_callback,
                        dtype='float32',
                        extra_settings=extra,
                    )
                    self._stream.start()
                    self.channels = ch
                    tag = "WASAPI" if extra else "standard"
                    logger.info(f"Audio capture started ({tag}, device={device_id}, "
                                f"rate={self.sample_rate}, ch={ch})")
                    return True
                except Exception as e:
                    logger.debug(f"Failed ch={ch} extra={extra is not None}: {e}")
                    self._stream = None
                    continue

            # Last resort: default input
            logger.warning("All attempts failed. Using default input (microphone)...")
            self._stream = sd.InputStream(
                channels=1,
                samplerate=self.sample_rate,
                blocksize=self.block_size,
                callback=self._audio_callback,
                dtype='float32',
            )
            self._stream.start()
            self.channels = 1
            logger.info("Audio capture started using default input (microphone)")
            return True

        except Exception as e:
            logger.error(f"Failed to start audio capture: {e}")
            logger.error("Make sure you have a working audio device.")
            return False

    def _audio_callback(self, indata, frames, time_info, status):
        """Called by sounddevice on each audio block (runs in a separate thread)."""
        if status:
            logger.debug(f"Audio callback status: {status}")
        # Keep left and right channels separate for stereo panning
        with self._lock:
            if indata.shape[1] > 1:
                self._left_buffer = indata[:, 0].copy()
                self._right_buffer = indata[:, 1].copy()
            else:
                # Mono source — same data for both
                self._left_buffer = indata[:, 0].copy()
                self._right_buffer = indata[:, 0].copy()

    def _get_band_energies(self, data):
        """Run FFT on audio data and return per-band RMS energies."""
        windowed = data * np.hanning(len(data))
        fft_data = np.abs(np.fft.rfft(windowed))
        freqs = np.fft.rfftfreq(len(windowed), 1.0 / self.sample_rate)

        energies = []
        for band_idx in range(len(BAND_EDGES) - 1):
            lo = BAND_EDGES[band_idx]
            hi = BAND_EDGES[band_idx + 1]
            mask = (freqs >= lo) & (freqs < hi)
            if np.any(mask):
                energy = np.sqrt(np.mean(fft_data[mask] ** 2))
            else:
                energy = 0.0
            energies.append(energy)
        return energies

    def get_zone_colors(self):
        """Analyze current audio and return 4 (R,G,B) tuples with stereo-aware reactive effects."""
        with self._lock:
            left_data = self._left_buffer.copy()
            right_data = self._right_buffer.copy()

        # --- Per-channel FFT ---
        left_energies = self._get_band_energies(left_data)
        right_energies = self._get_band_energies(right_data)

        # --- Blend per zone based on stereo position ---
        raw_energies = []
        for zone_idx in range(self.num_zones):
            lw, rw = self._stereo_weights[zone_idx % len(self._stereo_weights)]
            blended = []
            for band_idx in range(len(BAND_EDGES) - 1):
                e = left_energies[band_idx] * lw + right_energies[band_idx] * rw
                blended.append(e)
            # Each zone uses its corresponding band, but weighted by stereo position
            raw_energies.append(blended[zone_idx] if zone_idx < len(blended) else 0.0)

        # --- Running normalization (adapts to volume over ~2 seconds) ---
        frame_max = max(max(raw_energies), 1e-6)
        # Slowly track the max: rise quickly, fall slowly
        if frame_max > self._running_max:
            self._running_max = self._running_max * 0.5 + frame_max * 0.5  # fast rise
        else:
            self._running_max = self._running_max * 0.995 + frame_max * 0.005  # slow decay

        # Normalize to [0, 1] with sensitivity
        energies = [min(1.0, (e / self._running_max) * self.sensitivity) for e in raw_energies]

        # --- Beat detection (bass onset) ---
        bass_energy = energies[0]
        bass_delta = bass_energy - self._prev_bass
        self._prev_bass = bass_energy

        # Trigger beat flash on sharp bass increase
        if bass_delta > 0.3:
            self._beat_flash = 1.0
        else:
            self._beat_flash *= 0.85  # fast decay

        # --- Smooth energies (EMA) with fast attack, slow release ---
        for i in range(len(energies)):
            if energies[i] > self._smooth_energy[i]:
                # Fast attack
                self._smooth_energy[i] = self._smooth_energy[i] * 0.3 + energies[i] * 0.7
            else:
                # Slow release
                self._smooth_energy[i] = self._smooth_energy[i] * 0.8 + energies[i] * 0.2

        # --- Peak hold with decay ---
        for i in range(len(energies)):
            if self._smooth_energy[i] > self._peak_energy[i]:
                self._peak_energy[i] = self._smooth_energy[i]
            else:
                self._peak_energy[i] *= 0.96  # slow fade

        # --- Map to RGB colors ---
        colors = []
        base_b = self.base_brightness / 255.0

        if self.color_scheme == "energy":
            # All zones pulse with the same dynamic color based on overall energy
            avg_energy = sum(self._smooth_energy) / len(self._smooth_energy)
            color = _energy_to_gradient_color(avg_energy, ENERGY_GRADIENT)

            # Beat flash: boost brightness on kick
            for i in range(self.num_zones):
                peak = self._peak_energy[i] if i < len(self._peak_energy) else avg_energy
                intensity = base_b + peak * (1.0 - base_b)
                intensity = min(1.0, intensity + self._beat_flash * 0.3)

                r = int(min(255, color[0] * intensity))
                g = int(min(255, color[1] * intensity))
                b = int(min(255, color[2] * intensity))
                colors.append((r, g, b))
        else:
            # Spectrum mode: each zone has its own hue, dynamic brightness
            for i in range(self.num_zones):
                energy = self._smooth_energy[i] if i < len(self._smooth_energy) else 0.0
                peak = self._peak_energy[i] if i < len(self._peak_energy) else 0.0

                # Use peak for brightness (gives a trailing glow effect)
                intensity = base_b + peak * (1.0 - base_b)

                # Beat flash: bass hits brighten everything
                if i == 0:
                    # Zone 1 (bass) gets the strongest flash
                    intensity = min(1.0, intensity + self._beat_flash * 0.5)
                else:
                    # Other zones get a subtle sympathetic flash
                    intensity = min(1.0, intensity + self._beat_flash * 0.15)

                # Shift hue slightly based on energy (more energy → warmer)
                base_color = SPECTRUM_HUES[i % len(SPECTRUM_HUES)]
                warm_shift = (
                    min(255, base_color[0] + int(energy * 40)),
                    max(0,   base_color[1] - int(energy * 20)),
                    max(0,   base_color[2] - int(energy * 30)),
                )

                r = int(min(255, warm_shift[0] * intensity))
                g = int(min(255, warm_shift[1] * intensity))
                b = int(min(255, warm_shift[2] * intensity))
                colors.append((max(0, r), max(0, g), max(0, b)))

        return colors

    def close(self):
        """Stop and close the audio stream."""
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None


if __name__ == "__main__":
    import time

    logging.basicConfig(level=logging.DEBUG)
    analyzer = AudioAnalyzer()
    if analyzer.initialize():
        print("Listening to audio... Play some music!")
        try:
            while True:
                colors = analyzer.get_zone_colors()
                bars = ""
                for i, (r, g, b) in enumerate(colors):
                    level = max(r, g, b)
                    bar = "█" * (level // 8)
                    bars += f"  Z{i+1}: ({r:3d},{g:3d},{b:3d}) {bar}"
                print(f"\r{bars}", end="", flush=True)
                time.sleep(0.04)
        except KeyboardInterrupt:
            pass
        finally:
            print()
            analyzer.close()
