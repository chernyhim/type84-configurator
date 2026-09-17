# Forensic Report: Исследование механизма переключения профилей Type 84

## 1. Исполнительное резюме (Executive Summary)

В ходе исследования механизма переключения профилей для клавиатуры **IO by Red Square Type 84 Magnetic Black** (VID `0x0C45`, PID `0x80D6`) был проведён детальный статический и протокольный анализ производственного JavaScript-бандла официального веб-конфигуратора `https://web.io.vision/` (`layout-classic-DXDjowst.js`, `useProfiles-CdvILL1K.js`, `profiles-DRc7-3Ol.js`).

### Главный вывод:
> **В аппаратном HID-протоколе клавиатуры ОТСУТСТВУЕТ атомарная команда (WRITE opcode) переключения профилей.**  
> В официальном конфигураторе профили являются **клиентскими (Software-Defined Profiles)** и хранятся в браузере (`IndexedDB` / `localforage`).  
> Переключение профиля в интерфейсе выполняет **полную перезапись (re-flash) всех подсистем клавиатуры** через последовательность существующих команд записи (`AA 22`, `AA 26`, `AA 23`, `AA 24`, `AA 25`, `AA 27`, `AA 28`).

---

## 2. Полная матрица опкодов официального протокола (`Ve`)

Из исходного кода `scratch/layout-classic.js` извлечён полный enum команд контроллера (`Ve`):

```javascript
Ve = {
  COMMUNICATION_START: 1,           // 0x01
  COMMUNICATION_END: 2,             // 0x02
  SET_FACTORY_RESET: 15,            // 0x0F
  GET_DEVICE_INFO: 16,              // 0x10 -> AA 10
  GET_GAME_MODE: 17,                // 0x11 -> AA 11 (Настройки производительности)
  GET_KEY: 18,                      // 0x12 -> AA 12 (Remap Layer 1)
  GET_LED_EFFECT: 19,               // 0x13 -> AA 13 (RGB Global)
  GET_CUSTOM_LED_DATA: 20,          // 0x14 -> AA 14 (RGB Matrix / Per-Key)
  GET_MACRO: 21,                    // 0x15 -> AA 15 (Macro table)
  GET_FN_KEY: 22,                   // 0x16 -> AA 16 (Remap Layer 2 / Fn)
  GET_MAGNETIC_AXIS_RT: 23,         // 0x17 -> AA 17 (Hall Switch RT Thresholds)
  GET_MAGNETIC_AXIS_DKS_DATA: 24,   // 0x18 -> AA 18 (DKS Data, НЕ Profile 2!)
  GET_LIGHT_BOX: 27,                // 0x1B -> AA 1B
  GET_DEFAULT_FN_KEY_MATRIX: 28,    // 0x1C -> AA 1C (Factory default Fn matrix)
  GET_SIDE_LIGHT: 29,               // 0x1D -> AA 1D
  GET_DEFAULT_KEY_MATRIX: 31,       // 0x1F -> AA 1F
  SET_GAME_MODE: 33,                // 0x21 -> AA 21 (Запись настроек производительности)
  SET_KEY: 34,                      // 0x22 -> AA 22 (Запись Remap Layer 1)
  SET_LED_EFFECT: 35,               // 0x23 -> AA 23 (Запись RGB Global)
  SET_CUSTOM_LED_DATA: 36,          // 0x24 -> AA 24 (Запись Per-Key RGB)
  SET_MACRO: 37,                    // 0x25 -> AA 25 (Запись Macro table)
  SET_FN_KEY: 38,                   // 0x26 -> AA 26 (Запись Remap Layer 2 / Fn)
  SET_MAGNETIC_AXIS_RT: 39,         // 0x27 -> AA 27 (Запись Hall Switch RT)
  SET_MAGNETIC_AXIS_DKS_DATA: 40,   // 0x28 -> AA 28 (Запись DKS Data)
  SET_LIGHT_BOX: 43,                // 0x2B -> AA 2B
  SET_SIDE_LIGHT: 45,               // 0x2D -> AA 2D
  SET_CALIBRATION_ON: 100,          // 0x64 -> AA 64
  SET_CALIBRATION_OFF: 101,         // 0x65 -> AA 65
  GET_MAGNETIC_AXIS_STATUS: 104     // 0x68 -> AA 68
}
```

---

## 3. Критические открытия о структуре данных

### 3.1. Развенчание гипотезы "Active Profile" в `AA 11` / `55 11`
Ранее в проекте предполагалось, что пакет `55 11` содержит динамический флаг `Active Profile = 1` в байте 11.  
Реальный код функции `Po` (`getGameMode`) доказывает:
```javascript
l = {
    gameMode: r[1],          // offset 9: gameMode
    fnSwitch: r[2],          // offset 10: fnSwitch
    sleepTime: r[3],         // offset 11: sleepTime (время до сна, минут) -> было принято за Active Profile!
    keyDelay: r[4],          // offset 12: keyDelay (debounce)
    reportRate: r[5],        // offset 13: reportRate (6 = 8000 Hz) -> было принято за Lock Flags!
    systemMode: r[6],        // offset 14: systemMode (0 = Win)
    tftDisplayTime: r[7],    // offset 15: tftDisplayTime
    topDeadZone: r[8] / 100, // offset 16: topDeadZone (мм)
    bottomDeadZone: r[9]/100,// offset 17: bottomDeadZone (мм)
    stabilityMode: r[11],    // offset 19: stabilityMode -> было принято за OS Mode!
    autoCalibration: r[14],  // offset 22: autoCalibration -> было принято за Connection State!
    singleKeyWakeup: r[15],  // offset 23: singleKeyWakeup
    pushButtonMode: r[16]    // offset 24: pushButtonMode
}
```
Значение `0x01` в байте 11 — это **таймаут ухода клавиатуры в спящий режим (sleepTime = 1 мин)**, а не идентификатор профиля.

### 3.2. Развенчание гипотезы "Hall Profile 2" в `AA 18` / `55 18`
Команда `AA 18` в enum `Ve` называется **`GET_MAGNETIC_AXIS_DKS_DATA`** (размер 1024 байта):
- Это таблица настроек **Dynamic Keystroke (DKS)**: 64 слота $\times$ 16 байт.
- В дефолтном состоянии с завода таблица DKS пуста (заполнена нулями).
- Именно поэтому в `read_01_initial_load.json` пакет `55 18` содержал исключительно нули.
- Команда `AA 28` — это **`SET_MAGNETIC_AXIS_DKS_DATA`**, а не запись "второго профиля".

---

## 4. Реализация переключения профилей в `web.io.vision`

В модуле `scratch/useProfiles-CdvILL1K.js` функция `handleSwitchProfile` вызывает `oe(deviceId, index)`, которая вызывает `cn(device, index)`:

```javascript
cn = async (device, targetIndex, profileData) => {
    const { sendReport } = Ca();
    let profile = profileData;
    if (!profile) {
        // Чтение профиля из браузерного хранилища IndexedDB:
        const storage = await za(device);
        profile = storage.profileList[targetIndex];
    }
    // Обновление индекса в IndexedDB:
    await sl(device, targetIndex);

    // ПАКЕТНАЯ ПЕРЕЗАПИСЬ ПОДСИСТЕМ КЛАВИАТУРЫ:
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

## 5. Классификация статусов исследования

| Параметр / Гипотеза | Статус | Доказательная база |
|:---|:---:|:---|
| Наличие атомарного опкода переключения профиля | **`DISPROVED`** | Enum `Ve` в коде конфигуратора содержит все команды, опкода смены профиля нет |
| Клиентское хранение профилей (IndexedDB) | **`CONFIRMED`** | Модули `useProfiles-CdvILL1K.js` и `layout-classic-DXDjowst.js` (`za`, `sl`, `qe.getItem`) |
| Механизм переключения профиля (Bulk Re-flash) | **`CONFIRMED`** | Функция `cn` последовательно вызывает `setKeyData`, `setFnKeyData`, `setLEDEffect`, `setMagneticAxisRT` и др. |
| Байт 11 пакета `AA 11` = `sleepTime` (не active profile) | **`CONFIRMED`** | Функция `Po` (`getGameMode`) и `iu` (`setGameMode`) |
| Байт 13 пакета `AA 11` = `reportRate` (не lock flags) | **`CONFIRMED`** | Функция `Po` и конфигурация `rc.settingsConfig` |
| `AA 18` / `AA 28` = DKS Data (не Hall Profile 2) | **`CONFIRMED`** | Функции `Jo` (`getMagneticAxisDKSData`) и `Uu` (`setMagneticAxisDKSData`) |
