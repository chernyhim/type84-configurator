# Forensic Report: Profile Switching Architecture of Type 84

## 1. Executive Summary

During the reverse-engineering of profile management for the **IO by Red Square Type 84 Magnetic Black** keyboard (VID `0x0C45`, PID `0x80D6`), detailed static and protocol forensic analysis was performed on the production JavaScript bundles of the official web configurator `https://web.io.vision/` (`layout-classic-DXDjowst.js`, `useProfiles-CdvILL1K.js`, `profiles-DRc7-3Ol.js`).

### Core Finding:
> **The keyboard's physical HID protocol DOES NOT possess an atomic profile switching command (WRITE opcode).**  
> In the official configurator, profiles are strictly **Software-Defined Profiles (client-side)** persisted in browser storage (`IndexedDB` / `localforage`).  
> Switching profiles in the UI triggers a **bulk re-flash across all keyboard subsystems** via the standard sequence of write commands (`AA 22`, `AA 26`, `AA 23`, `AA 24`, `AA 25`, `AA 27`, `AA 28`).

---

## 2. Complete Official Protocol Opcode Matrix (`Ve`)

Extracted from the source code of `scratch/layout-classic.js`, the complete controller opcode enum (`Ve`):

```javascript
Ve = {
  COMMUNICATION_START: 1,           // 0x01
  COMMUNICATION_END: 2,             // 0x02
  SET_FACTORY_RESET: 15,            // 0x0F
  GET_DEVICE_INFO: 16,              // 0x10 -> AA 10
  GET_GAME_MODE: 17,                // 0x11 -> AA 11 (Performance / Game Mode settings)
  GET_KEY: 18,                      // 0x12 -> AA 12 (Remap Layer 1)
  GET_LED_EFFECT: 19,               // 0x13 -> AA 13 (RGB Global)
  GET_CUSTOM_LED_DATA: 20,          // 0x14 -> AA 14 (RGB Matrix / Per-Key)
  GET_MACRO: 21,                    // 0x15 -> AA 15 (Macro table)
  GET_FN_KEY: 22,                   // 0x16 -> AA 16 (Remap Layer 2 / Fn)
  GET_MAGNETIC_AXIS_RT: 23,         // 0x17 -> AA 17 (Hall Switch RT Thresholds)
  GET_MAGNETIC_AXIS_DKS_DATA: 24,   // 0x18 -> AA 18 (DKS Data, NOT Profile 2!)
  GET_LIGHT_BOX: 27,                // 0x1B -> AA 1B
  GET_DEFAULT_FN_KEY_MATRIX: 28,    // 0x1C -> AA 1C (Factory default Fn matrix)
  GET_SIDE_LIGHT: 29,               // 0x1D -> AA 1D
  GET_DEFAULT_KEY_MATRIX: 31,       // 0x1F -> AA 1F
  SET_GAME_MODE: 33,                // 0x21 -> AA 21 (Write Performance / Game Mode)
  SET_KEY: 34,                      // 0x22 -> AA 22 (Write Remap Layer 1)
  SET_LED_EFFECT: 35,               // 0x23 -> AA 23 (Write RGB Global)
  SET_CUSTOM_LED_DATA: 36,          // 0x24 -> AA 24 (Write Per-Key RGB)
  SET_MACRO: 37,                    // 0x25 -> AA 25 (Write Macro table)
  SET_FN_KEY: 38,                   // 0x26 -> AA 26 (Write Remap Layer 2 / Fn)
  SET_MAGNETIC_AXIS_RT: 39,         // 0x27 -> AA 27 (Write Hall Switch RT)
  SET_MAGNETIC_AXIS_DKS_DATA: 40,   // 0x28 -> AA 28 (Write DKS Data)
  SET_LIGHT_BOX: 43,                // 0x2B -> AA 2B
  SET_SIDE_LIGHT: 45,               // 0x2D -> AA 2D
  SET_CALIBRATION_ON: 100,          // 0x64 -> AA 64
  SET_CALIBRATION_OFF: 101,         // 0x65 -> AA 65
  GET_MAGNETIC_AXIS_STATUS: 104     // 0x68 -> AA 68
}
```

---

## 3. Critical Discoveries Regarding Data Semantics

### 3.1. Refutation of the "Active Profile" Hypothesis in `AA 11` / `55 11`
Early project documentation assumed packet `55 11` contained an `Active Profile = 1` flag at byte 11.  
Vendor disassembly of `Po` (`getGameMode`) conclusively disproves this:
```javascript
l = {
    gameMode: r[1],          // offset 9: gameMode
    fnSwitch: r[2],          // offset 10: fnSwitch
    sleepTime: r[3],         // offset 11: sleepTime (minutes to sleep) -> was misidentified as Active Profile!
    keyDelay: r[4],          // offset 12: keyDelay (debounce)
    reportRate: r[5],        // offset 13: reportRate (6 = 8000 Hz) -> was misidentified as Lock Flags!
    systemMode: r[6],        // offset 14: systemMode (0 = Win)
    tftDisplayTime: r[7],    // offset 15: tftDisplayTime
    topDeadZone: r[8] / 100, // offset 16: topDeadZone (mm)
    bottomDeadZone: r[9]/100,// offset 17: bottomDeadZone (mm)
    stabilityMode: r[11],    // offset 19: stabilityMode -> was misidentified as OS Mode!
    autoCalibration: r[14],  // offset 22: autoCalibration -> was misidentified as Connection State!
    singleKeyWakeup: r[15],  // offset 23: singleKeyWakeup
    pushButtonMode: r[16]    // offset 24: pushButtonMode
}
```
The value `0x01` at byte 11 represents **keyboard sleep idle timeout (`sleepTime = 1` minute)**, not an active profile index.

### 3.2. Refutation of the "Hall Profile 2" Hypothesis in `AA 18` / `55 18`
The command `AA 18` in enum `Ve` is **`GET_MAGNETIC_AXIS_DKS_DATA`** (size 1024 bytes):
- This is the **Dynamic Keystroke (DKS)** matrix: 64 slots $\times$ 16 bytes.
- In default factory configuration, the DKS table is empty (all zeros).
- This explains why `55 18` in `read_01_initial_load.json` contained solely zero bytes.
- Opcode `AA 28` is **`SET_MAGNETIC_AXIS_DKS_DATA`**, not a second hardware profile slot.

---

## 4. Profile Switching Mechanism in `web.io.vision`

In module `scratch/useProfiles-CdvILL1K.js`, `handleSwitchProfile` calls `oe(deviceId, index)`, which delegates to `cn(device, index)`:

```javascript
cn = async (device, targetIndex, profileData) => {
    const { sendReport } = Ca();
    let profile = profileData;
    if (!profile) {
        // Read profile from client-side IndexedDB:
        const storage = await za(device);
        profile = storage.profileList[targetIndex];
    }
    // Update active index in IndexedDB:
    await sl(device, targetIndex);

    // BULK SUBSYSTEM RE-FLASH TO KEYBOARD:
    if (profile.keyList)
        await sendReport("setKeyData", device, profile.keyList, profile.wheelKeys); // AA 22
    if (profile.fnKeyList)
        await sendReport("setFnKeyData", device, profile.fnKeyList);                 // AA 26
    if (profile.ledEffect)
        await sendReport("setLEDEffect", device, profile.ledEffect);                 // AA 23
    if (profile.customLedData)
        await sendReport("setCustomLEDData", device, profile.customLedData);         // AA 24
    if (profile.macroDataList)
        await sendReport("setMacroData", device, profile.macroDataList);             // AA 25
    if (profile.magneticAxisRT)
        await sendReport("setMagneticAxisRT", device, profile.magneticAxisRT);       // AA 27
    if (profile.magneticAxisDKS)
        await sendReport("setMagneticAxisDKSData", device, profile.magneticAxisDKS); // AA 28
    if (profile.lightBoxData)
        await sendReport("setLightBox", device, profile.lightBoxData);               // AA 2B
    if (profile.sideLightData)
        await sendReport("setSideLight", device, profile.sideLightData);             // AA 2D
    if (profile.fnKeyList)
        await sendReport("getFnKeyData", device);                                    // AA 16
    return true;
};
```

---

## 5. Research Status Classification

| Parameter / Hypothesis | Status | Evidence Base |
|:---|:---:|:---|
| Existence of atomic hardware profile-switching opcode | **`DISPROVED`** | Enum `Ve` in configurator code defines all commands; no profile switch opcode exists |
| Client-side profile persistence (IndexedDB) | **`CONFIRMED`** | Modules `useProfiles-CdvILL1K.js` and `layout-classic-DXDjowst.js` (`za`, `sl`, `qe.getItem`) |
| Profile switching mechanism (Bulk Re-flash) | **`CONFIRMED`** | Function `cn` sequentially executes `setKeyData`, `setFnKeyData`, `setLEDEffect`, `setMagneticAxisRT`, etc. |
| Byte 11 of packet `AA 11` = `sleepTime` (not active profile) | **`CONFIRMED`** | Functions `Po` (`getGameMode`) and `iu` (`setGameMode`) |
| Byte 13 of packet `AA 11` = `reportRate` (not lock flags) | **`CONFIRMED`** | Function `Po` and config structure `rc.settingsConfig` |
| `AA 18` / `AA 28` = DKS Data (not Hall Profile 2) | **`CONFIRMED`** | Functions `Jo` (`getMagneticAxisDKSData`) and `Uu` (`setMagneticAxisDKSData`) |
