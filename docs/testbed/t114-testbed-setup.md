# T114 Two-Device Testbed Setup

Hardware testbed guide for reproducing and logging T114 instability during Friend Finder use.

## Background

The brick reproduces on battery, not on USB. USB provides a stable 3.3V rail that suppresses the voltage sags during SX1262 TX bursts — which is the primary trigger for the non-atomic `nodes.proto` write corruption. See [docs/design/t114-brick-fix.md](../design/t114-brick-fix.md) for full root-cause analysis.

The Heisenberg problem: normal USB serial logging prevents the failure from occurring. The workarounds below let you capture logs while the device runs on battery.

---

## Step 1 — Flash Both T114s

A pre-built UF2 is in `firmware-examples/`. To flash:

1. Double-tap the reset button on the T114 — it enumerates as `NRF52BOOT` mass storage
2. Drag the UF2 onto it; device reboots automatically

If a device is already bricked (fails to mount LittleFS on boot), two-step recovery:
1. Flash stock Meshtastic UF2 → formats LittleFS
2. Flash this firmware on top

---

## Step 2 — Configure the Mesh

Do this over USB before switching to battery. Both devices need the same region and channel.

```bash
pip install meshtastic

# On each device:
meshtastic --set lora.region US --set lora.channel_num 0
meshtastic --ch-set name default --ch-set psk default --ch-index 0
```

Alternatively, configure via the Meshtastic app over Bluetooth. Confirm both devices see each other in the node list before going to battery.

---

## Step 3 — Log Capture Without Heisenberg Interference

### Option A — UART Tap (Recommended)

The T114 exposes UART pads. Wire up a USB-UART adapter (CP2102, FTDI, etc.):

| Adapter | T114 pad |
|---------|----------|
| RX      | TX       |
| GND     | GND      |
| **3.3V/5V** | **leave disconnected** |

The device runs entirely from battery. The adapter is powered by the laptop USB port and passively taps the serial line — it does not feed current into the T114 rail.

Capture logs on the laptop:

```bash
# Raw capture:
cat /dev/ttyUSB0

# With timestamps (requires moreutils):
unbuffer cat /dev/ttyUSB0 | ts '[%H:%M:%S]' | tee node-a.log

# Or minicom at 115200 8N1:
minicom -D /dev/ttyUSB0 -b 115200
```

### Option B — BLE Logging

The nRF52840 can stream logs over Bluetooth (Nordic UART Service). This works but is less reliable than UART for sustained capture.

```bash
# Find BLE MAC:
meshtastic --ble-scan

# Connect and stream:
meshtastic --ble-address AA:BB:CC:DD:EE:FF --debug-port
```

---

## Step 4 — Reproduce the Crash

Once both devices are on battery with logging active:

1. Let both boot and discover each other — look for `[Router] received from=...` in logs
2. Device A: enter **Friend Finder → Track** (the target being tracked)
3. Device B: enter **Friend Finder → Track** (tracking Device A)
4. On either device, run **Compass Cal → Figure-8** followed by **Flat-Spin**

This is the confirmed repro sequence from the field report. Extended battery-powered use without calibration also triggers it, just more slowly.

---

## Step 5 — What to Look For in Logs

### The dangerous write sequence (primary failure path)

```
[Router] Save to disk 16
[Router] Opening /prefs/nodes.proto, fullAtomic=0   ← non-atomic write starts here
[Router] Save /prefs/nodes.proto                    ← write completes (or device dies)
```

`SEGMENT_NODEDATABASE = 16`. Any reset between the second and third line above leaves `nodes.proto` torn. LittleFS refuses to mount on next boot → brick.

### TX events that open the voltage-sag window

```
[RadioInterface] TX                                 ← BOD risk window starts
```

A write overlapping a TX event is the primary mechanism. Cross-reference timestamps from both log lines.

### I2C bus recovery (Patch B from PR #15)

```
[Magnetometer] I2C hang on Wire1; attempting bus recovery
```

This fires after 3 consecutive QMC5883L read failures. If you see it, the H2 hypothesis (Wire1 stall → WDT reset mid-write) is worth revisiting.

### Write-policy gate (P0 + P1, not yet landed)

Once P0+P1 patches ship, safe-condition deferrals will appear as:

```
[NodeDB] WARN Defer non-atomic save of /prefs/nodes.proto: unsafe conditions
```

Absence of bricks combined with presence of these lines is the validation signal.

---

## Crash vs. Brick — Distinguishing the Failure Mode

| Symptom | Likely cause |
|---------|-------------|
| Device stops responding, reboots, comes back normally | Software crash (hard fault, stack overflow, WDT). Check for `[CRASH]` or `[HardFault]` lines at boot |
| Device stops responding, won't boot after power cycle | LittleFS corruption brick — two-step UF2 recovery required |
| Device reboots mid-session repeatedly | BOD firing (voltage sag on TX), or WDT from I2C stall |

On nRF52, a clean reboot after a crash will print the fault reason on boot. Look for lines like:

```
[NRF52] Reset reason: 0x04   ← RESETPIN
[NRF52] Reset reason: 0x01   ← power-on reset (unexpected mid-session)
[NRF52] Reset reason: 0x02   ← watchdog
```

A reset reason of `0x01` mid-session (not from user power cycle) is a BOD event — the MCU lost power briefly and came back. If `nodes.proto` was being written at that moment, the next boot attempt to mount LittleFS may fail.

---

## Log Collection Checklist

Before each test run:

- [ ] Both devices fully charged
- [ ] Both on same LoRa region and channel
- [ ] UART tap wired, adapter powered, logging to file with timestamps
- [ ] Note battery level at session start
- [ ] Note battery level at crash/brick (use Meshtastic app over BLE on a third device or phone)

After a crash or brick:

- [ ] Save logs from both nodes immediately
- [ ] Note the last log line on the crashed device
- [ ] Check whether the device boots normally or requires UF2 recovery
- [ ] Note approximate session duration and activity at time of failure
