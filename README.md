<div align="center">

# Meshtastic Firmware: Friend Finder Edition

**An enhanced version of Meshtastic with real-time, compass-based friend tracking.**

</div>

<div align="center">

![License](https://img.shields.io/badge/License-GPL--2.0-blue.svg)
![Version](https://img.shields.io/badge/Version-2.7--FF-brightgreen)
![Hardware](https://img.shields.io/badge/Tested_On-Heltec_V3,_T--LoRa_T3--S3-orange)
![T114](https://img.shields.io/badge/Experimental-Heltec_T114_(nRF52840)-blueviolet)
![Status](https://img.shields.io/badge/Status-Work_In_Progress-yellow)

</div>

This is a custom build of the official Meshtastic firmware that introduces the **Friend Finder** module. This feature allows you to pair with and track your friends in real-time. The tracking screen shows their distance, and if you have a magnetometer installed, a directional arrow makes it easy to find each other when you get separated.

It was developed and tested in real-world conditions, including a crowded outdoor music concert, where it performed exceptionally well.

---

## 🚀 Quick Install & Get Started

The easiest way to get started is by using the custom web flasher. It handles everything for you.

### **➡️ [Install via Web Flasher](https://leapyeet.github.io/Meshtastic-Firmware-Friend-Finder-Edition/)**
### **➡️ [Source Code HERE](https://github.com/LeapYeet/firmware)**

Once installed, here's how to get up and running:
1.  **Calibrate the Compass:** A proper calibration is **essential** for the arrow to be accurate. Follow the detailed [**Calibration Guide**](#-a-guide-to-magnetometer-calibration) below.
2.  **Pair with Friends:** Go to `Friend Finder -> Start Pairing` on your device and have your friend do the same.
3.  **Start Tracking:** Navigate to `Friend Finder -> Track a Friend` and select them from the list.

---

## 📖 Table of Contents

- [Core Features](#-core-features)
- [UI Showcase](#-ui-showcase)
- [Understanding the Tracking Screen](#-understanding-the-tracking-screen)
- [Hardware Requirements](#️-hardware-requirements)
- [Detailed Usage Guide](#-detailed-usage-guide)
- [Advanced Features & Tools](#-advanced-features--tools)
- [A Guide to Magnetometer Calibration](#-a-guide-to-magnetometer-calibration)
- [Project Status & Future Goals](#-project-status--future-goals)
- [Local Development — Native (Portduino) Build](#-local-development--native-portduino-build)
- [Performance & Technical Notes](#-performance--technical-notes)
- [License](#-license)

---

## 🎯 Core Features

-   🛰️ **Friend Finder Module**: A new interface to securely pair with other devices and initiate a tracking session.
-   🧭 **Real-Time Tracking Screen**: Shows the live distance to your friend. With a magnetometer, it also displays a large arrow pointing in their direction.
-   📍 **Saved Places**: Save your current GPS location (like your car or campsite) and get a directional arrow to guide you back to it later.
-   🗺️ **Friend Map**: View the locations of all your paired friends simultaneously on a simple map display.
-   ⚙️ **Optional Magnetometer Support**: Includes all necessary drivers and logic to use a QMC5883L magnetometer for directional tracking.
-   🏞️ **Designed for the Outdoors**: Perfect for staying connected at festivals, while skiing, hiking, or at any large-scale event.

---

## ✨ UI Showcase

| Main Menu | Tracking (with Magnetometer) | Tracking (without Magnetometer) | Calibration Menu |
| :---: | :---: | :---: | :---: |
| <img src="img/home_menu.jpg" width="200"> | <img src="img/tracking_with_mag.jpg" width="200"> | <img src="img/tracking_no_mag.jpg" width="200"> | <img src="img/cal_menu.jpg" width="200"> |
| *Friend Finder on the main menu.* | *Arrow points to your friend.* | *Only the distance is displayed.* | *Easy access to compass calibration.* |

---

## 📊 Understanding the Tracking Screen

The tracking screen provides live information about your friend's status and location.

### With a Magnetometer

When a calibrated magnetometer is installed, the screen displays a large directional arrow.

<div align="center">

<img src="img/tracking_with_mag_info.jpg" width="600">
</div>

| Element | Description |
| :--- | :--- |
| **Who you are tracking** | Displays the short name of the friend you are currently in a session with. |
| **Direction to friend** | A large arrow that points in the real-world direction of your friend. |
| **Distance to friend** | The live distance between you and your friend. |
| **Friend's battery** | Your friend's current battery level percentage. |
| **Time since update** | Shows how long ago the last position packet was received from your friend. |

### Without a Magnetometer

If you don't have a magnetometer, the screen will only show the distance to your friend, as there is no directional data.

<div align="center">

<img src="img/tracking_no_mag_info.jpg" width="600">
</div>

---

## ⚙️ Hardware Requirements

For the Friend Finder module to work, specific hardware is required.

| Component | Model / Specification | Notes |
| :--- | :--- | :--- |
| **Primary Device** | Heltec (V3), Heltec (V4), LilyGo T-LoRa T3-S3 V1 | Other ESP32-S3 devices may work but require individual testing. |
| **Primary Device (Experimental)** | Heltec T114 (nRF52840) | Experimental support. Flashed via UF2, not serial. See the [T114 section](#heltec-t114-nrf52840-experimental) below. |
| **GPS Module** | Any Meshtastic-compatible GPS | **Required.** Any module supported by the base firmware will work. The u-blox M8N is recommended for its high accuracy. |
| **Magnetometer** | QMC5883L | **Highly Recommended.** The directional arrow makes finding friends in crowded spaces much easier. Other magnetometers may work but are untested. |

> **IMPORTANT: Magnetometer Wiring**
> The correct I²C pins vary by device. Here are the confirmed wiring configurations. The firmware is designed to use the **secondary I²C bus** (`I2C1`) where available.

#### Heltec (V3) (Confirmed)
| Magnetometer | Pin | Heltec V3 | Pin |
| :--- | :---: | :--- | ---: |
| **SDA** | -> | **GPIO 41** | |
| **SCL** | -> | **GPIO 42** | |

#### LilyGo T-LoRa T3-S3 V1 (Confirmed)
| Magnetometer | Pin | T-LoRa S3 | Pin |
| :--- | :---: | :--- | ---: |
| **SDA** | -> | **GPIO 43** | |
| **SCL** | -> | **GPIO 44** | |

> **T3-S3 Notes:**
> An Igmopnrq GY-271 (QMC5883L) magnetometer purchased from AliExpress was connected via the Qwiic I²C port using pins 43 and 44. A set of 10 Qwiic I²C cables was purchased from Amazon, as it was more cost-effective than buying individual cables.
If you prefer not to buy Qwiic connector cables, you can solder normal cables directly to the Qwiic port; however, this may limit its usefulness for future applications.
Testing went well the board appears to function the same as the Heltec V3 when running the Friend Finder firmware.

#### Heltec T114 (nRF52840) — Experimental

> **Note:** T114 support is experimental. The firmware compiles against the Meshtastic T114 variant and the QMC5883L driver uses the secondary I²C bus wired via the header pins exposed by [meshtastic/firmware PR #4745](https://github.com/meshtastic/firmware/pull/4745).

| Magnetometer | Pin | Heltec T114 | Pin |
| :--- | :---: | :--- | ---: |
| **SDA** | -> | **P0.16** | |
| **SCL** | -> | **P0.13** | |

##### Flashing the T114

The T114 uses an nRF52840 MCU with an Adafruit-compatible UF2 bootloader. It **cannot** be flashed via the web flasher's serial method. Instead:

1. Double-press the **reset button** on the T114. A USB drive (e.g. `T114_BOOT`) will appear on your computer.
2. Download the `firmware.uf2` file from the [web flasher](https://leapyeet.github.io/Meshtastic-Firmware-Friend-Finder-Edition/) by selecting **Heltec T114** in the dropdown.
3. Drag and drop the `.uf2` file onto the USB drive.
4. The device will flash automatically and reboot.

#### Finding Pins on Other Devices
To find the correct I²C pins for an unsupported ESP32-S3 board, look in the firmware source code for the board's variant file.
1.  Navigate to the variants directory in the source code. The path will be similar to `firmware/src/variants/esp32s3/your_board_name/`.
2.  Open the `variant.h` file.
3.  Search for the following definitions to find the correct GPIO pin numbers for the secondary I²C bus:
    ```cpp
    #define I2C_SDA1 SDA
    #define I2C_SCL1 SCL
    ```

For **nRF52840** boards (like the T114), the variant file lives under `firmware/src/variants/nrf52/your_board_name/` instead.

---

## 📖 Detailed Usage Guide

1.  **Ensure GPS is Active**
    * Before starting, make sure your GPS module is connected and has acquired a solid satellite lock.

2.  **Calibrate the Magnetometer (If Installed)**
    * A proper compass calibration is **essential** for the directional arrow to be accurate. Please follow the detailed instructions in the calibration guide below.

3.  **Pair with Friends**
    * From the main menu, go to `Friend Finder` -> `Start Pairing`.
    * Have your friend do the same on their device. The devices will discover each other and allow you to securely pair.

4.  **Track a Friend**
    * Navigate to `Friend Finder` -> `Track a Friend`.
    * Select your friend from the list to begin a mutual tracking session.
    * The device will switch to the tracking screen.

---
## 🛠️ Advanced Features & Tools

### 📍 Using Saved Places
This feature lets you save your current location to one of four available slots. You can then start a tracking session to get a directional arrow that guides you back to that spot—perfect for finding your car, tent, or campsite.

**To Save a Location:**
1.  Stand at the physical location you wish to save. A GPS lock is required.
2.  Navigate to `Friend Finder` -> `Saved Places`.
3.  Select **"Save Current Location"**.
4.  The device will save your coordinates to the first available empty slot (e.g., "Place 1").

**To Track a Saved Location:**
1.  Navigate to `Friend Finder` -> `Saved Places`.
2.  Select the place you want to track from the list (e.g., "Place 1").
3.  From the action menu, choose **"Track"**.
4.  The tracking screen will appear and guide you to your saved location.

### 🔬 Developer Tools
This menu contains diagnostic tools for testing and debugging. More tools may be added in the future.

**Spoof Test**
The Spoof Test is a tool to verify that your compass and the arrow-drawing logic are working perfectly, independent of any real-world GPS or LoRa issues.

When you start a test, it creates a fake target exactly 1km away in a perfect cardinal direction (North, East, South, or West) from your current location.

**How to Run the Test:**
1.  Navigate to `Friend Finder` -> `Dev Tools` -> `Run Spoof Test`.
2.  Select a target to track, for example, **"Track East"**.
3.  Physically point the top of your device towards **True North** (your compass screen should read 0°).
4.  The arrow on the tracking screen should now point directly to the **right** (90°). If it does, your UI is behaving as expected.

---
## 🧭 A Guide to Magnetometer Calibration

### Why is Calibration Important?
Your magnetometer (compass) is sensitive to magnetic fields. This includes not only the Earth's magnetic field but also interference from nearby electronics, batteries, and metal objects. Calibration is crucial to teach the sensor how to distinguish between the Earth's magnetic north and this local interference, ensuring the directional arrow is accurate.

### When to Calibrate
You should perform a full two-step calibration:
-   **Upon first installation** of the magnetometer.
-   If you add or move components inside your device's case.
-   If the directional arrow seems consistently inaccurate or erratic.

### The Two-Step Calibration Process
For the best results, perform these calibrations outdoors, away from large metal objects like cars, buildings, or metal benches.

#### **1. Figure-8 Calibration (Hard & Soft Iron)**
This is the most important step. It corrects for magnetic fields originating from the device itself (like the battery, speaker, or wires).

1.  On the device, navigate to `Friend Finder` -> `Compass Cal`.
2.  Select **Figure-8 Cal** from the menu.
3.  Hold the device out in front of you.
4.  For 15 seconds, slowly move the device in a large figure-8 pattern. As you move it, twist and rotate your wrist to ensure the device points in every possible direction (up, down, left, right, forwards, backward, etc.). Imagine you are a flying a toy airplane through loops and rolls.
5.  The screen will return to the compass view when complete.

#### **2. Flat-Spin Calibration (2D Alignment)**
This routine fine-tunes the compass for when it's held relatively flat, which is a common use case.

1.  On the device, navigate to `Friend Finder` -> `Compass Cal`.
2.  Select **Flat-Spin Cal** from the menu.
3.  Place the device face-up on a flat, **non-metallic** surface (a wooden table or the ground works well).
4.  For 12 seconds, smoothly rotate the device **CLOCKWISE**. Try for at least 2-3 full rotations.
5.  The screen will return to the compass view when complete.

### Additional Compass Tools
The calibration menu has a few other useful tools:
-   **Set North Here**: If you have a real compass and know which way is true north, point the top of the device in that direction and select this option. It will apply an offset so the device's 0° heading aligns with true north.
-   **Clear North Offset**: Removes any manual offset created by "Set North Here".
-   **Flip North**: This flips the compass heading by 180°. Use this if your magnetometer was accidentally installed backward and the arrow points directly away from your friend.

### Troubleshooting Tips
-   **Arrow points the wrong way:** If the arrow consistently points in the wrong direction (e.g., behind you), first try the **Flip North** option. If it's still off, you likely spun the device **counter-clockwise** during the Flat-Spin calibration. Redo the flat-spin, ensuring you rotate smoothly clockwise.
-   **Arrow is erratic or "jumpy":** This usually indicates strong local magnetic interference. Make sure the magnetometer is mounted away from wires (especially the battery lead) and other metal components. You should also redo the **Figure-8 calibration** away from any potential sources of interference.

---

## 🚧 Project Status & Future Goals

Please be aware that this entire project is a **work in progress**.

-   **Source Code**: The source code is available [**HERE**](https://github.com/LeapYeet/firmware). The module files are at `src/modules/FriendFinderModule.cpp`.
-   **End Goal**: The ultimate goal is to refine this module and merge it with the official Meshtastic firmware repository so everyone can benefit from it.
-   **Testing Needed**: Before an official merge is possible, extensive testing is required. Community feedback and testing are highly encouraged!
-   **Community Support**: Special thanks to contributor **Nullvoid3771** for testing and confirming support for the LilyGo T-LoRa T3-S3 V1. The project owner is willing to investigate pin definitions and create new web flasher builds for other devices upon request. Please open an issue on GitHub to start the process.

---

## 🧪 Local Development — Native (Portduino) Build

For iterating on FriendFinder logic without flashing real hardware, this fork ships a containerised Meshtastic native (Portduino) build. PlatformIO's `env:native` compiles the firmware as a Linux x86_64 binary and links against [SimRadio](https://github.com/meshtastic/firmware/tree/master/src/mesh/sim) (UDP loopback) so you can spin up multiple nodes that mesh with each other, all on one laptop. Useful for protocol/state-machine work and integration tests.

[Dockerfile.native](Dockerfile.native) bakes the apt deps, PlatformIO, a shallow [LeapYeet/firmware](https://github.com/LeapYeet/firmware) clone, and the Portduino platform packages into image layers (~840 MB). After the one-time image build, iterative compiles take ~70 seconds with zero network I/O.

```bash
# Build the image once.
podman build -f Dockerfile.native -t ff-builder-native .

# Compile env:native against the firmware tree baked into the image.
mkdir -p /tmp/ff-native-out
podman run --rm -v /tmp/ff-native-out:/output:Z ff-builder-native
# -> /tmp/ff-native-out/build.log (full pio output)

# Iterate on a host firmware checkout — bind-mount it over /firmware-src.
podman run --rm \
  -v /path/to/your/LeapYeet-firmware:/firmware-src:Z \
  -v /tmp/ff-native-out:/output:Z \
  ff-builder-native

# Drop into a shell to run the linked binary, debug, etc.
podman run --rm -it --entrypoint bash ff-builder-native
# Inside: cd /firmware-src-baked && python3 /usr/local/bin/patch-native.py
#         pio run -e native && ./.pio/build/native/program
```

[patch-native.py](patch-native.py) applies four small workarounds to the upstream firmware tree so it compiles cleanly under Portduino: I2C pin macro definitions, a case-sensitive include fix in `FriendFinderModule.cpp`, and `#if !defined(ARCH_PORTDUINO)` guards around `MagnetometerModule` (replaced with a no-op stub class on native — the simulator has no magnetometer hardware to drive). The patcher is idempotent.

**Limitations.** The native build is a logic harness, not a radio simulator. SimRadio uses UDP loopback so airtime, range, collision, and PHY-level behaviours are not modelled. Magnetometer-dependent UI paths fall through to their `magnetometerModule == nullptr` branches. Real hardware is still required for RF testing and to validate the actual UX on the target display.

---

## 📡 Performance & Technical Notes

-   **LoRa Settings**: All testing has been conducted using the **LongFast** channel preset.
-   **Update Interval**: To keep channel utilization low, position updates are sent every **20 seconds**. Faster LoRa settings may support more frequent updates, but this requires further community testing.
-   **Range**: The effective tracking range depends entirely on your device, antenna, LoRa settings, and the surrounding environment. Hops may not be supported, this hasn't been tested.

---

## 📝 Notes:

1. 
    * If the tracking compass is rotating the wrong way: You need to redo the flat-spin calibration **CLOCKWISE ->**
    * When installing the magnetometer chip to the device, keep it away from other components and wires for better accuracy. Make sure it's firmly secured and flat.
    * As said above, use ports SDA=41, SCL=42. If it doesn't work, try flipping the wires.
    * A decent GPS module is recommended. All testing was done with u-blox m8n chips. My testing showed they were accurate to 1-3m when used outdoors.
    * The Meshtastic Android app is fully usable with this custom firmware.
    * If you want to stop a friend tracking you permanently, you will have to remove them as a friend on the "Track a friend" menu.
    * I recommend a 3000mah battery when paried with a heltec v3 if you want it to last a full day.

---

## Final words:
This project as been my main focus since Feburary 2025. I've had lots of fun developing it and I feel like it will be a great addition to the offical firmware when it's fully polished. It's really exciting to see how everyone will be using it. 

I tested it at a large 1 day music festival in Milton Keynes in august and it worked great. Security didn't know what to think when getting in but saw no issue after showing them how it worked. I was able to walk into the crowd and later find my friends with no issues at all. I simply started a tracking session and the arrow pointed me to the group from 350 meters away.

I love you all, have fun!


## 📜 License

This project is licensed under the **GNU General Public License v3.0 only (GPL-3.0-only)**. The full license text is in [LICENSE](LICENSE).

It is a derivative work of:

-   [LeapYeet/Meshtastic-Firmware-Friend-Finder-Edition](https://github.com/LeapYeet/Meshtastic-Firmware-Friend-Finder-Edition) — the upstream Friend Finder distribution (GPL-3.0)
-   [LeapYeet/firmware](https://github.com/LeapYeet/firmware) — the Meshtastic firmware fork carrying the Friend Finder module (GPL-3.0)
-   [meshtastic/firmware](https://github.com/meshtastic/firmware) — the official Meshtastic firmware (GPL-3.0)

The firmware binaries distributed via this repository's [GitHub releases](https://github.com/soniccyclone/Meshtastic-Firmware-Friend-Finder-Edition/releases) are built from the source at [LeapYeet/firmware](https://github.com/LeapYeet/firmware) with the build-environment patches in [patch-t114.py](patch-t114.py) applied. Per GPL-3 §6, the corresponding source for any distributed binary is available at those upstream repositories plus this repository's patch scripts.
