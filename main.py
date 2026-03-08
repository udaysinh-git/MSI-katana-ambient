import time
import logging
import sys
from rich.live import Live
from rich.table import Table
from rich import box

import config
from msi_kb_hid import MSIKeyboardHID

logging.basicConfig(level=config.LOG_LEVEL, format=config.LOG_FORMAT)
logger = logging.getLogger(__name__)

MODE_LABELS = {
    "screen": "[SCREEN]",
    "audio":  "[AUDIO]",
    "hybrid": "[HYBRID]",
}

def generate_table(colors, fps, mode):
    """Generates a rich table to visualize the current colors."""
    label = MODE_LABELS.get(mode, mode.upper())
    table = Table(
        title=f"MSI Ambient Light Sync {label} (FPS: {fps:.1f})",
        box=box.ROUNDED
    )
    table.add_column("Zone 1 (L)", justify="center", style="bold")
    table.add_column("Zone 2 (ML)", justify="center", style="bold")
    table.add_column("Zone 3 (MR)", justify="center", style="bold")
    table.add_column("Zone 4 (R)", justify="center", style="bold")
    
    rich_bg = [f"[bold white on rgb({r},{g},{b})]" for r, g, b in colors]
    
    row_data = [
        f"{bg} RGB({r:03d},{g:03d},{b:03d}) [/]" 
        for (r, g, b), bg in zip(colors, rich_bg)
    ]
    table.add_row(*row_data)
    return table


def blend_hybrid(screen_colors, audio_colors, audio_weight=0.4):
    """
    Blend screen and audio colors for hybrid mode.
    
    Strategy: Use screen color as the base hue, then modulate
    brightness based on audio energy. The audio "pumps" the screen colors.
    """
    blended = []
    for (sr, sg, sb), (ar, ag, ab) in zip(screen_colors, audio_colors):
        # Audio brightness factor: how bright the audio wants this zone
        audio_brightness = max(ar, ag, ab) / 255.0

        # Boost screen color by audio energy
        # When music is loud, colors get brighter; when quiet, they dim slightly
        boost = 1.0 + (audio_brightness - 0.3) * audio_weight * 2.0
        boost = max(0.5, min(1.5, boost))

        r = int(min(255, sr * boost))
        g = int(min(255, sg * boost))
        b = int(min(255, sb * boost))
        blended.append((max(0, r), max(0, g), max(0, b)))
    return blended


def main():
    mode = config.MODE

    # Initialize HID keyboard controller
    kb = MSIKeyboardHID()
    if not kb.initialize():
        logger.error("Failed to open keyboard HID device. Run as Administrator!")
        sys.exit(1)

    # Initialize analyzers based on mode
    screen_analyzer = None
    audio_analyzer = None

    if mode in ("screen", "hybrid"):
        from screen_capture import ScreenCaptureAnalyzer
        screen_analyzer = ScreenCaptureAnalyzer()

    if mode in ("audio", "hybrid"):
        from audio_capture import AudioAnalyzer
        audio_analyzer = AudioAnalyzer()
        if not audio_analyzer.initialize():
            if mode == "hybrid":
                logger.warning("Audio capture failed — falling back to screen-only mode.")
                mode = "screen"
                audio_analyzer = None
            else:
                logger.error("Failed to start audio capture. Exiting.")
                sys.exit(1)

    target_frame_time = 1.0 / config.TARGET_FPS
    
    logger.info(f"Starting ambient light sync (mode: {mode}). Press Ctrl+C to stop.")
    
    try:
        with Live(generate_table([(0,0,0)]*config.NUM_ZONES, 0, mode), refresh_per_second=10) as live:
            frames_rendered = 0
            fps_start = time.perf_counter()
            current_fps = 0.0

            while True:
                start_time = time.perf_counter()
                
                # Get colors based on mode
                if mode == "hybrid":
                    screen_colors = screen_analyzer.get_zone_colors()
                    audio_colors = audio_analyzer.get_zone_colors()
                    colors = blend_hybrid(screen_colors, audio_colors)
                elif mode == "audio":
                    colors = audio_analyzer.get_zone_colors()
                else:
                    colors = screen_analyzer.get_zone_colors()
                
                # Send to keyboard via HID
                kb.set_all_zones(colors)
                
                frames_rendered += 1
                now = time.perf_counter()
                if now - fps_start >= 1.0:
                    current_fps = frames_rendered / (now - fps_start)
                    frames_rendered = 0
                    fps_start = now
                
                # Update TUI
                live.update(generate_table(colors, current_fps, mode))
                
                # FPS limiter
                elapsed = time.perf_counter() - start_time
                sleep_time = target_frame_time - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

    except KeyboardInterrupt:
        logger.info("\nStopping ambient light sync.")
    finally:
        if screen_analyzer:
            screen_analyzer.close()
        if audio_analyzer:
            audio_analyzer.close()
        kb.close()

if __name__ == "__main__":
    main()
