const dashboardStatus = document.getElementById("dashboard-status");
const dashboardTimezone = document.getElementById("dashboard-timezone");
const dashboardClock = document.getElementById("dashboard-clock");
const todayNetCard = document.getElementById("today-net-card");
const todayCountCard = document.getElementById("today-count-card");
const allNetCard = document.getElementById("all-net-card");
const selectedPeriodLabel = document.getElementById("selected-period-label");
const selectedCountLabel = document.getElementById("selected-count-label");
const todayNetValue = document.getElementById("today-net-value");
const todayDateCopy = document.getElementById("today-date-copy");
const todayTradesValue = document.getElementById("today-trades-value");
const todayBreakdownCopy = document.getElementById("today-breakdown-copy");
const allNetValue = document.getElementById("all-net-value");
const allBreakdownCopy = document.getElementById("all-breakdown-copy");
const missedTradesCard = document.getElementById("missed-trades-card");
const missedTradesValue = document.getElementById("missed-trades-value");
const missedTradesCopy = document.getElementById("missed-trades-copy");
const historyCountCopy = document.getElementById("history-count-copy");
const historyTableBody = document.getElementById("history-table-body");
const equityCurveCopy = document.getElementById("equity-curve-copy");
const equityCurveChart = document.getElementById("equity-curve-chart");
const setupPerformanceCopy = document.getElementById("setup-performance-copy");
const setupPerformanceChart = document.getElementById("setup-performance-chart");
const setupHeatmapCopy = document.getElementById("setup-heatmap-copy");
const setupHeatmapChart = document.getElementById("setup-heatmap-chart");
const snapshotCopy = document.getElementById("snapshot-copy");
const snapshotGrid = document.getElementById("snapshot-grid");
const sessionCopy = document.getElementById("session-copy");
const sessionChart = document.getElementById("session-chart");
const aiFrictionCopy = document.getElementById("ai-friction-copy");
const aiBlockedChart = document.getElementById("ai-blocked-chart");
const aiSetupChart = document.getElementById("ai-setup-chart");
const historyFilters = Array.from(document.querySelectorAll(".history-filter"));
const historyDateFrom = document.getElementById("history-date-from");
const historyDateTo = document.getElementById("history-date-to");
const historyApply = document.getElementById("history-apply");

let activePeriod = "daily";

function formatMoney(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return number.toFixed(2);
}

function titleCase(value) {
  const text = String(value || "").trim().replaceAll("_", " ");
  return text ? text.replace(/\b\w/g, (char) => char.toUpperCase()) : "--";
}

function formatClock() {
  if (!dashboardClock) return;
  dashboardClock.textContent = new Date().toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function setCardTone(card, value) {
  if (!card) return;
  card.classList.remove("is-positive", "is-negative");
  const number = Number(value);
  if (!Number.isFinite(number)) return;
  if (number > 0) card.classList.add("is-positive");
  if (number < 0) card.classList.add("is-negative");
}

function humanizePeriod(period) {
  const map = {
    daily: "Daily",
    weekly: "Weekly",
    monthly: "Monthly",
    yearly: "Yearly",
    all: "Entire History",
    custom: "Custom Range",
  };
  return map[String(period || "").toLowerCase()] || "Selected";
}

function buildHistoryRow(deal) {
  const row = document.createElement("tr");
  if (deal.is_today) row.classList.add("today-row");
  const profitClass = Number(deal.net) >= 0 ? "value-positive" : "value-negative";
  const changeClass = Number(deal.change) >= 0 ? "value-positive" : "value-negative";
  const aiVerdict = String(deal.ai_verdict || "").trim().toUpperCase();
  const aiSummary = String(deal.ai_summary || deal.ai_decision || "").trim();
  const aiClass = aiVerdict === "STRONG" ? "ai-strong" : aiVerdict === "WEAK" ? "ai-weak" : aiVerdict === "ACCEPTABLE" ? "ai-acceptable" : "";
  row.innerHTML = `
    <td>${deal.open_time_label || "--"}</td>
    <td>${deal.symbol || "--"}</td>
    <td>${deal.ticket || "--"}</td>
    <td title="${aiSummary || "No AI review logged"}"><span class="ai-chip ${aiClass}">${aiVerdict || "--"}</span></td>
    <td>${String(deal.side || "--").toLowerCase()}</td>
    <td>${Number(deal.volume || 0).toFixed(2)}</td>
    <td>${formatMoney(deal.open_price)}</td>
    <td>${formatMoney(deal.sl)}</td>
    <td>${formatMoney(deal.tp)}</td>
    <td>${deal.close_time_label || "--"}</td>
    <td>${formatMoney(deal.close_price)}</td>
    <td class="${profitClass}">${formatMoney(deal.net)}</td>
    <td class="${changeClass}">${formatMoney(deal.change)}</td>
  `;
  return row;
}

function renderEquityCurve(points) {
  if (!equityCurveChart) return;
  const rows = Array.isArray(points) ? points : [];
  if (!rows.length) {
    equityCurveChart.innerHTML = `<div class="curve-empty">No closed trades in this range.</div>`;
    equityCurveCopy.textContent = "No curve available";
    return;
  }

  const width = 760;
  const height = 280;
  const padding = { top: 24, right: 20, bottom: 34, left: 44 };
  const values = rows.map((point) => Number(point.cumulative || 0));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(max - min, 1);
  const usableWidth = width - padding.left - padding.right;
  const usableHeight = height - padding.top - padding.bottom;
  const baselineValue = min > 0 ? min : 0;
  const stepX = rows.length > 1 ? usableWidth / (rows.length - 1) : 0;
  const toY = (value) => padding.top + ((max - value) / range) * usableHeight;
  const toX = (index) => padding.left + stepX * index;
  const gridValues = [min, min + range * 0.33, min + range * 0.66, max];
  const linePath = rows
    .map((point, index) => `${index === 0 ? "M" : "L"} ${toX(index).toFixed(2)} ${toY(Number(point.cumulative || 0)).toFixed(2)}`)
    .join(" ");
  const areaPath = `${linePath} L ${toX(rows.length - 1).toFixed(2)} ${(height - padding.bottom).toFixed(2)} L ${toX(0).toFixed(2)} ${(height - padding.bottom).toFixed(2)} Z`;
  const start = rows[0];
  const last = rows[rows.length - 1];
  const peak = rows.reduce((best, point) => Number(point.cumulative || 0) > Number(best.cumulative || 0) ? point : best, rows[0]);
  const avgTrade = rows.length ? values[values.length - 1] / rows.length : 0;
  const drawdown = roundTo2(Number(peak.cumulative || 0) - Number(last.cumulative || 0));
  equityCurveCopy.textContent = `${rows.length} closed trades plotted | Latest cumulative ${formatMoney(last.cumulative)}`;
  equityCurveChart.innerHTML = `
    <div class="curve-summary">
      <div class="curve-summary-pill">
        <span>Start</span>
        <strong>${formatMoney(start.cumulative)}</strong>
      </div>
      <div class="curve-summary-pill">
        <span>Peak</span>
        <strong>${formatMoney(peak.cumulative)}</strong>
      </div>
      <div class="curve-summary-pill">
        <span>Latest</span>
        <strong>${formatMoney(last.cumulative)}</strong>
      </div>
      <div class="curve-summary-pill">
        <span>Drawdown</span>
        <strong>${formatMoney(drawdown)}</strong>
      </div>
      <div class="curve-summary-pill">
        <span>Avg / Trade</span>
        <strong>${formatMoney(avgTrade)}</strong>
      </div>
    </div>
    <svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-label="Cumulative net curve">
      ${gridValues.map((value) => `
        <line class="curve-grid" x1="${padding.left}" y1="${toY(value).toFixed(2)}" x2="${width - padding.right}" y2="${toY(value).toFixed(2)}"></line>
        <text x="${padding.left - 8}" y="${(toY(value) + 4).toFixed(2)}" text-anchor="end" fill="#9ab0d3" font-size="11">${formatMoney(value)}</text>
      `).join("")}
      <line class="curve-axis" x1="${padding.left}" y1="${height - padding.bottom}" x2="${width - padding.right}" y2="${height - padding.bottom}"></line>
      <line class="curve-axis" x1="${padding.left}" y1="${padding.top}" x2="${padding.left}" y2="${height - padding.bottom}"></line>
      ${baselineValue >= min && baselineValue <= max ? `<line class="curve-baseline" x1="${padding.left}" y1="${toY(baselineValue).toFixed(2)}" x2="${width - padding.right}" y2="${toY(baselineValue).toFixed(2)}"></line>` : ""}
      <path class="curve-area" d="${areaPath}"></path>
      <path class="curve-line" d="${linePath}"></path>
      ${rows.map((point, index) => `<circle class="curve-point" cx="${toX(index).toFixed(2)}" cy="${toY(Number(point.cumulative || 0)).toFixed(2)}" r="3"></circle>`).join("")}
      ${rows.map((point, index) => index === rows.length - 1 || index === 0 || index === Math.floor(rows.length / 2) ? `<text x="${toX(index).toFixed(2)}" y="${height - 10}" text-anchor="middle" fill="#9ab0d3" font-size="11">${String(point.label || "").slice(5, 16)}</text>` : "").join("")}
      <text x="${width - padding.right - 4}" y="${(toY(Number(last.cumulative || 0)) - 10).toFixed(2)}" text-anchor="end" fill="#edf4ff" font-size="11">${formatMoney(last.cumulative)}</text>
    </svg>
  `;
}

function roundTo2(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.round(number * 100) / 100 : 0;
}

function renderSetupPerformance(aiAnalytics, setupSessionStats = []) {
  if (!setupPerformanceChart || !setupPerformanceCopy) return;
  const rows = (Array.isArray(aiAnalytics?.setup_types) ? aiAnalytics.setup_types : []).slice().sort((left, right) => {
    const leftExecuted = Number(left?.executed || 0);
    const rightExecuted = Number(right?.executed || 0);
    const leftWin = Number(left?.win_rate || 0);
    const rightWin = Number(right?.win_rate || 0);
    if (rightExecuted !== leftExecuted) return rightExecuted - leftExecuted;
    if (rightWin !== leftWin) return rightWin - leftWin;
    const leftActivation = Number(left?.activation_rate || 0);
    const rightActivation = Number(right?.activation_rate || 0);
    if (rightActivation !== leftActivation) return rightActivation - leftActivation;
    return Number(right?.decisions || 0) - Number(left?.decisions || 0);
  });
  const heatmapRows = Array.isArray(setupSessionStats) ? setupSessionStats : [];
  if (!rows.length && !heatmapRows.length) {
    setupPerformanceChart.innerHTML = `<div class="curve-empty">No strategy setup performance available yet.</div>`;
    setupPerformanceCopy.textContent = "No setup performance in this range";
    if (setupHeatmapChart && setupHeatmapCopy) {
      setupHeatmapChart.innerHTML = `<div class="curve-empty">No setup-session heatmap available yet.</div>`;
      setupHeatmapCopy.textContent = "No heatmap available in this range";
    }
    return;
  }
  setupPerformanceCopy.textContent = "Best place to tune trade frequency and quality";
  const executedRows = rows.filter((row) => Number(row.executed || 0) > 0);
  const activationLeader = rows.reduce((best, row) => {
    if (!best) return row;
    return Number(row.activation_rate || 0) > Number(best.activation_rate || 0) ? row : best;
  }, null);
  const expectancyLeader = executedRows.reduce((best, row) => {
    if (!best) return row;
    return Number(row.expectancy || 0) > Number(best.expectancy || 0) ? row : best;
  }, null);
  const winRateLeader = executedRows.reduce((best, row) => {
    if (!best) return row;
    return Number(row.win_rate || 0) > Number(best.win_rate || 0) ? row : best;
  }, null);
  const netLeader = executedRows.reduce((best, row) => {
    if (!best) return row;
    return Number(row.net || 0) > Number(best.net || 0) ? row : best;
  }, null);
  const leadersHtml = `
    <section class="leaders-grid">
      <div class="snapshot-tile">
        <span>Highest Win Rate</span>
        <strong>${winRateLeader ? titleCase(winRateLeader.setup_type) : "--"}</strong>
        <p>${winRateLeader ? `${Number(winRateLeader.win_rate || 0).toFixed(1)}% | ${Number(winRateLeader.executed || 0)} executed` : "Needs more executed samples"}</p>
      </div>
      <div class="snapshot-tile">
        <span>Best Net</span>
        <strong>${netLeader ? titleCase(netLeader.setup_type) : "--"}</strong>
        <p>${netLeader ? `${formatMoney(netLeader.net)} | ${Number(netLeader.wins || 0)}W / ${Number(netLeader.losses || 0)}L` : "Needs more executed samples"}</p>
      </div>
      <div class="snapshot-tile">
        <span>Most Activated</span>
        <strong>${activationLeader ? titleCase(activationLeader.setup_type) : "--"}</strong>
        <p>${activationLeader ? `${Number(activationLeader.activation_rate || 0).toFixed(1)}% activation` : "Waiting for setup flow"}</p>
      </div>
      <div class="snapshot-tile">
        <span>Best Expectancy</span>
        <strong>${expectancyLeader ? titleCase(expectancyLeader.setup_type) : "--"}</strong>
        <p>${expectancyLeader ? `${formatMoney(expectancyLeader.expectancy)} per trade` : "Needs more executed samples"}</p>
      </div>
    </section>
  `;
  const setupBarsHtml = `
    <section class="lens-group">
      <div class="lens-group-header">
        <h3>Setup Comparison</h3>
        <p>Full setup list with decisions, live triggers, executions, and win rate</p>
      </div>
      <div class="setup-graph-scroll">
      <div class="setup-graph">
        ${rows.map((row) => {
          const decisions = Number(row.decisions || 0);
          const live = Number(row.live || 0);
          const executed = Number(row.executed || 0);
          const activation = Math.max(0, Math.min(100, Number(row.activation_rate || 0)));
          const winRate = Math.max(0, Math.min(100, Number(row.win_rate || 0)));
          const decisionWidth = decisions > 0 ? Math.max(12, Math.min(100, decisions * 10)) : 0;
          return `
            <div class="setup-graph-row">
              <div class="setup-graph-label">
                <strong>${titleCase(row.setup_type)}</strong>
                <span>${decisions} decisions | ${formatMoney(row.net)}</span>
              </div>
              <div class="setup-graph-metrics">
                <div class="setup-metric">
                  <span>Volume</span>
                  <div class="setup-metric-track"><div class="setup-metric-fill tone-volume" style="width:${decisionWidth}%"></div></div>
                </div>
                <div class="setup-metric">
                  <span>Activation</span>
                  <div class="setup-metric-track"><div class="setup-metric-fill tone-activation" style="width:${activation}%"></div></div>
                </div>
                <div class="setup-metric">
                  <span>Win Rate</span>
                  <div class="setup-metric-track"><div class="setup-metric-fill tone-winrate" style="width:${winRate}%"></div></div>
                </div>
              </div>
              <div class="setup-graph-meta">
                <span>Live ${live}</span>
                <span>Exec ${executed}</span>
                <span>Exp ${formatMoney(row.expectancy)}</span>
              </div>
            </div>
          `;
        }).join("")}
      </div>
      </div>
    </section>
  `;
  setupPerformanceChart.innerHTML = `
    ${leadersHtml}
    ${setupBarsHtml}
  `;
  if (setupHeatmapChart && setupHeatmapCopy) {
    setupHeatmapCopy.textContent = "Executed setup outcomes by session";
    setupHeatmapChart.innerHTML = heatmapRows.length ? `
      <section class="lens-group heatmap-group">
        <div class="heatmap-legend">
          <span class="heatmap-legend-item"><i class="heatmap-swatch is-positive"></i> Strong</span>
          <span class="heatmap-legend-item"><i class="heatmap-swatch is-mixed"></i> Mixed</span>
          <span class="heatmap-legend-item"><i class="heatmap-swatch is-negative"></i> Weak</span>
          <span class="heatmap-legend-item"><i class="heatmap-swatch is-neutral"></i> No sample</span>
        </div>
        <div class="heatmap-grid heatmap-grid-header">
          <span></span>
          <span>Asia</span>
          <span>London</span>
          <span>New York</span>
          <span>Unknown</span>
          <span>Total</span>
        </div>
        <div class="heatmap-body">
          ${heatmapRows
            .slice()
            .sort((left, right) => {
              const leftBest = Math.max(...left.sessions.map((cell) => Number(cell.win_rate || 0)), 0);
              const rightBest = Math.max(...right.sessions.map((cell) => Number(cell.win_rate || 0)), 0);
              if (rightBest !== leftBest) return rightBest - leftBest;
              const leftTrades = left.sessions.reduce((sum, cell) => sum + Number(cell.trades || 0), 0);
              const rightTrades = right.sessions.reduce((sum, cell) => sum + Number(cell.trades || 0), 0);
              return rightTrades - leftTrades;
            })
            .map((row) => `
            <div class="heatmap-grid heatmap-grid-row">
              <strong class="heatmap-label">${row.setup_type}</strong>
              ${row.sessions.map((cell) => {
                const winRate = Number(cell.win_rate || 0);
                const trades = Number(cell.trades || 0);
                const tone = trades === 0 ? "neutral" : winRate >= 60 ? "positive" : winRate >= 45 ? "mixed" : "negative";
                return `
                  <div class="heatmap-cell is-${tone}">
                    <strong>${trades || "--"}</strong>
                    <span>${trades ? `${winRate.toFixed(0)}%` : "no data"}</span>
                    <small>${trades ? formatMoney(cell.net) : "--"}</small>
                  </div>
                `;
              }).join("")}
              <div class="heatmap-cell is-summary">
                <strong>${row.sessions.reduce((sum, cell) => sum + Number(cell.trades || 0), 0)}</strong>
                <span>${Math.max(...row.sessions.map((cell) => Number(cell.win_rate || 0)), 0).toFixed(0)}% best</span>
                <small>${formatMoney(row.sessions.reduce((sum, cell) => sum + Number(cell.net || 0), 0))}</small>
              </div>
            </div>
          `).join("")}
        </div>
      </section>
    ` : `<div class="curve-empty compact-empty">No executed strategy setup data yet. The heatmap will fill as trades accumulate.</div>`;
  }
}

function renderStrategyLens(aiAnalytics, sessionStats) {
  if (!snapshotGrid) return;
  const setupRows = Array.isArray(aiAnalytics?.setup_types) ? aiAnalytics.setup_types : [];
  const gateStats = aiAnalytics?.entry_gate_stats && typeof aiAnalytics.entry_gate_stats === "object" ? aiAnalytics.entry_gate_stats : {};
  const missedStats = aiAnalytics?.missed_trade_stats && typeof aiAnalytics.missed_trade_stats === "object" ? aiAnalytics.missed_trade_stats : {};
  if (!setupRows.length && !Number(gateStats.samples || 0) && !Number(missedStats.total || 0)) {
    snapshotCopy.textContent = "No entry-readiness analytics available yet.";
    snapshotGrid.innerHTML = `<div class="curve-empty">No entry-readiness lens available yet.</div>`;
    return;
  }

  const samples = Number(gateStats.samples || 0);
  const zoneOkCount = Number(gateStats.zone_ok || 0);
  const confirmationOkCount = Number(gateStats.confirmation_ok || 0);
  const fullyReady = Number(gateStats.fully_ready || 0);
  const zoneRate = samples ? (zoneOkCount / samples) * 100 : 0;
  const confirmationRate = samples ? (confirmationOkCount / samples) * 100 : 0;
  const readyRate = samples ? (fullyReady / samples) * 100 : 0;
  const missedTotal = Number(missedStats.total || 0);
  const zoneMissed = Number(missedStats.zone_missed || 0);
  const confirmationMissed = Number(missedStats.confirmation_missed || 0);
  const blockedInZone = Number(missedStats.blocked_in_zone || 0);
  const nearMiss = Number(missedStats.near_miss || 0);

  let focus = "Waiting for more read samples before calling the main execution bottleneck.";
  if (missedTotal > 0) {
    if (zoneMissed > confirmationMissed) {
      focus = "Price is leaving planned zones more often than timing is failing. Zone discipline matters most right now.";
    } else if (confirmationMissed > 0) {
      focus = "The bigger bottleneck is lower-timeframe confirmation. The engine sees locations more often than clean turn candles.";
    } else if (blockedInZone > 0) {
      focus = "A meaningful share of misses happen inside zone. Confirmation and invalidation quality are the current bottlenecks.";
    } else if (nearMiss > 0) {
      focus = "The engine is getting close often. Near-miss setups are worth reviewing before adding more patterns.";
    }
  } else if (samples > 0) {
    focus = "The engine is collecting enough gate data to judge readiness quality even without many missed trades.";
  }

  snapshotCopy.textContent = `${samples || 0} reads with entry-gate data | ${missedTotal} missed setups`;
  snapshotGrid.innerHTML = `
    <div class="snapshot-tile">
      <span>Zone Fit</span>
      <strong>${samples ? `${zoneRate.toFixed(0)}%` : "--"}</strong>
      <p>${samples ? `${zoneOkCount}/${samples} reads stayed in zone` : "Waiting for entry-gate samples"}</p>
    </div>
    <div class="snapshot-tile">
      <span>Confirmation</span>
      <strong>${samples ? `${confirmationRate.toFixed(0)}%` : "--"}</strong>
      <p>${samples ? `${confirmationOkCount}/${samples} reads had M5/M1 confirmation` : "Waiting for turn-candle samples"}</p>
    </div>
    <div class="snapshot-tile">
      <span>Ready Rate</span>
      <strong>${samples ? `${readyRate.toFixed(0)}%` : "--"}</strong>
      <p>${samples ? `${fullyReady}/${samples} reads passed both gates` : "Waiting for fully-ready samples"}</p>
    </div>
    <div class="snapshot-tile">
      <span>Near Misses</span>
      <strong>${nearMiss}</strong>
      <p>${missedTotal ? `${blockedInZone} blocked in zone` : "No near-miss pressure logged"}</p>
    </div>
    <div class="snapshot-tile">
      <span>Zone Misses</span>
      <strong>${zoneMissed}</strong>
      <p>${missedTotal ? `${missedTotal ? ((zoneMissed / Math.max(missedTotal, 1)) * 100).toFixed(0) : 0}% of missed setups` : "No zone-pressure misses logged"}</p>
    </div>
    <div class="snapshot-tile">
      <span>Confirmation Misses</span>
      <strong>${confirmationMissed}</strong>
      <p>${missedTotal ? `${missedTotal ? ((confirmationMissed / Math.max(missedTotal, 1)) * 100).toFixed(0) : 0}% of missed setups` : "No timing-pressure misses logged"}</p>
    </div>
    <div class="snapshot-tile span-all">
      <span>Current Focus</span>
      <strong>${zoneMissed > confirmationMissed ? "Zone discipline is the main bottleneck" : confirmationMissed > 0 ? "Timing confirmation is the main bottleneck" : "Entry quality is stable for this range"}</strong>
      <p>${focus}</p>
      <div class="type-stat-meta classifier-inline-meta">
        <span>${samples ? `${setupRows.length} setup types feeding the readiness gate` : "Waiting for enough gate samples"}</span>
        <span>${missedTotal ? `${blockedInZone} blocked in zone | ${nearMiss} near misses` : "No missed-trade pressure in this range"}</span>
      </div>
    </div>
  `;
}

function renderSessionPerformance(sessionStats) {
  if (!sessionChart || !sessionCopy) return;
  const rows = (Array.isArray(sessionStats) ? sessionStats : []).slice().sort((left, right) => {
    const leftWin = Number(left?.win_rate || 0);
    const rightWin = Number(right?.win_rate || 0);
    if (rightWin !== leftWin) return rightWin - leftWin;
    return Number(right?.trades || 0) - Number(left?.trades || 0);
  });
  if (!rows.length) {
    sessionChart.innerHTML = `<div class="bars-empty">No entry session performance available.</div>`;
    sessionCopy.textContent = "No session breakdown available";
    return;
  }
  const maxTrades = Math.max(...rows.map((item) => Number(item.trades || 0)), 1);
  sessionCopy.textContent = "Asia, London, and New York ranked by win rate";
  sessionChart.innerHTML = rows
    .map((item) => `
      <div class="type-stat-row">
        <div class="type-stat-head">
          <strong>${item.session}</strong>
          <span>${formatMoney(item.net)}</span>
        </div>
        <div class="type-stat-bar">
          <div class="type-stat-bar-fill" style="width:${(Number(item.trades || 0) / maxTrades) * 100}%"></div>
        </div>
        <div class="type-stat-meta">
          <span>${Number(item.trades || 0)} trades | W ${Number(item.wins || 0)} / L ${Number(item.losses || 0)}</span>
          <span>${Number(item.win_rate || 0).toFixed(1)}% | Exp ${formatMoney(item.expectancy)}</span>
        </div>
      </div>
    `)
    .join("");
}

function renderAiFriction(aiAnalytics) {
  const blockedRows = Array.isArray(aiAnalytics?.blocked_reasons) ? aiAnalytics.blocked_reasons : [];
  const familyRows = Array.isArray(aiAnalytics?.family_stats) ? aiAnalytics.family_stats : [];
  const gateStats = aiAnalytics?.entry_gate_stats && typeof aiAnalytics.entry_gate_stats === "object" ? aiAnalytics.entry_gate_stats : {};
  const gateSamples = Number(gateStats.samples || 0);
  if (aiFrictionCopy) {
    aiFrictionCopy.textContent = blockedRows.length || familyRows.length || gateSamples
      ? "Entry quality, no-trade friction, and family-level behavior"
      : "No execution-lens data available yet.";
  }

  if (aiBlockedChart) {
    if (!blockedRows.length && !gateSamples) {
      aiBlockedChart.innerHTML = `<div class="bars-empty">No entry-quality data logged yet.</div>`;
    } else {
      const maxCount = Math.max(...blockedRows.map((item) => Number(item.count || 0)), 1);
      const summaryTiles = gateSamples ? `
        <div class="snapshot-grid compact-snapshot-grid">
          <div class="snapshot-tile">
            <span>In Zone</span>
            <strong>${Number(gateStats.zone_ok || 0)}</strong>
            <p>${gateSamples ? `${((Number(gateStats.zone_ok || 0) / gateSamples) * 100).toFixed(0)}% of logged reads` : "--"}</p>
          </div>
          <div class="snapshot-tile">
            <span>Confirmed</span>
            <strong>${Number(gateStats.confirmation_ok || 0)}</strong>
            <p>${gateSamples ? `${((Number(gateStats.confirmation_ok || 0) / gateSamples) * 100).toFixed(0)}% with M5/M1 turn` : "--"}</p>
          </div>
          <div class="snapshot-tile">
            <span>Ready</span>
            <strong>${Number(gateStats.fully_ready || 0)}</strong>
            <p>${gateSamples ? `${((Number(gateStats.fully_ready || 0) / gateSamples) * 100).toFixed(0)}% passed both gates` : "--"}</p>
          </div>
        </div>
      ` : "";
      const blockerRows = blockedRows.length ? `
        <section class="lens-group">
          <div class="lens-group-header">
            <h3>Top No-Trade Reasons</h3>
            <p>What most often keeps the engine out of the market</p>
          </div>
          ${blockedRows.slice(0, 6).map((item) => `
            <div class="bar-row ai-bar-row">
              <span>${item.reason}</span>
              <div class="bar-track">
                <div class="bar-fill" style="width:${(Number(item.count || 0) / maxCount) * 100}%"></div>
              </div>
              <span>${Number(item.count || 0)}</span>
            </div>
          `).join("")}
        </section>
      ` : `<div class="curve-empty compact-empty">No blocker data yet.</div>`;
      aiBlockedChart.innerHTML = `${summaryTiles}${blockerRows}`;
    }
  }

  if (aiSetupChart) {
    if (!familyRows.length) {
      aiSetupChart.innerHTML = `<div class="curve-empty">No setup-family analytics available yet.</div>`;
    } else {
      aiSetupChart.innerHTML = `
        <section class="lens-group">
          <div class="lens-group-header">
            <h3>Setup Families</h3>
            <p>Continuation, breakout, failure, and range behavior</p>
          </div>
          ${familyRows.map((row) => `
            <div class="type-stat-row">
              <div class="type-stat-head">
                <strong>${titleCase(row.family)}</strong>
                <span>${Number(row.decisions || 0)} decisions</span>
              </div>
              <div class="type-stat-bar">
                <div class="type-stat-bar-fill" style="width:${Math.max(0, Math.min(100, Number(row.win_rate || 0)))}%"></div>
              </div>
              <div class="type-stat-meta">
                <span>Live ${Number(row.live || 0)} | Executed ${Number(row.executed || 0)} | Net ${formatMoney(row.net)}</span>
                <span>Act ${Number(row.activation_rate || 0).toFixed(1)}% | Win ${Number(row.win_rate || 0).toFixed(1)}%</span>
              </div>
            </div>
          `).join("")}
        </section>
      `;
    }
  }
}

function renderFilterState() {
  for (const button of historyFilters) {
    button.classList.toggle("is-active", button.dataset.period === activePeriod);
  }
  const customActive = activePeriod === "custom";
  historyDateFrom.disabled = !customActive;
  historyDateTo.disabled = !customActive;
  historyApply.disabled = !customActive;
}

async function loadHistoryDashboard() {
  dashboardStatus.textContent = "Syncing";
  try {
    const params = new URLSearchParams();
    params.set("period", activePeriod);
    if (activePeriod === "custom") {
      if (historyDateFrom.value) params.set("date_from", historyDateFrom.value);
      if (historyDateTo.value) params.set("date_to", historyDateTo.value);
    }
    const response = await fetch(`/api/history/dashboard?${params.toString()}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Failed to load MT5 history.");

    dashboardStatus.textContent = "Live";
    dashboardTimezone.textContent = payload.timezone || "--";

    const selected = payload.selected || {};
    const allTime = payload.all_time || {};
    const deals = Array.isArray(payload.deals) ? payload.deals : [];
    const equityCurve = Array.isArray(payload.equity_curve) ? payload.equity_curve : [];
    const sessionStats = Array.isArray(payload.session_stats) ? payload.session_stats : [];
    const setupSessionStats = Array.isArray(payload.setup_session_stats) ? payload.setup_session_stats : [];
    const aiAnalytics = payload.ai_analytics || {};
    const periodLabel = humanizePeriod(payload.period || activePeriod);

    selectedPeriodLabel.textContent = `${periodLabel} Net`;
    selectedCountLabel.textContent = `${periodLabel} Trades`;
    todayNetValue.textContent = formatMoney(selected.net);
    if (payload.period === "custom" && payload.date_from && payload.date_to) {
      todayDateCopy.textContent = `${payload.date_from} to ${payload.date_to} realized net`;
    } else if (payload.date_from) {
      todayDateCopy.textContent = `${payload.date_from} realized net`;
    } else {
      todayDateCopy.textContent = `${periodLabel} realized net`;
    }
    todayTradesValue.textContent = String(selected.trade_count ?? "--");
    todayBreakdownCopy.textContent = `Wins ${selected.wins ?? 0} | Losses ${selected.losses ?? 0} | Gross ${formatMoney(selected.gross_profit)} / ${formatMoney(selected.gross_loss)}`;
    allNetValue.textContent = formatMoney(allTime.net);
    allBreakdownCopy.textContent = `Trades ${allTime.trade_count ?? 0} | Wins ${allTime.wins ?? 0} | Losses ${allTime.losses ?? 0}`;

    const missedStats = aiAnalytics?.missed_trade_stats && typeof aiAnalytics.missed_trade_stats === "object" ? aiAnalytics.missed_trade_stats : {};
    const missedTotal = Number(missedStats.total || 0);
    if (missedTradesValue && missedTradesCopy) {
      missedTradesValue.textContent = String(missedTotal);
      missedTradesValue.className = missedTotal > 0 ? "value-negative" : "";
      missedTradesCopy.textContent = missedTotal
        ? `Near miss ${Number(missedStats.near_miss || 0)} | In-zone blocked ${Number(missedStats.blocked_in_zone || 0)} | Zone misses ${Number(missedStats.zone_missed || 0)}`
        : "No missed-trade pressure in this range.";
    } else {
      if (missedTradesValue) missedTradesValue.textContent = "--";
      if (missedTradesCopy) missedTradesCopy.textContent = "Missed-trade analytics unavailable.";
    }

    setCardTone(todayNetCard, selected.net);
    setCardTone(allNetCard, allTime.net);
    setCardTone(todayCountCard, Number(selected.trade_count || 0));
    renderStrategyLens(aiAnalytics, sessionStats);
    renderSessionPerformance(sessionStats);
    renderEquityCurve(equityCurve);
    renderSetupPerformance(aiAnalytics, setupSessionStats);
    renderAiFriction(aiAnalytics);

    historyCountCopy.textContent = `${deals.length} closed deals shown | ${payload.all_deals_count ?? deals.length} total loaded`;
    historyTableBody.innerHTML = "";
    if (!deals.length) {
      historyTableBody.innerHTML = `<tr><td colspan="13">No closed MT5 deals found for this range.</td></tr>`;
      return;
    }

    for (const deal of deals) {
      historyTableBody.appendChild(buildHistoryRow(deal));
    }
  } catch (error) {
    dashboardStatus.textContent = "History failed";
    historyCountCopy.textContent = error instanceof Error ? error.message : "Failed to load MT5 history.";
    historyTableBody.innerHTML = `<tr><td colspan="13">${historyCountCopy.textContent}</td></tr>`;
    if (aiFrictionCopy) aiFrictionCopy.textContent = "Strategy friction analytics unavailable.";
    if (aiBlockedChart) aiBlockedChart.innerHTML = `<div class="bars-empty">Strategy friction analytics unavailable.</div>`;
    if (aiSetupChart) aiSetupChart.innerHTML = `<div class="curve-empty">Strategy setup analytics unavailable.</div>`;
    if (sessionCopy) sessionCopy.textContent = "Session performance unavailable.";
    if (sessionChart) sessionChart.innerHTML = `<div class="bars-empty">Session performance unavailable.</div>`;
    if (setupPerformanceCopy) setupPerformanceCopy.textContent = "Strategy setup performance unavailable.";
    if (setupPerformanceChart) setupPerformanceChart.innerHTML = `<div class="curve-empty">Strategy setup performance unavailable.</div>`;
    if (missedTradesValue) missedTradesValue.textContent = "--";
    if (missedTradesCopy) missedTradesCopy.textContent = "Missed-trade analytics unavailable.";
  }
}

for (const button of historyFilters) {
  button.addEventListener("click", async () => {
    activePeriod = button.dataset.period || "daily";
    renderFilterState();
    if (activePeriod !== "custom") {
      await loadHistoryDashboard();
    }
  });
}

historyApply?.addEventListener("click", loadHistoryDashboard);

renderFilterState();
formatClock();
window.setInterval(formatClock, 1000);
loadHistoryDashboard();
window.setInterval(loadHistoryDashboard, 15000);
