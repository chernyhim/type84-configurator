/**
 * WebHID Bidirectional Passive Sniffer & State-Sync Logger
 * for IO by Red Square Type 84 Magnetic Black (VID 0x0C45, PID 0x80D6)
 * 
 * Safety Guarantee:
 * - Pure passive inspection: hooks HIDDevice methods and event listeners.
 * - ALWAYS calls original browser methods with untouched arguments.
 * - Does NOT inject, replay, modify, or send any packets to the keyboard.
 * - Runs client-side in the DevTools console on https://web.io.vision/
 */

(function () {
  if (window.__kbCaptureInstalled) {
    console.warn("[KB-Capture] WebHID hook is already installed. Use kbResetCapture() to clear buffer.");
    return;
  }
  window.__kbCaptureInstalled = true;
  window.__kbEvents = [];
  window.__kbDeviceRegistry = new Map();
  let devCounter = 0;
  let startTime = performance.now();

  function getDeviceId(device) {
    if (!device) return "dev_unknown";
    if (!window.__kbDeviceRegistry.has(device)) {
      const id = `dev_${devCounter++}`;
      window.__kbDeviceRegistry.set(device, {
        id: id,
        vendor_id: "0x" + (device.vendorId != null ? device.vendorId.toString(16).padStart(4, "0").toUpperCase() : "0C45"),
        product_id: "0x" + (device.productId != null ? device.productId.toString(16).padStart(4, "0").toUpperCase() : "80D6"),
        product_name: device.productName || "IO Type 84",
        opened: device.opened || false,
        collections: (device.collections || []).map(c => ({
          usage_page: "0x" + (c.usagePage != null ? c.usagePage.toString(16).padStart(4, "0").toUpperCase() : "0000"),
          usage: "0x" + (c.usage != null ? c.usage.toString(16).padStart(4, "0").toUpperCase() : "0000"),
          type: c.type,
          output_report_ids: (c.outputReports || []).map(r => r.reportId),
          feature_report_ids: (c.featureReports || []).map(r => r.reportId),
          input_report_ids: (c.inputReports || []).map(r => r.reportId)
        }))
      });
      // Automatically attach passive inputreport listener
      attachInputReportListener(device);
    }
    return window.__kbDeviceRegistry.get(device);
  }

  function findCollectionInfo(device, reportType, reportId) {
    if (!device || !device.collections) return null;
    for (const c of device.collections) {
      let list = null;
      if (reportType === "feature") list = c.featureReports;
      else if (reportType === "output") list = c.outputReports;
      else if (reportType === "input") list = c.inputReports;

      if (list && list.some(r => r.reportId === reportId)) {
        return {
          usage_page: "0x" + (c.usagePage != null ? c.usagePage.toString(16).padStart(4, "0").toUpperCase() : "0000"),
          usage: "0x" + (c.usage != null ? c.usage.toString(16).padStart(4, "0").toUpperCase() : "0000")
        };
      }
    }
    if (device.collections.length > 0) {
      const c = device.collections[0];
      return {
        usage_page: "0x" + (c.usagePage != null ? c.usagePage.toString(16).padStart(4, "0").toUpperCase() : "0000"),
        usage: "0x" + (c.usage != null ? c.usage.toString(16).padStart(4, "0").toUpperCase() : "0000")
      };
    }
    return null;
  }

  function toHex(data) {
    if (!data) return "";
    let bytes;
    if (data instanceof ArrayBuffer) {
      bytes = new Uint8Array(data);
    } else if (ArrayBuffer.isView(data)) {
      bytes = new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
    } else {
      bytes = new Uint8Array(data);
    }
    return Array.from(bytes).map(b => b.toString(16).padStart(2, '0')).join('');
  }

  function recordEvent(params) {
    const devMeta = getDeviceId(params.device);
    const hex = toHex(params.data);
    const colInfo = findCollectionInfo(params.device, params.report_type, params.report_id);
    const now = performance.now();
    const relMs = Number((now - startTime).toFixed(2));

    const entry = {
      index: window.__kbEvents.length,
      timestamp_ms: relMs,
      direction: params.direction,
      event_type: params.event_type,
      report_type: params.report_type,
      device_id: devMeta ? devMeta.id : "unknown",
      device: devMeta || null,
      usage_page: colInfo ? colInfo.usage_page : null,
      usage: colInfo ? colInfo.usage : null,
      report_id: params.report_id != null ? params.report_id : null,
      length: hex.length / 2,
      data_hex: hex
    };

    window.__kbEvents.push(entry);

    // Color-coded console logging
    let dirBadge = params.direction === "HOST -> DEVICE" ? "%c[HOST->DEV]" : "%c[DEV->HOST]";
    let dirColor = params.direction === "HOST -> DEVICE" ? "color: #2196F3; font-weight: bold;" : "color: #00E676; font-weight: bold;";
    if (params.direction === "LIFECYCLE") {
      dirBadge = "%c[LIFECYCLE]";
      dirColor = "color: #FF9800; font-weight: bold;";
    }

    const op = hex.slice(0, 6).toUpperCase();
    const preview = hex.length > 0 ? ` [${op}] ${hex.slice(0, 32)}...` : "";
    const upStr = colInfo ? `[${colInfo.usage_page}:${colInfo.usage}]` : "";
    console.log(
      `${dirBadge} %c[t=+${relMs}ms] [${entry.device_id}] ${upStr} ${params.event_type} (ID=${entry.report_id} len=${entry.length})${preview}`,
      dirColor,
      "color: inherit;"
    );
  }

  function attachInputReportListener(device) {
    if (!device || device.__kbInputSnifferAttached) return;
    device.__kbInputSnifferAttached = true;

    device.addEventListener("inputreport", function (event) {
      recordEvent({
        device: event.device || device,
        direction: "DEVICE -> HOST",
        event_type: "inputreport",
        report_type: "input",
        report_id: event.reportId,
        data: event.data
      });
    });
  }

  // Hook HIDDevice.prototype.open
  const origOpen = HIDDevice.prototype.open;
  HIDDevice.prototype.open = async function () {
    getDeviceId(this);
    attachInputReportListener(this);
    recordEvent({
      device: this,
      direction: "LIFECYCLE",
      event_type: "deviceOpen",
      report_type: "none",
      report_id: null,
      data: null
    });
    const result = await origOpen.apply(this, arguments);
    attachInputReportListener(this);
    return result;
  };

  // Hook HIDDevice.prototype.sendReport (HOST -> DEVICE Output Report)
  const origSendReport = HIDDevice.prototype.sendReport;
  HIDDevice.prototype.sendReport = function (reportId, data) {
    recordEvent({
      device: this,
      direction: "HOST -> DEVICE",
      event_type: "sendReport",
      report_type: "output",
      report_id: reportId,
      data: data
    });
    return origSendReport.apply(this, arguments);
  };

  // Hook HIDDevice.prototype.sendFeatureReport (HOST -> DEVICE Feature Report)
  if (HIDDevice.prototype.sendFeatureReport) {
    const origSendFeatureReport = HIDDevice.prototype.sendFeatureReport;
    HIDDevice.prototype.sendFeatureReport = function (reportId, data) {
      recordEvent({
        device: this,
        direction: "HOST -> DEVICE",
        event_type: "sendFeatureReport",
        report_type: "feature",
        report_id: reportId,
        data: data
      });
      return origSendFeatureReport.apply(this, arguments);
    };
  }

  // Hook HIDDevice.prototype.receiveFeatureReport (DEVICE -> HOST Feature Report)
  if (HIDDevice.prototype.receiveFeatureReport) {
    const origReceiveFeatureReport = HIDDevice.prototype.receiveFeatureReport;
    HIDDevice.prototype.receiveFeatureReport = async function (reportId) {
      const dataView = await origReceiveFeatureReport.apply(this, arguments);
      recordEvent({
        device: this,
        direction: "DEVICE -> HOST",
        event_type: "receiveFeatureReport",
        report_type: "feature",
        report_id: reportId,
        data: dataView
      });
      return dataView;
    };
  }

  // Hook navigator.hid.getDevices & requestDevice if available
  if (navigator.hid) {
    const origGetDevices = navigator.hid.getDevices;
    if (origGetDevices) {
      navigator.hid.getDevices = async function () {
        const devs = await origGetDevices.apply(this, arguments);
        for (const d of devs) {
          getDeviceId(d);
          attachInputReportListener(d);
        }
        return devs;
      };
    }
    const origRequestDevice = navigator.hid.requestDevice;
    if (origRequestDevice) {
      navigator.hid.requestDevice = async function () {
        const devs = await origRequestDevice.apply(this, arguments);
        for (const d of devs) {
          getDeviceId(d);
          attachInputReportListener(d);
        }
        return devs;
      };
    }
  }

  /**
   * Save captured events to a JSON file.
   */
  window.kbSaveCapture = function (filename = "capture.json", description = "") {
    if (!window.__kbEvents || window.__kbEvents.length === 0) {
      console.warn("[KB-Capture] Buffer is empty. Perform actions on web.io.vision first.");
      return;
    }

    const devicesList = Array.from(window.__kbDeviceRegistry.values());

    const payload = {
      version: "2.0",
      target: "https://web.io.vision/",
      captured_at: new Date().toISOString(),
      description: description,
      devices_count: devicesList.length,
      devices: devicesList,
      events_count: window.__kbEvents.length,
      events: window.__kbEvents,
      // Backward compatibility with v1 parsers
      reports_count: window.__kbEvents.length,
      reports: window.__kbEvents
    };

    const jsonStr = JSON.stringify(payload, null, 2);
    const blob = new Blob([jsonStr], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename.endsWith(".json") ? filename : `${filename}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    console.log(`%c[KB-Capture] Exported ${window.__kbEvents.length} events to ${filename}`, "color: #4CAF50; font-weight: bold;");
  };

  /**
   * Dedicated shortcut for the 4 State-Sync research scenarios:
   * A: Initial Load
   * B: Reload
   * C: Disconnect / Reconnect
   * D: Single parameter change
   */
  window.kbSaveScenario = function (scenarioLetter, description = "") {
    const map = {
      "A": "read_01_initial_load.json",
      "B": "read_02_reload.json",
      "C": "read_03_reconnect.json",
      "D": "read_04_param_change.json",
      "a": "read_01_initial_load.json",
      "b": "read_02_reload.json",
      "c": "read_03_reconnect.json",
      "d": "read_04_param_change.json"
    };
    const fname = map[scenarioLetter] || `read_scenario_${scenarioLetter}.json`;
    window.kbSaveCapture(fname, description || `Scenario ${scenarioLetter.toUpperCase()}`);
  };

  /**
   * Print interactive timeline table to console.
   */
  window.kbTimeline = function () {
    if (window.__kbEvents.length === 0) {
      console.log("[KB-Capture] No events recorded.");
      return;
    }
    console.table(window.__kbEvents.map(e => ({
      index: e.index,
      timestamp_ms: e.timestamp_ms,
      dir: e.direction,
      type: e.event_type,
      dev: e.device_id,
      up: e.usage_page,
      id: e.report_id,
      len: e.length,
      prefix: e.data_hex ? e.data_hex.slice(0, 12).toUpperCase() : ""
    })));
  };

  /**
   * Clear capture buffer and reset timeline clock.
   */
  window.kbResetCapture = function () {
    const prev = window.__kbEvents.length;
    window.__kbEvents = [];
    startTime = performance.now();
    console.log(`%c[KB-Capture] Buffer cleared (${prev} events dropped). Clock reset.`, "color: #9C27B0; font-weight: bold;");
  };

  /**
   * Status overview of captured traffic.
   */
  window.kbStatus = function () {
    console.log(`%c[KB-Capture Status] Events: ${window.__kbEvents.length}`, "font-weight: bold; font-size: 13px;");
    const directions = {};
    const types = {};
    const reportIds = new Set();
    const opcodes = new Set();

    for (const e of window.__kbEvents) {
      directions[e.direction] = (directions[e.direction] || 0) + 1;
      types[e.event_type] = (types[e.event_type] || 0) + 1;
      if (e.report_id != null) reportIds.add(e.report_id);
      if (e.data_hex && e.data_hex.length >= 4) {
        opcodes.add(e.data_hex.slice(0, 4).toUpperCase());
      }
    }

    console.log("  Directions:", directions);
    console.log("  Event types:", types);
    console.log("  Report IDs:", Array.from(reportIds));
    console.log("  Observed Opcodes:", Array.from(opcodes));
  };

  console.log("%c[KB-Capture] Bidirectional State-Sync Sniffer Activated!", "color: #00E676; font-weight: bold; font-size: 14px;");
  console.log("%cCommands:\n  kbSaveScenario('A' | 'B' | 'C' | 'D', 'notes')\n  kbTimeline()\n  kbStatus()\n  kbResetCapture()", "color: #FFEB3B; font-weight: bold;");
})();
