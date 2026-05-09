## ADDED Requirements

### Requirement: QMC5883L SHALL be configured for the lowest-current continuous-mode setting

`MagnetometerModule::qmcInit` ([MagnetometerModule.cpp:111-142](../../../../code-stuff/LeapYeet-firmware/src/modules/MagnetometerModule.cpp#L111-L142)) SHALL write `0xD1` to register `QMC_REG_CTRL1` (`0x09`). Per the QMC5883L datasheet this encodes OSR=64, RNG=2G, ODR=10 Hz, MODE=continuous — the lowest-current operating point that still produces a continuous sample stream. The previously-shipped `0x1D` value (OSR=512, ODR=200 Hz) is the highest-current operating point and SHALL NOT be reintroduced.

#### Scenario: CTRL1 register write at QMC init

- **WHEN** `MagnetometerModule::qmcInit` runs against a detected QMC5883L on either I2C bus
- **THEN** the second-to-last register write SHALL be `CTRL1 = 0xD1`, and the diagnostic `LOG_INFO` printed on success SHALL state "10Hz", "2G", and "OSR64" as the configured values

#### Scenario: Calibration still produces non-degenerate values at the lower rate

- **WHEN** a user runs the figure-8 calibration (15 s) on a device patched to the new configuration
- **THEN** the post-calibration `bias` and `scale` values SHALL be within reasonable bounds (bias magnitudes in the hundreds, scale factors near 1.0) — equivalent in practice to runs done before the rate change, because figure-8 cal's min/max windowing is dominated by motion-coverage rather than sample count

### Requirement: LIS3DH SHALL be configured for low-power mode at 25 Hz

After a successful `lis.begin(...)` in `MagnetometerModule::initSensors` ([MagnetometerModule.cpp:226-238](../../../../code-stuff/LeapYeet-firmware/src/modules/MagnetometerModule.cpp#L226-L238)), the code SHALL call `lis.setDataRate(LIS3DH_DATARATE_25_HZ)` and `lis.setPerformanceMode(LIS3DH_MODE_LOW_POWER)` before `filter.begin(...)`. Range SHALL remain at the Adafruit driver default (`LIS3DH_RANGE_2_G`). The Adafruit driver's post-`begin` default of `LIS3DH_DATARATE_400_HZ` ([Adafruit_LIS3DH.cpp:138](../../../../code-stuff/LeapYeet-firmware/.pio/libdeps/heltec-mesh-node-t114/Adafruit%20LIS3DH/Adafruit_LIS3DH.cpp#L138)) SHALL NOT be allowed to stand.

#### Scenario: LIS3DH detected on Wire1

- **WHEN** `lis.begin(0x18)` or `lis.begin(0x19)` returns true
- **THEN** the module SHALL call `setDataRate(LIS3DH_DATARATE_25_HZ)` followed by `setPerformanceMode(LIS3DH_MODE_LOW_POWER)` before initializing the Madgwick filter, and the diagnostic `LOG_INFO` printed at this point SHALL reflect the 25 Hz / low-power configuration

#### Scenario: Heading remains tilt-compensated after the rate change

- **WHEN** a user holds the device at a non-zero pitch/roll angle while in the FriendFinder tracking view
- **THEN** the displayed heading SHALL remain stable (within the noise envelope of the EMA smoother) — i.e., low-power-mode 8-bit accelerometer resolution SHALL be sufficient for tilt compensation at the heading display's effective resolution

### Requirement: `runOnce` poll period SHALL match the QMC sample rate

`MagnetometerModule::runOnce` ([MagnetometerModule.cpp:260-498](../../../../code-stuff/LeapYeet-firmware/src/modules/MagnetometerModule.cpp#L260-L498)) SHALL return `100` (milliseconds) at its tail when normal sampling is active, so that the OSThread poll cadence matches the QMC5883L's 10 Hz output rate.

#### Scenario: Steady-state poll cadence

- **WHEN** the magnetometer is detected and `headingIsValid` is true
- **THEN** the tail of `runOnce` SHALL return `100`, and the OSThread SHALL be scheduled to call `runOnce` no more often than every ~100 ms in steady state

### Requirement: Madgwick fusion rate SHALL match the sample arrival rate

`MagnetometerModule::initSensors` SHALL call `filter.begin(10)` (not `filter.begin(20)`), so that the Madgwick filter's internal time-step matches the actual sample arrival rate (10 Hz from the QMC5883L).

#### Scenario: Madgwick init follows the LIS3DH config

- **WHEN** `lis.begin(...)` succeeds and `setDataRate` / `setPerformanceMode` have been called
- **THEN** the very next call SHALL be `filter.begin(10)`

### Requirement: Patches SHALL ship as marker-guarded blocks in both patch scripts

The new register-value and API-arg changes SHALL be implemented as idempotent, marker-guarded blocks in `patch-t114.py` and `patch-native.py`. The blocks SHALL anchor on stable substrings in upstream `MagnetometerModule.cpp` such that running either script twice on a fresh upstream clone produces the same result as running it once. Both scripts SHALL apply equivalent transformations so that the native smoke build sees the same source tree as the T114 build.

#### Scenario: Patch is idempotent

- **WHEN** `patch-t114.py` (or `patch-native.py`) is run twice in succession against a freshly-cloned upstream firmware tree
- **THEN** the second run SHALL print the existing patch scripts' "skipped" log shape for each magnetometer-related block, and SHALL produce no further file modifications

#### Scenario: Anchor disappears upstream

- **WHEN** a future upstream commit changes the `qmcWriteReg(bus, addr, QMC_REG_CTRL1, 0x1D)` call shape so that the anchor substring no longer matches
- **THEN** the patch script SHALL exit non-zero with an explicit error referencing the missing anchor — matching the existing `sys.exit(f"ERROR: ...")` discipline in `patch-t114.py` — rather than silently no-op'ing
