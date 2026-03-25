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
const latestResultValue = document.getElementById("latest-result-value");
const latestResultCopy = document.getElementById("latest-result-copy");
const historyCountCopy = document.getElementById("history-count-copy");
const historyTableBody = document.getElementById("history-table-body");
const equityCurveCopy = document.getElementById("equity-curve-copy");
const equityCurveChart = document.getElementById("equity-curve-chart");
const typeStatsCopy = document.getElementById("type-stats-copy");
const typeStatsChart = document.getElementById("type-stats-chart");
const snapshotCopy = document.getElementById("snapshot-copy");
const snapshotGrid = document.getElementById("snapshot-grid");
const hourlyCopy = document.getElementById("hourly-copy");
const hourlyChart = document.getElementById("hourly-chart");
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
  row.innerHTML = `
    <td>${deal.open_time_label || "--"}</td>
    <td>${deal.symbol || "--"}</td>
    <td>${deal.ticket || "--"}</td>
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
  const height = 260;
  const padding = { top: 18, right: 20, bottom: 28, left: 20 };
  const values = rows.map((point) => Number(point.cumulative || 0));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(max - min, 1);
  const usableWidth = width - padding.left - padding.right;
  const usableHeight = height - padding.top - padding.bottom;
  const stepX = rows.length > 1 ? usableWidth / (rows.length - 1) : 0;
  const toY = (value) => padding.top + ((max - value) / range) * usableHeight;
  const toX = (index) => padding.left + stepX * index;
  const linePath = rows
    .map((point, index) => `${index === 0 ? "M" : "L"} ${toX(index).toFixed(2)} ${toY(Number(point.cumulative || 0)).toFixed(2)}`)
    .join(" ");
  const areaPath = `${linePath} L ${toX(rows.length - 1).toFixed(2)} ${(height - padding.bottom).toFixed(2)} L ${toX(0).toFixed(2)} ${(height - padding.bottom).toFixed(2)} Z`;
  const last = rows[rows.length - 1];
  equityCurveCopy.textContent = `${rows.length} closed trades plotted | Latest cumulative ${formatMoney(last.cumulative)}`;
  equityCurveChart.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-label="Cumulative net curve">
      <line class="curve-axis" x1="${padding.left}" y1="${height - padding.bottom}" x2="${width - padding.right}" y2="${height - padding.bottom}"></line>
      <line class="curve-axis" x1="${padding.left}" y1="${padding.top}" x2="${padding.left}" y2="${height - padding.bottom}"></line>
      <path class="curve-area" d="${areaPath}"></path>
      <path class="curve-line" d="${linePath}"></path>
      ${rows.map((point, index) => `<circle class="curve-point" cx="${toX(index).toFixed(2)}" cy="${toY(Number(point.cumulative || 0)).toFixed(2)}" r="3"></circle>`).join("")}
      <text x="${padding.left}" y="${padding.top + 6}" fill="#9ab0d3" font-size="11">${formatMoney(max)}</text>
      <text x="${padding.left}" y="${height - padding.bottom - 6}" fill="#9ab0d3" font-size="11">${formatMoney(min)}</text>
      <text x="${width - padding.right - 110}" y="${height - 8}" fill="#9ab0d3" font-size="11">${rows[0].label || ""}</text>
      <text x="${width - padding.right - 10}" y="${height - 8}" text-anchor="end" fill="#9ab0d3" font-size="11">${last.label || ""}</text>
    </svg>
  `;
}

function renderTypeStats(typeStats) {
  if (!typeStatsChart) return;
  const groups = typeStats || {};
  const directionRows = Array.isArray(groups.direction) ? groups.direction : [];
  const sourceRows = Array.isArray(groups.source) ? groups.source : [];
  const exitRows = Array.isArray(groups.exit) ? groups.exit : [];
  if (!directionRows.length && !sourceRows.length && !exitRows.length) {
    typeStatsChart.innerHTML = `<div class="curve-empty">No breakdowns available in this range.</div>`;
    typeStatsCopy.textContent = "No actionable breakdowns";
    return;
  }
  typeStatsCopy.textContent = "Direction, exit quality, and source quality";

  const buildRows = (rows, labelKey) =>
    rows
      .map((row) => `
        <div class="type-stat-row">
          <div class="type-stat-head">
            <strong>${row[labelKey]}</strong>
            <span>${formatMoney(row.net)}</span>
          </div>
          <div class="type-stat-bar">
            <div class="type-stat-bar-fill" style="width:${Math.max(0, Math.min(100, Number(row.win_rate || 0)))}%"></div>
          </div>
          <div class="type-stat-meta">
            <span>${row.trades} | W ${row.wins} / L ${row.losses}</span>
            <span>${Number(row.win_rate || 0).toFixed(1)}%</span>
          </div>
        </div>
      `)
      .join("");

  typeStatsChart.innerHTML = `
    <section class="lens-group">
      <div class="lens-group-header">
        <h3>Direction</h3>
        <p>Long vs Short</p>
      </div>
      ${buildRows(directionRows, "direction")}
    </section>
    <section class="lens-group">
      <div class="lens-group-header">
        <h3>Exit</h3>
        <p>How trades end</p>
      </div>
      ${buildRows(exitRows, "exit")}
    </section>
    <section class="lens-group">
      <div class="lens-group-header">
        <h3>Source</h3>
        <p>Who placed them</p>
      </div>
      ${buildRows(sourceRows, "source")}
    </section>
  `;
}

function renderSnapshot(selected, deals) {
  if (!snapshotGrid) return;
  const rows = Array.isArray(deals) ? deals : [];
  const tradeCount = Number(selected.trade_count || 0);
  const wins = Number(selected.wins || 0);
  const losses = Number(selected.losses || 0);
  const winRate = tradeCount ? (wins / tradeCount) * 100 : 0;
  const avgNet = tradeCount ? Number(selected.net || 0) / tradeCount : 0;
  const best = rows.length ? Math.max(...rows.map((item) => Number(item.net || 0))) : 0;
  const worst = rows.length ? Math.min(...rows.map((item) => Number(item.net || 0))) : 0;
  snapshotCopy.textContent = `${tradeCount} trades in focus`;
  snapshotGrid.innerHTML = `
    <div class="snapshot-tile ${Number(selected.net) >= 0 ? "is-positive" : "is-negative"}">
      <span>Net</span>
      <strong>${formatMoney(selected.net)}</strong>
      <p>Selected range</p>
    </div>
    <div class="snapshot-tile ${winRate >= 50 ? "is-positive" : "is-negative"}">
      <span>Win Rate</span>
      <strong>${winRate.toFixed(1)}%</strong>
      <p>${wins}W / ${losses}L</p>
    </div>
    <div class="snapshot-tile ${avgNet >= 0 ? "is-positive" : "is-negative"}">
      <span>Avg Trade</span>
      <strong>${formatMoney(avgNet)}</strong>
      <p>Net per trade</p>
    </div>
    <div class="snapshot-tile ${best >= Math.abs(worst) ? "is-positive" : "is-negative"}">
      <span>Best / Worst</span>
      <strong>${formatMoney(best)} / ${formatMoney(worst)}</strong>
      <p>Closed outcomes</p>
    </div>
  `;
}

function renderHourlyRhythm(deals) {
  if (!hourlyChart) return;
  const rows = Array.isArray(deals) ? deals : [];
  if (!rows.length) {
    hourlyChart.innerHTML = `<div class="bars-empty">No trade rhythm available.</div>`;
    hourlyCopy.textContent = "No hourly distribution";
    return;
  }

  const buckets = new Map();
  for (const row of rows) {
    const label = String(row.close_time_label || "");
    const match = label.match(/(\d{2}):(\d{2}):(\d{2})$/);
    const hourKey = match ? match[1] : "--";
    const bucket = buckets.get(hourKey) || { hour: hourKey, trades: 0, net: 0 };
    bucket.trades += 1;
    bucket.net += Number(row.net || 0);
    buckets.set(hourKey, bucket);
  }

  const ranked = [...buckets.values()]
    .sort((left, right) => right.trades - left.trades || left.hour.localeCompare(right.hour))
    .slice(0, 8);
  const maxTrades = Math.max(...ranked.map((item) => item.trades), 1);
  hourlyCopy.textContent = `${ranked.length} busiest close hours`;
  hourlyChart.innerHTML = ranked
    .map((item) => `
      <div class="bar-row">
        <span>${item.hour}:00</span>
        <div class="bar-track">
          <div class="bar-fill" style="width:${(item.trades / maxTrades) * 100}%"></div>
        </div>
        <span>${item.trades} | ${formatMoney(item.net)}</span>
      </div>
    `)
    .join("");
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
    const typeStats = payload.breakdowns || {};
    const latest = deals[0] || null;
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

    if (latest) {
      latestResultValue.textContent = `${latest.symbol} ${String(latest.side || "").toUpperCase()} ${formatMoney(latest.net)}`;
      latestResultValue.className = Number(latest.net) >= 0 ? "value-positive" : "value-negative";
      latestResultCopy.textContent = `${latest.close_time_label || "--"} | Volume ${Number(latest.volume || 0).toFixed(2)} | ${latest.comment || "No comment"}`;
    } else {
      latestResultValue.textContent = "--";
      latestResultCopy.textContent = "No closed MT5 deals found for this range.";
    }

    setCardTone(todayNetCard, selected.net);
    setCardTone(allNetCard, allTime.net);
    setCardTone(todayCountCard, Number(selected.trade_count || 0));
    renderSnapshot(selected, deals);
    renderHourlyRhythm(deals);
    renderEquityCurve(equityCurve);
    renderTypeStats(typeStats);

    historyCountCopy.textContent = `${deals.length} closed deals shown | ${payload.all_deals_count ?? deals.length} total loaded`;
    historyTableBody.innerHTML = "";
    if (!deals.length) {
      historyTableBody.innerHTML = `<tr><td colspan="12">No closed MT5 deals found for this range.</td></tr>`;
      return;
    }

    for (const deal of deals) {
      historyTableBody.appendChild(buildHistoryRow(deal));
    }
  } catch (error) {
    dashboardStatus.textContent = "History failed";
    historyCountCopy.textContent = error instanceof Error ? error.message : "Failed to load MT5 history.";
    historyTableBody.innerHTML = `<tr><td colspan="12">${historyCountCopy.textContent}</td></tr>`;
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
