/* Modo enseñar · captura y gestión de muestras ORB */
(() => {
  "use strict";

  const FALLBACK_LABELS = {
    arduino_uno_r4_wifi: "Arduino UNO R4 WiFi",
    raspberry_pi_4: "Raspberry Pi 4 Model B",
    arduino_mkr_wifi_1010: "Arduino MKR WiFi 1010",
    esp32: "ESP32 DevKit V1",
  };
  let LABELS = { ...FALLBACK_LABELS };
  const POLL_HEALTH_MS = 2500;

  const select = document.getElementById("label-select");
  const captureBtn = document.getElementById("capture-btn");
  const reloadBtn = document.getElementById("reload-btn");
  const resetBtn = document.getElementById("reset-btn");
  const resetAllBtn = document.getElementById("reset-all-btn");
  const countsEl = document.getElementById("counts");
  const feedbackEl = document.getElementById("feedback");
  const statusPill = document.getElementById("status-pill");
  const footerStats = document.getElementById("footer-stats");
  const stream = document.getElementById("stream");

  function populateSelect() {
    select.innerHTML = Object.keys(LABELS)
      .map((id) => `<option value="${id}">${LABELS[id]}</option>`)
      .join("");
  }

  async function loadBoards() {
    try {
      const response = await fetch("/api/boards", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const boards = await response.json();
      const map = {};
      boards.forEach((board) => {
        if (board && board.id) {
          map[board.id] = board.name || board.short_name || board.id;
        }
      });
      if (Object.keys(map).length > 0) {
        LABELS = map;
        populateSelect();
      }
    } catch (error) {
      populateSelect();
    }
  }

  function setFeedback(message, type) {
    feedbackEl.textContent = message || "";
    feedbackEl.classList.remove("ok", "error");
    if (type) feedbackEl.classList.add(type);
  }

  function setPill(text, variant) {
    statusPill.textContent = text;
    statusPill.classList.remove("ok", "warn");
    if (variant) statusPill.classList.add(variant);
  }

  function renderCounts(byLabel) {
    const labels = Object.keys(LABELS);
    Object.keys(byLabel || {}).forEach((label) => {
      if (!labels.includes(label)) labels.push(label);
    });

    countsEl.innerHTML = labels
      .map((label) => {
        const count = Number((byLabel || {})[label] || 0);
        const name = LABELS[label] || label;
        const zeroClass = count === 0 ? " zero" : "";
        return `<li><span>${name}</span><span class="count-value${zeroClass}">${count}</span></li>`;
      })
      .join("");
  }

  function applyHealth(health) {
    const camera = health.camera || {};
    const templates = health.templates || {};
    renderCounts(templates.by_label || {});

    if (camera.present) {
      setPill(`en vivo · ${Number(camera.fps || 0).toFixed(1)} fps`, "ok");
    } else {
      setPill("sin cámara", "warn");
    }

    if (footerStats) {
      footerStats.textContent = `${templates.count || 0} muestra(s) guardada(s) · ${(templates.labels || []).length} etiqueta(s) entrenada(s)`;
    }
  }

  async function postJSON(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch (error) {
      payload = { ok: false, message: `Respuesta inválida del servidor (HTTP ${response.status})` };
    }
    if (!response.ok) {
      return {
        ok: false,
        message: payload.message || payload.error || `Error HTTP ${response.status}`,
      };
    }
    return payload;
  }

  async function pollHealth() {
    try {
      const response = await fetch("/api/health", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      applyHealth(await response.json());
    } catch (error) {
      setPill("sin conexión", "warn");
    } finally {
      window.setTimeout(pollHealth, POLL_HEALTH_MS);
    }
  }

  function setBusy(busy) {
    [captureBtn, reloadBtn, resetBtn, resetAllBtn].forEach((button) => {
      button.disabled = busy;
    });
  }

  captureBtn.addEventListener("click", async () => {
    setBusy(true);
    setFeedback("Capturando muestra…", null);
    try {
      const result = await postJSON("/api/teach/capture", { label: select.value });
      setFeedback(result.message || "Captura procesada.", result.ok ? "ok" : "error");
    } catch (error) {
      setFeedback(`No se pudo conectar con el servidor: ${error.message}`, "error");
    } finally {
      setBusy(false);
      pollHealthOnce();
    }
  });

  reloadBtn.addEventListener("click", async () => {
    setBusy(true);
    setFeedback("Recargando plantillas…", null);
    try {
      const result = await postJSON("/api/teach/reload", {});
      const counts = result.counts || {};
      const total = Object.values(counts).reduce((acc, value) => acc + value, 0);
      setFeedback(
        result.ok
          ? `Detector actualizado: ${total} plantilla(s) en ${Object.keys(counts).length} etiqueta(s).`
          : result.message || "No se pudo recargar.",
        result.ok ? "ok" : "error"
      );
    } catch (error) {
      setFeedback(`No se pudo conectar con el servidor: ${error.message}`, "error");
    } finally {
      setBusy(false);
      pollHealthOnce();
    }
  });

  resetBtn.addEventListener("click", async () => {
    const label = select.value;
    const name = LABELS[label] || label;
    if (!window.confirm(`¿Borrar todas las muestras de "${name}"?`)) return;
    setBusy(true);
    try {
      const result = await postJSON("/api/teach/reset", { label });
      setFeedback(result.message || "Muestras borradas.", result.ok ? "ok" : "error");
    } catch (error) {
      setFeedback(`No se pudo conectar con el servidor: ${error.message}`, "error");
    } finally {
      setBusy(false);
      pollHealthOnce();
    }
  });

  resetAllBtn.addEventListener("click", async () => {
    if (!window.confirm("¿Borrar TODAS las muestras de TODAS las etiquetas?")) return;
    setBusy(true);
    try {
      const result = await postJSON("/api/teach/reset", { label: "__all__" });
      setFeedback(result.message || "Muestras borradas.", result.ok ? "ok" : "error");
    } catch (error) {
      setFeedback(`No se pudo conectar con el servidor: ${error.message}`, "error");
    } finally {
      setBusy(false);
      pollHealthOnce();
    }
  });

  let onceTimer = null;
  async function pollHealthOnce() {
    if (onceTimer) window.clearTimeout(onceTimer);
    onceTimer = window.setTimeout(async () => {
      onceTimer = null;
      try {
        const response = await fetch("/api/health", { cache: "no-store" });
        if (response.ok) applyHealth(await response.json());
      } catch (error) {
        /* silencioso: el polling periódico reintenta */
      }
    }, 250);
  }

  let reconnectTimer = null;
  stream.addEventListener("error", () => {
    if (reconnectTimer) return;
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null;
      stream.src = `/stream.mjpg?t=${Date.now()}`;
    }, 1500);
  });

  loadBoards();
  pollHealth();
})();
