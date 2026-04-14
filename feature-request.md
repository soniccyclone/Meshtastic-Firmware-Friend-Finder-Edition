[Feature Request]: Support for Heltec Mesh Node T114 (nRF52840)
Device:
Heltec Mesh Node T114 — MCU: nRF52840, LoRa chip: SX1262, display: 1.14" TFT-LCD (135×240)

What I'm trying to do:
I'd like to run the Friend Finder firmware on a Heltec T114. The T114 is a low-power nRF52840-based Meshtastic node and I think it would be a great fit for this project.

Hardware wiring (already completed):
The Meshtastic firmware already supports a second I2C bus on the T114's header pins via [PR #4745](https://github.com/meshtastic/firmware/pull/4745), merged September 2024.

My QMC5883L is wired as follows:
SDA → 0.16
SCL → 0.13
VCC → 3V3
GND → GND

I don't know much about things like this, but based on my research, adding T114 support would likely require:
Adding a new PlatformIO build target for the T114 using the existing Meshtastic variant file
Adapting any display rendering code for the T114's TFT-LCD (ST7789 or compatible driver)
Verifying the QMC5883L driver works correctly on the nRF52840 I2C bus
Adding a T114 build to the web flasher if feasible

I'm happy to help test! I have the hardware assembled and ready to go. I'm happy to test builds and report back with results.