/* Detector de tarjetas · lógica del cliente
 * - Stream MJPEG en <img>
 * - Polling de /api/detections cada 250 ms
 * - Dibujo de recuadros verdes y globos cómic en <canvas>
 * - Panel lateral con la ficha técnica completa
 */
(() => {
  "use strict";

  const FONT = '"Comic Sans MS", "Comic Neue", "Chalkboard SE", "Segoe Print", system-ui, sans-serif';
  const COLOR_BOX = "#c9a35a";
  const COLOR_INK = "#191919";
  const COLOR_BUBBLE = "#f3efe6";
  const COLOR_BUBBLE_SHADOW = "rgba(201, 163, 90, 0.55)";
  const COLOR_SCAN_QUAD = "#FFD400";
  const SCAN_QUAD_LABEL = "ÁREA DE ESCANEO";
  // Fallback del área de escaneo (mismas coordenadas que DEFAULT_SCAN_QUAD de
  // app/config.py) para dibujar el marco desde el primer frame, antes de que
  // llegue /api/health. Cuando health responde, se usa el quad del servidor.
  const DEFAULT_SCAN_QUAD = [
    [0.1762, 0.2962],
    [0.6477, 0.2962],
    [0.6477, 0.6499],
    [0.1762, 0.6499],
  ];
  const POLL_DETECTIONS_MS = 250;
  const POLL_HEALTH_MS = 2000;
  const MISSING_POLLS_TO_CLOSE = 8; // ~2 s sin el label seleccionado → cerrar panel
  const SMOOTH_WINDOW = 9;  // frames de historia para la mediana (rechaza saltos)
  const SMOOTH_ALPHA = 0.15; // 0..1: cuánto sigue el globo a la mediana
  const DEADBAND = 18;      // px: por debajo de esto el globo queda congelado
  const FLIP_MARGIN = 20;   // px de histéresis para no alternar arriba/abajo

  const stream = document.getElementById("stream");
  const canvas = document.getElementById("overlay");
  const ctx = canvas.getContext("2d");
  const panel = document.getElementById("panel");
  const panelContent = document.getElementById("panelContent");
  const panelClose = document.getElementById("panelClose");
  const bannerCamera = document.getElementById("banner-camera");
  const bannerTeach = document.getElementById("banner-teach");
  const statusPill = document.getElementById("status-pill");
  const placeholder = document.getElementById("streamPlaceholder");
  const hintMeta = document.getElementById("hint-meta");
  const footerStats = document.getElementById("footer-stats");
  const cameraUrlInput = document.getElementById("camera-url");
  const cameraApply = document.getElementById("camera-apply");
  const cameraStatus = document.getElementById("camera-status");

  const state = {
    boards: new Map(),
    detections: [],
    bubbles: [],
    selectedLabel: null,
    selectedMissingPolls: 0,
    health: null,
    dpr: 1,
    smoothed: new Map(),
  };

  // ------------------------------------------------------------------ utils
  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function setPill(text, variant) {
    if (!statusPill) return;
    statusPill.textContent = text;
    statusPill.classList.remove("ok", "warn");
    if (variant) statusPill.classList.add(variant);
  }

  function median(values) {
    const a = [...values].sort((p, q) => p - q);
    const mid = a.length >> 1;
    return a.length % 2 ? a[mid] : (a[mid - 1] + a[mid]) / 2;
  }

  function roundRectPath(context, x, y, w, h, r) {
    const radius = Math.min(r, w / 2, h / 2);
    context.beginPath();
    context.moveTo(x + radius, y);
    context.lineTo(x + w - radius, y);
    context.quadraticCurveTo(x + w, y, x + w, y + radius);
    context.lineTo(x + w, y + h - radius);
    context.quadraticCurveTo(x + w, y + h, x + w - radius, y + h);
    context.lineTo(x + radius, y + h);
    context.quadraticCurveTo(x, y + h, x, y + h - radius);
    context.lineTo(x, y + radius);
    context.quadraticCurveTo(x, y, x + radius, y);
    context.closePath();
  }

  function intersects(a, b) {
    return !(a.x + a.w < b.x || b.x + b.w < a.x || a.y + a.h < b.y || b.y + b.h < a.y);
  }

  function drawScanQuad(cw, ch) {
    // Marco fijo del área donde apoyar la tarjeta: cuadrilátero amarillo fino,
    // sin relleno, alineado con las cuatro marcas en T de la hoja. Se dibuja
    // siempre (haya o no detecciones) usando la misma escala que las cajas.
    // Hasta que llega /api/health se usa DEFAULT_SCAN_QUAD para que el marco
    // aparezca desde el primer frame; luego manda el valor del servidor.
    const healthQuad = state.health && state.health.scan_quad;
    const quad = Array.isArray(healthQuad) && healthQuad.length >= 3
      ? healthQuad
      : DEFAULT_SCAN_QUAD;
    const points = quad.map((point) => ({
      x: Number(point[0]) * cw,
      y: Number(point[1]) * ch,
    }));
    if (points.some((point) => !Number.isFinite(point.x) || !Number.isFinite(point.y))) {
      return;
    }

    ctx.save();
    ctx.strokeStyle = COLOR_SCAN_QUAD;
    ctx.lineWidth = 2;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    for (let index = 1; index < points.length; index += 1) {
      ctx.lineTo(points[index].x, points[index].y);
    }
    ctx.closePath();
    ctx.stroke();

    const topLeft = points[0];
    ctx.font = `12px ${FONT}`;
    ctx.textBaseline = "bottom";
    ctx.fillStyle = COLOR_SCAN_QUAD;
    const labelY = topLeft.y > 18 ? topLeft.y - 6 : topLeft.y + 16;
    ctx.fillText(SCAN_QUAD_LABEL, Math.max(2, topLeft.x), labelY);
    ctx.restore();
  }

  // ------------------------------------------------------------- canvas size
  function syncCanvas() {
    const rect = stream.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    state.dpr = dpr;
    const width = Math.round(rect.width * dpr);
    const height = Math.round(rect.height * dpr);
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
  }

  // ----------------------------------------------------------------- drawing
  function summaryLines(detection) {
    const board = state.boards.get(detection.label);
    const summary = detection.summary || (board && board.summary) || {};
    const lines = [];
    const title = detection.short_name || (board && board.short_name) || detection.label;
    lines.push(title);
    Object.keys(summary)
      .slice(0, 3)
      .forEach((key) => {
        lines.push(`${key}: ${summary[key]}`);
      });
    return lines;
  }

  function drawBox(detection, cw, ch) {
    const box = detection.box || { x: 0, y: 0, w: 1, h: 1 };
    const x = box.x * cw;
    const y = box.y * ch;
    const w = box.w * cw;
    const h = box.h * ch;
    const selected = state.selectedLabel === detection.label;

    ctx.save();
    ctx.lineWidth = selected ? 5 : 3;
    ctx.strokeStyle = COLOR_BOX;
    ctx.shadowColor = COLOR_BUBBLE_SHADOW;
    ctx.shadowBlur = selected ? 16 : 8;
    ctx.strokeRect(x + 1.5, y + 1.5, Math.max(1, w - 3), Math.max(1, h - 3));

    // Esquinas estilo visor
    const corner = Math.max(10, Math.min(w, h) * 0.18);
    ctx.lineWidth = selected ? 6 : 4;
    ctx.shadowBlur = 0;
    const corners = [
      [x, y, corner, 0, 0, corner],
      [x + w, y, -corner, 0, 0, corner],
      [x, y + h, corner, 0, 0, -corner],
      [x + w, y + h, -corner, 0, 0, -corner],
    ];
    corners.forEach(([cx, cy, dx, dy, ex, ey]) => {
      ctx.beginPath();
      ctx.moveTo(cx + dx, cy + dy);
      ctx.lineTo(cx, cy);
      ctx.lineTo(cx + ex, cy + ey);
      ctx.stroke();
    });
    ctx.restore();

    return { x, y, w, h };
  }

  function drawBubble(detection, box, lines, cw, ch, occupied, aboveHint) {
    const GAP = 34; // separación entre el globo y la tarjeta
    const base = Math.max(10, Math.min(16, ch * 0.026));
    const titleSize = base + 1;
    const lineHeight = base * 1.28;
    const padX = base * 0.7;
    const padY = base * 0.55;

    let widest = 0;
    ctx.font = `bold ${titleSize}px ${FONT}`;
    widest = Math.max(widest, ctx.measureText(lines[0]).width);
    ctx.font = `${base}px ${FONT}`;
    for (let i = 1; i < lines.length; i += 1) {
      widest = Math.max(widest, ctx.measureText(lines[i]).width);
    }

    const width = Math.min(cw - 8, widest + padX * 2);
    const height = padY * 2 + lineHeight * lines.length;
    const boxCenterX = box.x + box.w / 2;
    const boxCenterY = box.y + box.h / 2;

    let above = aboveHint;
    if (above === undefined || above === null) {
      above = box.y - height - GAP >= 4;
    }
    let bubbleX = Math.max(4, Math.min(cw - width - 4, boxCenterX - width / 2));
    let bubbleY = above ? box.y - height - GAP : box.y + box.h + GAP;

    if (!above && bubbleY + height > ch - 4) {
      bubbleY = Math.max(4, box.y - height - GAP);
      above = true;
    }

    // Evitar solaparse con globos ya dibujados
    const rect = { x: bubbleX, y: bubbleY, w: width, h: height };
    for (let attempt = 0; attempt < 4; attempt += 1) {
      const collision = occupied.some((other) => intersects(rect, other));
      if (!collision) break;
      const shift = height + 8;
      if (above && bubbleY - shift >= 4) {
        bubbleY -= shift;
        rect.y = bubbleY;
      } else if (!above && bubbleY + height + shift <= ch - 4) {
        bubbleY += shift;
        rect.y = bubbleY;
      } else {
        break;
      }
    }
    occupied.push(rect);

    // Colita triangular
    const tailX = Math.max(bubbleX + 14, Math.min(bubbleX + width - 14, boxCenterX));
    const tailBaseY = above ? bubbleY + height : bubbleY;
    const tailTipY = above ? box.y : box.y + box.h;
    const clampedTipX = Math.max(box.x + 4, Math.min(box.x + box.w - 4, tailX));

    ctx.save();
    ctx.fillStyle = COLOR_BUBBLE;
    ctx.strokeStyle = COLOR_INK;
    ctx.lineWidth = 3;
    ctx.lineJoin = "round";

    ctx.beginPath();
    ctx.moveTo(tailX - 9, tailBaseY);
    ctx.lineTo(clampedTipX, tailTipY);
    ctx.lineTo(tailX + 9, tailBaseY);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();

    // Globo
    ctx.beginPath();
    roundRectPath(ctx, bubbleX, bubbleY, width, height, 10);
    ctx.fillStyle = COLOR_BUBBLE;
    ctx.fill();
    ctx.stroke();

    // Texto
    let textY = bubbleY + padY + titleSize;
    ctx.fillStyle = COLOR_INK;
    ctx.textBaseline = "alphabetic";
    ctx.font = `bold ${titleSize}px ${FONT}`;
    ctx.fillText(lines[0], bubbleX + padX, textY);
    ctx.font = `${base}px ${FONT}`;
    for (let i = 1; i < lines.length; i += 1) {
      textY += lineHeight;
      ctx.fillText(lines[i], bubbleX + padX, textY);
    }
    ctx.restore();

    return rect;
  }

  function smoothDetection(detection, cw, ch) {
    const b = detection.box;
    const raw = { x: b.x * cw, y: b.y * ch, w: b.w * cw, h: b.h * ch };
    const now = Date.now();
    let s = state.smoothed.get(detection.label);
    if (!s) {
      s = {
        hist: [raw],
        x: raw.x, y: raw.y, w: raw.w, h: raw.h,
        above: raw.y - 120 - 34 >= 4,
        lastSeen: now,
      };
      state.smoothed.set(detection.label, s);
      return { box: { x: s.x, y: s.y, w: s.w, h: s.h }, above: s.above };
    }
    s.lastSeen = now;

    // Historia corta + mediana: la mediana rechaza los saltos aislados del
    // detector (ORB+RANSAC hace jitter frame a frame incluso con la tarjeta
    // quieta). Solo se sigue cuando la mediana confirma un movimiento real.
    s.hist.push(raw);
    if (s.hist.length > SMOOTH_WINDOW) s.hist.shift();
    const med = {
      x: median(s.hist.map((h) => h.x)),
      y: median(s.hist.map((h) => h.y)),
      w: median(s.hist.map((h) => h.w)),
      h: median(s.hist.map((h) => h.h)),
    };

    // Banda muerta: si el centro de la mediana apenas se movió, congelar.
    const cx = s.x + s.w / 2;
    const cy = s.y + s.h / 2;
    const mx = med.x + med.w / 2;
    const my = med.y + med.h / 2;
    if (Math.hypot(mx - cx, my - cy) > DEADBAND) {
      const a = SMOOTH_ALPHA;
      s.x += a * (med.x - s.x);
      s.y += a * (med.y - s.y);
      s.w += a * (med.w - s.w);
      s.h += a * (med.h - s.h);
    }

    // Histéresis arriba/abajo para que el globo no salte.
    const estH = Math.max(40, Math.min(120, s.h * 0.6));
    const threshold = estH + 18;
    if (s.above && s.y < threshold - FLIP_MARGIN) s.above = false;
    else if (!s.above && s.y > threshold + FLIP_MARGIN) s.above = true;

    return { box: { x: s.x, y: s.y, w: s.w, h: s.h }, above: s.above };
  }

  function draw() {
    if (!canvas.width || !canvas.height) return;
    const dpr = state.dpr || 1;
    const cw = canvas.width / dpr;
    const ch = canvas.height / dpr;

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cw, ch);

    state.bubbles = [];
    if (!stream.naturalWidth) return;
    drawScanQuad(cw, ch);
    if (!state.detections.length) return;

    const occupied = [];
    const seen = new Set();
    state.detections.forEach((detection) => {
      if (!detection.box) return;
      seen.add(detection.label);
      const { box, above } = smoothDetection(detection, cw, ch);
      const lines = summaryLines(detection);
      const rect = drawBubble(detection, box, lines, cw, ch, occupied, above);
      state.bubbles.push({ ...rect, detection });
    });
    // Podar suavizados de etiquetas que llevan rato sin detectarse.
    const now = Date.now();
    for (const [key, s] of state.smoothed.entries()) {
      if (!seen.has(key) && now - (s.lastSeen || 0) > 1500) {
        state.smoothed.delete(key);
      }
    }
  }

  // ----------------------------------------------------------------- panel
  function openPanel(label) {
    const board = state.boards.get(label);
    state.selectedLabel = label;
    state.selectedMissingPolls = 0;
    draw();
    if (!board) {
      panelContent.innerHTML = `<h2>${escapeHtml(label)}</h2>
        <p class="panel-description">No hay una ficha técnica para esta etiqueta.
        Agregala en <code>data/boards.json</code>.</p>`;
      panel.classList.remove("hidden");
      return;
    }

    const f = board.formatted || {};
    const rows = [
      ["MCU / SoC", f.mcu_soc || board.mcu_soc],
      ["Reloj", f.clock],
      ["RAM", f.ram],
      ["Almacenamiento", f.storage],
      ["GPIO", f.gpio],
      ["Voltaje", f.voltage],
      ["Dimensiones", f.dimensions],
      ["Conectividad", f.connectivity],
      ["Año", f.year || board.year],
    ]
      .filter(([, value]) => value !== undefined && value !== null && value !== "")
      .map(
        ([key, value]) =>
          `<tr><th>${escapeHtml(key)}</th><td>${escapeHtml(value)}</td></tr>`
      )
      .join("");

    const links = (board.urls || [])
      .map(
        (url) =>
          `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">Documentación ↗</a>`
      )
      .join("");

    panelContent.innerHTML = `
      <h2>${escapeHtml(board.name)}</h2>
      <p class="panel-subtitle">${escapeHtml(board.id)} · ${escapeHtml(board.year)}</p>
      <table class="spec-table">${rows}</table>
      <p class="panel-description">${escapeHtml(board.description)}</p>
      ${links ? `<div class="panel-links">${links}</div>` : ""}
    `;
    panel.classList.remove("hidden");
  }

  function closePanel() {
    state.selectedLabel = null;
    state.selectedMissingPolls = 0;
    panel.classList.add("hidden");
    draw();
  }

  // ------------------------------------------------------------- interacción
  canvas.addEventListener("click", (event) => {
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;

    const bubble = [...state.bubbles]
      .reverse()
      .find((item) => x >= item.x && x <= item.x + item.w && y >= item.y && y <= item.y + item.h);
    if (bubble) {
      openPanel(bubble.detection.label);
      return;
    }

    const dpr = state.dpr || 1;
    const cw = canvas.width / dpr;
    const ch = canvas.height / dpr;
    const hit = [...state.detections].reverse().find((detection) => {
      if (!detection.box) return false;
      const bx = detection.box.x * cw;
      const by = detection.box.y * ch;
      const bw = detection.box.w * cw;
      const bh = detection.box.h * ch;
      return x >= bx && x <= bx + bw && y >= by && y <= by + bh;
    });
    if (hit) openPanel(hit.label);
  });

  panelClose.addEventListener("click", closePanel);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closePanel();
  });

  // --------------------------------------------------------------- stream
  let reconnectTimer = null;
  stream.addEventListener("load", () => {
    if (placeholder) placeholder.classList.add("hidden");
    syncCanvas();
    draw();
  });
  stream.addEventListener("error", () => {
    if (placeholder) {
      placeholder.textContent = "Reconectando stream…";
      placeholder.classList.remove("hidden");
    }
    if (reconnectTimer) return;
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null;
      stream.src = `/stream.mjpg?t=${Date.now()}`;
    }, 1500);
  });

  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(() => {
      syncCanvas();
      draw();
    }).observe(stream);
  }
  window.addEventListener("resize", () => {
    syncCanvas();
    draw();
  });

  // ------------------------------------------------------------------ datos
  async function loadBoards() {
    try {
      const response = await fetch("/api/boards", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const boards = await response.json();
      state.boards = new Map((Array.isArray(boards) ? boards : []).map((b) => [b.id, b]));
    } catch (error) {
      console.warn("No se pudieron cargar las fichas técnicas:", error);
    }
  }

  function applyHealth(health) {
    state.health = health;
    const camera = health.camera || {};
    const templates = health.templates || {};

    if (bannerCamera) bannerCamera.classList.toggle("hidden", Boolean(camera.present));
    if (bannerTeach) bannerTeach.classList.toggle("hidden", (templates.count || 0) > 0);

    if (camera.present) {
      const fps = Number(camera.fps || 0).toFixed(1);
      setPill(`en vivo · ${fps} fps`, "ok");
    } else {
      setPill("sin cámara", "warn");
    }

    // Refleja la fuente actual en el campo de URL (solo si el usuario no está editando).
    if (cameraUrlInput && document.activeElement !== cameraUrlInput) {
      const src = camera.index;
      if (typeof src === "string" && src) {
        cameraUrlInput.value = src;
      }
    }

    const inference = health.detector && health.detector.last_inference_ms;
    if (hintMeta) {
      hintMeta.textContent = inference
        ? `inferencia ${Number(inference).toFixed(0)} ms`
        : "";
    }
    if (footerStats) {
      const loaded = templates.loaded ? Object.keys(templates.loaded).length : 0;
      footerStats.textContent = `${state.detections.length} detección(es) · ${templates.count || 0} muestra(s) · ${loaded} etiqueta(s) entrenada(s)`;
    }
    // El marco del área de escaneo depende de /api/health; se redibuja apenas
    // llega para que aparezca antes de la primera detección.
    draw();
  }

  function trackSelectedPresence() {
    // Auto-cierre del panel: si el label seleccionado deja de aparecer en el
    // último poll exitoso durante ~2 s (8 polls de 250 ms), se cierra la ficha.
    // No se cuenta un error de red: un fallo transitorio no debe cerrar nada.
    if (!state.selectedLabel) {
      state.selectedMissingPolls = 0;
      return;
    }
    const present = state.detections.some((d) => d.label === state.selectedLabel);
    if (present) {
      state.selectedMissingPolls = 0;
      return;
    }
    state.selectedMissingPolls += 1;
    if (state.selectedMissingPolls >= MISSING_POLLS_TO_CLOSE) {
      closePanel();
    }
  }

  async function pollDetections() {
    try {
      const response = await fetch("/api/detections", { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      state.detections = Array.isArray(data.detections) ? data.detections : [];
      trackSelectedPresence();
      draw();
    } catch (error) {
      state.detections = [];
      draw();
      setPill("sin conexión", "warn");
    } finally {
      window.setTimeout(pollDetections, POLL_DETECTIONS_MS);
    }
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

  async function applyCameraUrl() {
    const url = (cameraUrlInput && cameraUrlInput.value ? cameraUrlInput.value : "").trim();
    if (!url) {
      if (cameraStatus) cameraStatus.textContent = "Escribí una URL";
      return;
    }
    if (cameraStatus) cameraStatus.textContent = "Aplicando…";
    try {
      const response = await fetch("/api/camera", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      const data = await response.json();
      if (response.ok && data.ok) {
        if (cameraStatus) cameraStatus.textContent = "OK ✓";
        pollHealth();
      } else {
        if (cameraStatus) cameraStatus.textContent = "Error: " + (data.error || `HTTP ${response.status}`);
      }
    } catch (error) {
      if (cameraStatus) cameraStatus.textContent = "Error de red";
    }
  }

  async function init() {
    syncCanvas();
    await loadBoards();
    if (cameraApply) cameraApply.addEventListener("click", applyCameraUrl);
    if (cameraUrlInput) {
      cameraUrlInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") applyCameraUrl();
      });
    }
    pollHealth();
    pollDetections();
  }

  init();
})();
