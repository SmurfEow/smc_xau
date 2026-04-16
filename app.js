const TIMEFRAMES = [
  "M1", "M5", "M15", "M30", "H1", "H4",
];
// Per-timeframe default candle counts — each TF gets an amount appropriate to its
// role in the top-down strategy rather than one flat limit for all.
//   M1/M5  — entry precision/confirmation: covers the current + last session (~5-20 hrs)
//   M15/M30 — trade location / structure: covers 2-3 trading days
//   H1      — trend & weekly bias: covers ~1 trading week (5 sessions)
//   H4      — phase & directional bias: covers ~3 weeks of structure
const TIMEFRAME_DEFAULT_BARS = {
  M1: 300,
  M5: 250,
  M15: 160,
  M30: 120,
  H1: 100,
  H4: 80,
};
// Per-timeframe overlay config — what each chart should plot beyond candles.
// Mirrors the strategy's top-down role for each TF.
const TIMEFRAME_OVERLAYS = {
  M1:  { emas: [9, 20],      vwap: true,  swings: false },
  M5:  { emas: [9, 20, 50],  vwap: true,  swings: true  },
  M15: { emas: [20, 50],     vwap: false, swings: true  },
  M30: { emas: [20, 50],     vwap: false, swings: true  },
  H1:  { emas: [50, 200],    vwap: false, swings: true  },
  H4:  { emas: [50, 200],    vwap: false, swings: true  },
};
const EMA_STYLE = {
  9:   { color: "rgba(251, 191, 36, 0.90)", width: 1.2, label: "E9"   },
  20:  { color: "rgba(168, 85, 247, 0.90)", width: 1.2, label: "E20"  },
  50:  { color: "rgba(249, 115, 22, 0.90)", width: 1.4, label: "E50"  },
  200: { color: "rgba(239, 68,  68, 0.75)", width: 1.8, label: "E200" },
};
const MARKET_STATE_WINDOWS = {
  regime: 200,
  trend: 50,
  state: 20,
  level: 120,
};
const DISPLAY_TIME_OFFSET_MS = -(8 * 60 * 60 * 1000);
const SMC_SETUP_SCORE_THRESHOLD = 60;
const TIMEFRAME_GROUPS = [
  {
    key: "LTF",
    title: "LTF",
    copy: "Entry precision — OB/FVG retest and M5/M1 confirmation candle.",
    timeframes: ["M1", "M5"],
  },
  {
    key: "MTF",
    title: "MTF",
    copy: "Trade location — order blocks, fair value gaps, and BOS/CHoCH structure.",
    timeframes: ["M15", "M30"],
  },
  {
    key: "HTF",
    title: "HTF",
    copy: "Market structure bias — premium/discount zones and institutional order flow.",
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
const newsCalendarStatus = document.getElementById("news-calendar-status");
const newsCalendarBrokerNow = document.getElementById("news-calendar-broker-now");
const newsCalendarPanelElement = document.getElementById("news-calendar-panel");
const newsCalendarToggle = document.getElementById("news-calendar-toggle");
const newsCalendarMonthLabel = document.getElementById("news-calendar-month-label");
const newsCalendarGrid = document.getElementById("news-calendar-grid");
const newsCalendarPrevButton = document.getElementById("news-calendar-prev");
const newsCalendarNextButton = document.getElementById("news-calendar-next");
const newsCalendarSelectedLabel = document.getElementById("news-calendar-selected-label");
const newsCalendarSelectedCopy = document.getElementById("news-calendar-selected-copy");
const newsEventTimeInput = document.getElementById("news-event-time");
const newsEventTitleInput = document.getElementById("news-event-title");
const newsEventAddButton = document.getElementById("news-event-add");
const newsEventCancelButton = document.getElementById("news-event-cancel");
const newsCalendarApplyButton = document.getElementById("news-calendar-apply");
const newsCalendarEventCopy = document.getElementById("news-calendar-event-copy");
const newsCalendarEventList = document.getElementById("news-calendar-event-list");
const aiBriefPanelElement = document.getElementById("ai-brief-panel");
const aiBriefToggle = document.getElementById("ai-brief-toggle");
const aiStatusLabel = document.getElementById("ai-status-label");
const aiBriefMeta = document.getElementById("ai-brief-meta");
const aiBriefContent = document.getElementById("ai-brief-content");
const refreshAiBriefButton = document.getElementById("refresh-ai-brief");
const contentScrollElement = document.querySelector(".content-scroll");

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
    volatility: null,
    orderBlocks:  { bullish: [], bearish: [] },
    fairValueGaps: { bullish: [], bearish: [] },
    bosChoch:        [],
    activeStructure: null,
    emaValues:       {},
    vwapValues:      [],
  };
}

let autoRefreshEnabled = true;
let autoRefreshHandle = null;
const LIVE_SYNC_MS = 1000;
const TICK_SYNC_MS = 500;
const SNAPSHOT_SYNC_MS = 60 * 1000;
let tickRefreshHandle = null;
let snapshotRefreshHandle = null;
let cooldownTickHandle = null;
let aiBriefAutoRefreshHandle = null;
let latestTradeOverview = null;
let autoTradeConfig = {
  enabled: false,
  lot: 0.01,
};
let cooldownRemainingSeconds = 0;
let tradeActive = false;
let activeTradeSnapshot = null;
let newsCalendarState = {
  before_minutes: 45,
  after_minutes: 45,
  events: [],
  days_with_events: [],
  event_count: 0,
  blocked: false,
  active_event: null,
  upcoming_event: null,
  broker_now: "",
  updated_at: "",
  selectedDate: "",
  viewMonth: "",
  editingEventId: "",
  hasUnsavedChanges: false,
};
let aiBriefState = {
  available: false,
  inFlight: false,
  lastHash: "",
  hasAutoLoaded: false,
  review: null,
  lastTradeSignalId: "",
  lastExecutionSignalId: "",
  lastAutoRefreshBoundary: "",
};
const WORKSPACE_SESSION_KEY = "quantum.workspaceSession";
const WORKSPACE_SESSION_MAX_CANDLES = 320;
const WORKSPACE_SESSION_MAX_AGE_MS = 10 * 60 * 1000;
let lastTickSnapshot = null;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function pad2(value) {
  return String(value).padStart(2, "0");
}

function monthKeyFromDate(date) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}`;
}

function dateKeyFromDate(date) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

function parseBrokerDateTime(value) {
  const text = String(value || "").trim();
  if (!text) return null;
  const normalized = text.length === 16 ? `${text}:00` : text;
  const date = new Date(normalized.replace(" ", "T"));
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatMonthLabel(monthKey) {
  if (!monthKey) return "--";
  const [year, month] = String(monthKey).split("-").map(Number);
  const date = new Date(year, (month || 1) - 1, 1);
  return date.toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

function formatSelectedDateLabel(dateKey) {
  if (!dateKey) return "Select a day";
  const [year, month, day] = String(dateKey).split("-").map(Number);
  const date = new Date(year, (month || 1) - 1, day || 1);
  return date.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
}

function getBrokerNowDate() {
  const parsed = parseBrokerDateTime(newsCalendarState.broker_now);
  return parsed || new Date();
}

function parseNewsEventDateTime(dateKey, timeValue = "00:00") {
  const text = `${String(dateKey || "").trim()} ${String(timeValue || "00:00").trim()}`.trim();
  return parseBrokerDateTime(text);
}

function isPastNewsDate(dateKey) {
  const selected = parseNewsEventDateTime(dateKey, "00:00");
  const brokerNow = getBrokerNowDate();
  if (!selected) return false;
  return selected.getTime() < new Date(brokerNow.getFullYear(), brokerNow.getMonth(), brokerNow.getDate()).getTime();
}

function isPastNewsEvent(dateKey, timeValue) {
  const eventDate = parseNewsEventDateTime(dateKey, timeValue);
  const brokerNow = getBrokerNowDate();
  if (!eventDate) return false;
  return eventDate.getTime() < brokerNow.getTime();
}

function normalizeNewsCalendarPayload(payload) {
  const fallbackDate = dateKeyFromDate(getBrokerNowDate());
  const events = Array.isArray(payload?.events)
    ? payload.events
        .map((item) => ({
          id: String(item?.id || `${item?.date || fallbackDate}-${item?.time || "00:00"}-${Math.random().toString(36).slice(2, 8)}`),
          date: String(item?.date || "").trim(),
          time: String(item?.time || "").trim(),
          title: String(item?.title || "").trim(),
        }))
        .filter((item) => item.date && item.time && item.title)
        .sort((a, b) => `${a.date} ${a.time} ${a.title}`.localeCompare(`${b.date} ${b.time} ${b.title}`))
    : [];
  const selectedDate = newsCalendarState.selectedDate || payload?.selectedDate || (events[0]?.date || fallbackDate);
  const viewMonth = newsCalendarState.viewMonth || payload?.viewMonth || selectedDate.slice(0, 7);
  return {
    before_minutes: 45,
    after_minutes: 45,
    events,
    days_with_events: Array.isArray(payload?.days_with_events) ? payload.days_with_events.map((item) => String(item || "").trim()).filter(Boolean) : [],
    event_count: Number(payload?.event_count || events.length),
    blocked: Boolean(payload?.blocked),
    active_event: payload?.active_event || null,
    upcoming_event: payload?.upcoming_event || null,
    broker_now: String(payload?.broker_now || ""),
    updated_at: String(payload?.updated_at || ""),
    selectedDate,
    viewMonth,
  };
}

function readWorkspaceSession() {
  try {
    const raw = window.sessionStorage.getItem(WORKSPACE_SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

function writeWorkspaceSession(payload) {
  try {
    window.sessionStorage.setItem(WORKSPACE_SESSION_KEY, JSON.stringify(payload));
  } catch {
    // Ignore storage quota or session storage access errors.
  }
}

function buildWorkspaceSessionSnapshot() {
  const timeframes = {};
  for (const timeframe of TIMEFRAMES) {
    const state = chartState[timeframe];
    const refs = domRefs[timeframe];
    timeframes[timeframe] = {
      candles: Array.isArray(state?.candles) ? state.candles.slice(-WORKSPACE_SESSION_MAX_CANDLES) : [],
      summary: state?.summary || null,
      visibleCount: Number(state?.visibleCount || 90),
      offset: Number(state?.offset || 0),
      collapsed: Boolean(refs?.card?.classList.contains("is-collapsed")),
    };
  }
  return {
    saved_at: Date.now(),
    symbol: String(activeSymbolLabel.textContent || symbolInput?.value || "XAUUSD").trim().toUpperCase(),
    limit: String(limitInput?.value || "ALL").trim().toUpperCase() || "ALL",
    autoRefreshEnabled: Boolean(autoRefreshEnabled),
    autotradeCollapsed: Boolean(autotradePanelElement?.classList.contains("is-collapsed")),
    newsCalendarCollapsed: Boolean(newsCalendarPanelElement?.classList.contains("is-collapsed")),
    aiCollapsed: Boolean(aiBriefPanelElement?.classList.contains("is-collapsed")),
    aiReview: aiBriefState.review || null,
    aiMeta: String(aiBriefMeta?.textContent || "").trim(),
    lastTickSnapshot,
    scrollTop: Number(contentScrollElement?.scrollTop || 0),
    timeframes,
  };
}

function saveWorkspaceSessionState() {
  writeWorkspaceSession(buildWorkspaceSessionSnapshot());
}

function restoreWorkspaceSessionState() {
  const snapshot = readWorkspaceSession();
  if (!snapshot) return false;
  const savedAt = Number(snapshot.saved_at || 0);
  if (!savedAt || (Date.now() - savedAt) > WORKSPACE_SESSION_MAX_AGE_MS) return false;

  if (symbolInput && snapshot.symbol) symbolInput.value = String(snapshot.symbol);
  if (limitInput && snapshot.limit) limitInput.value = String(snapshot.limit);
  if (activeSymbolLabel && snapshot.symbol) activeSymbolLabel.textContent = String(snapshot.symbol);
  if (bridgeStatus) bridgeStatus.textContent = "Live";
  autoRefreshEnabled = snapshot.autoRefreshEnabled !== false;
  if (toggleMotionButton) {
    toggleMotionButton.textContent = autoRefreshEnabled ? "Pause Live Sync" : "Resume Live Sync";
  }

  lastTickSnapshot = snapshot.lastTickSnapshot || null;

  const timeframeData = snapshot.timeframes && typeof snapshot.timeframes === "object" ? snapshot.timeframes : {};
  let restoredAny = false;
  for (const timeframe of TIMEFRAMES) {
    const cached = timeframeData[timeframe];
    const state = chartState[timeframe];
    const refs = domRefs[timeframe];
    if (!cached || !state) continue;
    state.candles = Array.isArray(cached.candles) ? cached.candles : [];
    state.summary = cached.summary || null;
    state.visibleCount = Math.max(20, Math.min(240, Number(cached.visibleCount || state.visibleCount || 90)));
    state.offset = Math.max(0, Number(cached.offset || 0));
    state.hoverIndex = null;
    if (state.candles.length) {
      refreshDerivedState(state);
      state.offset = Math.min(state.offset, getMaxOffset(state));
      restoredAny = true;
    }
    if (refs && typeof cached.collapsed === "boolean") {
      setCollapsedState(refs.card, cached.collapsed, refs.collapseButton);
    }
  }

  setCollapsedState(autotradePanelElement, false, autotradePanelToggle);
  setCollapsedState(newsCalendarPanelElement, false, newsCalendarToggle);
  setCollapsedState(aiBriefPanelElement, false, aiBriefToggle);

  if (snapshot.aiReview && typeof snapshot.aiReview === "object") {
    aiBriefState.review = snapshot.aiReview;
    const decision = String(snapshot.aiReview.decision || "no_trade").toUpperCase();
    setAiBriefText(
      renderTradePlanHtml(snapshot.aiReview),
      String(snapshot.aiMeta || "").trim() || `Decision: ${decision}`,
      true
    );
  }

  renderBoard();
  renderCooldownLabel();
  renderActiveTradePanel();
  if (contentScrollElement && Number.isFinite(Number(snapshot.scrollTop))) {
    window.requestAnimationFrame(() => {
      contentScrollElement.scrollTop = Number(snapshot.scrollTop || 0);
    });
  }
  return restoredAny;
}

function buildBoard() {
  boardElement.innerHTML = "";
  for (const group of TIMEFRAME_GROUPS) {
    const section = document.createElement("section");
    section.className = "timeframe-group";
    section.style.setProperty("--group-columns", String(group.timeframes.length));
    section.innerHTML = `
      <div class="timeframe-group-header">
        <div>
          <p class="eyebrow">SMC Layer</p>
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

function getSessionLabel(date = new Date()) {
  const hour = date.getHours();
  if (hour >= 0 && hour < 8) return "Asia";
  if (hour >= 8 && hour < 16) return "London";
  return "New York";
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

function getChartGeometry(canvas, candleCount, atLiveEdge = false) {
  const width = Number(canvas.style.width.replace("px", "")) || canvas.clientWidth || 720;
  const height = Number(canvas.style.height.replace("px", "")) || canvas.clientHeight || 380;
  const padding = { top: 24, right: 78, bottom: 42, left: 16 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  // At the live edge, reserve a fixed pixel margin on the right so fib lines/labels always
  // have clear space at every zoom level. Fixed pixels = consistent gap regardless of bar width.
  const RIGHT_RESERVE_PX = atLiveEdge ? 85 : 0;
  const candleAreaWidth = chartWidth - RIGHT_RESERVE_PX;
  const barSpacing = candleCount > 0 ? candleAreaWidth / candleCount : candleAreaWidth;
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

// ---------------------------------------------------------------------------
// SMC overlay compute helpers (client-side, mirrors server-side Python logic)
// ---------------------------------------------------------------------------

function computeEMA(candles, period) {
  if (candles.length < period) return [];
  const k = 2 / (period + 1);
  const out = new Array(period - 1).fill(null);
  let ema = candles.slice(0, period).reduce((s, c) => s + Number(c.close), 0) / period;
  out.push(ema);
  for (let i = period; i < candles.length; i++) {
    ema = Number(candles[i].close) * k + ema * (1 - k);
    out.push(ema);
  }
  return out;
}

function computeVWAP(candles) {
  const out = [];
  let tpv = 0, vol = 0, lastDay = null;
  for (const c of candles) {
    const dt = new Date(Number(c.time) * 1000);
    const day = `${dt.getUTCFullYear()}-${dt.getUTCMonth()}-${dt.getUTCDate()}`;
    if (day !== lastDay) { tpv = 0; vol = 0; lastDay = day; }
    const tp = (Number(c.high) + Number(c.low) + Number(c.close)) / 3;
    const v = Math.max(Number(c.tick_volume || 0), 1);
    tpv += tp * v; vol += v;
    out.push(tpv / vol);
  }
  return out;
}

function computeOrderBlocks(candles, side, atrValue) {
  if (candles.length < 5) return [];
  const impulseMin = Math.max((atrValue || 6) * 1.5, 4);
  const window = candles.slice(-Math.min(80, candles.length));
  const obs = [];
  for (let i = 1; i < window.length - 2; i++) {
    const c = window[i];
    const co = Number(c.open), cc = Number(c.close), ch = Number(c.high), cl = Number(c.low);
    const next = window.slice(i + 1, i + 4);
    if (!next.length) continue;
    if (side === "buy") {
      if (cc >= co) continue;
      const impulse = next.some(n => Number(n.close) - Number(n.open) >= impulseMin || Number(n.high) > ch + impulseMin * 0.5);
      if (!impulse) continue;
      const mid = (co + cc) / 2;
      const mitigated = window.slice(i + 1).some(n => Number(n.close) < mid);
      obs.push({ high: Math.max(co, cc), low: Math.min(co, cc), midpoint: mid, time: Number(c.time), mitigated });
    } else {
      if (cc <= co) continue;
      const impulse = next.some(n => Number(n.open) - Number(n.close) >= impulseMin || Number(n.low) < cl - impulseMin * 0.5);
      if (!impulse) continue;
      const mid = (co + cc) / 2;
      const mitigated = window.slice(i + 1).some(n => Number(n.close) > mid);
      obs.push({ high: Math.max(co, cc), low: Math.min(co, cc), midpoint: mid, time: Number(c.time), mitigated });
    }
  }
  return obs.filter(ob => !ob.mitigated).reverse().slice(0, 5);
}

// Returns the canvas X for a given unix timestamp relative to visible candles.
// If the timestamp predates the visible window the OB/FVG still starts at the
// left edge (it was created earlier but is still unmitigated/unfilled).
function timeToX(time, visible, padding, barSpacing) {
  const t = Number(time);
  if (!visible.length || t <= Number(visible[0].time)) return padding.left;
  for (let i = 1; i < visible.length; i++) {
    if (Number(visible[i].time) >= t) return padding.left + i * barSpacing;
  }
  return padding.left + visible.length * barSpacing;
}

function computeFVGs(candles, side) {
  if (candles.length < 3) return [];
  const window = candles.slice(-Math.min(80, candles.length));
  const fvgs = [];
  for (let i = 1; i < window.length - 1; i++) {
    const prev = window[i - 1], mid = window[i], nxt = window[i + 1];
    if (side === "buy") {
      const gapLow = Number(prev.high), gapHigh = Number(nxt.low);
      if (gapHigh - gapLow < 0.5) continue;
      const gapMid = (gapLow + gapHigh) / 2;
      const filled = window.slice(i + 2).some(n => Number(n.close) <= gapMid);
      fvgs.push({ high: gapHigh, low: gapLow, midpoint: gapMid, time: Number(mid.time), filled });
    } else {
      const gapHigh = Number(prev.low), gapLow = Number(nxt.high);
      if (gapHigh - gapLow < 0.5) continue;
      const gapMid = (gapLow + gapHigh) / 2;
      const filled = window.slice(i + 2).some(n => Number(n.close) >= gapMid);
      fvgs.push({ high: gapHigh, low: gapLow, midpoint: gapMid, time: Number(mid.time), filled });
    }
  }
  return fvgs.filter(f => !f.filled).reverse().slice(0, 5);
}

function computeBOSCHoCH(candles) {
  if (!Array.isArray(candles) || candles.length < 20) return [];

  const lookback = 10;
  const window = candles.slice(-Math.min(180, candles.length));
  const n = window.length;
  const events = [];

  const getHighestBar = (index, bars = lookback) => {
    const start = Math.max(0, index - bars + 1);
    let highestIdx = start;
    for (let i = start + 1; i <= index; i += 1) {
      if (Number(window[i].high) >= Number(window[highestIdx].high)) highestIdx = i;
    }

    let pivotIdx = highestIdx;
    for (let i = Math.max(start + 1, index - bars + 1); i <= index - 1; i += 1) {
      if (
        i + 2 <= index &&
        Number(window[i].high) > Number(window[i - 1].high) &&
        Number(window[i].high) > Number(window[i + 1].high) &&
        Number(window[i].high) >= Number(window[pivotIdx].high)
      ) {
        pivotIdx = i;
      }
    }
    return pivotIdx;
  };

  const getLowestBar = (index, bars = lookback) => {
    const start = Math.max(0, index - bars + 1);
    let lowestIdx = start;
    for (let i = start + 1; i <= index; i += 1) {
      if (Number(window[i].low) <= Number(window[lowestIdx].low)) lowestIdx = i;
    }

    let pivotIdx = lowestIdx;
    for (let i = Math.max(start + 1, index - bars + 1); i <= index - 1; i += 1) {
      if (
        i + 2 <= index &&
        Number(window[i].low) < Number(window[i - 1].low) &&
        Number(window[i].low) < Number(window[i + 1].low) &&
        Number(window[i].low) <= Number(window[pivotIdx].low)
      ) {
        pivotIdx = i;
      }
    }
    return pivotIdx;
  };

  let structureHighStartIndex = 0;
  let structureLowStartIndex = 0;
  let structureHigh = Number(window[0].high);
  let structureLow = Number(window[0].low);
  let structureDirection = 0; // 0 neutral, 1 bearish leg, 2 bullish leg

  for (let i = 1; i < n; i += 1) {
    const bodyBreakHigh = Number(window[i].close);
    const bodyBreakLow = Number(window[i].close);

    const highBroken =
      (
        bodyBreakHigh > structureHigh &&
        i - 1 > structureHighStartIndex &&
        Number(window[i - 1]?.close ?? -Infinity) <= structureHigh &&
        Number(window[i - 2]?.close ?? -Infinity) <= structureHigh &&
        Number(window[i - 3]?.close ?? -Infinity) <= structureHigh
      ) ||
      (structureDirection === 1 && bodyBreakHigh > structureHigh);

    const lowBroken =
      (
        bodyBreakLow < structureLow &&
        i - 1 > structureLowStartIndex &&
        Number(window[i - 1]?.close ?? Infinity) >= structureLow &&
        Number(window[i - 2]?.close ?? Infinity) >= structureLow &&
        Number(window[i - 3]?.close ?? Infinity) >= structureLow
      ) ||
      (structureDirection === 2 && bodyBreakLow < structureLow);

    if (lowBroken) {
      events.push({
        type: structureDirection === 1 ? "BOS" : "CHoCH",
        dir: "bear",
        level: structureLow,
        levelIdx: structureLowStartIndex,
        breakIdx: i,
      });

      const structureMaxBar = getHighestBar(i, lookback);
      structureDirection = 1;
      structureHighStartIndex = structureMaxBar;
      structureLowStartIndex = i;
      structureHigh = Number(window[structureMaxBar]?.high ?? window[i].high);
      structureLow = Number(window[i].low);
      continue;
    }

    if (highBroken) {
      events.push({
        type: structureDirection === 2 ? "BOS" : "CHoCH",
        dir: "bull",
        level: structureHigh,
        levelIdx: structureHighStartIndex,
        breakIdx: i,
      });

      const structureMinBar = getLowestBar(i, lookback);
      structureDirection = 2;
      structureHighStartIndex = i;
      structureLowStartIndex = structureMinBar;
      structureHigh = Number(window[i].high);
      structureLow = Number(window[structureMinBar]?.low ?? window[i].low);
      continue;
    }

    if ((structureDirection === 0 || structureDirection === 2) && Number(window[i].high) > structureHigh) {
      structureHigh = Number(window[i].high);
      structureHighStartIndex = i;
    } else if ((structureDirection === 0 || structureDirection === 1) && Number(window[i].low) < structureLow) {
      structureLow = Number(window[i].low);
      structureLowStartIndex = i;
    }
  }

  return events.slice(-10);
}

// Returns the current ACTIVE (unbroken) structure high, low, direction, and their
// candle indices within the 180-bar window — used to draw the live structure lines
// and Fibonacci retracement levels on the chart (mirrors the Pine structureHighLine /
// structureLowLine + Fibonacci drawing logic).
function computeActiveStructure(candles) {
  if (!Array.isArray(candles) || candles.length < 10) return null;
  const lookback = 10;
  const window = candles.slice(-Math.min(180, candles.length));
  const n = window.length;

  const getHighestBar = (index) => {
    const start = Math.max(0, index - lookback + 1);
    let best = start;
    for (let i = start + 1; i <= index; i++) {
      if (Number(window[i].high) >= Number(window[best].high)) best = i;
    }
    let pivot = best;
    for (let i = Math.max(start + 1, index - lookback + 1); i <= index - 1; i++) {
      if (i + 2 <= index &&
          Number(window[i].high) > Number(window[i - 1].high) &&
          Number(window[i].high) > Number(window[i + 1].high) &&
          Number(window[i].high) >= Number(window[pivot].high)) pivot = i;
    }
    return pivot;
  };

  const getLowestBar = (index) => {
    const start = Math.max(0, index - lookback + 1);
    let best = start;
    for (let i = start + 1; i <= index; i++) {
      if (Number(window[i].low) <= Number(window[best].low)) best = i;
    }
    let pivot = best;
    for (let i = Math.max(start + 1, index - lookback + 1); i <= index - 1; i++) {
      if (i + 2 <= index &&
          Number(window[i].low) < Number(window[i - 1].low) &&
          Number(window[i].low) < Number(window[i + 1].low) &&
          Number(window[i].low) <= Number(window[pivot].low)) pivot = i;
    }
    return pivot;
  };

  let highIdx = 0;
  let lowIdx  = 0;
  let structureHigh = Number(window[0].high);
  let structureLow  = Number(window[0].low);
  let direction = 0; // 0=neutral, 1=bearish leg, 2=bullish leg

  for (let i = 1; i < n; i++) {
    const close = Number(window[i].close);
    const highBroken =
      (close > structureHigh &&
       i - 1 > highIdx &&
       Number(window[i - 1]?.close ?? -Infinity) <= structureHigh &&
       Number(window[i - 2]?.close ?? -Infinity) <= structureHigh &&
       Number(window[i - 3]?.close ?? -Infinity) <= structureHigh) ||
      (direction === 1 && close > structureHigh);
    const lowBroken =
      (close < structureLow &&
       i - 1 > lowIdx &&
       Number(window[i - 1]?.close ?? Infinity) >= structureLow &&
       Number(window[i - 2]?.close ?? Infinity) >= structureLow &&
       Number(window[i - 3]?.close ?? Infinity) >= structureLow) ||
      (direction === 2 && close < structureLow);

    if (lowBroken) {
      direction     = 1;
      highIdx       = getHighestBar(i);
      lowIdx        = i;
      structureHigh = Number(window[highIdx]?.high ?? window[i].high);
      structureLow  = Number(window[i].low);
      continue;
    }
    if (highBroken) {
      direction     = 2;
      highIdx       = i;
      lowIdx        = getLowestBar(i);
      structureHigh = Number(window[i].high);
      structureLow  = Number(window[lowIdx]?.low ?? window[i].low);
      continue;
    }
    if ((direction === 0 || direction === 2) && Number(window[i].high) > structureHigh) {
      structureHigh = Number(window[i].high);
      highIdx       = i;
    } else if ((direction === 0 || direction === 1) && Number(window[i].low) < structureLow) {
      structureLow = Number(window[i].low);
      lowIdx       = i;
    }
  }

  return {
    high:      structureHigh,
    low:       structureLow,
    highTime:  Number(window[highIdx]?.time ?? 0),
    lowTime:   Number(window[lowIdx]?.time  ?? 0),
    direction, // 1=bearish leg in progress, 2=bullish leg in progress
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


// ---------------------------------------------------------------------------
// SMC — top-down structure overview (replaces old EMA/RSI/ADX scoring system)
// Reads BOS/CHoCH direction, OB/FVG presence, and M5 entry zone from chartState.
// ---------------------------------------------------------------------------

function calculateTradeOverview() {
  const m5Candles = chartState.M5?.candles || [];
  const currentPrice = m5Candles.length ? Number(m5Candles[m5Candles.length - 1].close) : null;

  // H1 structural bias from last BOS/CHoCH event
  const h1Events = chartState.H1?.bosChoch || [];
  const lastH1 = h1Events[h1Events.length - 1] || null;
  const h1Bull = lastH1?.dir === "bull";
  const h1Bear = lastH1?.dir === "bear";
  const h1StructureLabel = lastH1 ? `${lastH1.type} ${lastH1.dir}` : "No event";

  // M15 BOS/CHoCH direction
  const m15Events = chartState.M15?.bosChoch || [];
  const lastM15 = m15Events[m15Events.length - 1] || null;
  const m15Bull = lastM15?.dir === "bull";
  const m15Bear = lastM15?.dir === "bear";
  const m15StructureLabel = lastM15 ? `${lastM15.type} ${lastM15.dir}` : "No event";

  // M15 PDA arrays (unmitigated OBs / unfilled FVGs)
  const m15BullOBs  = chartState.M15?.orderBlocks?.bullish  || [];
  const m15BearOBs  = chartState.M15?.orderBlocks?.bearish  || [];
  const m15BullFVGs = chartState.M15?.fairValueGaps?.bullish || [];
  const m15BearFVGs = chartState.M15?.fairValueGaps?.bearish || [];

  // M5 PDA arrays
  const m5BullOBs  = chartState.M5?.orderBlocks?.bullish  || [];
  const m5BearOBs  = chartState.M5?.orderBlocks?.bearish  || [];
  const m5BullFVGs = chartState.M5?.fairValueGaps?.bullish || [];
  const m5BearFVGs = chartState.M5?.fairValueGaps?.bearish || [];

  // Is current price inside (or touching) the nearest unmitigated bullish / bearish PDA on M5?
  const inBullM5OB  = currentPrice != null && m5BullOBs.find(ob  => currentPrice >= ob.low  && currentPrice <= ob.high  * 1.001);
  const inBullM5FVG = currentPrice != null && m5BullFVGs.find(fvg => currentPrice >= fvg.low && currentPrice <= fvg.high * 1.001);
  const inBearM5OB  = currentPrice != null && m5BearOBs.find(ob  => currentPrice >= ob.low  * 0.999 && currentPrice <= ob.high);
  const inBearM5FVG = currentPrice != null && m5BearFVGs.find(fvg => currentPrice >= fvg.low * 0.999 && currentPrice <= fvg.high);

  const longZoneLabel  = inBullM5OB  ? `In OB ${inBullM5OB.low?.toFixed(2)}-${inBullM5OB.high?.toFixed(2)}`
                       : inBullM5FVG ? `In FVG ${inBullM5FVG.low?.toFixed(2)}-${inBullM5FVG.high?.toFixed(2)}`
                       : `${m5BullOBs.length} OB, ${m5BullFVGs.length} FVG unmitigated`;
  const shortZoneLabel = inBearM5OB  ? `In OB ${inBearM5OB.low?.toFixed(2)}-${inBearM5OB.high?.toFixed(2)}`
                       : inBearM5FVG ? `In FVG ${inBearM5FVG.low?.toFixed(2)}-${inBearM5FVG.high?.toFixed(2)}`
                       : `${m5BearOBs.length} OB, ${m5BearFVGs.length} FVG unmitigated`;

  // Build long checks
  const longChecks = {
    structure: [
      { label: "H1 structure (BOS/CHoCH)", expected: "Bullish break", actual: h1StructureLabel,  passed: h1Bull,  score: h1Bull  ? 30 : 0 },
      { label: "M15 BOS/CHoCH confirms",   expected: "Bullish break", actual: m15StructureLabel, passed: m15Bull, score: m15Bull ? 20 : 0 },
    ],
    pda: [
      { label: "M15 bullish PDA present", expected: "OB or FVG on M15", actual: `${m15BullOBs.length} OB, ${m15BullFVGs.length} FVG`, passed: m15BullOBs.length > 0 || m15BullFVGs.length > 0, score: (m15BullOBs.length > 0 || m15BullFVGs.length > 0) ? 20 : 0 },
      { label: "M5 entry zone",            expected: "Price at OB or FVG", actual: longZoneLabel,  passed: Boolean(inBullM5OB || inBullM5FVG), score: (inBullM5OB || inBullM5FVG) ? 30 : 0 },
    ],
  };

  // Build short checks
  const shortChecks = {
    structure: [
      { label: "H1 structure (BOS/CHoCH)", expected: "Bearish break", actual: h1StructureLabel,  passed: h1Bear,  score: h1Bear  ? 30 : 0 },
      { label: "M15 BOS/CHoCH confirms",   expected: "Bearish break", actual: m15StructureLabel, passed: m15Bear, score: m15Bear ? 20 : 0 },
    ],
    pda: [
      { label: "M15 bearish PDA present", expected: "OB or FVG on M15", actual: `${m15BearOBs.length} OB, ${m15BearFVGs.length} FVG`, passed: m15BearOBs.length > 0 || m15BearFVGs.length > 0, score: (m15BearOBs.length > 0 || m15BearFVGs.length > 0) ? 20 : 0 },
      { label: "M5 entry zone",            expected: "Price at OB or FVG", actual: shortZoneLabel, passed: Boolean(inBearM5OB || inBearM5FVG), score: (inBearM5OB || inBearM5FVG) ? 30 : 0 },
    ],
  };

  const allLong  = [...longChecks.structure,  ...longChecks.pda];
  const allShort = [...shortChecks.structure, ...shortChecks.pda];
  const longScore  = allLong.reduce((s, c) => s + c.score, 0);
  const shortScore = allShort.reduce((s, c) => s + c.score, 0);

  let action = "No Setup";
  if      (longScore  >= SMC_SETUP_SCORE_THRESHOLD && longScore  > shortScore) action = "Buy Setup";
  else if (shortScore >= SMC_SETUP_SCORE_THRESHOLD && shortScore > longScore)  action = "Sell Setup";

  const summary = action === "No Setup"
    ? `No aligned SMC setup yet. Long ${longScore}/100, Short ${shortScore}/100.`
    : `${action} aligned. Long ${longScore}/100, Short ${shortScore}/100.`;

  return { action, longScore, shortScore, longChecks, shortChecks, summary };
}

function renderTradeOverview() {
  latestTradeOverview = calculateTradeOverview();
}

function buildNewsEventsByDate() {
  const grouped = new Map();
  for (const event of newsCalendarState.events || []) {
    const bucket = grouped.get(event.date) || [];
    bucket.push(event);
    grouped.set(event.date, bucket);
  }
  for (const bucket of grouped.values()) {
    bucket.sort((a, b) => `${a.time} ${a.title}`.localeCompare(`${b.time} ${b.title}`));
  }
  return grouped;
}

function renderNewsCalendarDayEditor() {
  if (!newsCalendarSelectedLabel || !newsCalendarSelectedCopy || !newsCalendarEventCopy || !newsCalendarEventList) return;
  const selectedDate = newsCalendarState.selectedDate || dateKeyFromDate(getBrokerNowDate());
  const eventsByDate = buildNewsEventsByDate();
  const events = eventsByDate.get(selectedDate) || [];
  const selectedDateIsPast = isPastNewsDate(selectedDate);
  newsCalendarSelectedLabel.textContent = formatSelectedDateLabel(selectedDate);
  newsCalendarSelectedCopy.textContent = selectedDateIsPast
    ? "This broker-time date has already passed. You can review old items here, but you cannot add a new event to this day."
    : "Events use broker time. Every saved event hard-blocks the bot for 45 min before and 45 min after.";
  const editingEvent = (newsCalendarState.events || []).find((event) => event.id === newsCalendarState.editingEventId) || null;
  if (newsEventAddButton) newsEventAddButton.textContent = editingEvent ? "Update Event" : "Add Event";
  if (newsEventCancelButton) newsEventCancelButton.style.display = editingEvent ? "" : "none";
  if (newsEventTimeInput) newsEventTimeInput.disabled = selectedDateIsPast;
  if (newsEventTitleInput) newsEventTitleInput.disabled = selectedDateIsPast;
  if (newsEventAddButton) newsEventAddButton.disabled = selectedDateIsPast;
  newsCalendarEventCopy.textContent = events.length ? `${events.length} event${events.length === 1 ? "" : "s"} on this date.` : "No events on this date yet.";
  newsCalendarEventList.innerHTML = "";
  if (!events.length) {
    newsCalendarEventList.innerHTML = `<div class="news-calendar-event-card"><div class="news-calendar-event-meta"><strong>No events</strong><span>Add a broker-time entry for CPI, FOMC, NFP, or any manual block.</span></div></div>`;
    newsCalendarEventList.innerHTML += `<p class="news-calendar-empty-hint">Edit and Remove buttons appear here after you add an event.</p>`;
    return;
  }
  for (const event of events) {
    const eventIsPast = isPastNewsEvent(event.date, event.time);
    const row = document.createElement("div");
    row.className = "news-calendar-event-card";
    row.innerHTML = `
      <div class="news-calendar-event-meta">
        <strong>${escapeHtml(event.title)}</strong>
        <span>${escapeHtml(event.date)} | ${escapeHtml(event.time)}${eventIsPast ? " | Passed" : ""}</span>
      </div>
      <div class="news-calendar-event-actions">
        <button class="action-button" type="button" data-edit-event-id="${escapeHtml(event.id)}" ${eventIsPast ? "disabled" : ""}>Edit</button>
        <button class="action-button news-calendar-remove" type="button" data-event-id="${escapeHtml(event.id)}">Remove</button>
      </div>
    `;
    newsCalendarEventList.appendChild(row);
  }
}

function renderNewsCalendarGrid() {
  if (!newsCalendarGrid || !newsCalendarMonthLabel) return;
  const viewMonth = newsCalendarState.viewMonth || monthKeyFromDate(getBrokerNowDate());
  const [year, month] = viewMonth.split("-").map(Number);
  const monthStart = new Date(year, (month || 1) - 1, 1);
  const startDay = monthStart.getDay();
  const gridStart = new Date(year, (month || 1) - 1, 1 - startDay);
  const eventsByDate = buildNewsEventsByDate();
  const activeDate = newsCalendarState.active_event?.date || "";
  const appliedDates = new Set(Array.isArray(newsCalendarState.days_with_events) ? newsCalendarState.days_with_events : []);
  newsCalendarMonthLabel.textContent = formatMonthLabel(viewMonth);
  newsCalendarGrid.innerHTML = "";
  for (const label of ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]) {
    const head = document.createElement("div");
    head.className = "news-calendar-weekday";
    head.textContent = label;
    newsCalendarGrid.appendChild(head);
  }
  for (let index = 0; index < 42; index += 1) {
    const day = new Date(gridStart);
    day.setDate(gridStart.getDate() + index);
    const dateKey = dateKeyFromDate(day);
    const monthKey = monthKeyFromDate(day);
    const events = eventsByDate.get(dateKey) || [];
    const cell = document.createElement("button");
    cell.type = "button";
    cell.className = "news-calendar-day";
    if (monthKey !== viewMonth) cell.classList.add("is-muted");
    if (events.length) cell.classList.add("is-has-events");
    if (appliedDates.has(dateKey)) cell.classList.add("is-applied");
    if (dateKey === newsCalendarState.selectedDate) cell.classList.add("is-selected");
    if (dateKey === activeDate) cell.classList.add("is-active-block");
    if (isPastNewsDate(dateKey)) cell.classList.add("is-past");
    const preview = events.slice(0, 2).map((event) => `<div class="news-calendar-day-item">${escapeHtml(event.time)} ${escapeHtml(event.title)}</div>`).join("");
    cell.innerHTML = `
      <div class="news-calendar-day-top">
        <strong class="news-calendar-day-number">${day.getDate()}</strong>
        <span class="news-calendar-day-count">${events.length ? `${events.length} item${events.length === 1 ? "" : "s"}` : ""}</span>
      </div>
      <div class="news-calendar-day-items">${preview}</div>
    `;
    cell.dataset.date = dateKey;
    newsCalendarGrid.appendChild(cell);
  }
}

function renderNewsCalendarPanel() {
  if (newsCalendarStatus) {
    if (newsCalendarState.blocked && newsCalendarState.active_event) {
      newsCalendarStatus.textContent = `Blocking now for ${newsCalendarState.active_event.title} until ${newsCalendarState.active_event.block_end}.`;
    } else if (newsCalendarState.upcoming_event) {
      newsCalendarStatus.textContent = `Next block starts with ${newsCalendarState.upcoming_event.title} at ${newsCalendarState.upcoming_event.event_at}.`;
    } else if ((newsCalendarState.event_count || 0) > 0) {
      newsCalendarStatus.textContent = `${newsCalendarState.event_count} saved event${newsCalendarState.event_count === 1 ? "" : "s"} in the calendar.`;
    } else {
      newsCalendarStatus.textContent = "No saved news block yet.";
    }
  }
  if (newsCalendarBrokerNow) {
    newsCalendarBrokerNow.textContent = newsCalendarState.broker_now ? `Broker Time ${newsCalendarState.broker_now}` : "Broker Time --";
  }
  renderNewsCalendarGrid();
  renderNewsCalendarDayEditor();
}

function updateNewsCalendarState(payload) {
  const nextState = normalizeNewsCalendarPayload(payload);
  const preserveLocalDraft = Boolean(newsCalendarState.hasUnsavedChanges);
  newsCalendarState = {
    ...newsCalendarState,
    ...nextState,
    events: preserveLocalDraft ? newsCalendarState.events : nextState.events,
    event_count: preserveLocalDraft ? Number(newsCalendarState.events?.length || 0) : nextState.event_count,
    days_with_events: preserveLocalDraft ? newsCalendarState.days_with_events : nextState.days_with_events,
    updated_at: preserveLocalDraft ? newsCalendarState.updated_at : nextState.updated_at,
    selectedDate: newsCalendarState.selectedDate || nextState.selectedDate,
    viewMonth: newsCalendarState.viewMonth || nextState.viewMonth,
  };
  if (!newsCalendarState.selectedDate) newsCalendarState.selectedDate = nextState.selectedDate;
  if (!newsCalendarState.viewMonth) newsCalendarState.viewMonth = nextState.viewMonth;
  renderNewsCalendarPanel();
}

function clearNewsCalendarEditor() {
  newsCalendarState.editingEventId = "";
  if (newsEventTimeInput) newsEventTimeInput.value = "";
  if (newsEventTitleInput) newsEventTitleInput.value = "";
}

function startEditingNewsCalendarEvent(eventId) {
  const event = (newsCalendarState.events || []).find((item) => item.id === eventId);
  if (!event) return;
  newsCalendarState.editingEventId = event.id;
  newsCalendarState.selectedDate = event.date;
  newsCalendarState.viewMonth = String(event.date || "").slice(0, 7) || newsCalendarState.viewMonth;
  if (newsEventTimeInput) newsEventTimeInput.value = event.time || "";
  if (newsEventTitleInput) newsEventTitleInput.value = event.title || "";
  renderNewsCalendarPanel();
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
    updateNewsCalendarState(payload.news_calendar || {});
    setAutoTradeUi();
    renderCooldownLabel();
    renderActiveTradePanel();
    saveWorkspaceSessionState();
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
      news_calendar: {
        before_minutes: 45,
        after_minutes: 45,
        events: Array.isArray(newsCalendarState.events) ? newsCalendarState.events : [],
      },
    }),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || "Failed to save auto trade config.");
  autoTradeConfig.enabled = Boolean(payload.enabled);
  autoTradeConfig.lot = Number(payload.lot || nextLot);
  newsCalendarState.hasUnsavedChanges = false;
  updateNewsCalendarState(payload.news_calendar || {});
  setAutoTradeUi();
}

function buildExecutionPayload() {
  const review = aiBriefState.review;
  if (!review || !review.should_trade) {
    return null;
  }
  const side = String(review.decision || "").trim().toLowerCase();
  const m5LastTime = Number(chartState.M5?.candles?.[chartState.M5.candles.length - 1]?.time);
  const entryRaw = Number.isFinite(Number(review.entry)) ? Number(review.entry) : Number(review.suggested_entry);
  const slRaw = Number.isFinite(Number(review.sl)) ? Number(review.sl) : Number(review.suggested_sl);
  const tpRaw = Number.isFinite(Number(review.tp)) ? Number(review.tp) : Number(review.suggested_tp);
  const entryIdx = Number.isFinite(Number(review.entry_idx)) ? Number(review.entry_idx) : null;
  const slIdx = Number.isFinite(Number(review.sl_idx)) ? Number(review.sl_idx) : null;
  const tpIdx = Number.isFinite(Number(review.tp_idx)) ? Number(review.tp_idx) : null;
  if (!Number.isFinite(m5LastTime) || !Number.isFinite(Number(entryRaw)) || !Number.isFinite(Number(slRaw)) || !Number.isFinite(Number(tpRaw))) {
    return null;
  }
  const entry = Number(entryRaw);
  const sl = Number(slRaw);
  const tp = Number(tpRaw);
  if (side !== "buy" && side !== "sell") return null;
  const validPlan = side === "buy" ? sl < entry && entry < tp : tp < entry && entry < sl;
  if (!validPlan) return null;
  return {
    symbol: String(activeSymbolLabel.textContent || symbolInput.value || "XAUUSD").trim().toUpperCase(),
    side,
    action: `AI ${side === "buy" ? "Buy" : "Sell"} Ready`,
    lot: Math.max(0.01, Number(autotradeLotInput?.value || autoTradeConfig.lot || 0.01)),
    entry,
    sl,
    tp,
    entry_idx: entryIdx,
    sl_idx: slIdx,
    tp_idx: tpIdx,
    signal_id: `ai:${side}:${m5LastTime}:${modelSafeToken(normalizeAiModel())}`,
    decision_key: String(review.signal_key || "").trim(),
    ai_trade: review,
  };
}

async function maybeExecuteAutoTrade() {
  return;
}

function modelSafeToken(model) {
  return String(model || "model").replace(/[^a-z0-9]+/gi, "-").replace(/^-+|-+$/g, "").toLowerCase() || "model";
}

function normalizeAiModel() {
  return "local-setup-engine";
}

function setAiStatus(text, available = false) {
  aiBriefState.available = available;
  if (aiStatusLabel) aiStatusLabel.textContent = text;
}

function setAiBriefText(content, meta, allowHtml = false) {
  if (aiBriefContent) {
    if (allowHtml) aiBriefContent.innerHTML = content;
    else aiBriefContent.textContent = content;
  }
  if (aiBriefMeta) aiBriefMeta.textContent = meta;
  saveWorkspaceSessionState();
}

async function buildBoardVisionImage() {
  const canvases = TIMEFRAMES
    .map((timeframe) => ({
      timeframe,
      canvas: domRefs[timeframe]?.canvas || null,
      latest: chartState[timeframe]?.candles?.[chartState[timeframe].candles.length - 1] || null,
      state: chartState[timeframe]?.marketState || null,
      levels: chartState[timeframe]?.levels || null,
    }))
    .filter((item) => item.canvas && item.canvas.width > 0 && item.canvas.height > 0);

  if (!canvases.length) return null;

  const cardWidth = 360;
  const cardHeight = 210;
  const headerHeight = 42;
  const gap = 12;
  const columns = 2;
  const rows = Math.ceil(canvases.length / columns);
  const padding = 16;
  const width = padding * 2 + columns * cardWidth + (columns - 1) * gap;
  const height = padding * 2 + rows * (cardHeight + headerHeight) + (rows - 1) * gap + 34;

  const surface = document.createElement("canvas");
  surface.width = width;
  surface.height = height;
  const ctx = surface.getContext("2d");
  if (!ctx) return null;

  ctx.fillStyle = "#040811";
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = "#edf4ff";
  ctx.font = "bold 18px Segoe UI";
  ctx.fillText(`Quantum | ${String(activeSymbolLabel.textContent || symbolInput.value || "XAUUSD").trim().toUpperCase()}`, padding, 26);
  ctx.fillStyle = "#9ab0d3";
  ctx.font = "12px Segoe UI";
  ctx.fillText(`${new Date().toLocaleString()}`, padding, 44);

  canvases.forEach((item, index) => {
    const column = index % columns;
    const row = Math.floor(index / columns);
    const x = padding + column * (cardWidth + gap);
    const y = padding + 56 + row * (cardHeight + headerHeight + gap);

    ctx.fillStyle = "#0d1524";
    ctx.fillRect(x, y, cardWidth, cardHeight + headerHeight);
    ctx.strokeStyle = "rgba(145, 182, 255, 0.18)";
    ctx.lineWidth = 1;
    ctx.strokeRect(x, y, cardWidth, cardHeight + headerHeight);

    ctx.fillStyle = "#edf4ff";
    ctx.font = "bold 15px Segoe UI";
    ctx.fillText(item.timeframe, x + 10, y + 22);
    ctx.fillStyle = "#9ab0d3";
    ctx.font = "11px Segoe UI";
    const regime = item.state?.regime || "--";
    const trend = item.state?.trend || "--";
    const rangePosition = item.state?.rangePosition || "--";
    const latestClose = item.latest ? formatPrice(item.latest.close) : "--";
    ctx.fillText(`${regime} | ${trend} | ${rangePosition}`, x + 10, y + 36);

    const support = item.levels?.support != null ? formatPrice(item.levels.support) : "--";
    const resistance = item.levels?.resistance != null ? formatPrice(item.levels.resistance) : "--";
    ctx.fillStyle = "#6fa4ff";
    ctx.fillText(`S ${support}`, x + 10, y + cardHeight + headerHeight - 10);
    ctx.fillStyle = "#ff7f7f";
    ctx.fillText(`R ${resistance} | C ${latestClose}`, x + cardWidth - 150, y + cardHeight + headerHeight - 10);

    ctx.drawImage(item.canvas, x + 8, y + headerHeight, cardWidth - 16, cardHeight - 12);
  });

  const dataUrl = surface.toDataURL("image/png");
  return dataUrl.includes(",") ? dataUrl.split(",")[1] : null;
}

async function saveLatestBoardSnapshot() {
  const image = await buildBoardVisionImage();
  if (!image) return false;
  try {
    const response = await fetch("/api/ai/snapshot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image,
        symbol: String(activeSymbolLabel.textContent || symbolInput.value || "XAUUSD").trim().toUpperCase(),
      }),
    });
    if (!response.ok) return false;
    return true;
  } catch (error) {
    return false;
  }
}

function buildAiTriggerFingerprint() {
  const boundaryKey = getAiDecisionBoundaryKey();
  return JSON.stringify({
    model: normalizeAiModel(),
    symbol: String(activeSymbolLabel.textContent || symbolInput.value || "XAUUSD").trim().toUpperCase(),
    boundary: boundaryKey || "manual",
  });
}

function getAiDecisionBoundaryKey(date = new Date()) {
  const minutes = date.getMinutes();
  const seconds = date.getSeconds();
  if (minutes % 5 !== 0 || seconds > 20) return "";
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(minutes).padStart(2, "0");
  return `${year}-${month}-${day}T${hour}:${minute}`;
}

function getAiRefreshBoundaryKey(date = new Date()) {
  const minutes = date.getMinutes();
  if (minutes % 5 !== 0) return "";
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(minutes).padStart(2, "0");
  return `${year}-${month}-${day}T${hour}:${minute}`;
}

function normalizeAiList(value, fallback = []) {
  if (Array.isArray(value)) {
    return value.map((item) => String(item || "").trim()).filter(Boolean);
  }
  const text = String(value || "").trim();
  return text ? [text] : fallback;
}

function normalizeIndicatorChecks(value) {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => ({
      label: String(item?.label || "").trim(),
      expected: String(item?.expected || "").trim(),
      actual: String(item?.actual ?? "").trim(),
      passed: Boolean(item?.passed),
    }))
    .filter((item) => item.label);
}

function getTradePlanTone(decision) {
  const side = String(decision || "").toLowerCase();
  if (side === "buy") return { badge: "BULLISH SMC SETUP", prefix: "BUY", tone: "is-buy" };
  if (side === "sell") return { badge: "BEARISH SMC SETUP", prefix: "SELL", tone: "is-sell" };
  return { badge: "WAIT FOR CLEANER STRUCTURE", prefix: "WAIT", tone: "is-wait" };
}

function getZoneSourceLabel(signal) {
  const map = {
    bullish_ob_retest:        "Bullish OB Retest",
    bullish_fvg_retest:       "Bullish FVG Retest",
    sell_side_sweep_reclaim:  "Sell-Side Sweep Reclaim",
    failed_breakdown_reclaim: "Failed Breakdown Reclaim",
    bullish_bos_retest:       "Bullish BOS Retest",
    bullish_displacement:     "Bullish Displacement",
    bearish_ob_retest:        "Bearish OB Retest",
    bearish_fvg_retest:       "Bearish FVG Retest",
    buy_side_sweep_reject:    "Buy-Side Sweep Rejection",
    failed_breakout_reject:   "Failed Breakout Rejection",
    bearish_bos_retest:       "Bearish BOS Retest",
    bearish_displacement:     "Bearish Displacement",
  };
  const key = String(signal || "").trim();
  return key ? (map[key] || key.replaceAll("_", " ")) : "";
}

function renderTradePlanHtml(payload) {
  if (!payload) {
    return `<div class="ai-plan-empty">${escapeHtml("No AI analysis returned.")}</div>`;
  }

  const tone = getTradePlanTone(payload.decision);
  const isLivePlan = Boolean(payload.should_trade) && String(payload.trigger_state || "").toLowerCase() === "active_now";
  const entryChecks = payload.entry_checks && typeof payload.entry_checks === "object" ? payload.entry_checks : {};
  const signalText = String(entryChecks.signal || "").trim();
  const zoneSourceLabel = getZoneSourceLabel(signalText);
  const smcParameters = payload.smc_parameters && typeof payload.smc_parameters === "object" ? payload.smc_parameters : {};

  const zoneText = String(payload.zone || "").trim()
    || (Number.isFinite(Number(payload.entry)) ? formatPrice(payload.entry) : "")
    || "No zone identified yet";

  const whyItems = normalizeAiList(payload.why, [
    String(payload.reason || "").trim() || "No grounded reason returned.",
  ]);
  const blockedItems = normalizeAiList(payload.blocked_reasons, []);

  const tpItems = isLivePlan ? normalizeAiList(payload.tp_plan) : [];
  if (isLivePlan && !tpItems.length && Number.isFinite(Number(payload.tp))) {
    tpItems.push(`TP1: ${formatPrice(payload.tp)}`);
  }

  const setupLabel = (String(payload.setup || "").trim() || tone.badge).toUpperCase();
  const slText = isLivePlan && Number.isFinite(Number(payload.sl)) ? formatPrice(payload.sl) : "--";
  const rrText = isLivePlan && Number.isFinite(Number(payload.rr)) ? Number(payload.rr).toFixed(2) : "--";
  const triggerState = String(payload.trigger_state || "waiting").replaceAll("_", " ");
  const executionText = String(payload.execution_summary || "").trim()
    || String(payload.plan || "").trim()
    || (isLivePlan && Number.isFinite(Number(payload.entry))
      ? `${String(payload.decision || "").toUpperCase()} from ${zoneText}`
      : "");
  const invalidationText = String(payload.invalidation || "").trim();

  const zoneGate = entryChecks.zone_ok === true ? "Aligned" : entryChecks.zone_ok === false ? "Waiting" : "--";
  const triggerGate = entryChecks.confirmation_ok === true ? "Confirmed" : entryChecks.confirmation_ok === false ? "Waiting" : "--";

  const pipelineRows = [
    `Bias: ${String(payload.bias || "mixed").toUpperCase()} | Phase: ${String(payload.market_phase || "transition").toUpperCase()}`,
    `Location: ${String(payload.location || "middle").replaceAll("_", " ")}`,
    `Zone: ${zoneGate}`,
    `M5/M1: ${triggerGate}`,
  ];
  if (signalText) pipelineRows.splice(2, 0, `Signal: ${zoneSourceLabel || signalText.replaceAll("_", " ")}`);

  const smcRows = [
    `H1 Bias: ${String(smcParameters.h1Bias || payload.bias || "--").toUpperCase()}`,
    `M15 Phase: ${String(smcParameters.m15Phase || payload.market_phase || "--").toUpperCase()}`,
    `M15 High: ${Number.isFinite(Number(smcParameters.m15StructureHigh)) ? formatPrice(smcParameters.m15StructureHigh) : "--"}`,
    `M15 Low: ${Number.isFinite(Number(smcParameters.m15StructureLow)) ? formatPrice(smcParameters.m15StructureLow) : "--"}`,
    `EQ: ${Number.isFinite(Number(smcParameters.m15Equilibrium)) ? formatPrice(smcParameters.m15Equilibrium) : "--"}`,
    `PD Position: ${String(smcParameters.m15PdPosition || "--").replaceAll("_", " ")}`,
    `Discount: ${String(smcParameters.m15DiscountZone || "--")}`,
    `Premium: ${String(smcParameters.m15PremiumZone || "--")}`,
    `Buy Zone: ${String(smcParameters.buyExecutionZone || "--")}`,
    `Sell Zone: ${String(smcParameters.sellExecutionZone || "--")}`,
    `Price: ${Number.isFinite(Number(smcParameters.activePrice)) ? formatPrice(smcParameters.activePrice) : "--"}`,
    `LTF Tone: ${String(smcParameters.ltfTone || "--").toUpperCase()}`,
  ];

  const zoneHeading = isLivePlan ? "Entry Zone" : "Watch Zone";
  const zoneNote = isLivePlan
    ? ""
    : `<p class="ai-plan-zone-note">Price needs to return here for the setup to activate.</p>`;
  const zoneSourceHtml = zoneSourceLabel
    ? `<span class="ai-plan-zone-source">${escapeHtml(zoneSourceLabel)}</span>`
    : "";

  return `
    <article class="ai-plan-card ${tone.tone}">
      <div class="ai-plan-topline">
        <span class="ai-plan-kicker">SMC Plan</span>
        <span class="ai-plan-trigger">Trigger: ${escapeHtml(triggerState.toUpperCase())}</span>
      </div>
      <h3 class="ai-plan-title"><span class="ai-plan-title-prefix">${escapeHtml(tone.prefix)}</span> ${escapeHtml(setupLabel)}</h3>
      <div class="ai-plan-grid">
        <section class="ai-plan-section ai-plan-section-zone">
          <h4>${escapeHtml(zoneHeading)}</h4>
          <p class="ai-plan-zone">${escapeHtml(zoneText)} ${zoneSourceHtml}</p>
          ${zoneNote}
        </section>
        <section class="ai-plan-section">
          <h4>Pipeline</h4>
          <ul>${pipelineRows.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
        </section>
        <section class="ai-plan-section">
          <h4>SMC Parameters</h4>
          <ul>${smcRows.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
        </section>
        <section class="ai-plan-section">
          <h4>SMC Read</h4>
          <ul>${whyItems.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
        </section>
        ${isLivePlan ? `
        <section class="ai-plan-stats">
          <div class="ai-plan-stat">
            <span>SL</span>
            <strong>${escapeHtml(slText)}</strong>
          </div>
          <div class="ai-plan-stat">
            <span>TP</span>
            <strong>${tpItems.length ? escapeHtml(tpItems[0]) : "--"}</strong>
          </div>
          <div class="ai-plan-stat">
            <span>R:R</span>
            <strong>${escapeHtml(rrText)}</strong>
          </div>
        </section>
        <section class="ai-plan-section">
          <h4>Execution</h4>
          <p>${escapeHtml(executionText)}</p>
        </section>
        ${invalidationText ? `
        <section class="ai-plan-section ai-plan-breakdown">
          <h4>Invalidation</h4>
          <p>${escapeHtml(invalidationText)}</p>
        </section>` : ""}
        ${tpItems.length ? `
        <section class="ai-plan-section">
          <h4>Targets</h4>
          <ul>${tpItems.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
        </section>` : ""}` : ""}
        ${blockedItems.length ? `
        <section class="ai-plan-section ai-plan-warning">
          <h4>Why No Trade</h4>
          <ul>${blockedItems.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
        </section>` : ""}
      </div>
    </article>
  `;
}

function simplifyCheck(item) {
  if (!item) return null;
  return {
    label: item.label ?? "",
    expected: item.expected ?? "",
    actual: item.actual ?? "",
    passed: Boolean(item.passed),
    score: Number(item.score || 0),
    value: item.value ?? "",
    detail: item.detail ?? "",
    raw: item.raw ?? null,
  };
}

function buildAiSnapshot() {
  const symbol = String(activeSymbolLabel.textContent || symbolInput.value || "XAUUSD").trim().toUpperCase() || "XAUUSD";
  const timeframes = {};
  for (const timeframe of TIMEFRAMES) {
    const state = chartState[timeframe];
    const latest = state?.candles?.[state.candles.length - 1] || null;
    const candles = state?.candles || [];
    const recentCandles = candles.slice(-6).map((candle) => ({
      time: Number(candle.time),
      open: Number(candle.open),
      high: Number(candle.high),
      low: Number(candle.low),
      close: Number(candle.close),
      tick_volume: Number(candle.tick_volume ?? 0),
    }));
    const latestClose = latest ? Number(latest.close) : null;
    const support = Number(state?.levels?.support);
    const resistance = Number(state?.levels?.resistance);
    timeframes[timeframe] = {
      latestClose,
      latestTime: latest ? Number(latest.time) : null,
      summary: state?.summary || null,
      levels: state?.levels || null,
      marketState: state?.marketState || null,
      location: {
        distanceToSupport: Number.isFinite(latestClose) && Number.isFinite(support) ? latestClose - support : null,
        distanceToResistance: Number.isFinite(latestClose) && Number.isFinite(resistance) ? resistance - latestClose : null,
      },
      structure: {
        buyLiquidity: getLiquidityPools(candles, "high", timeframe === "M1" ? 40 : 80).slice(-4),
        sellLiquidity: getLiquidityPools(candles, "low", timeframe === "M1" ? 40 : 80).slice(-4),
        swingHighs: getSwingCandidates(candles, "high", timeframe === "M1" ? 40 : 80).slice(-4),
        swingLows: getSwingCandidates(candles, "low", timeframe === "M1" ? 40 : 80).slice(-4),
      },
      recentCandles,
      volatility: {
        atr14: state?.volatility?.atr14 ?? null,
      },
    };
  }
  return {
    symbol,
    generated_at: new Date().toISOString(),
    market: {
      bid: Number(lastTickSnapshot?.bid ?? 0) || null,
      ask: Number(lastTickSnapshot?.ask ?? 0) || null,
      last_price: Number(lastTickSnapshot?.last ?? chartState.M1?.candles?.[chartState.M1.candles.length - 1]?.close ?? 0) || null,
      spread: Number.isFinite(Number(lastTickSnapshot?.ask)) && Number.isFinite(Number(lastTickSnapshot?.bid))
        ? Number(lastTickSnapshot.ask) - Number(lastTickSnapshot.bid)
        : null,
      session: getSessionLabel(),
    },
    timeframes,
  };
}

async function loadAiStatus() {
  try {
    const response = await fetch("/api/ai/status", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Failed to reach AI bridge.");
    const engine = String(payload.decision_engine || "local").trim().toLowerCase();
    const engineLabel = engine === "local" ? "Local Strategy Engine" : "Decision Engine";
    const label = payload.available ? "Connected" : "Offline";
    const model = payload.default_model || "local-setup-engine";
    setAiStatus(label, Boolean(payload.available));
    const autonomous = payload?.autonomous || null;
    const autonomousReview = autonomous?.last_result || null;
    if (autonomousReview && typeof autonomousReview === "object" && autonomousReview.decision) {
      aiBriefState.review = autonomousReview;
      const decision = String(autonomousReview.decision || "no_trade").toUpperCase();
      const updatedAt = autonomous.last_run_at
        ? new Date(autonomous.last_run_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })
        : "--:--:--";
      setAiBriefText(
        renderTradePlanHtml(autonomousReview),
        `Engine: ${autonomous.decision_engine || engineLabel} | Model: ${autonomous.model || autonomousReview.model || model} | Decision: ${decision} | Updated: ${updatedAt}`,
        true
      );
    }
    if (!payload.available) {
      setAiBriefText(
        `${engineLabel} is not connected yet.`,
        `Engine: ${engineLabel} | Model: ${model}`
      );
    }
  } catch (error) {
    setAiStatus("Offline", false);
    setAiBriefText(
      "AI bridge is unavailable right now.",
      "The trading board still works normally, but AI auto-trading is unavailable."
    );
  }
}

async function requestAiBrief(force = false) {
  if (aiBriefState.inFlight) return null;

  const model = normalizeAiModel();
  const snapshot = buildAiSnapshot();
  const nextHash = buildAiTriggerFingerprint();
  if (!force && aiBriefState.lastHash === nextHash) return aiBriefState.review;

  aiBriefState.inFlight = true;
  aiBriefState.lastHash = nextHash;
  if (refreshAiBriefButton) refreshAiBriefButton.disabled = true;
  setAiBriefText("Requesting live trade decision from the server...", `Engine: Local Strategy Engine | Input: live board context`);

  try {
    const response = await fetch("/api/ai/trade", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model,
        board: snapshot,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "AI brief request failed.");
    setAiStatus("Connected", true);
    aiBriefState.review = payload;
    const decision = String(payload.decision || "no_trade").toUpperCase();
    setAiBriefText(
      renderTradePlanHtml(payload),
      `Engine: Local Strategy Engine | Model: ${payload.model || model} | Decision: ${decision} | Updated: ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })}`,
      true
    );
    return payload;
  } catch (error) {
    setAiStatus("Offline", false);
    aiBriefState.review = null;
    setAiBriefText(
      error.message || "AI brief request failed.",
      "The server could not produce a fresh trade decision."
    );
    return null;
  } finally {
    aiBriefState.inFlight = false;
    if (refreshAiBriefButton) refreshAiBriefButton.disabled = false;
  }
}

function refreshDerivedState(state) {
  state.levels = calculateLevels(state.candles);
  state.marketState = calculateMarketState(state.candles);
  state.indicators = null;
  const atr14 = calculateATR(state.candles, 14);
  state.volatility = { atr14 };
  // SMC overlays — computed from the full loaded candle set
  const atr = atr14 || 6;
  state.orderBlocks = {
    bullish: computeOrderBlocks(state.candles, "buy",  atr),
    bearish: computeOrderBlocks(state.candles, "sell", atr),
  };
  state.fairValueGaps = {
    bullish: computeFVGs(state.candles, "buy"),
    bearish: computeFVGs(state.candles, "sell"),
  };
  // EMAs for all periods used by any TF (cheap to compute, skip if too few candles)
  state.emaValues = {
    9:   computeEMA(state.candles, 9),
    20:  computeEMA(state.candles, 20),
    50:  computeEMA(state.candles, 50),
    200: computeEMA(state.candles, 200),
  };
  state.vwapValues      = computeVWAP(state.candles);
  state.bosChoch        = computeBOSCHoCH(state.candles);
  state.activeStructure = computeActiveStructure(state.candles);
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

  // --- visible slice + index range (needed to align EMA/VWAP arrays) ---
  const allCandles = state.candles;
  const count = Math.max(20, Math.min(state.visibleCount, allCandles.length));
  const sliceEnd = Math.max(count, allCandles.length - state.offset);
  const sliceStart = Math.max(0, sliceEnd - count);
  const visible = allCandles.slice(sliceStart, sliceEnd);

  const { width, height, padding, chartWidth, chartHeight, barSpacing } =
    getChartGeometry(canvas, visible.length, state.offset === 0);

  // OB/FVG boxes end here — at live edge they extend to chart border, when scrolled they
  // cap at the last visible candle so historical views stay clean.
  const xActiveRight = state.offset === 0
    ? width - padding.right
    : Math.min(padding.left + visible.length * barSpacing, width - padding.right);

  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#040811";
  ctx.fillRect(0, 0, width, height);

  if (!visible.length) {
    ctx.fillStyle = "#9ab0d3";
    ctx.font = "14px Segoe UI";
    ctx.fillText("Waiting for MT5 candles...", 18, 24);
    return;
  }

  // Expand price range slightly to stop OB/FVG bands from touching edges
  const highs = visible.map((c) => Number(c.high));
  const lows  = visible.map((c) => Number(c.low));
  const maxPrice = Math.max(...highs);
  const minPrice = Math.min(...lows);
  const range = Math.max(maxPrice - minPrice, 0.00001);
  const priceToY = (p) => padding.top + ((maxPrice - p) / range) * chartHeight;
  const inPriceRange = (lo, hi) => hi >= minPrice && lo <= maxPrice;
  // Any overlay whose originating candle is newer than the last visible candle gets clipped.
  const visibleTimeEnd = visible.length > 0 ? Number(visible[visible.length - 1].time) : Infinity;

  // ── 1. Grid ─────────────────────────────────────────────────────────────
  ctx.strokeStyle = "rgba(145, 182, 255, 0.08)";
  ctx.lineWidth = 1;
  for (let row = 0; row <= 5; row++) {
    const y = padding.top + (chartHeight / 5) * row;
    ctx.beginPath(); ctx.moveTo(padding.left, y); ctx.lineTo(width - padding.right, y); ctx.stroke();
  }
  for (let col = 0; col <= 6; col++) {
    const x = padding.left + (chartWidth / 6) * col;
    ctx.beginPath(); ctx.moveTo(x, padding.top); ctx.lineTo(x, height - padding.bottom); ctx.stroke();
  }

  const overlays = TIMEFRAME_OVERLAYS[timeframe] || {};

  // ── 2. Fair Value Gaps — positioned rectangles from origin candle to right edge
  const drawFVGs = (fvgs, fillColor, borderColor, midColor) => {
    for (const fvg of (fvgs || [])) {
      if (Number(fvg.time) > visibleTimeEnd) continue;
      if (!inPriceRange(fvg.low, fvg.high)) continue;
      const xStart  = timeToX(fvg.time, visible, padding, barSpacing);
      const xEnd    = xActiveRight;
      const rectW   = Math.max(4, xEnd - xStart);
      const yTop    = priceToY(fvg.high);
      const yBottom = priceToY(fvg.low);
      const rectH   = Math.max(1, yBottom - yTop);
      // Fill
      ctx.fillStyle = fillColor;
      ctx.fillRect(xStart, yTop, rectW, rectH);
      // Border top/bottom
      ctx.strokeStyle = borderColor;
      ctx.lineWidth = 0.8;
      ctx.setLineDash([]);
      ctx.strokeRect(xStart, yTop, rectW, rectH);
      // Midpoint dashed line
      const yMid = priceToY(fvg.midpoint);
      ctx.strokeStyle = midColor;
      ctx.lineWidth = 0.7;
      ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(xStart, yMid); ctx.lineTo(xEnd, yMid); ctx.stroke();
      ctx.setLineDash([]);
      // Label
      ctx.fillStyle = borderColor;
      ctx.font = "bold 9px Segoe UI";
      ctx.fillText("FVG", xStart + 3, yTop + 10);
    }
  };
  drawFVGs(state.fairValueGaps?.bullish, "rgba(34,197,94,0.08)", "rgba(34,197,94,0.55)", "rgba(34,197,94,0.70)");
  drawFVGs(state.fairValueGaps?.bearish, "rgba(239,68,68,0.08)", "rgba(239,68,68,0.55)", "rgba(239,68,68,0.70)");

  // ── 3. Order Blocks — positioned rectangles anchored at originating candle
  const drawOBs = (obs, fillColor, borderColor, labelColor) => {
    for (const ob of (obs || [])) {
      if (Number(ob.time) > visibleTimeEnd) continue;
      if (!inPriceRange(ob.low, ob.high)) continue;
      const xStart  = timeToX(ob.time, visible, padding, barSpacing);
      const xEnd    = xActiveRight;
      const rectW   = Math.max(4, xEnd - xStart);
      const yTop    = priceToY(ob.high);
      const yBottom = priceToY(ob.low);
      const rectH   = Math.max(2, yBottom - yTop);
      // Fill body
      ctx.fillStyle = fillColor;
      ctx.fillRect(xStart, yTop, rectW, rectH);
      // Border
      ctx.strokeStyle = borderColor;
      ctx.lineWidth = 1.2;
      ctx.setLineDash([]);
      ctx.strokeRect(xStart, yTop, rectW, rectH);
      // Midpoint line
      const yMid = priceToY(ob.midpoint);
      ctx.strokeStyle = borderColor;
      ctx.lineWidth = 0.7;
      ctx.setLineDash([4, 3]);
      ctx.beginPath(); ctx.moveTo(xStart, yMid); ctx.lineTo(xEnd, yMid); ctx.stroke();
      ctx.setLineDash([]);
      // "OB" label inside box (top-left) + price range outside right edge
      ctx.fillStyle = labelColor;
      ctx.font = "bold 10px Segoe UI";
      ctx.fillText("OB", xStart + 3, yTop + 11);
      if (state.offset === 0) {
        ctx.font = "9px Segoe UI";
        ctx.fillText(`${formatPrice(ob.low)}–${formatPrice(ob.high)}`, xEnd + 3, yTop + 10);
      }
    }
  };
  drawOBs(state.orderBlocks?.bullish, "rgba(34,197,94,0.12)", "rgba(34,197,94,0.65)", "rgba(34,197,94,0.95)");
  drawOBs(state.orderBlocks?.bearish, "rgba(239,68,68,0.12)", "rgba(239,68,68,0.65)", "rgba(239,68,68,0.95)");

  // ── 4. Support / Resistance lines ────────────────────────────────────────
  if (state.levels?.resistance != null) {
    const y = priceToY(Number(state.levels.resistance));
    ctx.strokeStyle = "rgba(239,68,68,0.85)";
    ctx.lineWidth = 1.2;
    ctx.setLineDash([8, 6]);
    ctx.beginPath(); ctx.moveTo(padding.left, y); ctx.lineTo(width - padding.right, y); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(239,68,68,0.95)";
    ctx.font = "11px Segoe UI";
    ctx.fillText(`R ${formatPrice(state.levels.resistance)}`, padding.left + 6, y - 5);
  }
  if (state.levels?.support != null) {
    const y = priceToY(Number(state.levels.support));
    ctx.strokeStyle = "rgba(103,166,255,0.80)";
    ctx.lineWidth = 1.2;
    ctx.setLineDash([8, 6]);
    ctx.beginPath(); ctx.moveTo(padding.left, y); ctx.lineTo(width - padding.right, y); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(103,166,255,0.92)";
    ctx.font = "11px Segoe UI";
    ctx.fillText(`S ${formatPrice(state.levels.support)}`, padding.left + 6, y - 5);
  }

  // ── 5. Swing high / low tick marks (MTF and HTF only) ────────────────────
  if (overlays.swings) {
    const swingHighs = getSwingCandidates(visible, "high", 40);
    const swingLows  = getSwingCandidates(visible, "low",  40);
    ctx.strokeStyle = "rgba(251,191,36,0.45)";
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 4]);
    for (const lvl of swingHighs) {
      if (!inPriceRange(lvl, lvl)) continue;
      const y = priceToY(lvl);
      ctx.beginPath(); ctx.moveTo(padding.left, y); ctx.lineTo(width - padding.right, y); ctx.stroke();
    }
    ctx.strokeStyle = "rgba(103,166,255,0.35)";
    for (const lvl of swingLows) {
      if (!inPriceRange(lvl, lvl)) continue;
      const y = priceToY(lvl);
      ctx.beginPath(); ctx.moveTo(padding.left, y); ctx.lineTo(width - padding.right, y); ctx.stroke();
    }
    ctx.setLineDash([]);
  }

  // ── 6. Candles ───────────────────────────────────────────────────────────
  const candleWidth = Math.max(3, barSpacing * 0.56);
  visible.forEach((candle, index) => {
    const x      = padding.left + index * barSpacing + (barSpacing - candleWidth) / 2;
    const openY  = priceToY(Number(candle.open));
    const closeY = priceToY(Number(candle.close));
    const highY  = priceToY(Number(candle.high));
    const lowY   = priceToY(Number(candle.low));
    const bull   = Number(candle.close) >= Number(candle.open);
    ctx.strokeStyle = bull ? "rgba(103,166,255,0.92)" : "rgba(160,168,183,0.92)";
    ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.moveTo(x + candleWidth / 2, highY); ctx.lineTo(x + candleWidth / 2, lowY); ctx.stroke();
    ctx.fillStyle = bull ? "rgba(103,166,255,0.90)" : "rgba(160,168,183,0.90)";
    ctx.fillRect(x, Math.min(openY, closeY), candleWidth, Math.max(2, Math.abs(closeY - openY)));
  });

  // ── 7. BOS / CHoCH — all timeframes ─────────────────────────────────────
  {
    const allC = state.candles || [];
    const wcStart = Math.max(0, allC.length - Math.min(180, allC.length));
    const structureWindow = allC.slice(wcStart);

    for (const ev of (state.bosChoch || [])) {
      const levelPrice = Number(ev.level);
      if (!Number.isFinite(levelPrice) || !inPriceRange(levelPrice, levelPrice)) continue;

      const levelCandle = structureWindow[ev.levelIdx];
      const breakCandle = structureWindow[ev.breakIdx];
      if (!levelCandle || !breakCandle) continue;
      if (Number(breakCandle.time) > visibleTimeEnd) continue;

      const xLevel = timeToX(Number(levelCandle.time), visible, padding, barSpacing);
      const xBreak = timeToX(Number(breakCandle.time), visible, padding, barSpacing);
      if (xLevel === padding.left && xBreak === padding.left) continue;

      const y        = priceToY(levelPrice);
      const isBOS    = ev.type === "BOS";
      const lineColor = isBOS ? "rgba(184,184,184,0.85)" : "rgba(247,208,70,0.95)";
      const lineEnd  = Math.min(xBreak, width - padding.right - 4);
      const labelText = isBOS ? "BOS" : "CHoCH";
      const midX     = xLevel + (lineEnd - xLevel) * 0.5;

      ctx.strokeStyle = lineColor;
      ctx.lineWidth   = isBOS ? 1 : 1.2;
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(xLevel, y);
      ctx.lineTo(lineEnd, y);
      ctx.stroke();

      ctx.fillStyle = lineColor;
      ctx.font      = `${isBOS ? "bold " : ""}11px Segoe UI`;
      const tw      = ctx.measureText(labelText).width;
      ctx.fillText(labelText, Math.max(padding.left + 2, midX - tw / 2), y - 5);
    }
  }

  // ── 7b. Current active structure lines + Fibonacci retracement ───────────
  // Only draw when at the live edge — these are current-structure levels, not historical.
  if (state.offset === 0) {
    const struct = state.activeStructure;
    if (struct && Number.isFinite(struct.high) && Number.isFinite(struct.low) && struct.high > struct.low) {
      const structRange = struct.high - struct.low;

      // Structure high line (blue, dashed — level to watch for bullish break)
      if (inPriceRange(struct.high, struct.high)) {
        const xHigh = timeToX(struct.highTime, visible, padding, barSpacing);
        const yHigh = priceToY(struct.high);
        ctx.strokeStyle = "rgba(100,181,246,0.70)";
        ctx.lineWidth   = 1.2;
        ctx.setLineDash([6, 4]);
        ctx.beginPath();
        ctx.moveTo(xHigh, yHigh);
        ctx.lineTo(width - padding.right, yHigh);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = "rgba(100,181,246,0.80)";
        ctx.font      = "10px Segoe UI";
        ctx.fillText(`H ${formatPrice(struct.high)}`, width - padding.right + 3, yHigh - 3);
      }

      // Structure low line (blue, dashed — level to watch for bearish break)
      if (inPriceRange(struct.low, struct.low)) {
        const xLow = timeToX(struct.lowTime, visible, padding, barSpacing);
        const yLow = priceToY(struct.low);
        ctx.strokeStyle = "rgba(100,181,246,0.70)";
        ctx.lineWidth   = 1.2;
        ctx.setLineDash([6, 4]);
        ctx.beginPath();
        ctx.moveTo(xLow, yLow);
        ctx.lineTo(width - padding.right, yLow);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = "rgba(100,181,246,0.80)";
        ctx.font      = "10px Segoe UI";
        ctx.fillText(`L ${formatPrice(struct.low)}`, width - padding.right + 3, yLow + 10);
      }

      // Fibonacci retracement levels within the active structure range.
      // dir=1 (bearish leg): fib measured from low → high (0.786 = deep premium, 0.382 = discount)
      // dir=2 (bullish leg): fib measured from high → low (0.786 = deep discount, 0.382 = premium)
      // Mirrors the Pine "Structure Fibonacci" calculation exactly.
      const FIBS = [
        { value: 0.786, color: "rgba(100,181,246,0.65)",  label: "0.786" },
        { value: 0.705, color: "rgba(242,54,69,0.65)",    label: "0.705" },
        { value: 0.618, color: "rgba(8,153,129,0.70)",    label: "0.618" },
        { value: 0.500, color: "rgba(76,175,80,0.65)",    label: "0.5"   },
        { value: 0.382, color: "rgba(129,199,132,0.65)",  label: "0.382" },
      ];

      ctx.lineWidth = 0.8;
      ctx.font      = "9px Segoe UI";
      for (const fib of FIBS) {
        const fibPrice = struct.direction === 1
          ? struct.low  + structRange * fib.value          // bearish leg: from low upward
          : struct.low  + structRange * (1 - fib.value);   // bullish leg: from low, inverted
        if (!inPriceRange(fibPrice, fibPrice)) continue;
        const yFib = priceToY(fibPrice);
        const xStart = Math.min(
          timeToX(struct.direction === 1 ? struct.highTime : struct.lowTime, visible, padding, barSpacing),
          width - padding.right
        );
        ctx.strokeStyle = fib.color;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(xStart, yFib);
        ctx.lineTo(width - padding.right, yFib);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = fib.color;
        ctx.textAlign = "right";
        ctx.fillText(`${fib.label} (${formatPrice(fibPrice)})`, width - padding.right - 6, yFib + 3);
        ctx.textAlign = "left";
      }
      ctx.setLineDash([]);
    }
  } // end live-edge structure+fib block

  // ── 8. EMA lines ─────────────────────────────────────────────────────────
  for (const period of (overlays.emas || [])) {
    const series = state.emaValues?.[period];
    if (!series || series.length < 2) continue;
    const slice = series.slice(sliceStart, sliceEnd);
    const style = EMA_STYLE[period];
    ctx.strokeStyle = style.color;
    ctx.lineWidth   = style.width;
    ctx.setLineDash([]);
    ctx.beginPath();
    let started = false;
    slice.forEach((val, i) => {
      if (val == null || !Number.isFinite(val)) { started = false; return; }
      if (!inPriceRange(val, val)) { started = false; return; }
      const x = padding.left + i * barSpacing + barSpacing / 2;
      const y = priceToY(val);
      if (!started) { ctx.moveTo(x, y); started = true; }
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    // Label only at live edge — when scrolled back these stack up and clutter the y-axis
    if (state.offset === 0) {
      const lastVal = [...slice].reverse().find(v => v != null && Number.isFinite(v));
      if (lastVal != null && inPriceRange(lastVal, lastVal)) {
        ctx.fillStyle = style.color;
        ctx.font = "10px Segoe UI";
        ctx.fillText(style.label, width - padding.right + 4, priceToY(lastVal) + 4);
      }
    }
  }

  // ── 9. VWAP (LTF only) ───────────────────────────────────────────────────
  if (overlays.vwap && state.vwapValues?.length) {
    const slice = state.vwapValues.slice(sliceStart, sliceEnd);
    ctx.strokeStyle = "rgba(125,211,252,0.75)";
    ctx.lineWidth = 1.4;
    ctx.setLineDash([5, 4]);
    ctx.beginPath();
    let started = false;
    slice.forEach((val, i) => {
      if (!Number.isFinite(val)) { started = false; return; }
      if (!inPriceRange(val, val)) { started = false; return; }
      const x = padding.left + i * barSpacing + barSpacing / 2;
      const y = priceToY(val);
      if (!started) { ctx.moveTo(x, y); started = true; }
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.setLineDash([]);
    if (state.offset === 0) {
      const lastVwap = [...slice].reverse().find(v => Number.isFinite(v));
      if (lastVwap != null && inPriceRange(lastVwap, lastVwap)) {
        ctx.fillStyle = "rgba(125,211,252,0.85)";
        ctx.font = "10px Segoe UI";
        ctx.fillText("VWAP", width - padding.right + 4, priceToY(lastVwap) + 4);
      }
    }
  }

  // ── 10. Price axis ────────────────────────────────────────────────────────
  ctx.fillStyle = "rgba(154,176,211,0.90)";
  ctx.font = "11px Segoe UI";
  for (let row = 0; row <= 5; row++) {
    const price = maxPrice - (range / 5) * row;
    const y = padding.top + (chartHeight / 5) * row;
    ctx.fillText(formatPrice(price), width - padding.right + 10, y + 4);
  }

  // ── 11. Time axis ────────────────────────────────────────────────────────
  const labelIndexes = [0, Math.floor(visible.length * 0.25), Math.floor(visible.length * 0.5), Math.floor(visible.length * 0.75), visible.length - 1];
  ctx.fillStyle = "rgba(154,176,211,0.90)";
  ctx.font = "11px Segoe UI";
  for (const index of labelIndexes) {
    const candle = visible[index];
    if (!candle) continue;
    const x = padding.left + index * barSpacing;
    const prev = index > 0 ? visible[index - 1] : null;
    ctx.fillText(formatAxisLabel(candle.time, prev?.time ?? null), x, height - 14);
  }

  // ── 12. Hover crosshair ──────────────────────────────────────────────────
  const hoveredIndex = state.hoverIndex;
  if (hoveredIndex != null && visible[hoveredIndex]) {
    const candle  = visible[hoveredIndex];
    const centerX = padding.left + hoveredIndex * barSpacing + barSpacing / 2;
    const closeY  = priceToY(Number(candle.close));
    ctx.strokeStyle = "rgba(125,211,252,0.65)";
    ctx.lineWidth = 1;
    ctx.setLineDash([6, 4]);
    ctx.beginPath(); ctx.moveTo(centerX, padding.top); ctx.lineTo(centerX, height - padding.bottom); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(padding.left, closeY); ctx.lineTo(width - padding.right, closeY); ctx.stroke();
    ctx.setLineDash([]);
    const hoverText = `${formatDisplayTimestamp(candle.time)}  O ${formatPrice(candle.open)}  H ${formatPrice(candle.high)}  L ${formatPrice(candle.low)}  C ${formatPrice(candle.close)}  V ${Number(candle.tick_volume ?? 0).toLocaleString()}`;
    ctx.fillStyle = "rgba(10,18,30,0.92)";
    ctx.fillRect(18, 10, Math.min(620, width - 36), 26);
    ctx.fillStyle = "#edf4ff";
    ctx.font = "12px Segoe UI";
    ctx.fillText(hoverText, 24, 28);
  }
}

function renderBoard() {
  renderTradeOverview();
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
  lastTickSnapshot = tickPayload || null;
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
    updateCardMeta(timeframe);
    drawChart(timeframe);
  }
  maybeExecuteAutoTrade();
}

async function loadBoard() {
  const symbol = String(symbolInput.value || "XAUUSD").trim().toUpperCase() || "XAUUSD";
  const limitRaw = String(limitInput.value || "ALL").trim().toUpperCase() || "ALL";
  // If the user typed a specific limit, honour it globally.
  // Otherwise fall back to the per-TF context-appropriate default from TIMEFRAME_DEFAULT_BARS.
  const globalLimitOverride = limitRaw === "ALL" ? null : Math.max(80, Math.min(99999, Number(limitRaw || 99999)));
  refreshButton.disabled = true;
  bridgeStatus.textContent = "Syncing";
  activeSymbolLabel.textContent = symbol;
  try {
    let loadedCount = 0;
    for (const timeframe of TIMEFRAMES) {
      const tfLimit = globalLimitOverride !== null ? globalLimitOverride : (TIMEFRAME_DEFAULT_BARS[timeframe] ?? 200);
      const response = await fetch(
        `/api/timeframe?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}&limit=${encodeURIComponent(tfLimit)}`,
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
    await saveLatestBoardSnapshot();
    if (!aiBriefState.hasAutoLoaded) {
      aiBriefState.hasAutoLoaded = true;
      await loadAiStatus();
    }
    saveWorkspaceSessionState();
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
    saveWorkspaceSessionState();
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
    lastTickSnapshot = payload;
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

function startSnapshotSync() {
  if (snapshotRefreshHandle) clearInterval(snapshotRefreshHandle);
  if (!autoRefreshEnabled) return;
  snapshotRefreshHandle = window.setInterval(() => {
    saveLatestBoardSnapshot();
  }, SNAPSHOT_SYNC_MS);
}

function startAiBriefAutoRefresh() {
  if (aiBriefAutoRefreshHandle) clearInterval(aiBriefAutoRefreshHandle);
  if (!autoRefreshEnabled) return;
  aiBriefAutoRefreshHandle = window.setInterval(loadAiStatus, 10000);
}

function refreshWorkspaceStatusOnResume() {
  const hasRecentCache = Boolean(aiBriefState.review || lastTickSnapshot || chartState.M1?.candles?.length);
  bridgeStatus.textContent = hasRecentCache ? "Live" : "Syncing";
  syncRecentBoard();
  loadTick();
  loadAiStatus();
  loadAutoTradeStatus();
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

function shiftNewsCalendarMonth(delta) {
  const viewMonth = newsCalendarState.viewMonth || monthKeyFromDate(getBrokerNowDate());
  const [year, month] = viewMonth.split("-").map(Number);
  const next = new Date(year, (month || 1) - 1 + delta, 1);
  newsCalendarState.viewMonth = monthKeyFromDate(next);
  renderNewsCalendarPanel();
}

function selectNewsCalendarDate(dateKey) {
  newsCalendarState.selectedDate = dateKey;
  newsCalendarState.viewMonth = String(dateKey || "").slice(0, 7) || newsCalendarState.viewMonth;
  renderNewsCalendarPanel();
}

function addNewsCalendarEvent() {
  const selectedDate = newsCalendarState.selectedDate || dateKeyFromDate(getBrokerNowDate());
  const timeValue = String(newsEventTimeInput?.value || "").trim();
  const titleValue = String(newsEventTitleInput?.value || "").trim();
  if (!selectedDate || !timeValue || !titleValue) return;
  if (isPastNewsEvent(selectedDate, timeValue)) {
    bridgeStatus.textContent = "Past broker-time events cannot be added";
    return;
  }
  const editingId = newsCalendarState.editingEventId || "";
  const nextEvent = {
    id: editingId || `manual-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    date: selectedDate,
    time: timeValue,
    title: titleValue,
  };
  const existingEvents = (newsCalendarState.events || []).filter((event) => event.id !== editingId);
  newsCalendarState.events = [...existingEvents, nextEvent].sort((a, b) => `${a.date} ${a.time} ${a.title}`.localeCompare(`${b.date} ${b.time} ${b.title}`));
  newsCalendarState.event_count = newsCalendarState.events.length;
  newsCalendarState.hasUnsavedChanges = true;
  clearNewsCalendarEditor();
  renderNewsCalendarPanel();
}

function removeNewsCalendarEvent(eventId) {
  newsCalendarState.events = (newsCalendarState.events || []).filter((event) => event.id !== eventId);
  newsCalendarState.event_count = newsCalendarState.events.length;
  newsCalendarState.hasUnsavedChanges = true;
  if (newsCalendarState.editingEventId === eventId) clearNewsCalendarEditor();
  renderNewsCalendarPanel();
}

function setCollapsedState(target, collapsed, button, expandedLabel = "-", collapsedLabel = "+") {
  if (!target || !button) return;
  target.classList.toggle("is-collapsed", collapsed);
  button.textContent = collapsed ? collapsedLabel : expandedLabel;
  button.setAttribute("aria-expanded", String(!collapsed));
  saveWorkspaceSessionState();
}

function toggleAutoTradePanelCollapsed() {
  const collapsed = !autotradePanelElement?.classList.contains("is-collapsed");
  setCollapsedState(autotradePanelElement, collapsed, autotradePanelToggle);
}

function toggleAiBriefCollapsed() {
  const collapsed = !aiBriefPanelElement?.classList.contains("is-collapsed");
  setCollapsedState(aiBriefPanelElement, collapsed, aiBriefToggle);
}

function toggleNewsCalendarCollapsed() {
  const collapsed = !newsCalendarPanelElement?.classList.contains("is-collapsed");
  setCollapsedState(newsCalendarPanelElement, collapsed, newsCalendarToggle);
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
    const geometry = getChartGeometry(canvas, visible.length, state.offset === 0);

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
window.addEventListener("pageshow", refreshWorkspaceStatusOnResume);
window.addEventListener("focus", refreshWorkspaceStatusOnResume);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") refreshWorkspaceStatusOnResume();
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
  startAiBriefAutoRefresh();
});
autotradePanelToggle?.addEventListener("click", toggleAutoTradePanelCollapsed);
newsCalendarToggle?.addEventListener("click", toggleNewsCalendarCollapsed);
aiBriefToggle?.addEventListener("click", toggleAiBriefCollapsed);
refreshAiBriefButton?.addEventListener("click", () => {
  requestAiBrief(true);
});
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
newsCalendarPrevButton?.addEventListener("click", () => {
  shiftNewsCalendarMonth(-1);
});
newsCalendarNextButton?.addEventListener("click", () => {
  shiftNewsCalendarMonth(1);
});
newsCalendarGrid?.addEventListener("click", (event) => {
  const button = event.target instanceof Element ? event.target.closest(".news-calendar-day") : null;
  if (!button) return;
  const dateKey = button.getAttribute("data-date");
  if (dateKey) selectNewsCalendarDate(dateKey);
});
newsEventAddButton?.addEventListener("click", () => {
  addNewsCalendarEvent();
});
newsCalendarEventList?.addEventListener("click", (event) => {
  const target = event.target instanceof Element ? event.target : null;
  const editButton = target ? target.closest("[data-edit-event-id]") : null;
  if (editButton) {
    startEditingNewsCalendarEvent(String(editButton.getAttribute("data-edit-event-id") || ""));
    return;
  }
  const removeButton = target ? target.closest("[data-event-id]") : null;
  if (!removeButton) return;
  removeNewsCalendarEvent(String(removeButton.getAttribute("data-event-id") || ""));
});
newsEventCancelButton?.addEventListener("click", () => {
  clearNewsCalendarEditor();
  renderNewsCalendarPanel();
});
newsCalendarApplyButton?.addEventListener("click", async () => {
  newsCalendarState.before_minutes = 45;
  newsCalendarState.after_minutes = 45;
  try {
    await saveAutoTradeConfig();
    bridgeStatus.textContent = "Calendar saved";
  } catch (error) {
    bridgeStatus.textContent = "Calendar save failed";
  }
});
contentScrollElement?.addEventListener("scroll", () => {
  saveWorkspaceSessionState();
});
window.addEventListener("beforeunload", () => {
  saveWorkspaceSessionState();
});

buildBoard();
for (const timeframe of TIMEFRAMES) bindChartInteractions(timeframe);
formatClock();
window.setInterval(formatClock, 1000);
renderCooldownLabel();
renderActiveTradePanel();
renderNewsCalendarPanel();
startCooldownTicker();
resizeCanvases();
renderBoard();
setAutoTradeUi();
const restoredWorkspace = restoreWorkspaceSessionState();
loadAutoTradeStatus();
loadAiStatus();
if (restoredWorkspace) {
  syncRecentBoard();
} else {
  loadBoard();
}
startLiveSync();
startTickSync();
startSnapshotSync();
startAiBriefAutoRefresh();
