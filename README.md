# MSI Katana Ambient Light Sync

Sync your MSI laptop keyboard RGB lighting with the colors on your screen — an Ambilight effect for your keyboard.

![Demo](https://img.shields.io/badge/Status-Working-brightgreen)

## How It Works

1. **Screen Capture** — Captures your screen and divides it into 4 vertical zones
2. **Color Extraction** — Calculates the dominant color of each zone using median + percentile blending
3. **HID Control** — nds colors directly to the keyboard via USB HID feature reports

## Supported Hardware

- **MSI Katana 17 B12VFK** (tested)
- Should work with other MSI laptops using the `MysticLight MS-1565` HID device (VID: `0x1462`, PID: `0x1601`)

## Requirements

- Windows 10/11
- Python 3.10+
- **Administrator privileges** (required for HID device access)
- MSI Center installed (the `LEDKeeper2.exe` service must be running)

## Installation

```bash
pip install -r requirements.txt
```

> **Disclaimer:** I am not responsible for any damage or loss if running this software somehow affects your system. Use at your own risk.

## Usage

**Must be run as Administrator:**

```bash
python main.py
```

Or use the batch file (right-click → Run as Administrator):

```bash
run.bat
```

## Configuration

Edit `config.yaml` to adjust. See all settings below.

### Performance Presets

Copy one of these profiles into your `config.yaml` depending on your hardware:

#### [LITE] Low CPU (~15 FPS — for older/budget laptops)

```yaml
target_fps: 15
smoothing_factor: 0.1
screen:
  color_mode: "accurate"
  bottom_weight: 0.5
  zone_overlap: 0.0
  capture_region: "bottom_third"
```

#### [MEDIUM] Balanced (~25 FPS — recommended default)

```yaml
target_fps: 25
smoothing_factor: 0.15
screen:
  color_mode: "vibrant"
  bottom_weight: 0.7
  zone_overlap: 0.15
  capture_region: "full"
```

#### [BEST] High Quality (~30 FPS — for gaming rigs)

```yaml
target_fps: 30
smoothing_factor: 0.25
screen:
  color_mode: "hyper"
  bottom_weight: 0.7
  zone_overlap: 0.15
  capture_region: "full"
```

#### [REALTIME] Zero Delay (not recommended — high CPU, may flicker)

```yaml
target_fps: 60
smoothing_factor: 1.0
screen:
  color_mode: "hyper"
  bottom_weight: 0.7
  zone_overlap: 0.0
  capture_region: "bottom"
```

> **Tip:** Higher `target_fps` + higher `smoothing_factor` = smoother but more CPU. Lower `zone_overlap` and `"bottom_third"` capture region save processing time. `"accurate"` color mode skips the saturation/brightness pass entirely. `[REALTIME]` removes all smoothing — colors change instantly but may cause visible flicker on the keyboard.

### General Settings

| Setting            | Default    | Description                                                   |
| ------------------ | ---------- | ------------------------------------------------------------- |
| `mode`             | `"screen"` | `"screen"`, `"audio"`, or `"hybrid"` (beta — not recommended) |
| `target_fps`       | 25         | Update rate (higher = smoother, more CPU)                     |
| `smoothing_factor` | 0.15       | Color transition smoothing (lower = smoother)                 |
| `num_zones`        | 4          | Number of keyboard zones                                      |
| `monitor`          | 1          | Which monitor to capture (screen mode only)                   |
| `min_brightness`   | 10         | Minimum LED brightness (0-255)                                |

### White Balance (`white_balance:`)

| Setting | Default | Description                                       |
| ------- | ------- | ------------------------------------------------- |
| `r`     | 1.0     | Red multiplier (0.0-1.0)                          |
| `g`     | 1.0     | Green multiplier (0.0-1.0)                        |
| `b`     | 1.0     | Blue multiplier — lower to fix blue-tinted whites |

### Screen Settings (`screen:`)

| Setting          | Default     | Description                                             |
| ---------------- | ----------- | ------------------------------------------------------- |
| `color_mode`     | `"vibrant"` | `"accurate"`, `"vibrant"`, or `"hyper"`                 |
| `bottom_weight`  | 0.7         | Bottom-edge emphasis (0.0 = uniform, 1.0 = bottom only) |
| `zone_overlap`   | 0.15        | Zone blending overlap (0.0-0.5)                         |
| `capture_region` | `"full"`    | `"full"`, `"bottom"`, or `"bottom_third"`               |

### Audio Settings (`audio:`)

| Setting           | Default      | Description                                                    |
| ----------------- | ------------ | -------------------------------------------------------------- |
| `color_scheme`    | `"spectrum"` | `"spectrum"` (hue per zone) or `"energy"` (white pulse)        |
| `sensitivity`     | 1.5          | Reactivity multiplier (higher = more reactive)                 |
| `base_brightness` | 30           | Brightness when audio is silent                                |
| `device_id`       | auto         | Audio device index (run `python list_audio.py` to see devices) |

## Project Structure

```
├── main.py              # Main application loop + TUI
├── msi_kb_hid.py        # MSI keyboard HID controller (OpenRGB protocol)
├── screen_capture.py    # Screen capture + color extraction
├── audio_capture.py     # Audio reactive analyzer (FFT + stereo panning)
├── config.py            # Configuration loader
├── config.yaml          # User settings
├── requirements.txt     # Python dependencies
└── run.bat              # Quick-start batch file
```

## Technical Details

- **HID Protocol**: OpenRGB-derived protocol for MSI MS-1565 keyboards (64-byte feature reports, report ID `0x02`)
- **Screen Colors**: Dominant color histogram + bottom-weighted average with configurable saturation boost
- **Audio Reactive**: Stereo-aware FFT splits audio into 4 bands (Bass → Treble) with beat detection, peak hold, and dynamic color shifting
- **Anti-Flicker**: Delta threshold skips HID writes when colors haven't changed enough
- **White Balance**: Per-channel RGB correction to compensate for LED color bias

## Why Not the Mystic Light SDK?

MSI provides a [Mystic Light SDK](https://www.msi.com/Landing/mystic-light-rgb-gaming-pc/mystic-light-sdk) for controlling RGB lighting. I tried it — here's why it didn't work for laptop keyboards:

1. **The SDK only exposes `MSI_MB` (motherboard)** on the Katana 17, with just 1 LED zone. The keyboard is **not registered** as a Mystic Light device.
2. **Requires Administrator** — without it, every SDK call (`GetDeviceInfo`, `SetLedColor`) times out with error `-2`.
3. **Calling convention mismatch** — the SDK uses `cdecl`, but many Python examples use `ctypes.WinDLL` (stdcall), causing silent hangs.
4. **COM/BSTR marshalling issues** — `SAFEARRAY` and `BSTR` parameters are painful to handle from Python's `ctypes`.

On MSI laptops, the keyboard LEDs are controlled by the **Embedded Controller** via USB HID, managed by `LEDKeeper2.exe`. The HID device (`MysticLight MS-1565`, VID:`0x1462` PID:`0x1601`) accepts 64-byte feature reports directly — no SDK needed. The protocol was reverse-engineered by the [OpenRGB](https://openrgb.org/) project.

## How to Reverse Engineer for Your Laptop

If this program doesn't work for your MSI laptop, you may need to reverse engineer the USB HID commands for keyboard lighting.

### Step 1 — Capture USB Traffic

You need to record what MSI Center sends to your keyboard when you change lighting.

1. Download and install [Wireshark](https://www.wireshark.org/) — during installation, make sure to check **USBPcap** (it's an option in the installer).
2. Open Wireshark, and you'll see USB capture interfaces listed (e.g., `USBPcap1`, `USBPcap2`). Pick the one your keyboard is on — if you're not sure, try each one.
3. Start the capture, then open **MSI Center → Mystic Light** and change the keyboard color (e.g., set all zones to solid red, then solid green, then solid blue).
4. Stop the capture in Wireshark after you've made a few color changes.

### Step 2 — Isolate HID Reports

Now filter out the noise and find the actual lighting commands.

1. In Wireshark's filter bar, type:

   ```
   usb.transfer_type == 0x02 && usb.bmRequestType == 0x21
   ```

   This filters for **SET_REPORT** requests (HID feature reports sent to the device). If that doesn't show results, try:

   ```
   usbhid.setup.bRequest == 0x09
   ```

2. Look for packets going **to** the device (direction = `host → device`).
3. Click on a packet, expand the data section at the bottom, and look at the **HID Data** or **SET_REPORT Data** bytes — this is the raw payload being sent.
4. Note the **device address** and **endpoint** so you can filter only traffic for that specific device.

> **Tip:** Change the color to pure red `(255,0,0)`, capture, then pure green `(0,255,0)`, then pure blue `(0,0,255)`. This makes it very easy to spot where RGB bytes appear in the payload — look for `FF 00 00`, `00 FF 00`, and `00 00 FF`.

### Step 3 — Analyze the Data

Compare multiple captured payloads to decode the command structure.

1. Right-click a packet → **Copy → …as Hex Stream** to get the raw bytes.
2. Put several captures side by side (red, green, blue) and look for:
   - **Fixed bytes** — these are the command header (report ID, command type, etc.)
   - **Changing bytes** — these are likely the RGB color values
   - **Zone identifiers** — if you changed one zone at a time, spot which byte controls which zone

3. A typical MSI HID lighting payload looks something like:

   ```
   [Report ID] [Command] [Zone] [R] [G] [B] [Mode] [Speed] [Padding...]
   ```

   But this varies by model — your job is to figure out your device's specific layout.

4. Test your theory by replaying modified bytes with the Python script below.

### Step 4 — Implement in Python

Use a Python library like `hid` or `hidapi` to send these commands to your keyboard.

Here's a minimal example to get you started:

```python
import hid

# Step 1: Find your keyboard's HID device
# Replace VID and PID with your device's values
VENDOR_ID  = 0x1462  # MSI
PRODUCT_ID = 0x1601  # Replace with your keyboard's PID

# List all HID devices to find yours
for dev in hid.enumerate():
    if dev["vendor_id"] == VENDOR_ID:
        print(f"Found: {dev['product_string']}")
        print(f"  VID: {hex(dev['vendor_id'])}, PID: {hex(dev['product_id'])}")
        print(f"  Usage Page: {hex(dev['usage_page'])}, Usage: {hex(dev['usage'])}")
        print(f"  Path: {dev['path']}")
        print()

# Step 2: Open the device and send a feature report
# Use the path from the enumeration above
device = hid.Device(vid=VENDOR_ID, pid=PRODUCT_ID)

# Step 3: Send a feature report
# The payload below is an EXAMPLE — replace it with the bytes
# you captured from Wireshark. Feature reports start with the
# report ID as the first byte.
payload = bytes([
    0x0D,  # Report ID (varies by device)
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    # ... fill in the rest from your Wireshark capture
])

device.send_feature_report(payload)
print("Feature report sent!")

device.close()
```

> **Tip:** Run `hid.enumerate()` first to discover your device's VID, PID, usage page, and path. Then compare the Wireshark capture bytes with what MSI Center sends when you change colors — the RGB values are usually embedded directly in the payload.

This is a complex process and requires some technical knowledge. Good luck T_T

### Using OpenRGB as a Shortcut

[OpenRGB](https://openrgb.org/) is an open-source project that has already reverse-engineered lighting protocols for **hundreds** of devices — including many MSI laptops. Before doing all the Wireshark work yourself, check if someone has already done it for your device.

#### Check if Your Device is Already Supported

1. Go to the [OpenRGB GitLab](https://gitlab.com/CalcProgrammer1/OpenRGB).
2. Browse to `Controllers/` — each subfolder is a device family. For MSI laptops, look in:
   ```
   Controllers/MSIController/
   ```
3. Search the issue tracker and merge requests for your laptop model or PID. For example, this project's protocol came from [MR !2619](https://gitlab.com/CalcProgrammer1/OpenRGB/-/merge_requests/2619) which added the `MS-1565` device.

#### Read the Source Code

If your device (or a similar one) is already in OpenRGB, you can read the C++ source to understand the protocol without any Wireshark work:

1. Find your device's controller file, e.g.:
   ```
   Controllers/MSIController/MSIMysticLightMS1565Controller.cpp
   ```
2. Look for the `SetDirect()` or `SetLEDs()` functions — these contain the exact byte layout for feature reports.
3. Note the **report ID**, **packet size**, **zone masks**, **color byte positions**, and **mode values**.
4. Translate those bytes directly into your Python `send_feature_report()` payload.

For example, this project's protocol was decoded from OpenRGB's source. The key details were:

| Field         | Value                      | Source                       |
| ------------- | -------------------------- | ---------------------------- |
| Report ID     | `0x02`                     | First byte of every packet   |
| Packet size   | 64 bytes                   | Fixed HID report length      |
| Zone select   | `0x01` cmd + bitmask       | Step 1 of two-step protocol  |
| Color payload | `R, G, B` at fixed offsets | Step 2 struct layout         |
| Mode (steady) | `0x01`                     | Constant color, no animation |

#### If Your Device Isn't in OpenRGB

You can still use OpenRGB's tools to help:

1. **Run OpenRGB** on your laptop — even if your keyboard isn't supported, the detection log (`OpenRGB.log`) will list all HID devices it found, with VID, PID, usage page, and interface info. This saves you from hunting through Device Manager.
2. **File a device request** on the OpenRGB GitLab with your Wireshark captures — the community may help decode the protocol.
3. **Look at similar devices** — MSI tends to reuse protocols across models. If a device with a nearby PID is supported, your protocol is likely very similar with only minor byte differences.

> **In short:** Always check OpenRGB first. There's a good chance someone has already figured out the hard part, and you just need to translate their C++ into Python.

## Future Scope

- **Multi-monitor support** — Split zones across multiple displays for ultrawide/dual-monitor setups
- **Per-key RGB control** — Move beyond 4-zone lighting to individual key colors (if the HID protocol supports it) ( you do it i dont have individual key colors)
- **Linux support** — Port HID communication to work on Linux (hidapi already supports it, just needs testing i dont have linux on my main system)
- **GUI / System Tray app** (maybe idc serves my purpose , pay me ill make it for you :D )— A lightweight tray application with a settings panel instead of CLI-only usage
- **More laptop models** — Community-contributed (im too busy to do these myself) HID profiles for other MSI models (GE, Stealth, Raider, etc.)
- **Game integration** — Hook into game events or health bars for context-aware lighting (low HP = red pulse)
- **Music visualizer modes** — Expand audio mode with more color schemes (waveform, VU meter, genre-based palettes)
- **Plugin system** — Allow users to write custom lighting modes as Python scripts that plug into the main loop

## Credits

- [OpenRGB](https://openrgb.org/) — MS-1565 HID protocol reverse engineering (MR !2619)
- [mss](https://github.com/BoboTiG/python-mss) — Fast screen capture
- [hidapi](https://github.com/trezor/cython-hidapi) — HID device communication

## License

MIT

---

This is my first time working with my laptop's hardware internals if something breaks im probably not the best person to ask major question but i can help with simple ones.. :D
