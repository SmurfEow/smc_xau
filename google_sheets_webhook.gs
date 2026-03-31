const JOURNAL_SHEET_NAME = 'performance_log';
const EXPECTED_SECRET = '';

function doPost(e) {
  try {
    const payload = JSON.parse(e.postData.contents || '{}');
    if (EXPECTED_SECRET && payload.secret !== EXPECTED_SECRET) {
      return jsonResponse({ ok: false, error: 'unauthorized' });
    }

    const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
    const journalSheet = spreadsheet.getSheetByName(JOURNAL_SHEET_NAME);

    if (!journalSheet) {
      return jsonResponse({ ok: false, error: 'missing journal sheet' });
    }

    const rows = payload.journal_rows || [];
    const updated = upsertJournalRows_(journalSheet, rows);

    return jsonResponse({
      ok: true,
      mode: String(payload.mode || 'all'),
      journal_updated: updated,
    });
  } catch (err) {
    return jsonResponse({ ok: false, error: String(err) });
  }
}

function upsertJournalRows_(sheet, rows) {
  const records = rows.map((row) => [
      row.level || '',
      row.period || '',
      Number(row.pnl || 0),
      Number(row.trades || 0),
      Number(row.wins || 0),
      Number(row.losses || 0),
      Number(row.win_pct || 0),
      row.log_time_local || '',
      row.symbol || '',
      row.magic || '',
    ]);

  if (sheet.getMaxRows() > 1) {
    sheet.getRange(2, 1, sheet.getMaxRows() - 1, 10).clearContent();
  }
  sheet.getRange('J:J').setNumberFormat('@');
  if (records.length) {
    sheet.getRange(2, 1, records.length, 10).setValues(records);
  }
  return records.length;
}

function jsonResponse(payload) {
  return ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}
