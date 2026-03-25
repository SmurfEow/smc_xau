const TIMEFRAMES = [
  "M1", "M5", "M15", "M30", "H1", "H4",
];
const MARKET_STATE_WINDOWS = {
  regime: 200,
  trend: 50,
  state: 20,
  level: 120,
};
const DISPLAY_TIME_OFFSET_MS = -(8 * 60 * 60 * 1000);
const TRADE_SCORE_THRESHOLD = 55;
const TRADE_SCORE_EDGE = 5;
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
const cooldownLabel = document.getElementById("cooldown-label");
const autotradePanelElement = document.getElementById("autotrade-panel");
const autotradePanelToggle = document.getElementById("autotrade-panel-toggle");
const overviewAction = document.getElementById("overview-action");
const overviewScore = document.getElementById("overview-score");
const overviewLongScore = document.getElementById("overview-long-score");
const overviewShortScore = document.getElementById("overview-short-score");
const overviewLongChecks = document.getElementById("overview-long-checks");
const overviewShortChecks = document.getElementById("overview-short-checks");
const overviewSummary = document.getElementById("overview-summary");
const tradeOverviewElement = document.getElementById("trade-overview");
const tradeOverviewToggle = document.getElementById("trade-overview-toggle");
const indicatorGrid = document.getElementById("indicator-grid");
const autotradeStatusLabel = document.getElementById("autotrade-status");
const autotradeLotInput = document.getElementById("autotrade-lot");
const autotradeToggleButton = document.getElementById("autotrade-toggle");
const activeTradeStateLabel = document.getElementById("active-trade-state");
const activeTradeTicketLabel = document.getElementById("active-trade-ticket");
const activeTradeCopy = document.getElementById("active-trade-copy");
const activeTradeSymbol = document.getElementById("active-trade-symbol");
const activeTradeSide = document.getElementById("active-trade-side");
const activeTradeVolume = document.getElementById("active-trade-volume");
const activeTradePrice = document.getElementById("active-trade-price");
const activeTradeSl = document.getElementById("active-trade-sl");
const activeTradeTp = document.getElementById("active-trade-tp");

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
    levels: { support: null, resistance: null },
    marketState: null,
    indicators: null,
  };
}

let autoRefreshEnabled = true;
let autoRefreshHandle = null;
const LIVE_SYNC_MS = 1000;
const TICK_SYNC_MS = 250;
let tickRefreshHandle = null;
let cooldownTickHandle = null;
let latestTradeOverview = null;
let autoTradeConfig = {
  enabled: false,
  lot: 0.01,
};
let cooldownRemainingSeconds = 0;
let tradeActive = false;
let activeTradeSnapshot = null;

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
          <div class="card-title-row">
            <div>
            <p class="eyebrow">Timeframe</p>
            <h3>${timeframe}</h3>
            </div>
            <button class="card-reset-button" type="button" data-timeframe="${timeframe}">Newest</button>
          </div>
          <div class="card-header-actions">
            <div class="card-stats">
              <span class="trend-badge">Neutral</span>
              <strong>--</strong>
            </div>
            <button class="collapse-button card-collapse-button" type="button" data-timeframe="${timeframe}" aria-expanded="true">-</button>
          </div>
        </div>
        <div class="chart-body">
          <canvas width="720" height="380"></canvas>
          <div class="card-footer">
            <span>Loading...</span>
            <span>--</span>
          </div>
          <div class="state-strip">
            <span class="state-chip">Regime <strong>--</strong></span>
            <span class="state-chip">Trend <strong>--</strong></span>
            <span class="state-chip">Range <strong>--</strong></span>
          </div>
          <div class="level-strip">
            <span class="level-chip level-chip-resistance">R <strong>--</strong></span>
            <span class="level-chip level-chip-support">S <strong>--</strong></span>
          </div>
          <p class="browse-hint">Drag to pan. Mouse wheel zooms. Hover for OHLCV.</p>
        </div>
      `;
      grid.appendChild(card);

      domRefs[timeframe] = {
        card,
        badge: card.querySelector(".trend-badge"),
        price: card.querySelector(".card-stats strong"),
        canvas: card.querySelector("canvas"),
        summary: card.querySelector(".card-footer span:first-child"),
        range: card.querySelector(".card-footer span:last-child"),
        resetButton: card.querySelector(".card-reset-button"),
        collapseButton: card.querySelector(".card-collapse-button"),
        body: card.querySelector(".chart-body"),
        regimeChip: card.querySelector(".state-chip:nth-child(1)"),
        trendChip: card.querySelector(".state-chip:nth-child(2)"),
        rangeChip: card.querySelector(".state-chip:nth-child(3)"),
        regime: card.querySelector(".state-chip:nth-child(1) strong"),
        trend: card.querySelector(".state-chip:nth-child(2) strong"),
        rangeState: card.querySelector(".state-chip:nth-child(3) strong"),
        resistance: card.querySelector(".level-chip-resistance strong"),
        support: card.querySelector(".level-chip-support strong"),
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

function formatCooldown(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const minutes = Math.floor(total / 60);
  const remainder = total % 60;
  return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

function renderCooldownLabel() {
  if (!cooldownLabel) return;
  if (tradeActive) {
    cooldownLabel.textContent = "In Trade";
    return;
  }
  if (cooldownRemainingSeconds > 0) {
    cooldownLabel.textContent = formatCooldown(cooldownRemainingSeconds);
    return;
  }
  cooldownLabel.textContent = "Ready";
}

function renderActiveTradePanel() {
  if (activeTradeStateLabel) {
    activeTradeStateLabel.textContent = tradeActive ? "Active Trade" : "Idle";
  }
  if (activeTradeTicketLabel) {
    activeTradeTicketLabel.textContent = activeTradeSnapshot?.ticket ? String(activeTradeSnapshot.ticket) : "--";
  }
  if (activeTradeCopy) {
    if (tradeActive && activeTradeSnapshot) {
      const kind = activeTradeSnapshot.kind === "order" ? "Pending order" : "Live position";
      activeTradeCopy.textContent = `${kind} is active in MT5 and auto trade is locked to one live trade.`;
    } else {
      activeTradeCopy.textContent = "No live trade is open right now.";
    }
  }
  if (activeTradeSymbol) activeTradeSymbol.textContent = activeTradeSnapshot?.symbol || "--";
  if (activeTradeSide) activeTradeSide.textContent = activeTradeSnapshot?.side ? String(activeTradeSnapshot.side).toUpperCase() : "--";
  if (activeTradeVolume) activeTradeVolume.textContent = Number.isFinite(Number(activeTradeSnapshot?.volume)) ? Number(activeTradeSnapshot.volume).toFixed(2) : "--";
  if (activeTradePrice) activeTradePrice.textContent = formatPrice(activeTradeSnapshot?.price);
  if (activeTradeSl) activeTradeSl.textContent = formatPrice(activeTradeSnapshot?.sl);
  if (activeTradeTp) activeTradeTp.textContent = formatPrice(activeTradeSnapshot?.tp);
}

function startCooldownTicker() {
  if (cooldownTickHandle) clearInterval(cooldownTickHandle);
  cooldownTickHandle = window.setInterval(() => {
    if (cooldownRemainingSeconds > 0) {
      cooldownRemainingSeconds -= 1;
      renderCooldownLabel();
    }
  }, 1000);
}

function formatPrice(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  if (Math.abs(number) >= 100) return number.toFixed(2);
  if (Math.abs(number) >= 1) return number.toFixed(4);
  return number.toFixed(5);
}

function getDisplayDate(unixSeconds) {
  const sourceTime = Number(unixSeconds) * 1000;
  return new Date(sourceTime + DISPLAY_TIME_OFFSET_MS);
}

function formatDisplayTimestamp(unixSeconds) {
  const value = getDisplayDate(unixSeconds);
  if (Number.isNaN(value.getTime())) return "--";
  const date = value.toLocaleDateString([], { month: "short", day: "2-digit" });
  const time = value.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return `${date} ${time}`;
}

function formatAxisLabel(currentUnixSeconds, previousUnixSeconds) {
  const current = getDisplayDate(currentUnixSeconds);
  if (Number.isNaN(current.getTime())) return "--";
  if (previousUnixSeconds == null) {
    return current.toLocaleDateString([], { month: "short", day: "2-digit" });
  }
  const previous = getDisplayDate(previousUnixSeconds);
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

function classifyStructure(candles) {
  if (candles.length < 8) {
    return { trend: "Neutral", score: 0 };
  }

  const pivotsHigh = [];
  const pivotsLow = [];
  for (let index = 2; index < candles.length - 2; index += 1) {
    const candle = candles[index];
    const prev1 = candles[index - 1];
    const prev2 = candles[index - 2];
    const next1 = candles[index + 1];
    const next2 = candles[index + 2];

    const high = Number(candle.high);
    const low = Number(candle.low);

    const isPivotHigh =
      high >= Number(prev1.high) &&
      high >= Number(prev2.high) &&
      high >= Number(next1.high) &&
      high >= Number(next2.high);
    const isPivotLow =
      low <= Number(prev1.low) &&
      low <= Number(prev2.low) &&
      low <= Number(next1.low) &&
      low <= Number(next2.low);

    if (isPivotHigh) pivotsHigh.push(high);
    if (isPivotLow) pivotsLow.push(low);
  }

  const lastHighs = pivotsHigh.slice(-3);
  const lastLows = pivotsLow.slice(-3);
  if (lastHighs.length < 2 || lastLows.length < 2) {
    return { trend: "Neutral", score: 0 };
  }

  const risingHighs = lastHighs[lastHighs.length - 1] > lastHighs[0];
  const risingLows = lastLows[lastLows.length - 1] > lastLows[0];
  const fallingHighs = lastHighs[lastHighs.length - 1] < lastHighs[0];
  const fallingLows = lastLows[lastLows.length - 1] < lastLows[0];

  if (risingHighs && risingLows) return { trend: "Bullish", score: 2 };
  if (fallingHighs && fallingLows) return { trend: "Bearish", score: 2 };
  if (risingHighs || risingLows) return { trend: "Bullish", score: 1 };
  if (fallingHighs || fallingLows) return { trend: "Bearish", score: 1 };
  return { trend: "Neutral", score: 0 };
}

function calculateLevels(candles) {
  if (!candles.length) {
    return { support: null, resistance: null };
  }

  const window = candles.slice(-Math.min(MARKET_STATE_WINDOWS.level, candles.length));
  let support = null;
  let resistance = null;

  for (let index = 2; index < window.length - 2; index += 1) {
    const candle = window[index];
    const prev1 = window[index - 1];
    const prev2 = window[index - 2];
    const next1 = window[index + 1];
    const next2 = window[index + 2];

    const isPivotHigh =
      Number(candle.high) >= Number(prev1.high) &&
      Number(candle.high) >= Number(prev2.high) &&
      Number(candle.high) >= Number(next1.high) &&
      Number(candle.high) >= Number(next2.high);

    const isPivotLow =
      Number(candle.low) <= Number(prev1.low) &&
      Number(candle.low) <= Number(prev2.low) &&
      Number(candle.low) <= Number(next1.low) &&
      Number(candle.low) <= Number(next2.low);

    if (isPivotHigh) resistance = Number(candle.high);
    if (isPivotLow) support = Number(candle.low);
  }

  const fallbackWindow = window.slice(-Math.min(40, window.length));
  if (!Number.isFinite(resistance)) {
    resistance = Math.max(...fallbackWindow.map((candle) => Number(candle.high)));
  }
  if (!Number.isFinite(support)) {
    support = Math.min(...fallbackWindow.map((candle) => Number(candle.low)));
  }

  return { support, resistance };
}

function getLiquidityPools(candles, kind, lookback = 60) {
  const window = (candles || []).slice(-Math.min(lookback, candles.length));
  if (window.length < 8) return [];

  const pivots = [];
  for (let index = 2; index < window.length - 2; index += 1) {
    const candle = window[index];
    const prev1 = window[index - 1];
    const prev2 = window[index - 2];
    const next1 = window[index + 1];
    const next2 = window[index + 2];
    const price = kind === "high" ? Number(candle.high) : Number(candle.low);
    const isPivot =
      kind === "high"
        ? price >= Number(prev1.high) &&
          price >= Number(prev2.high) &&
          price >= Number(next1.high) &&
          price >= Number(next2.high)
        : price <= Number(prev1.low) &&
          price <= Number(prev2.low) &&
          price <= Number(next1.low) &&
          price <= Number(next2.low);
    if (!isPivot) continue;
    pivots.push({
      price,
      time: Number(candle.time),
    });
  }

  if (!pivots.length) return [];
  const averageRange =
    window.reduce((sum, candle) => sum + Math.abs(Number(candle.high) - Number(candle.low)), 0) / window.length;
  const tolerance = Math.max(averageRange * 0.35, 1.0);
  const clusters = [];

  for (const pivot of pivots) {
    const existing = clusters.find((cluster) => Math.abs(cluster.price - pivot.price) <= tolerance);
    if (existing) {
      existing.points.push(pivot);
      existing.price =
        existing.points.reduce((sum, point) => sum + point.price, 0) / existing.points.length;
      existing.latestTime = Math.max(existing.latestTime, pivot.time);
      continue;
    }
    clusters.push({
      price: pivot.price,
      latestTime: pivot.time,
      points: [pivot],
    });
  }

  return clusters
    .filter((cluster) => cluster.points.length >= 2)
    .sort((left, right) => left.latestTime - right.latestTime)
    .map((cluster) => cluster.price);
}

function getNearestLiquidity(price, pools, side, maxDistance = Infinity) {
  const sorted = [...(pools || [])]
    .filter((value) => Number.isFinite(value))
    .filter((value) => (side === "below" ? value < price : value > price))
    .sort((left, right) =>
      side === "below" ? right - left : left - right
    );
  return sorted.find((value) => Math.abs(price - value) <= maxDistance) ?? null;
}

function getSwingCandidates(candles, kind, lookback = 80) {
  const window = (candles || []).slice(-Math.min(lookback, candles.length));
  if (window.length < 6) return [];

  const swings = [];
  for (let index = 2; index < window.length - 2; index += 1) {
    const candle = window[index];
    const prev1 = window[index - 1];
    const prev2 = window[index - 2];
    const next1 = window[index + 1];
    const next2 = window[index + 2];
    const price = kind === "high" ? Number(candle.high) : Number(candle.low);
    const isSwing =
      kind === "high"
        ? price >= Number(prev1.high) &&
          price >= Number(prev2.high) &&
          price >= Number(next1.high) &&
          price >= Number(next2.high)
        : price <= Number(prev1.low) &&
          price <= Number(prev2.low) &&
          price <= Number(next1.low) &&
          price <= Number(next2.low);
    if (!isSwing) continue;
    swings.push(price);
  }

  return [...new Set(swings.map((value) => Number(value.toFixed(2))))];
}

function clampStopDistance(entry, sl, side, minDistance, maxDistance) {
  if (!Number.isFinite(entry) || !Number.isFinite(sl)) {
    return { value: sl, mode: "raw" };
  }
  const distance = Math.abs(entry - sl);
  if (Number.isFinite(minDistance) && distance < minDistance) {
    return {
      value: side === "long" ? entry - minDistance : entry + minDistance,
      mode: "floored",
    };
  }
  if (Number.isFinite(maxDistance) && distance > maxDistance) {
    return {
      value: side === "long" ? entry - maxDistance : entry + maxDistance,
      mode: "capped",
    };
  }
  return { value: sl, mode: "raw" };
}

function chooseDirectionalAnchor(entry, candidates, side, minGap = 0, maxGap = Infinity) {
  const directional = [...new Set((candidates || []).filter((value) => Number.isFinite(value)))]
    .filter((value) => (side === "below" ? value < entry : value > entry))
    .map((value) => ({
      value,
      gap: Math.abs(entry - value),
    }))
    .sort((left, right) =>
      side === "below" ? right.value - left.value : left.value - right.value
    );
  return (
    directional.find((item) => item.gap >= minGap && item.gap <= maxGap)?.value ??
    directional.find((item) => item.gap >= minGap)?.value ??
    directional.find((item) => item.gap <= maxGap)?.value ??
    directional[0]?.value ??
    null
  );
}

function calculateMarketState(candles) {
  if (!candles.length) {
    return { regime: "--", trend: "--", rangePosition: "--" };
  }

  const regimeWindow = candles.slice(-Math.min(MARKET_STATE_WINDOWS.regime, candles.length));
  const trendWindow = candles.slice(-Math.min(MARKET_STATE_WINDOWS.trend, candles.length));
  const stateWindow = candles.slice(-Math.min(MARKET_STATE_WINDOWS.state, candles.length));

  const closes = regimeWindow.map((candle) => Number(candle.close));
  const highs = regimeWindow.map((candle) => Number(candle.high));
  const lows = regimeWindow.map((candle) => Number(candle.low));
  const latest = closes[closes.length - 1];
  const visibleHigh = Math.max(...highs);
  const visibleLow = Math.min(...lows);
  const visibleRange = Math.max(visibleHigh - visibleLow, 0.00001);

  const recent = stateWindow;
  const prior = trendWindow.slice(0, Math.max(0, trendWindow.length - stateWindow.length));
  const recentCloses = recent.map((candle) => Number(candle.close));
  const priorCloses = (prior.length ? prior : recent).map((candle) => Number(candle.close));
  const recentAvg = recentCloses.reduce((sum, value) => sum + value, 0) / recentCloses.length;
  const priorAvg = priorCloses.reduce((sum, value) => sum + value, 0) / priorCloses.length;
  const avgBody =
    recent.reduce((sum, candle) => sum + Math.abs(Number(candle.close) - Number(candle.open)), 0) / recent.length;
  const recentHigh = Math.max(...recent.map((candle) => Number(candle.high)));
  const recentLow = Math.min(...recent.map((candle) => Number(candle.low)));
  const recentRange = Math.max(recentHigh - recentLow, 0.00001);
  const priorHigh = Math.max(...(prior.length ? prior : recent).map((candle) => Number(candle.high)));
  const priorLow = Math.min(...(prior.length ? prior : recent).map((candle) => Number(candle.low)));
  const priorRange = Math.max(priorHigh - priorLow, 0.00001);
  const recentSlope = recentAvg - priorAvg;
  const structure = classifyStructure(regimeWindow);
  const compressionThreshold = priorRange * 0.55;
  const bodyThreshold = visibleRange * 0.02;
  const strongSlopeThreshold = visibleRange * 0.05;
  const transitionSlopeThreshold = visibleRange * 0.025;
  const compressionSlopeThreshold = visibleRange * 0.012;
  const rangeThreshold = visibleRange * 0.018;

  let trend = structure.trend;
  if (trend === "Neutral") {
    if (recentSlope > transitionSlopeThreshold) trend = "Bullish";
    else if (recentSlope < -transitionSlopeThreshold) trend = "Bearish";
  }

  let regime = "Range";
  if (
    structure.trend === "Bullish" &&
    structure.score >= 1 &&
    recentSlope > transitionSlopeThreshold &&
    latest > recentAvg
  ) {
    regime = "Uptrend";
    trend = "Bullish";
  } else if (
    structure.trend === "Bearish" &&
    structure.score >= 1 &&
    recentSlope < -transitionSlopeThreshold &&
    latest < recentAvg
  ) {
    regime = "Downtrend";
    trend = "Bearish";
  } else if (
    recentRange < compressionThreshold &&
    avgBody < bodyThreshold &&
    Math.abs(recentSlope) < compressionSlopeThreshold &&
    structure.score === 0
  ) {
    regime = "Compression";
    if (trend === "Neutral") {
      trend = latest >= recentAvg ? "Bullish" : "Bearish";
    }
  } else if (Math.abs(recentSlope) > strongSlopeThreshold || structure.score === 1) {
    regime = "Transition";
  } else if (Math.abs(recentSlope) < rangeThreshold && structure.score === 0) {
    regime = "Range";
  }

  const rangeRatio = (latest - visibleLow) / visibleRange;
  let rangePosition = "Middle";
  if (rangeRatio >= 0.67) rangePosition = "Upper";
  else if (rangeRatio <= 0.33) rangePosition = "Lower";

  return { regime, trend, rangePosition };
}

function calculateEMA(candles, period) {
  if (!candles.length) return null;
  const k = 2 / (period + 1);
  let ema = Number(candles[0].close);
  for (let index = 1; index < candles.length; index += 1) {
    ema = Number(candles[index].close) * k + ema * (1 - k);
  }
  return ema;
}

function calculateRSI(candles, period = 14) {
  if (candles.length <= period) return null;
  let gains = 0;
  let losses = 0;
  for (let index = 1; index <= period; index += 1) {
    const change = Number(candles[index].close) - Number(candles[index - 1].close);
    if (change >= 0) gains += change;
    else losses += Math.abs(change);
  }
  let avgGain = gains / period;
  let avgLoss = losses / period;
  for (let index = period + 1; index < candles.length; index += 1) {
    const change = Number(candles[index].close) - Number(candles[index - 1].close);
    const gain = change > 0 ? change : 0;
    const loss = change < 0 ? Math.abs(change) : 0;
    avgGain = ((avgGain * (period - 1)) + gain) / period;
    avgLoss = ((avgLoss * (period - 1)) + loss) / period;
  }
  if (avgLoss === 0) return 100;
  const rs = avgGain / avgLoss;
  return 100 - (100 / (1 + rs));
}

function calculateATR(candles, period = 14) {
  if (candles.length <= period) return null;
  const trueRanges = [];
  for (let index = 1; index < candles.length; index += 1) {
    const high = Number(candles[index].high);
    const low = Number(candles[index].low);
    const previousClose = Number(candles[index - 1].close);
    trueRanges.push(Math.max(
      high - low,
      Math.abs(high - previousClose),
      Math.abs(low - previousClose)
    ));
  }
  if (trueRanges.length < period) return null;
  let atr = trueRanges.slice(0, period).reduce((sum, value) => sum + value, 0) / period;
  for (let index = period; index < trueRanges.length; index += 1) {
    atr = ((atr * (period - 1)) + trueRanges[index]) / period;
  }
  return atr;
}

function calculateADX(candles, period = 14) {
  if (candles.length <= period * 2) return null;
  const trs = [];
  const plusDMs = [];
  const minusDMs = [];
  for (let index = 1; index < candles.length; index += 1) {
    const current = candles[index];
    const previous = candles[index - 1];
    const upMove = Number(current.high) - Number(previous.high);
    const downMove = Number(previous.low) - Number(current.low);
    plusDMs.push(upMove > downMove && upMove > 0 ? upMove : 0);
    minusDMs.push(downMove > upMove && downMove > 0 ? downMove : 0);
    trs.push(Math.max(
      Number(current.high) - Number(current.low),
      Math.abs(Number(current.high) - Number(previous.close)),
      Math.abs(Number(current.low) - Number(previous.close))
    ));
  }

  let smoothedTR = trs.slice(0, period).reduce((sum, value) => sum + value, 0);
  let smoothedPlusDM = plusDMs.slice(0, period).reduce((sum, value) => sum + value, 0);
  let smoothedMinusDM = minusDMs.slice(0, period).reduce((sum, value) => sum + value, 0);
  const dxValues = [];

  for (let index = period; index < trs.length; index += 1) {
    smoothedTR = smoothedTR - (smoothedTR / period) + trs[index];
    smoothedPlusDM = smoothedPlusDM - (smoothedPlusDM / period) + plusDMs[index];
    smoothedMinusDM = smoothedMinusDM - (smoothedMinusDM / period) + minusDMs[index];
    if (smoothedTR === 0) continue;
    const plusDI = (smoothedPlusDM / smoothedTR) * 100;
    const minusDI = (smoothedMinusDM / smoothedTR) * 100;
    const denominator = plusDI + minusDI;
    if (denominator === 0) continue;
    dxValues.push((Math.abs(plusDI - minusDI) / denominator) * 100);
  }

  if (dxValues.length < period) return null;
  let adx = dxValues.slice(0, period).reduce((sum, value) => sum + value, 0) / period;
  for (let index = period; index < dxValues.length; index += 1) {
    adx = ((adx * (period - 1)) + dxValues[index]) / period;
  }
  return adx;
}

function calculateVWAP(candles) {
  if (!candles.length) return null;
  const latest = candles[candles.length - 1];
  const latestDate = getDisplayDate(latest.time);
  let cumulativePV = 0;
  let cumulativeVolume = 0;
  for (const candle of candles) {
    const candleDate = getDisplayDate(candle.time);
    const sameDay =
      candleDate.getFullYear() === latestDate.getFullYear() &&
      candleDate.getMonth() === latestDate.getMonth() &&
      candleDate.getDate() === latestDate.getDate();
    if (!sameDay) continue;
    const typicalPrice = (Number(candle.high) + Number(candle.low) + Number(candle.close)) / 3;
    const volume = Number(candle.tick_volume ?? 0) || 1;
    cumulativePV += typicalPrice * volume;
    cumulativeVolume += volume;
  }
  if (cumulativeVolume === 0) return null;
  return cumulativePV / cumulativeVolume;
}

function calculateIndicators(candles) {
  if (!candles.length) return null;
  const source = candles.slice(-Math.min(300, candles.length));
  const ema9 = calculateEMA(source, 9);
  const ema20 = calculateEMA(source, 20);
  const ema50 = calculateEMA(source, 50);
  const rsi14 = calculateRSI(source, 14);
  const atr14 = calculateATR(source, 14);
  const adx14 = calculateADX(source, 14);
  const vwap = calculateVWAP(source);
  const latestClose = Number(source[source.length - 1].close);

  let emaState = "Mixed";
  if (Number.isFinite(ema9) && Number.isFinite(ema20) && Number.isFinite(ema50)) {
    if (ema9 > ema20 && ema20 > ema50) emaState = "Bull Stack";
    else if (ema9 < ema20 && ema20 < ema50) emaState = "Bear Stack";
  }

  let rsiState = "Neutral";
  if (Number.isFinite(rsi14)) {
    if (rsi14 >= 55) rsiState = "Bullish";
    else if (rsi14 <= 45) rsiState = "Bearish";
  }

  let adxState = "Weak";
  if (Number.isFinite(adx14)) {
    if (adx14 >= 25) adxState = "Strong";
    else if (adx14 >= 20) adxState = "Building";
  }

  let vwapState = "Neutral";
  if (Number.isFinite(vwap)) {
    vwapState = latestClose >= vwap ? "Above" : "Below";
  }

  return {
    ema9,
    ema20,
    ema50,
    emaState,
    rsi14,
    rsiState,
    atr14,
    adx14,
    adxState,
    vwap,
    vwapState,
    latestClose,
  };
}

function detectTrigger(timeframeState) {
  const candles = timeframeState?.candles || [];
  if (candles.length < 12) {
    return { direction: "Neutral", label: "Waiting" };
  }

  const recent = candles.slice(-10);
  const last = recent[recent.length - 1];
  const previous = recent.slice(0, -1);
  const previousHigh = Math.max(...previous.map((candle) => Number(candle.high)));
  const previousLow = Math.min(...previous.map((candle) => Number(candle.low)));

  if (Number(last.close) > previousHigh) {
    return { direction: "Bullish", label: "Bullish Break" };
  }
  if (Number(last.close) < previousLow) {
    return { direction: "Bearish", label: "Bearish Break" };
  }

  const lastBody = Number(last.close) - Number(last.open);
  if (lastBody > 0 && Number(last.close) > Number(previous[previous.length - 1].close)) {
    return { direction: "Bullish", label: "Bullish Reclaim" };
  }
  if (lastBody < 0 && Number(last.close) < Number(previous[previous.length - 1].close)) {
    return { direction: "Bearish", label: "Bearish Reclaim" };
  }

  return { direction: "Neutral", label: "No trigger" };
}

function isNearLevel(price, levelA, levelB, side) {
  const currentPrice = Number(price);
  const primary = Number(side === "long" ? levelA : levelB);
  const opposite = Number(side === "long" ? levelB : levelA);
  if (!Number.isFinite(currentPrice) || !Number.isFinite(primary)) return false;
  const span = Number.isFinite(opposite) ? Math.abs(opposite - primary) : Math.abs(primary) * 0.003;
  const tolerance = Math.max(span * 0.22, Math.abs(currentPrice) * 0.0008, 1.2);
  return Math.abs(currentPrice - primary) <= tolerance;
}

function buildMtfSetup(side, m30State, m15State) {
  const m30MarketState = m30State?.marketState || {};
  const m15MarketState = m15State?.marketState || {};
  const m30Price = Number(m30State?.candles?.[m30State.candles.length - 1]?.close);
  const m15Price = Number(m15State?.candles?.[m15State.candles.length - 1]?.close);
  const m30NearSupport = isNearLevel(m30Price, m30State?.levels?.support, m30State?.levels?.resistance, "long");
  const m15NearSupport = isNearLevel(m15Price, m15State?.levels?.support, m15State?.levels?.resistance, "long");
  const m30NearResistance = isNearLevel(m30Price, m30State?.levels?.support, m30State?.levels?.resistance, "short");
  const m15NearResistance = isNearLevel(m15Price, m15State?.levels?.support, m15State?.levels?.resistance, "short");

  if (side === "long") {
    const locationPass = m30MarketState.rangePosition === "Lower" || m30MarketState.rangePosition === "Middle";
    const zonePass = m30NearSupport || m15NearSupport;
    const structurePass = !(m15MarketState.trend === "Bearish" && m15MarketState.regime === "Downtrend");
    const passed = locationPass && zonePass && structurePass;
    return {
      passed,
      label: passed ? "Armed Long Setup" : "No Long Setup",
      actual: `M30 ${m30MarketState.rangePosition ?? "--"}, M30 S ${m30NearSupport ? "near" : "far"}, M15 S ${m15NearSupport ? "near" : "far"}, M15 ${m15MarketState.trend ?? "--"}/${m15MarketState.regime ?? "--"}`,
    };
  }

  const locationPass = m30MarketState.rangePosition === "Upper" || m30MarketState.rangePosition === "Middle";
  const zonePass = m30NearResistance || m15NearResistance;
  const structurePass = !(m15MarketState.trend === "Bullish" && m15MarketState.regime === "Uptrend");
  const passed = locationPass && zonePass && structurePass;
  return {
    passed,
    label: passed ? "Armed Short Setup" : "No Short Setup",
    actual: `M30 ${m30MarketState.rangePosition ?? "--"}, M30 R ${m30NearResistance ? "near" : "far"}, M15 R ${m15NearResistance ? "near" : "far"}, M15 ${m15MarketState.trend ?? "--"}/${m15MarketState.regime ?? "--"}`,
  };
}

function detectLtfEntry(side, m5State, m1State) {
  const m5Candles = m5State?.candles || [];
  const m1Candles = m1State?.candles || [];
  if (m5Candles.length < 8) {
    return { passed: false, label: "Waiting", actual: "Need more M5 candles", stopAnchor: null };
  }

  const last = m5Candles[m5Candles.length - 1];
  const previous = m5Candles[m5Candles.length - 2];
  const localWindow = m5Candles.slice(-7, -1);
  const previousHigh = Math.max(...localWindow.map((candle) => Number(candle.high)));
  const previousLow = Math.min(...localWindow.map((candle) => Number(candle.low)));
  const m5Support = Number(m5State?.levels?.support);
  const m5Resistance = Number(m5State?.levels?.resistance);
  const m5Rsi = Number(m5State?.indicators?.rsi14);
  const m5VwapState = m5State?.indicators?.vwapState;
  const m5EmaState = m5State?.indicators?.emaState;
  const lastClose = Number(last.close);
  const previousClose = Number(previous.close);
  const bullishBody = Number(last.close) > Number(last.open);
  const bearishBody = Number(last.close) < Number(last.open);

  const m1Recent = m1Candles.slice(-6);
  const m1HigherLow =
    m1Recent.length >= 3 &&
    Number(m1Recent[m1Recent.length - 1].low) > Number(m1Recent[m1Recent.length - 3].low);
  const m1LowerHigh =
    m1Recent.length >= 3 &&
    Number(m1Recent[m1Recent.length - 1].high) < Number(m1Recent[m1Recent.length - 3].high);

  if (side === "long") {
    const closeBackAboveSupport =
      Number(last.close) > m5Support &&
      Number(previous.close) <= m5Support;
    const bullishImpulse =
      bullishBody &&
      Number(last.close) > Number(previous.high);
    const localBreak =
      Number(last.close) > previousHigh;
    const momentumContinuation =
      bullishBody &&
      lastClose > previousClose &&
      m5VwapState === "Above" &&
      m5EmaState === "Bull Stack";
    const rsiPush =
      bullishBody &&
      lastClose > previousClose &&
      m5EmaState === "Bull Stack" &&
      Number.isFinite(m5Rsi) &&
      m5Rsi >= 50;
    const passed = closeBackAboveSupport || bullishImpulse || localBreak || momentumContinuation || rsiPush;
    const triggerLabel = closeBackAboveSupport
      ? "M5 close back above support"
      : bullishImpulse
        ? "M5 bullish candle above previous high"
        : localBreak
          ? "M5 break above recent local high"
          : momentumContinuation
            ? "M5 bullish continuation above VWAP"
            : rsiPush
              ? "M5 bullish push with EMA stack and RSI"
              : "Waiting for long trigger";
    return {
      passed,
      label: passed ? "Long Triggered" : "Long Waiting",
      actual: `${triggerLabel}${m1HigherLow ? " + M1 higher low" : ""}`,
      stopAnchor: passed ? Number(last.low) : null,
    };
  }

  const closeBackBelowResistance =
    Number(last.close) < m5Resistance &&
    Number(previous.close) >= m5Resistance;
  const bearishImpulse =
    bearishBody &&
    Number(last.close) < Number(previous.low);
  const localBreak =
    Number(last.close) < previousLow;
  const momentumContinuation =
    bearishBody &&
    lastClose < previousClose &&
    m5VwapState === "Below" &&
    m5EmaState === "Bear Stack";
  const rsiPush =
    bearishBody &&
    lastClose < previousClose &&
    m5EmaState === "Bear Stack" &&
    Number.isFinite(m5Rsi) &&
    m5Rsi <= 50;
  const passed = closeBackBelowResistance || bearishImpulse || localBreak || momentumContinuation || rsiPush;
  const triggerLabel = closeBackBelowResistance
    ? "M5 close back below resistance"
    : bearishImpulse
      ? "M5 bearish candle below previous low"
      : localBreak
        ? "M5 break below recent local low"
        : momentumContinuation
          ? "M5 bearish continuation below VWAP"
          : rsiPush
            ? "M5 bearish push with EMA stack and RSI"
            : "Waiting for short trigger";
  return {
      passed,
      label: passed ? "Short Triggered" : "Short Waiting",
      actual: `${triggerLabel}${m1LowerHigh ? " + M1 lower high" : ""}`,
      stopAnchor: passed ? Number(last.high) : null,
  };
}

function calculateRiskPlan(side, chartBundle, triggerInfo = null) {
  const m5State = chartBundle.M5;
  const m15State = chartBundle.M15;
  const m30State = chartBundle.M30;
  const entry = Number(m5State?.candles?.[m5State.candles.length - 1]?.close);
  const atr = Number(m5State?.indicators?.atr14);
  const buffer = Number.isFinite(atr) ? atr * 0.2 : 0;
  if (!Number.isFinite(entry)) {
    return {
      entry: null,
      sl: null,
      tp: null,
      riskDistance: null,
      targetDistance: null,
      checks: [],
    };
  }

  const triggerPassed = Boolean(triggerInfo?.passed);
  if (!triggerPassed) {
    return {
      entry,
      sl: null,
      tp: null,
      riskDistance: null,
      targetDistance: null,
      checks: [
        {
          label: "Entry",
          value: formatPrice(entry),
          detail: "Current M5 close",
          raw: entry,
        },
        {
          label: "SL",
          value: "--",
          detail: "Waiting for live trigger before setting stop",
          raw: null,
        },
        {
          label: "TP",
          value: "--",
          detail: "Waiting for live trigger before selecting target",
          raw: null,
        },
        {
          label: "Distances",
          value: "R -- | T --",
          detail: "Risk plan appears only after a live trigger",
          raw: null,
        },
      ],
    };
  }

  let sl = null;
  let tp = null;
  let slDetail = `M5 liquidity / trigger swing ${Number.isFinite(atr) ? `+ ATR buffer ${formatPrice(buffer)}` : ""}`.trim();
  let tpDetail = `Halfway to nearest ${side === "long" ? "buy-side liquidity" : "sell-side liquidity"} from M15`;
  const recentM5 = (m5State?.candles || []).slice(-6);
  const recentSwingLow = recentM5.length ? Math.min(...recentM5.map((candle) => Number(candle.low))) : null;
  const recentSwingHigh = recentM5.length ? Math.max(...recentM5.map((candle) => Number(candle.high))) : null;
  const minStopDistance = Number.isFinite(atr) ? Math.max(atr * 0.75, 6) : 6;
  const maxStopDistance = Number.isFinite(atr) ? Math.max(atr * 1.8, 18) : 18;
  const minStructureGap = Number.isFinite(atr) ? Math.max(atr * 0.3, 2.5) : 2.5;
  const m5SellSideLiquidity = getLiquidityPools(m5State?.candles || [], "low", 24);
  const m5BuySideLiquidity = getLiquidityPools(m5State?.candles || [], "high", 24);
  const m15SellSideLiquidity = getLiquidityPools(m15State?.candles || [], "low", 80);
  const m15BuySideLiquidity = getLiquidityPools(m15State?.candles || [], "high", 80);
  const m15SwingLows = getSwingCandidates(m15State?.candles || [], "low", 100);
  const m15SwingHighs = getSwingCandidates(m15State?.candles || [], "high", 100);
  const m30SwingLows = getSwingCandidates(m30State?.candles || [], "low", 120);
  const m30SwingHighs = getSwingCandidates(m30State?.candles || [], "high", 120);
  const minTargetGapFloor = Number.isFinite(atr) ? Math.max(atr * 0.6, 4) : 4;
  if (side === "long") {
    const levelSupport = Number(m5State?.levels?.support);
    const triggerLow = Number(triggerInfo?.stopAnchor);
    const baseSl = chooseDirectionalAnchor(
      entry,
      [triggerLow, ...m5SellSideLiquidity, recentSwingLow, levelSupport],
      "below",
      minStructureGap,
      maxStopDistance
    );
    sl = Number.isFinite(baseSl) ? baseSl - buffer : null;
    if (m5SellSideLiquidity.some((value) => Number.isFinite(baseSl) && Math.abs(value - baseSl) < 0.0001)) {
      slDetail = `Below nearest M5 sell-side liquidity ${Number.isFinite(atr) ? `+ ATR buffer ${formatPrice(buffer)}` : ""}`.trim();
    } else if (Number.isFinite(triggerLow) && Number.isFinite(baseSl) && Math.abs(triggerLow - baseSl) < 0.0001) {
      slDetail = `Below M5 trigger candle low ${Number.isFinite(atr) ? `+ ATR buffer ${formatPrice(buffer)}` : ""}`.trim();
    } else {
      slDetail = `Below nearest M5 swing / level ${Number.isFinite(atr) ? `+ ATR buffer ${formatPrice(buffer)}` : ""}`.trim();
    }
    const normalizedStop = clampStopDistance(entry, sl, "long", minStopDistance, maxStopDistance);
    sl = normalizedStop.value;
    if (normalizedStop.mode === "floored") {
      slDetail = `Below M5 structure with minimum volatility floor ${formatPrice(minStopDistance)}`;
    } else if (normalizedStop.mode === "capped") {
      slDetail = `Below M5 structure capped for execution ${formatPrice(maxStopDistance)}`;
    }
    const riskSeed = Number.isFinite(sl) ? Math.abs(entry - sl) : null;
    const minTargetGap = Math.max(
      minTargetGapFloor,
      Number.isFinite(riskSeed) ? riskSeed * 0.35 : 0
    );
    const maxTargetGap = Number.isFinite(riskSeed)
      ? Math.max(riskSeed * 3, Number.isFinite(atr) ? atr * 6 : 24)
      : (Number.isFinite(atr) ? atr * 6 : 24);
    const m15ResistanceCandidates = [
      ...m15BuySideLiquidity,
      ...m15SwingHighs,
      Number(m15State?.levels?.resistance),
    ]
      .filter((value) => Number.isFinite(value) && value > entry)
      .sort((left, right) => left - right);
    const fullTarget = chooseDirectionalAnchor(entry, m15ResistanceCandidates, "above", minTargetGap, maxTargetGap);
    tp = Number.isFinite(fullTarget) ? entry + (fullTarget - entry) * 0.5 : null;
    if (!Number.isFinite(tp)) {
      const m30ResistanceCandidates = [
        ...m30SwingHighs,
        Number(m30State?.levels?.resistance),
      ]
        .filter((value) => Number.isFinite(value) && value > entry)
        .sort((left, right) => left - right);
      const fullFallback = chooseDirectionalAnchor(entry, m30ResistanceCandidates, "above", minTargetGap, maxTargetGap);
      tp = Number.isFinite(fullFallback) ? entry + (fullFallback - entry) * 0.5 : null;
      if (Number.isFinite(tp)) {
        tpDetail = "Halfway to nearest M30 fallback target";
      } else if (m15ResistanceCandidates.length) {
        const nearest = m15ResistanceCandidates[0];
        tp = Number.isFinite(nearest) ? entry + (nearest - entry) * 0.5 : null;
        tpDetail = "Halfway to nearest M15 liquidity / swing target";
      } else if (m30ResistanceCandidates.length) {
        const nearest = m30ResistanceCandidates[0];
        tp = Number.isFinite(nearest) ? entry + (nearest - entry) * 0.5 : null;
        tpDetail = "Halfway to nearest M30 fallback target";
      }
    }
  } else {
    const levelResistance = Number(m5State?.levels?.resistance);
    const triggerHigh = Number(triggerInfo?.stopAnchor);
    const baseSl = chooseDirectionalAnchor(
      entry,
      [triggerHigh, ...m5BuySideLiquidity, recentSwingHigh, levelResistance],
      "above",
      minStructureGap,
      maxStopDistance
    );
    sl = Number.isFinite(baseSl) ? baseSl + buffer : null;
    if (m5BuySideLiquidity.some((value) => Number.isFinite(baseSl) && Math.abs(value - baseSl) < 0.0001)) {
      slDetail = `Above nearest M5 buy-side liquidity ${Number.isFinite(atr) ? `+ ATR buffer ${formatPrice(buffer)}` : ""}`.trim();
    } else if (Number.isFinite(triggerHigh) && Number.isFinite(baseSl) && Math.abs(triggerHigh - baseSl) < 0.0001) {
      slDetail = `Above M5 trigger candle high ${Number.isFinite(atr) ? `+ ATR buffer ${formatPrice(buffer)}` : ""}`.trim();
    } else {
      slDetail = `Above nearest M5 swing / level ${Number.isFinite(atr) ? `+ ATR buffer ${formatPrice(buffer)}` : ""}`.trim();
    }
    const normalizedStop = clampStopDistance(entry, sl, "short", minStopDistance, maxStopDistance);
    sl = normalizedStop.value;
    if (normalizedStop.mode === "floored") {
      slDetail = `Above M5 structure with minimum volatility floor ${formatPrice(minStopDistance)}`;
    } else if (normalizedStop.mode === "capped") {
      slDetail = `Above M5 structure capped for execution ${formatPrice(maxStopDistance)}`;
    }
    const riskSeed = Number.isFinite(sl) ? Math.abs(entry - sl) : null;
    const minTargetGap = Math.max(
      minTargetGapFloor,
      Number.isFinite(riskSeed) ? riskSeed * 0.35 : 0
    );
    const maxTargetGap = Number.isFinite(riskSeed)
      ? Math.max(riskSeed * 3, Number.isFinite(atr) ? atr * 6 : 24)
      : (Number.isFinite(atr) ? atr * 6 : 24);
    const m15SupportCandidates = [
      ...m15SellSideLiquidity,
      ...m15SwingLows,
      Number(m15State?.levels?.support),
    ]
      .filter((value) => Number.isFinite(value) && value < entry)
      .sort((left, right) => right - left);
    const fullTarget = chooseDirectionalAnchor(entry, m15SupportCandidates, "below", minTargetGap, maxTargetGap);
    tp = Number.isFinite(fullTarget) ? entry - (entry - fullTarget) * 0.5 : null;
    if (!Number.isFinite(tp)) {
      const m30SupportCandidates = [
        ...m30SwingLows,
        Number(m30State?.levels?.support),
      ]
        .filter((value) => Number.isFinite(value) && value < entry)
        .sort((left, right) => right - left);
      const fullFallback = chooseDirectionalAnchor(entry, m30SupportCandidates, "below", minTargetGap, maxTargetGap);
      tp = Number.isFinite(fullFallback) ? entry - (entry - fullFallback) * 0.5 : null;
      if (Number.isFinite(tp)) {
        tpDetail = "Halfway to nearest M30 fallback target";
      } else if (m15SupportCandidates.length) {
        const nearest = m15SupportCandidates[0];
        tp = Number.isFinite(nearest) ? entry - (entry - nearest) * 0.5 : null;
        tpDetail = "Halfway to nearest M15 liquidity / swing target";
      } else if (m30SupportCandidates.length) {
        const nearest = m30SupportCandidates[0];
        tp = Number.isFinite(nearest) ? entry - (entry - nearest) * 0.5 : null;
        tpDetail = "Halfway to nearest M30 fallback target";
      }
    }
  }

  const riskDistance = Number.isFinite(sl) ? Math.abs(entry - sl) : null;
  const targetDistance = Number.isFinite(tp) ? Math.abs(tp - entry) : null;

  return {
    entry,
    sl,
    tp,
    riskDistance,
    targetDistance,
    checks: [
      {
        label: "Entry",
        value: formatPrice(entry),
        detail: "Current M5 close",
        raw: entry,
      },
      {
        label: "SL",
        value: Number.isFinite(sl) ? formatPrice(sl) : "--",
        detail: slDetail,
        raw: sl,
      },
      {
        label: "TP",
        value: Number.isFinite(tp) ? formatPrice(tp) : "--",
        detail: tpDetail,
        raw: tp,
      },
      {
        label: "Distances",
        value: `R ${Number.isFinite(riskDistance) ? formatPrice(riskDistance) : "--"} | T ${Number.isFinite(targetDistance) ? formatPrice(targetDistance) : "--"}`,
        detail: "Risk and target distance",
        raw: null,
      },
    ],
  };
}

function calculateTradeOverview() {
  const h4 = chartState.H4?.marketState;
  const h1 = chartState.H1?.marketState;
  const m30 = chartState.M30?.marketState;
  const m15 = chartState.M15?.marketState;
  const m5 = chartState.M5?.marketState;
  const m30State = chartState.M30;
  const m15State = chartState.M15;
  const m5State = chartState.M5;
  const m1State = chartState.M1;
  const h1State = chartState.H1;
  const h4State = chartState.H4;

  if (!h4 || !h1 || !m30 || !m15 || !m5 || !m30State || !m15State || !m5State || !m1State || !h1State || !h4State) {
    return {
      action: "No Trade",
      score: 0,
      longScore: 0,
      shortScore: 0,
      longChecks: { mandatory: [], confirmation: [], risk: [] },
      shortChecks: { mandatory: [], confirmation: [], risk: [] },
      summary: "Waiting for enough timeframe data to evaluate long and short thresholds.",
    };
  }

  const htfLongAllowed =
    (h4.regime === "Uptrend" || h4.regime === "Transition") &&
    (h1.trend === "Bullish" || h1.regime === "Transition");
  const htfShortAllowed =
    (h4.regime === "Downtrend" || h4.regime === "Transition") &&
    (h1.trend === "Bearish" || h1.regime === "Transition");
  const htfLongCountertrend =
    h4.regime === "Downtrend" || h1.trend === "Bearish";
  const htfShortCountertrend =
    h4.regime === "Uptrend" || h1.trend === "Bullish";

  const mtfLong = buildMtfSetup("long", m30State, m15State);
  const mtfShort = buildMtfSetup("short", m30State, m15State);
  const ltfLong = detectLtfEntry("long", m5State, m1State);
  const ltfShort = detectLtfEntry("short", m5State, m1State);
  const longRisk = calculateRiskPlan("long", chartState, ltfLong);
  const shortRisk = calculateRiskPlan("short", chartState, ltfShort);

  const m5EmaLong = m5State?.indicators?.emaState === "Bull Stack";
  const m5EmaShort = m5State?.indicators?.emaState === "Bear Stack";
  const h1AdxReady = Number(h1State?.indicators?.adx14) >= 20;
  const m15RsiLong = Number(m15State?.indicators?.rsi14) > 55;
  const m15RsiShort = Number(m15State?.indicators?.rsi14) < 45;
  const m5RsiLong = Number(m5State?.indicators?.rsi14) > 52;
  const m5RsiShort = Number(m5State?.indicators?.rsi14) < 48;
  const vwapLong = m5State?.indicators?.vwapState === "Above";
  const vwapShort = m5State?.indicators?.vwapState === "Below";

  const longContext = [
    {
      label: "HTF picture",
      expected: "Bullish picture from H4 and H1",
      actual: `H4 ${h4.regime}, H1 ${h1.trend}/${h1.regime}`,
      passed: htfLongAllowed,
      score: htfLongAllowed ? 25 : 0,
    },
    {
      label: "MTF placement",
      expected: "Placed near support with M30 Lower/Middle",
      actual: mtfLong.actual,
      passed: mtfLong.passed,
      score: mtfLong.passed ? 20 : 0,
    },
    {
      label: "M5 EMA alignment",
      expected: "EMA 9 > 20 > 50",
      actual: m5State?.indicators?.emaState ?? "--",
      passed: m5EmaLong,
      score: m5EmaLong ? 15 : 0,
    },
  ];

  const shortContext = [
    {
      label: "HTF picture",
      expected: "Bearish picture from H4 and H1",
      actual: `H4 ${h4.regime}, H1 ${h1.trend}/${h1.regime}`,
      passed: htfShortAllowed,
      score: htfShortAllowed ? 25 : 0,
    },
    {
      label: "MTF placement",
      expected: "Placed near resistance with M30 Upper/Middle",
      actual: mtfShort.actual,
      passed: mtfShort.passed,
      score: mtfShort.passed ? 20 : 0,
    },
    {
      label: "M5 EMA alignment",
      expected: "EMA 9 < 20 < 50",
      actual: m5State?.indicators?.emaState ?? "--",
      passed: m5EmaShort,
      score: m5EmaShort ? 15 : 0,
    },
  ];

  const longConfirmation = [
    {
      label: "H1 ADX",
      expected: "ADX >= 20",
      actual: Number.isFinite(h1State?.indicators?.adx14) ? h1State.indicators.adx14.toFixed(1) : "--",
      passed: h1AdxReady,
      score: h1AdxReady ? 8 : 0,
    },
    {
      label: "M15 RSI",
      expected: "RSI > 55",
      actual: Number.isFinite(m15State?.indicators?.rsi14) ? m15State.indicators.rsi14.toFixed(1) : "--",
      passed: m15RsiLong,
      score: m15RsiLong ? 12 : 0,
    },
    {
      label: "M5 RSI",
      expected: "RSI > 52",
      actual: Number.isFinite(m5State?.indicators?.rsi14) ? m5State.indicators.rsi14.toFixed(1) : "--",
      passed: m5RsiLong,
      score: m5RsiLong ? 12 : 0,
    },
    {
      label: "M5 VWAP",
      expected: "Price above VWAP",
      actual: m5State?.indicators?.vwapState ?? "--",
      passed: vwapLong,
      score: vwapLong ? 10 : 0,
    },
  ];

  const shortConfirmation = [
    {
      label: "H1 ADX",
      expected: "ADX >= 20",
      actual: Number.isFinite(h1State?.indicators?.adx14) ? h1State.indicators.adx14.toFixed(1) : "--",
      passed: h1AdxReady,
      score: h1AdxReady ? 8 : 0,
    },
    {
      label: "M15 RSI",
      expected: "RSI < 45",
      actual: Number.isFinite(m15State?.indicators?.rsi14) ? m15State.indicators.rsi14.toFixed(1) : "--",
      passed: m15RsiShort,
      score: m15RsiShort ? 12 : 0,
    },
    {
      label: "M5 RSI",
      expected: "RSI < 48",
      actual: Number.isFinite(m5State?.indicators?.rsi14) ? m5State.indicators.rsi14.toFixed(1) : "--",
      passed: m5RsiShort,
      score: m5RsiShort ? 12 : 0,
    },
    {
      label: "M5 VWAP",
      expected: "Price below VWAP",
      actual: m5State?.indicators?.vwapState ?? "--",
      passed: vwapShort,
      score: vwapShort ? 10 : 0,
    },
  ];

  const longBaseScore =
    (ltfLong.passed ? 25 : 0) +
    [...longContext, ...longConfirmation].reduce((sum, item) => sum + item.score, 0);
  const shortBaseScore =
    (ltfShort.passed ? 25 : 0) +
    [...shortContext, ...shortConfirmation].reduce((sum, item) => sum + item.score, 0);
  const longScore = longBaseScore;
  const shortScore = shortBaseScore;
  const longTriggerReady = ltfLong.passed;
  const shortTriggerReady = ltfShort.passed;
  const longQualityGate = Boolean(m5EmaLong && (mtfLong.passed || htfLongAllowed) && (m15RsiLong || vwapLong));
  const shortQualityGate = Boolean(m5EmaShort && (mtfShort.passed || htfShortAllowed) && (m15RsiShort || vwapShort));

  let action = "No Trade";
  let score = Math.max(longScore, shortScore);
  let tradeType = "No Setup";

  if (longTriggerReady && longQualityGate && longScore >= TRADE_SCORE_THRESHOLD && longScore >= shortScore + TRADE_SCORE_EDGE) {
    action = htfLongAllowed ? "Trend Buy Ready" : "Countertrend Buy Ready";
    score = longScore;
    tradeType = htfLongAllowed ? "Trend Buy" : htfLongCountertrend ? "Countertrend Buy" : "Buy";
  } else if (shortTriggerReady && shortQualityGate && shortScore >= TRADE_SCORE_THRESHOLD && shortScore >= longScore + TRADE_SCORE_EDGE) {
    action = htfShortAllowed ? "Trend Sell Ready" : "Countertrend Sell Ready";
    score = shortScore;
    tradeType = htfShortAllowed ? "Trend Sell" : htfShortCountertrend ? "Countertrend Sell" : "Sell";
  }
  const summary =
    action === "No Trade"
      ? `No trade yet. The engine now needs a live LTF trigger, a quality pass, plus a score of ${TRADE_SCORE_THRESHOLD} with at least a ${TRADE_SCORE_EDGE}-point edge. Long reads ${longScore}, short reads ${shortScore}.`
      : `${action} with score ${score}. This is a ${tradeType.toLowerCase()} where the live trigger is active and the score lead is strong enough.`;

  const longMandatory = [
    {
      label: "LTF detail",
      expected: "M5 detail trigger for a long entry",
      actual: ltfLong.actual,
      passed: ltfLong.passed,
      score: ltfLong.passed ? 25 : 0,
    },
    {
      label: "Score threshold",
      expected: `Score >= ${TRADE_SCORE_THRESHOLD}`,
      actual: String(longScore),
      passed: longScore >= TRADE_SCORE_THRESHOLD,
      score: 0,
    },
    {
      label: "Score edge",
      expected: `Lead short by >= ${TRADE_SCORE_EDGE}`,
      actual: `Lead ${longScore - shortScore}`,
      passed: longScore >= shortScore + TRADE_SCORE_EDGE,
      score: 0,
    },
    {
      label: "Quality gate",
      expected: "M5 EMA plus supportive HTF/MTF and momentum",
      actual: longQualityGate ? "Passed" : "Waiting for cleaner long alignment",
      passed: longQualityGate,
      score: 0,
    },
  ];

  const shortMandatory = [
    {
      label: "LTF detail",
      expected: "M5 detail trigger for a short entry",
      actual: ltfShort.actual,
      passed: ltfShort.passed,
      score: ltfShort.passed ? 25 : 0,
    },
    {
      label: "Score threshold",
      expected: `Score >= ${TRADE_SCORE_THRESHOLD}`,
      actual: String(shortScore),
      passed: shortScore >= TRADE_SCORE_THRESHOLD,
      score: 0,
    },
    {
      label: "Score edge",
      expected: `Lead long by >= ${TRADE_SCORE_EDGE}`,
      actual: `Lead ${shortScore - longScore}`,
      passed: shortScore >= longScore + TRADE_SCORE_EDGE,
      score: 0,
    },
    {
      label: "Quality gate",
      expected: "M5 EMA plus supportive HTF/MTF and momentum",
      actual: shortQualityGate ? "Passed" : "Waiting for cleaner short alignment",
      passed: shortQualityGate,
      score: 0,
    },
  ];

  return {
    action,
    score,
    tradeType,
    longScore,
    shortScore,
    longChecks: {
      mandatory: longMandatory,
      context: longContext,
      confirmation: longConfirmation,
      risk: longRisk.checks,
    },
    shortChecks: {
      mandatory: shortMandatory,
      context: shortContext,
      confirmation: shortConfirmation,
      risk: shortRisk.checks,
    },
    summary,
  };
}

function renderChecks(container, groupedChecks) {
  if (!container) return;
  container.innerHTML = "";
  const sections = [
    { key: "mandatory", title: "Mandatory" },
    { key: "context", title: "Context" },
    { key: "confirmation", title: "Confirmation" },
    { key: "risk", title: "Risk" },
  ];
  for (const section of sections) {
    const checks = groupedChecks?.[section.key] || [];
    if (!checks.length) continue;
    const block = document.createElement("section");
    block.className = "overview-check-group";
    const title = document.createElement("h4");
    title.className = "overview-check-group-title";
    title.textContent = section.title;
    block.appendChild(title);

    for (const item of checks) {
      const isRisk = section.key === "risk";
      const row = document.createElement("div");
      row.className = `overview-check ${isRisk ? "is-risk" : item.passed ? "is-pass" : "is-fail"}`;
      row.innerHTML = `
        <div class="overview-check-top">
          <span>${item.label}</span>
          <strong>${isRisk ? item.value : item.passed ? `+${item.score}` : "+0"}</strong>
        </div>
        <div class="overview-check-bottom">
          <span>${isRisk ? item.detail : `Need: ${item.expected}`}</span>
          <span>${isRisk ? "" : `Now: ${item.actual}`}</span>
        </div>
      `;
      block.appendChild(row);
    }
    container.appendChild(block);
  }
}

function setAutoTradeUi() {
  if (autotradeStatusLabel) {
    autotradeStatusLabel.textContent = autoTradeConfig.enabled ? "On" : "Off";
  }
  if (autotradeToggleButton) {
    autotradeToggleButton.textContent = autoTradeConfig.enabled ? "Disable Auto" : "Enable Auto";
  }
  if (autotradeLotInput) {
    autotradeLotInput.value = String(autoTradeConfig.lot);
  }
}

async function loadAutoTradeStatus() {
  try {
    const response = await fetch("/api/autotrade/status", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Failed to load auto trade status.");
    autoTradeConfig.enabled = Boolean(payload.enabled);
    autoTradeConfig.lot = Number(payload.lot || 0.01);
    tradeActive = Boolean(payload.trade_active);
    activeTradeSnapshot = payload.active_trade || null;
    cooldownRemainingSeconds = Number(payload.cooldown_remaining_seconds || 0);
    setAutoTradeUi();
    renderCooldownLabel();
    renderActiveTradePanel();
  } catch (error) {
    bridgeStatus.textContent = "Auto route missing";
  }
}

async function saveAutoTradeConfig() {
  const nextLot = Math.max(0.01, Number(autotradeLotInput?.value || autoTradeConfig.lot || 0.01));
  const response = await fetch("/api/autotrade/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      enabled: autoTradeConfig.enabled,
      lot: nextLot,
    }),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || "Failed to save auto trade config.");
  autoTradeConfig.enabled = Boolean(payload.enabled);
  autoTradeConfig.lot = Number(payload.lot || nextLot);
  setAutoTradeUi();
}

function buildExecutionPayload() {
  if (!latestTradeOverview || !String(latestTradeOverview.action || "").includes("Ready")) {
    return null;
  }
  const side = String(latestTradeOverview.action).includes("Buy") ? "buy" : "sell";
  const riskChecks = side === "buy" ? latestTradeOverview.longChecks?.risk : latestTradeOverview.shortChecks?.risk;
  const lookup = (label) => riskChecks?.find((item) => item.label === label)?.raw ?? null;
  const m5LastTime = Number(chartState.M5?.candles?.[chartState.M5.candles.length - 1]?.time);
  const entry = lookup("Entry");
  const sl = lookup("SL");
  const tp = lookup("TP");
  if (!Number.isFinite(m5LastTime) || !Number.isFinite(Number(entry)) || !Number.isFinite(Number(sl)) || !Number.isFinite(Number(tp))) {
    return null;
  }
  return {
    symbol: String(activeSymbolLabel.textContent || symbolInput.value || "XAUUSD").trim().toUpperCase(),
    side,
    action: latestTradeOverview.action,
    confidence: latestTradeOverview.score,
    lot: Math.max(0.01, Number(autotradeLotInput?.value || autoTradeConfig.lot || 0.01)),
    entry,
    sl,
    tp,
    signal_id: `${side}:${m5LastTime}`,
  };
}

async function maybeExecuteAutoTrade() {
  if (!autoTradeConfig.enabled) return;
  const payload = buildExecutionPayload();
  if (!payload) return;
  try {
    const response = await fetch("/api/autotrade/evaluate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Auto trade request failed.");
    if (result.status === "placed") {
      bridgeStatus.textContent = `${payload.side === "buy" ? "Buy" : "Sell"} sent`;
      tradeActive = true;
      activeTradeSnapshot = {
        kind: "position",
        ticket: result.ticket || activeTradeSnapshot?.ticket || "--",
        symbol: payload.symbol,
        side: payload.side,
        volume: payload.lot,
        price: payload.entry,
        sl: payload.sl,
        tp: payload.tp,
      };
      cooldownRemainingSeconds = 0;
      renderCooldownLabel();
      renderActiveTradePanel();
      await loadAutoTradeStatus();
    } else if (result.status === "blocked" || result.status === "rejected") {
      bridgeStatus.textContent = result.detail || "Trade blocked";
    } else if (result.status === "cooldown") {
      bridgeStatus.textContent = "Auto cooldown";
      tradeActive = false;
      activeTradeSnapshot = null;
      cooldownRemainingSeconds = Number(result.cooldown_remaining_seconds || cooldownRemainingSeconds || 0);
      renderCooldownLabel();
      renderActiveTradePanel();
    } else if (result.status === "duplicate") {
      bridgeStatus.textContent = "Signal already sent";
    } else if (result.status === "disabled") {
      bridgeStatus.textContent = "Auto disabled";
    }
  } catch (error) {
    bridgeStatus.textContent = "Auto trade failed";
  }
}

function renderTradeOverview() {
  const overview = calculateTradeOverview();
  latestTradeOverview = overview;
  overviewAction.textContent = overview.action;
  overviewScore.textContent = `${overview.score}/100`;
  overviewLongScore.textContent = `${overview.longScore}/100`;
  overviewShortScore.textContent = `${overview.shortScore}/100`;
  renderChecks(overviewLongChecks, overview.longChecks);
  renderChecks(overviewShortChecks, overview.shortChecks);
  overviewSummary.textContent = overview.summary;
  renderIndicatorPanel();
}

function refreshDerivedState(state) {
  state.levels = calculateLevels(state.candles);
  state.marketState = calculateMarketState(state.candles);
  state.indicators = calculateIndicators(state.candles);
}

function getIndicatorToneClass(type, value) {
  if (type === "ema") {
    if (value === "Bull Stack") return "tone-bull";
    if (value === "Bear Stack") return "tone-bear";
    return "tone-range";
  }
  if (type === "rsi") {
    if (value === "Bullish") return "tone-bull";
    if (value === "Bearish") return "tone-bear";
    return "tone-range";
  }
  if (type === "adx") {
    if (value === "Strong") return "tone-bull";
    if (value === "Building") return "tone-transition";
    return "tone-range";
  }
  if (type === "vwap") {
    if (value === "Above") return "tone-bull";
    if (value === "Below") return "tone-bear";
    return "tone-range";
  }
  return "tone-range";
}

function renderIndicatorPanel() {
  if (!indicatorGrid) return;
  indicatorGrid.innerHTML = "";
  for (const timeframe of TIMEFRAMES) {
    const indicators = chartState[timeframe]?.indicators;
    const card = document.createElement("section");
    card.className = "indicator-card";
    if (!indicators) {
      card.innerHTML = `
        <div class="indicator-card-header">
          <h4>${timeframe}</h4>
          <span>Waiting</span>
        </div>
      `;
      indicatorGrid.appendChild(card);
      continue;
    }

    const rows = [
      {
        label: "EMA 9/20/50",
        value: indicators.emaState,
        detail: `${formatPrice(indicators.ema9)} / ${formatPrice(indicators.ema20)} / ${formatPrice(indicators.ema50)}`,
        tone: getIndicatorToneClass("ema", indicators.emaState),
      },
      {
        label: "RSI 14",
        value: `${indicators.rsiState} ${Number.isFinite(indicators.rsi14) ? indicators.rsi14.toFixed(1) : "--"}`,
        detail: "Bull > 55 | Bear < 45",
        tone: getIndicatorToneClass("rsi", indicators.rsiState),
      },
      {
        label: "ADX 14",
        value: `${indicators.adxState} ${Number.isFinite(indicators.adx14) ? indicators.adx14.toFixed(1) : "--"}`,
        detail: "Strong >= 25 | Build >= 20",
        tone: getIndicatorToneClass("adx", indicators.adxState),
      },
      {
        label: "ATR 14",
        value: formatPrice(indicators.atr14),
        detail: "Live volatility buffer",
        tone: "tone-range",
      },
      {
        label: "VWAP",
        value: `${indicators.vwapState} ${formatPrice(indicators.vwap)}`,
        detail: `Close ${formatPrice(indicators.latestClose)}`,
        tone: getIndicatorToneClass("vwap", indicators.vwapState),
      },
    ];

    card.innerHTML = `
      <div class="indicator-card-header">
        <h4>${timeframe}</h4>
        <span>${chartState[timeframe]?.marketState?.regime ?? "--"}</span>
      </div>
      <div class="indicator-rows">
        ${rows.map((row) => `
          <div class="indicator-row ${row.tone}">
            <div>
              <strong>${row.label}</strong>
              <p>${row.detail}</p>
            </div>
            <span>${row.value}</span>
          </div>
        `).join("")}
      </div>
    `;
    indicatorGrid.appendChild(card);
  }
}

function setTrendBadge(element, trend) {
  if (!element) return;
  element.textContent = trend;
  element.classList.remove("is-bull", "is-bear", "is-neutral", "is-compression", "is-transition", "is-range");
  if (trend === "Bullish" || trend === "Uptrend") element.classList.add("is-bull");
  else if (trend === "Bearish" || trend === "Downtrend") element.classList.add("is-bear");
  else if (trend === "Compression") element.classList.add("is-compression");
  else if (trend === "Transition") element.classList.add("is-transition");
  else if (trend === "Range") element.classList.add("is-range");
  else element.classList.add("is-neutral");
}

function setStateChipTone(element, value, kind) {
  if (!element) return;
  element.classList.remove("tone-bull", "tone-bear", "tone-range", "tone-compression", "tone-transition", "tone-neutral");

  const text = String(value || "");
  if (text === "Uptrend" || text === "Bullish" || (kind === "range" && text === "Upper")) {
    element.classList.add("tone-bull");
    return;
  }
  if (text === "Downtrend" || text === "Bearish" || (kind === "range" && text === "Lower")) {
    element.classList.add("tone-bear");
    return;
  }
  if (text === "Range" || (kind === "range" && text === "Middle")) {
    element.classList.add("tone-range");
    return;
  }
  if (text === "Compression") {
    element.classList.add("tone-compression");
    return;
  }
  if (text === "Transition") {
    element.classList.add("tone-transition");
    return;
  }
  element.classList.add("tone-neutral");
}

function resizeCanvases() {
  for (const timeframe of TIMEFRAMES) {
    const canvas = domRefs[timeframe]?.canvas;
    const card = domRefs[timeframe]?.card;
    if (!canvas || card?.classList.contains("is-collapsed")) continue;
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
  setTrendBadge(refs.badge, state.marketState?.regime || getTrend(summary));
  refs.price.textContent = latest ? formatPrice(latest.close) : "--";
  refs.summary.textContent = summary?.tone
    ? `${summary.tone} tone | ${visibleCandles.length} visible / ${state.candles.length} loaded`
    : "Waiting for MT5 data";
  refs.range.textContent =
    summary?.range_low != null && summary?.range_high != null
      ? `${formatPrice(summary.range_low)} - ${formatPrice(summary.range_high)}`
      : "--";
  refs.regime.textContent = state.marketState?.regime ?? "--";
  refs.trend.textContent = state.marketState?.trend ?? "--";
  refs.rangeState.textContent = state.marketState?.rangePosition ?? "--";
  setStateChipTone(refs.regimeChip, state.marketState?.regime, "regime");
  setStateChipTone(refs.trendChip, state.marketState?.trend, "trend");
  setStateChipTone(refs.rangeChip, state.marketState?.rangePosition, "range");
  refs.resistance.textContent = state.levels?.resistance != null ? formatPrice(state.levels.resistance) : "--";
  refs.support.textContent = state.levels?.support != null ? formatPrice(state.levels.support) : "--";
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

  if (state.levels?.resistance != null) {
    const y = priceToY(Number(state.levels.resistance));
    ctx.strokeStyle = "rgba(239, 68, 68, 0.9)";
    ctx.lineWidth = 1.2;
    ctx.setLineDash([8, 6]);
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(width - padding.right, y);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(239, 68, 68, 0.95)";
    ctx.font = "12px Segoe UI";
    ctx.fillText(`R ${formatPrice(state.levels.resistance)}`, padding.left + 8, y - 6);
  }

  if (state.levels?.support != null) {
    const y = priceToY(Number(state.levels.support));
    ctx.strokeStyle = "rgba(103, 166, 255, 0.85)";
    ctx.lineWidth = 1.2;
    ctx.setLineDash([8, 6]);
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(width - padding.right, y);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(103, 166, 255, 0.92)";
    ctx.font = "12px Segoe UI";
    ctx.fillText(`S ${formatPrice(state.levels.support)}`, padding.left + 8, y - 6);
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
    const hoverText = `${formatDisplayTimestamp(candle.time)}  O ${formatPrice(candle.open)}  H ${formatPrice(candle.high)}  L ${formatPrice(candle.low)}  C ${formatPrice(candle.close)}  V ${Number(candle.tick_volume ?? 0).toLocaleString()}`;
    ctx.fillStyle = "rgba(10, 18, 30, 0.92)";
    ctx.fillRect(18, 10, Math.min(620, width - 36), 26);
    ctx.fillStyle = "#edf4ff";
    ctx.font = "12px Segoe UI";
    ctx.fillText(hoverText, 24, 28);
  }
}

function renderBoard() {
  renderTradeOverview();
  for (const timeframe of TIMEFRAMES) {
    const state = chartState[timeframe];
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
    refreshDerivedState(state);
  }
  renderBoard();
  maybeExecuteAutoTrade();
}

async function loadBoard() {
  const symbol = String(symbolInput.value || "XAUUSD").trim().toUpperCase() || "XAUUSD";
  const limitRaw = String(limitInput.value || "ALL").trim().toUpperCase() || "ALL";
  const limit = limitRaw === "ALL" ? "ALL" : String(Math.max(80, Math.min(99999, Number(limitRaw || 99999))));
  refreshButton.disabled = true;
  bridgeStatus.textContent = "Syncing";
  activeSymbolLabel.textContent = symbol;
  try {
    let loadedCount = 0;
    for (const timeframe of TIMEFRAMES) {
      const response = await fetch(
        `/api/timeframe?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}&limit=${encodeURIComponent(limit)}`,
        { cache: "no-store" }
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || `Failed to load ${timeframe} candles.`);

      const state = chartState[timeframe];
      if (!state) continue;
      activeSymbolLabel.textContent = payload.symbol || symbol;
      state.candles = Array.isArray(payload.candles) ? payload.candles : [];
      state.summary = payload.summary || null;
      refreshDerivedState(state);
      state.offset = Math.min(state.offset, getMaxOffset(state));
      state.hoverIndex = null;
      renderSingleChart(timeframe);

      loadedCount += 1;
      bridgeStatus.textContent = loadedCount === TIMEFRAMES.length ? "Live" : `Syncing ${loadedCount}/${TIMEFRAMES.length}`;
    }
    await maybeExecuteAutoTrade();
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
      refreshDerivedState(state);
    }
    renderBoard();
    await maybeExecuteAutoTrade();
    await loadAutoTradeStatus();
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

function jumpTimeframeToNewest(timeframe) {
  const state = chartState[timeframe];
  if (!state) return;
  state.offset = 0;
  state.hoverIndex = null;
  drawChart(timeframe);
  updateCardMeta(timeframe);
}

function renderSingleChart(timeframe) {
  updateCardMeta(timeframe);
  drawChart(timeframe);
}

function setCollapsedState(target, collapsed, button, expandedLabel = "-", collapsedLabel = "+") {
  if (!target || !button) return;
  target.classList.toggle("is-collapsed", collapsed);
  button.textContent = collapsed ? collapsedLabel : expandedLabel;
  button.setAttribute("aria-expanded", String(!collapsed));
}

function toggleTradeOverviewCollapsed() {
  const collapsed = !tradeOverviewElement?.classList.contains("is-collapsed");
  setCollapsedState(tradeOverviewElement, collapsed, tradeOverviewToggle);
}

function toggleAutoTradePanelCollapsed() {
  const collapsed = !autotradePanelElement?.classList.contains("is-collapsed");
  setCollapsedState(autotradePanelElement, collapsed, autotradePanelToggle);
}

function toggleChartCollapsed(timeframe) {
  const refs = domRefs[timeframe];
  if (!refs) return;
  const collapsed = !refs.card.classList.contains("is-collapsed");
  setCollapsedState(refs.card, collapsed, refs.collapseButton);
  if (!collapsed) {
    resizeCanvases();
    renderSingleChart(timeframe);
  }
}

function bindChartInteractions(timeframe) {
  const canvas = domRefs[timeframe]?.canvas;
  const state = chartState[timeframe];
  const resetButton = domRefs[timeframe]?.resetButton;
  const collapseButton = domRefs[timeframe]?.collapseButton;
  if (!canvas || !state) return;

  resetButton?.addEventListener("click", () => {
    jumpTimeframeToNewest(timeframe);
  });

  collapseButton?.addEventListener("click", () => {
    toggleChartCollapsed(timeframe);
  });

  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    const next = event.deltaY < 0 ? state.visibleCount - 8 : state.visibleCount + 8;
    state.visibleCount = Math.max(20, Math.min(240, next));
    state.offset = Math.min(state.offset, getMaxOffset(state));
    renderSingleChart(timeframe);
  }, { passive: false });

  canvas.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    state.dragging = true;
    state.lastPointerX = event.clientX;
    canvas.setPointerCapture(event.pointerId);
  });

  canvas.addEventListener("pointermove", (event) => {
    const rect = canvas.getBoundingClientRect();
    const visible = getVisibleCandles(state);
    if (!visible.length) return;
    const geometry = getChartGeometry(canvas, visible.length);

    if (state.dragging) {
      event.preventDefault();
      const pixelsPerBar = Math.max(4, geometry.barSpacing);
      const deltaX = event.clientX - state.lastPointerX;
      if (Math.abs(deltaX) >= pixelsPerBar) {
        const shift = Math.round(deltaX / pixelsPerBar);
        state.offset = Math.max(0, Math.min(getMaxOffset(state), state.offset + shift));
        state.lastPointerX = event.clientX;
        renderSingleChart(timeframe);
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

  const stopDragging = () => {
    state.hoverIndex = null;
    state.dragging = false;
    renderSingleChart(timeframe);
  };

  canvas.addEventListener("pointerup", stopDragging);
  canvas.addEventListener("pointercancel", stopDragging);
  canvas.addEventListener("mouseleave", () => {
    if (!state.dragging) {
      state.hoverIndex = null;
      drawChart(timeframe);
    }
  });
}

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
tradeOverviewToggle?.addEventListener("click", toggleTradeOverviewCollapsed);
autotradePanelToggle?.addEventListener("click", toggleAutoTradePanelCollapsed);
autotradeToggleButton?.addEventListener("click", async () => {
  autoTradeConfig.enabled = !autoTradeConfig.enabled;
  setAutoTradeUi();
  try {
    await saveAutoTradeConfig();
    bridgeStatus.textContent = autoTradeConfig.enabled ? "Auto enabled" : "Auto disabled";
  } catch (error) {
    autoTradeConfig.enabled = !autoTradeConfig.enabled;
    setAutoTradeUi();
    bridgeStatus.textContent = "Auto save failed";
  }
});
autotradeLotInput?.addEventListener("change", async () => {
  try {
    await saveAutoTradeConfig();
    bridgeStatus.textContent = "Lot updated";
  } catch (error) {
    setAutoTradeUi();
    bridgeStatus.textContent = "Lot save failed";
  }
});

buildBoard();
for (const timeframe of TIMEFRAMES) bindChartInteractions(timeframe);
formatClock();
window.setInterval(formatClock, 1000);
renderCooldownLabel();
renderActiveTradePanel();
startCooldownTicker();
resizeCanvases();
renderBoard();
setAutoTradeUi();
loadAutoTradeStatus();
loadBoard();
startLiveSync();
startTickSync();
