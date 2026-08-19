(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.CandidateDateFilters = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const BEIJING_TIME_ZONE = "Asia/Shanghai";

  function parseCandidateDateTime(value) {
    if (value instanceof Date || typeof value !== "string") return new Date(value);
    const match = value.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?$/);
    if (!match) return new Date(value);

    const [, year, month, day, hour, minute, second = "00", fraction = ""] = match;
    const milliseconds = Number(fraction.slice(0, 3).padEnd(3, "0"));
    const wallTime = new Date(Date.UTC(
      Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute), Number(second), milliseconds,
    ));
    if (wallTime.getUTCFullYear() !== Number(year)
      || wallTime.getUTCMonth() !== Number(month) - 1
      || wallTime.getUTCDate() !== Number(day)
      || wallTime.getUTCHours() !== Number(hour)
      || wallTime.getUTCMinutes() !== Number(minute)
      || wallTime.getUTCSeconds() !== Number(second)) return new Date(NaN);
    return new Date(wallTime.getTime() - 8 * 60 * 60 * 1000);
  }

  function beijingDateString(value) {
    const date = value === undefined ? new Date() : parseCandidateDateTime(value);
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: BEIJING_TIME_ZONE,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(date);
    const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    return `${values.year}-${values.month}-${values.day}`;
  }

  function addDaysToDate(dateString, days) {
    const date = new Date(`${dateString}T00:00:00Z`);
    date.setUTCDate(date.getUTCDate() + days);
    return date.toISOString().slice(0, 10);
  }

  function candidateDateRangeForPreset(preset, now) {
    const today = beijingDateString(now || new Date());
    let from = today;

    if (preset === "all") from = "";
    else if (preset === "this_week") {
      const weekday = new Date(`${today}T00:00:00Z`).getUTCDay();
      from = addDaysToDate(today, weekday === 0 ? -6 : 1 - weekday);
    } else if (preset === "last_7_days") from = addDaysToDate(today, -6);
    else if (preset === "last_30_days") from = addDaysToDate(today, -29);
    else if (preset === "this_month") from = `${today.slice(0, 8)}01`;
    else if (preset !== "today") throw new Error("未知的日期范围");

    return {
      date_range: preset,
      last_seen_from: from,
      last_seen_to: preset === "all" ? "" : today,
    };
  }

  function normalizeCandidateDateRange(range) {
    const candidate = range || {};
    const from = candidate.last_seen_from || "";
    const to = candidate.last_seen_to || "";
    if (!from && !to) {
      return { date_range: "all", last_seen_from: "", last_seen_to: "" };
    }
    const dateRange = ["today", "this_week", "last_7_days", "last_30_days", "this_month"]
      .includes(candidate.date_range) ? candidate.date_range : "custom";
    return {
      date_range: dateRange,
      last_seen_from: from,
      last_seen_to: to,
    };
  }

  function candidateDateValidationMessage(range) {
    const normalized = normalizeCandidateDateRange(range);
    const fromMessage = candidateDateFieldValidationMessage(normalized.last_seen_from, "开始日期");
    if (fromMessage) return fromMessage;
    const toMessage = candidateDateFieldValidationMessage(normalized.last_seen_to, "结束日期");
    if (toMessage) return toMessage;
    if (normalized.last_seen_from && normalized.last_seen_to
      && normalized.last_seen_from > normalized.last_seen_to) {
      return "开始日期不能晚于结束日期";
    }
    return "";
  }

  function candidateDateFieldValidationMessage(value, label) {
    if (!value) return "";
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return `${label}格式无效，请使用 YYYY-MM-DD`;
    const date = new Date(`${value}T00:00:00Z`);
    if (Number.isNaN(date.getTime()) || date.toISOString().slice(0, 10) !== value) {
      return `${label}格式无效，请使用 YYYY-MM-DD`;
    }
    if (value < "2000-01-01" || value > "2100-12-31") {
      return "日期必须在 2000-01-01 至 2100-12-31 之间";
    }
    return "";
  }

  function candidateExportUrl(appliedRange) {
    const range = appliedRange || {};
    const params = new URLSearchParams();
    if (range.last_seen_from) params.set("last_seen_from", range.last_seen_from);
    if (range.last_seen_to) params.set("last_seen_to", range.last_seen_to);
    const query = params.toString();
    return query ? `/export/candidates.xlsx?${query}` : "/export/candidates.xlsx";
  }

  async function commitCandidatePageAfterFetch(fetchPage, commit, reportError, isCurrent = () => true) {
    let data;
    try {
      data = await fetchPage();
    } catch (error) {
      if (!isCurrent()) return false;
      if (typeof reportError === "function") reportError(error);
      return false;
    }
    if (!isCurrent()) return false;
    if (typeof commit === "function") commit(data);
    return true;
  }

  function formatCandidateTime(value) {
    if (!value) return "—";
    const date = parseCandidateDateTime(value);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: BEIJING_TIME_ZONE,
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    }).format(date);
  }

  return {
    BEIJING_TIME_ZONE,
    beijingDateString,
    addDaysToDate,
    candidateDateRangeForPreset,
    normalizeCandidateDateRange,
    candidateDateValidationMessage,
    candidateExportUrl,
    commitCandidatePageAfterFetch,
    formatCandidateTime,
  };
});
