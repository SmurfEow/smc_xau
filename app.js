const TIMEFRAMES = [
  "M1", "M5", "M15", "M30", "H1", "H4",
];
const TIMEFRAME_GROUPS = [
  {
    key: "LTF",
    title: "LTF",
    copy: "Lower timeframe execution and fast momentum.",
    timeframes: ["M1", "M5"],
  },
  {
    key: "MTF",
    title: "MTF",
    copy: "Mid timeframe structure and trade decision layer.",
    timeframes: ["M15", "M30"],
  },
  {
    key: "HTF",
    title: "HTF",
    copy: "Higher timeframe context and directional bias.",
    timeframes: ["H1", "H4"],
  },
];

const boardElement = document.getElementById("board");
const refreshButton = document.getElementById("refresh-board");
const jumpNewestButton = document.getElementById("jump-newest");
const toggleMotionButton = document.getElementById("toggle-motion");
const symbolInput = document.getElementById("symbol-input");
const limitInput = document.getElementById("limit-input");
const activeSymbolLabel = document.getElementById("active-symbol-label");
const bridgeStatus = document.getElementById("bridge-status");

const chartState = {};
const domRefs = {};
for (const timeframe of TIMEFRAMES) {
  chartState[timeframe] = {
    candles: [],
    visibleCount: 90,
    offset: 0,
    hoverIndex: null,
    dragging: false,
    lastPointerX: 0,
    summary: null,
  };
}

let autoRefreshEnabled = true;
let autoRefreshHandle = null;
const LIVE_SYNC_MS = 1000;
const TICK_SYNC_MS = 250;
let tickRefreshHandle = null;

function buildBoard() {
  boardElement.innerHTML = "";
  for (const group of TIMEFRAME_GROUPS) {
    const section = document.createElement("section");
    section.className = "timeframe-group";
    section.style.setProperty("--group-columns", String(group.timeframes.length));
    section.innerHTML = `
      <div class="timeframe-group-header">
        <div>
          <p class="eyebrow">Board Group</p>
          <h3>${group.title}</h3>
        </div>
        <p class="timeframe-group-copy">${group.copy}</p>
      </div>
      <div class="timeframe-group-grid"></div>
    `;
    const grid = section.querySelector(".timeframe-group-grid");

    for (const timeframe of group.timeframes) {
      const card = document.createElement("section");
      card.className = "chart-card glass";
      card.dataset.timeframe = timeframe;
      card.innerHTML = `
        <div class="card-header">
          <div>
            <p class="eyebrow">Timeframe</p>
            <h3>${timeframe}</h3>
          </div>
          <div class="card-stats">
            <span class="trend-badge">Neutral</span>
            <strong>--</strong>
          </div>
        </div>
        <canvas width="720" height="380"></canvas>
        <div class="card-footer">
          <span>Loading...</span>
          <span>--</span>
        </div>
        <p class="browse-hint">Drag to pan. Mouse wheel zooms. Hover for OHLCV.</p>
      `;
      grid.appendChild(card);

      domRefs[timeframe] = {
        card,
        badge: card.querySelector(".trend-badge"),
        price: card.querySelector(".card-stats strong"),
        canvas: card.querySelector("canvas"),
        summary: card.querySelector(".card-footer span:first-child"),
        range: card.querySelector(".card-footer span:last-child"),
      };
    }

    boardElement.appendChild(section);
  }
}

function getMaxOffset(state) {
  return Math.max(0, state.candles.length - Math.min(state.visibleCount, state.candles.length));
}

function formatClock() {
  const target = document.getElementById("clock-label");
  if (!target) return;
  target.textContent = new Date().toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function formatPrice(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  if (Math.abs(number) >= 100) return number.toFixed(2);
  if (Math.abs(number) >= 1) return number.toFixed(4);
  return number.toFixed(5);
}

function formatAxisLabel(currentUnixSeconds, previousUnixSeconds) {
  const current = new Date(Number(currentUnixSeconds) * 1000);
  if (Number.isNaN(current.getTime())) return "--";
  if (previousUnixSeconds == null) {
    return current.toLocaleDateString([], { month: "short", day: "2-digit" });
  }
  const previous = new Date(Number(previousUnixSeconds) * 1000);
  const isNewDay =
    current.getFullYear() !== previous.getFullYear() ||
    current.getMonth() !== previous.getMonth() ||
    current.getDate() !== previous.getDate();
  if (isNewDay) {
    return current.toLocaleDateString([], { month: "short", day: "2-digit" });
  }
  return current.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function getChartGeometry(canvas, candleCount) {
  const width = Number(canvas.style.width.replace("px", "")) || canvas.clientWidth || 720;
  const height = Number(canvas.style.height.replace("px", "")) || canvas.clientHeight || 380;
  const padding = { top: 24, right: 78, bottom: 42, left: 16 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const barSpacing = candleCount > 0 ? chartWidth / candleCount : chartWidth;
  return { width, height, padding, chartWidth, chartHeight, barSpacing };
}

function getVisibleCandles(state) {
  if (!state.candles.length) return [];
  const count = Math.max(20, Math.min(state.visibleCount, state.candles.length));
  const end = Math.max(count, state.candles.length - state.offset);
  const start = Math.max(0, end - count);
  return state.candles.slice(start, end);
}

function getTrend(summary) {
  const tone = String(summary?.tone || "Neutral");
  return tone === "Bullish" || tone === "Bearish" ? tone : "Neutral";
}

function setTrendBadge(element, trend) {
  if (!element) return;
  element.textContent = trend;
  element.classList.remove("is-bull", "is-bear", "is-neutral");
  if (trend === "Bullish") element.classList.add("is-bull");
  else if (trend === "Bearish") element.classList.add("is-bear");
  else element.classList.add("is-neutral");
}

function resizeCanvases() {
  for (const timeframe of TIMEFRAMES) {
    const canvas = domRefs[timeframe]?.canvas;
    if (!canvas) continue;
    const width = Math.max(320, canvas.parentElement.clientWidth - 32);
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(380 * ratio);
    canvas.style.width = `${width}px`;
    canvas.style.height = "380px";
    const ctx = canvas.getContext("2d");
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  }
}

function updateCardMeta(timeframe) {
  const state = chartState[timeframe];
  const refs = domRefs[timeframe];
  if (!refs) return;
  const summary = state.summary || {};
  const visibleCandles = getVisibleCandles(state);
  const latest = state.candles[state.candles.length - 1];
  setTrendBadge(refs.badge, getTrend(summary));
  refs.price.textContent = latest ? formatPrice(latest.close) : "--";
  refs.summary.textContent = summary?.tone
    ? `${summary.tone} tone | ${visibleCandles.length} visible / ${state.candles.length} loaded`
    : "Waiting for MT5 data";
  refs.range.textContent =
    summary?.range_low != null && summary?.range_high != null
      ? `${formatPrice(summary.range_low)} - ${formatPrice(summary.range_high)}`
      : "--";
}

function drawChart(timeframe) {
  const state = chartState[timeframe];
  const canvas = domRefs[timeframe]?.canvas;
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const visible = getVisibleCandles(state);
  const { width, height, padding, chartWidth, chartHeight, barSpacing } = getChartGeometry(canvas, visible.length);

  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#040811";
  ctx.fillRect(0, 0, width, height);

  if (!visible.length) {
    ctx.fillStyle = "#9ab0d3";
    ctx.font = "14px Segoe UI";
    ctx.fillText("Waiting for MT5 candles...", 18, 24);
    return;
  }

  const highs = visible.map((candle) => Number(candle.high));
  const lows = visible.map((candle) => Number(candle.low));
  const maxPrice = Math.max(...highs);
  const minPrice = Math.min(...lows);
  const range = Math.max(maxPrice - minPrice, 0.00001);
  const priceToY = (price) => padding.top + ((maxPrice - price) / range) * chartHeight;

  ctx.strokeStyle = "rgba(145, 182, 255, 0.10)";
  ctx.lineWidth = 1;
  for (let row = 0; row <= 5; row += 1) {
    const y = padding.top + (chartHeight / 5) * row;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(width - padding.right, y);
    ctx.stroke();
  }
  for (let col = 0; col <= 6; col += 1) {
    const x = padding.left + (chartWidth / 6) * col;
    ctx.beginPath();
    ctx.moveTo(x, padding.top);
    ctx.lineTo(x, height - padding.bottom);
    ctx.stroke();
  }

  const candleWidth = Math.max(3, barSpacing * 0.56);
  visible.forEach((candle, index) => {
    const x = padding.left + index * barSpacing + (barSpacing - candleWidth) / 2;
    const openY = priceToY(Number(candle.open));
    const closeY = priceToY(Number(candle.close));
    const highY = priceToY(Number(candle.high));
    const lowY = priceToY(Number(candle.low));
    const bullish = Number(candle.close) >= Number(candle.open);
    ctx.strokeStyle = bullish ? "rgba(103, 166, 255, 0.92)" : "rgba(160, 168, 183, 0.92)";
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.moveTo(x + candleWidth / 2, highY);
    ctx.lineTo(x + candleWidth / 2, lowY);
    ctx.stroke();
    ctx.fillStyle = bullish ? "rgba(103, 166, 255, 0.9)" : "rgba(160, 168, 183, 0.9)";
    ctx.fillRect(x, Math.min(openY, closeY), candleWidth, Math.max(2, Math.abs(closeY - openY)));
  });

  ctx.fillStyle = "rgba(154, 176, 211, 0.92)";
  ctx.font = "12px Segoe UI";
  for (let row = 0; row <= 5; row += 1) {
    const price = maxPrice - (range / 5) * row;
    const y = padding.top + (chartHeight / 5) * row;
    ctx.fillText(formatPrice(price), width - padding.right + 10, y + 4);
  }

  const labelIndexes = [0, Math.floor(visible.length * 0.25), Math.floor(visible.length * 0.5), Math.floor(visible.length * 0.75), visible.length - 1];
  for (const index of labelIndexes) {
    const candle = visible[index];
    if (!candle) continue;
    const x = padding.left + index * barSpacing;
    const previousCandle = index > 0 ? visible[index - 1] : null;
    ctx.fillText(formatAxisLabel(candle.time, previousCandle?.time ?? null), x, height - 14);
  }

  const hoveredIndex = state.hoverIndex;
  if (hoveredIndex != null && visible[hoveredIndex]) {
    const candle = visible[hoveredIndex];
    const centerX = padding.left + hoveredIndex * barSpacing + barSpacing / 2;
    const closeY = priceToY(Number(candle.close));
    ctx.strokeStyle = "rgba(125, 211, 252, 0.7)";
    ctx.lineWidth = 1;
    ctx.setLineDash([6, 4]);
    ctx.beginPath();
    ctx.moveTo(centerX, padding.top);
    ctx.lineTo(centerX, height - padding.bottom);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(padding.left, closeY);
    ctx.lineTo(width - padding.right, closeY);
    ctx.stroke();
    ctx.setLineDash([]);
    const hoverText = `O ${formatPrice(candle.open)}  H ${formatPrice(candle.high)}  L ${formatPrice(candle.low)}  C ${formatPrice(candle.close)}  V ${Number(candle.tick_volume ?? 0).toLocaleString()}`;
    ctx.fillStyle = "rgba(10, 18, 30, 0.92)";
    ctx.fillRect(18, 10, Math.min(420, width - 36), 26);
    ctx.fillStyle = "#edf4ff";
    ctx.font = "12px Segoe UI";
    ctx.fillText(hoverText, 24, 28);
  }
}

function renderBoard() {
  for (const timeframe of TIMEFRAMES) {
    updateCardMeta(timeframe);
    drawChart(timeframe);
  }
}

function mergeRecentCandles(existingCandles, incomingCandles) {
  const merged = [...existingCandles];
  const indexByTime = new Map(merged.map((candle, index) => [Number(candle.time), index]));
  for (const candle of incomingCandles) {
    const candleTime = Number(candle.time);
    const existingIndex = indexByTime.get(candleTime);
    if (existingIndex != null) merged[existingIndex] = candle;
    else {
      merged.push(candle);
      indexByTime.set(candleTime, merged.length - 1);
    }
  }
  merged.sort((left, right) => Number(left.time) - Number(right.time));
  return merged;
}

function applyLiveTickToCharts(tickPayload) {
  const livePrice = Number(tickPayload?.last || tickPayload?.bid || tickPayload?.ask || 0);
  if (!Number.isFinite(livePrice) || livePrice <= 0) return;
  for (const timeframe of TIMEFRAMES) {
    const state = chartState[timeframe];
    if (!state.candles.length) continue;
    const candles = state.candles;
    const lastIndex = candles.length - 1;
    const candle = { ...candles[lastIndex] };
    candle.close = livePrice;
    candle.high = Math.max(Number(candle.high), livePrice);
    candle.low = Math.min(Number(candle.low), livePrice);
    candles[lastIndex] = candle;
    if (state.summary) {
      state.summary.last_close = livePrice;
      state.summary.range_high = Math.max(Number(state.summary.range_high), livePrice);
      state.summary.range_low = Math.min(Number(state.summary.range_low), livePrice);
    }
  }
  renderBoard();
}

async function loadBoard() {
  const symbol = String(symbolInput.value || "XAUUSD").trim().toUpperCase() || "XAUUSD";
  const limitRaw = String(limitInput.value || "ALL").trim().toUpperCase() || "ALL";
  const limit = limitRaw === "ALL" ? "ALL" : String(Math.max(80, Math.min(99999, Number(limitRaw || 99999))));
  refreshButton.disabled = true;
  bridgeStatus.textContent = "Syncing";
  activeSymbolLabel.textContent = symbol;
  try {
    const response = await fetch(`/api/board?symbol=${encodeURIComponent(symbol)}&limit=${encodeURIComponent(limit)}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Failed to load MT5 candles.");
    activeSymbolLabel.textContent = payload.symbol || symbol;
    bridgeStatus.textContent = "Live";
    for (const timeframe of TIMEFRAMES) {
      const item = payload.timeframes?.[timeframe];
      const state = chartState[timeframe];
      if (!item || !state) continue;
      state.candles = Array.isArray(item.candles) ? item.candles : [];
      state.summary = item.summary || null;
      state.offset = Math.min(state.offset, getMaxOffset(state));
      state.hoverIndex = null;
    }
    renderBoard();
  } catch (error) {
    bridgeStatus.textContent = "Error";
    for (const timeframe of TIMEFRAMES) {
      if (domRefs[timeframe]) domRefs[timeframe].summary.textContent = error.message || "Failed to load MT5 candles.";
    }
  } finally {
    refreshButton.disabled = false;
  }
}

async function syncRecentBoard() {
  const symbol = String(activeSymbolLabel.textContent || symbolInput.value || "XAUUSD").trim().toUpperCase() || "XAUUSD";
  try {
    const response = await fetch(`/api/sync?symbol=${encodeURIComponent(symbol)}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Failed to sync MT5 candles.");
    if (payload.symbol) activeSymbolLabel.textContent = payload.symbol;
    for (const timeframe of TIMEFRAMES) {
      const item = payload.timeframes?.[timeframe];
      const state = chartState[timeframe];
      if (!item || !state) continue;
      const incoming = Array.isArray(item.candles) ? item.candles : [];
      state.candles = mergeRecentCandles(state.candles, incoming);
      state.summary = { ...(state.summary || {}), ...(item.summary || {}) };
    }
    renderBoard();
  } catch (error) {
    // Keep board usable if a sync cycle fails.
  }
}

async function loadTick() {
  const symbol = String(activeSymbolLabel.textContent || symbolInput.value || "XAUUSD").trim().toUpperCase() || "XAUUSD";
  try {
    const response = await fetch(`/api/tick?symbol=${encodeURIComponent(symbol)}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Failed to load live tick.");
    if (payload.symbol) activeSymbolLabel.textContent = payload.symbol;
    applyLiveTickToCharts(payload);
  } catch (error) {
    // Ignore intermittent tick errors.
  }
}

function startLiveSync() {
  if (autoRefreshHandle) clearInterval(autoRefreshHandle);
  if (!autoRefreshEnabled) return;
  autoRefreshHandle = window.setInterval(syncRecentBoard, LIVE_SYNC_MS);
}

function startTickSync() {
  if (tickRefreshHandle) clearInterval(tickRefreshHandle);
  if (!autoRefreshEnabled) return;
  tickRefreshHandle = window.setInterval(loadTick, TICK_SYNC_MS);
}

function jumpAllToNewest() {
  for (const timeframe of TIMEFRAMES) {
    const state = chartState[timeframe];
    state.offset = 0;
    state.hoverIndex = null;
  }
  renderBoard();
}

function bindChartInteractions(timeframe) {
  const canvas = domRefs[timeframe]?.canvas;
  const state = chartState[timeframe];
  if (!canvas || !state) return;

  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    const next = event.deltaY < 0 ? state.visibleCount - 8 : state.visibleCount + 8;
    state.visibleCount = Math.max(20, Math.min(240, next));
    state.offset = Math.min(state.offset, getMaxOffset(state));
    renderBoard();
  }, { passive: false });

  canvas.addEventListener("mousedown", (event) => {
    state.dragging = true;
    state.lastPointerX = event.clientX;
  });

  canvas.addEventListener("mousemove", (event) => {
    const rect = canvas.getBoundingClientRect();
    const visible = getVisibleCandles(state);
    if (!visible.length) return;
    const geometry = getChartGeometry(canvas, visible.length);

    if (state.dragging) {
      const pixelsPerBar = Math.max(4, geometry.barSpacing);
      const deltaX = event.clientX - state.lastPointerX;
      if (Math.abs(deltaX) >= pixelsPerBar) {
        const shift = Math.round(deltaX / pixelsPerBar);
        state.offset = Math.max(0, Math.min(getMaxOffset(state), state.offset + shift));
        state.lastPointerX = event.clientX;
        renderBoard();
      }
      return;
    }

    const x = event.clientX - rect.left;
    if (x < geometry.padding.left || x > geometry.width - geometry.padding.right) {
      state.hoverIndex = null;
      drawChart(timeframe);
      return;
    }
    const relativeX = x - geometry.padding.left;
    state.hoverIndex = Math.max(0, Math.min(visible.length - 1, Math.floor(relativeX / geometry.barSpacing)));
    drawChart(timeframe);
  });

  canvas.addEventListener("mouseleave", () => {
    state.hoverIndex = null;
    state.dragging = false;
    drawChart(timeframe);
  });
}

window.addEventListener("mouseup", () => {
  for (const timeframe of TIMEFRAMES) {
    chartState[timeframe].dragging = false;
  }
});

window.addEventListener("resize", () => {
  resizeCanvases();
  renderBoard();
});

refreshButton?.addEventListener("click", loadBoard);
jumpNewestButton?.addEventListener("click", jumpAllToNewest);
symbolInput?.addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadBoard();
});
toggleMotionButton?.addEventListener("click", (event) => {
  autoRefreshEnabled = !autoRefreshEnabled;
  event.currentTarget.textContent = autoRefreshEnabled ? "Pause Live Sync" : "Resume Live Sync";
  startLiveSync();
  startTickSync();
});

buildBoard();
for (const timeframe of TIMEFRAMES) bindChartInteractions(timeframe);
formatClock();
window.setInterval(formatClock, 1000);
resizeCanvases();
renderBoard();
loadBoard();
startLiveSync();
startTickSync();
