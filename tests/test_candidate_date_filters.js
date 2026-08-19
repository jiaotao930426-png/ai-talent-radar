const test = require("node:test");
const assert = require("node:assert/strict");

const filters = require("../static/candidate_date_filters.js");

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

test("北京时间边界下的今日、本周和最近七天范围", () => {
  const now = new Date("2026-08-16T16:30:00Z");

  assert.deepEqual(filters.candidateDateRangeForPreset("today", now), {
    date_range: "today",
    last_seen_from: "2026-08-17",
    last_seen_to: "2026-08-17",
  });
  assert.deepEqual(filters.candidateDateRangeForPreset("this_week", now), {
    date_range: "this_week",
    last_seen_from: "2026-08-17",
    last_seen_to: "2026-08-17",
  });
  assert.deepEqual(filters.candidateDateRangeForPreset("last_7_days", now), {
    date_range: "last_7_days",
    last_seen_from: "2026-08-11",
    last_seen_to: "2026-08-17",
  });
});

test("跨月跨年的北京时间范围", () => {
  const now = new Date("2025-12-31T16:30:00Z");

  assert.deepEqual(filters.candidateDateRangeForPreset("last_7_days", now), {
    date_range: "last_7_days",
    last_seen_from: "2025-12-26",
    last_seen_to: "2026-01-01",
  });
  assert.deepEqual(filters.candidateDateRangeForPreset("this_month", now), {
    date_range: "this_month",
    last_seen_from: "2026-01-01",
    last_seen_to: "2026-01-01",
  });
});

test("完整预设范围包含全部、最近三十天和未知预设校验", () => {
  const now = new Date("2026-08-16T16:30:00Z");

  assert.deepEqual(filters.candidateDateRangeForPreset("all", now), {
    date_range: "all",
    last_seen_from: "",
    last_seen_to: "",
  });
  assert.deepEqual(filters.candidateDateRangeForPreset("last_30_days", now), {
    date_range: "last_30_days",
    last_seen_from: "2026-07-19",
    last_seen_to: "2026-08-17",
  });
  assert.throws(
    () => filters.candidateDateRangeForPreset("tomorrow", now),
    /未知的日期范围/,
  );
});

test("本周从北京时间周一开始", () => {
  const now = new Date("2026-08-18T16:30:00Z");

  assert.deepEqual(filters.candidateDateRangeForPreset("this_week", now), {
    date_range: "this_week",
    last_seen_from: "2026-08-17",
    last_seen_to: "2026-08-19",
  });
});

test("自定义范围会规范空边界并校验开始日期", () => {
  assert.equal(
    filters.candidateDateValidationMessage({
      date_range: "custom",
      last_seen_from: "2026-08-19",
      last_seen_to: "2026-08-18",
    }),
    "开始日期不能晚于结束日期",
  );
  assert.deepEqual(filters.normalizeCandidateDateRange({ date_range: "custom" }), {
    date_range: "all",
    last_seen_from: "",
    last_seen_to: "",
  });
});

test("自定义范围保留单边日期", () => {
  assert.deepEqual(filters.normalizeCandidateDateRange({
    date_range: "custom",
    last_seen_from: "2026-08-18",
  }), {
    date_range: "custom",
    last_seen_from: "2026-08-18",
    last_seen_to: "",
  });
  assert.deepEqual(filters.normalizeCandidateDateRange({
    date_range: "custom",
    last_seen_to: "2026-08-18",
  }), {
    date_range: "custom",
    last_seen_from: "",
    last_seen_to: "2026-08-18",
  });
});

test("自定义范围的非空边界强制使用 custom", () => {
  assert.deepEqual(filters.normalizeCandidateDateRange({
    date_range: "all",
    last_seen_from: "2026-08-18",
  }), {
    date_range: "custom",
    last_seen_from: "2026-08-18",
    last_seen_to: "",
  });
  assert.deepEqual(filters.normalizeCandidateDateRange({
    date_range: "all",
    last_seen_to: "2026-08-18",
  }), {
    date_range: "custom",
    last_seen_from: "",
    last_seen_to: "2026-08-18",
  });
});

test("合法快捷范围经规范化后保留预设", () => {
  const now = new Date("2026-08-18T16:30:00Z");
  for (const preset of ["today", "this_week", "last_7_days", "last_30_days", "this_month"]) {
    const range = filters.candidateDateRangeForPreset(preset, now);
    assert.equal(filters.normalizeCandidateDateRange(range).date_range, preset);
  }
  assert.equal(filters.normalizeCandidateDateRange({
    date_range: "custom",
    last_seen_from: "2026-08-18",
  }).date_range, "custom");
  assert.equal(filters.normalizeCandidateDateRange({
    date_range: "all",
    last_seen_from: "2026-08-18",
  }).date_range, "custom");
  assert.equal(filters.normalizeCandidateDateRange({
    date_range: "unexpected",
    last_seen_to: "2026-08-18",
  }).date_range, "custom");
});

test("日期倒序校验仅在两个边界都存在时触发", () => {
  assert.equal(filters.candidateDateValidationMessage({
    last_seen_from: "2026-08-18",
  }), "");
  assert.equal(filters.candidateDateValidationMessage({
    last_seen_to: "2026-08-18",
  }), "");
  assert.equal(filters.candidateDateValidationMessage({
    last_seen_from: "2026-08-19",
    last_seen_to: "2026-08-18",
  }), "开始日期不能晚于结束日期");
});

test("日期输入遵循服务端格式、日历和范围约束", () => {
  assert.equal(filters.candidateDateValidationMessage({
    last_seen_from: "2026-02-30",
  }), "开始日期格式无效，请使用 YYYY-MM-DD");
  assert.equal(filters.candidateDateValidationMessage({
    last_seen_to: "2026-8-18",
  }), "结束日期格式无效，请使用 YYYY-MM-DD");
  assert.equal(filters.candidateDateValidationMessage({
    last_seen_from: "\uFF12\uFF10\uFF12\uFF16-\uFF10\uFF18-\uFF11\uFF18",
  }), "开始日期格式无效，请使用 YYYY-MM-DD");
  assert.equal(filters.candidateDateValidationMessage({
    last_seen_from: "1999-12-31",
  }), "日期必须在 2000-01-01 至 2100-12-31 之间");
  assert.equal(filters.candidateDateValidationMessage({
    last_seen_to: "2101-01-01",
  }), "日期必须在 2000-01-01 至 2100-12-31 之间");
  assert.equal(filters.candidateDateValidationMessage({
    last_seen_from: "2026-08-18",
  }), "");
  assert.equal(filters.candidateDateValidationMessage({
    last_seen_from: "2026-08-19",
    last_seen_to: "2026-08-18",
  }), "开始日期不能晚于结束日期");
});

test("候选人导出仅使用已应用的日期边界", () => {
  assert.equal(
    filters.candidateExportUrl({
      last_seen_from: "2026-08-12",
      last_seen_to: "2026-08-18",
    }),
    "/export/candidates.xlsx?last_seen_from=2026-08-12&last_seen_to=2026-08-18",
  );
  assert.equal(
    filters.candidateExportUrl({ last_seen_from: "2026-08-12" }),
    "/export/candidates.xlsx?last_seen_from=2026-08-12",
  );
  assert.equal(
    filters.candidateExportUrl({ last_seen_to: "2026-08-18" }),
    "/export/candidates.xlsx?last_seen_to=2026-08-18",
  );
});

test("候选人分页只会在请求成功后提交状态", async () => {
  let committed = 0;
  const reported = [];
  const fetchFailure = async () => { throw new Error("network failed"); };

  const failed = await filters.commitCandidatePageAfterFetch(
    fetchFailure,
    () => { committed += 1; },
    (error) => { reported.push(error.message); },
  );
  assert.equal(failed, false);
  assert.equal(committed, 0);
  assert.deepEqual(reported, ["network failed"]);

  const result = await filters.commitCandidatePageAfterFetch(
    async () => ({ items: ["candidate"] }),
    (data) => { committed += 1; assert.deepEqual(data, { items: ["candidate"] }); },
    () => { throw new Error("should not report a successful fetch"); },
  );
  assert.equal(result, true);
  assert.equal(committed, 1);
});

test("候选人分页只捕获请求失败", async () => {
  const commitError = new Error("commit failed");
  await assert.rejects(
    filters.commitCandidatePageAfterFetch(
      async () => ({ items: [] }),
      () => { throw commitError; },
      () => { throw new Error("should not report commit failures"); },
    ),
    commitError,
  );

  const reportError = new Error("report failed");
  await assert.rejects(
    filters.commitCandidatePageAfterFetch(
      async () => { throw new Error("fetch failed"); },
      () => { throw new Error("should not commit failed fetches"); },
      () => { throw reportError; },
    ),
    reportError,
  );
});

test("过期候选人请求晚返回时不会覆盖最新提交", async () => {
  const olderPage = deferred();
  const committed = [];
  const reported = [];
  let currentRequestId = 1;

  const olderRequest = filters.commitCandidatePageAfterFetch(
    () => olderPage.promise,
    (data) => committed.push(data),
    (error) => reported.push(error.message),
    () => currentRequestId === 1,
  );
  currentRequestId = 2;
  const latestRequest = filters.commitCandidatePageAfterFetch(
    async () => "latest page",
    (data) => committed.push(data),
    (error) => reported.push(error.message),
    () => currentRequestId === 2,
  );

  assert.equal(await latestRequest, true);
  olderPage.resolve("stale page");
  assert.equal(await olderRequest, false);
  assert.deepEqual(committed, ["latest page"]);
  assert.deepEqual(reported, []);
});

test("过期请求失败不报错而最新请求仍正常报错", async () => {
  const olderPage = deferred();
  const committed = [];
  const reported = [];
  let currentRequestId = 1;

  const olderRequest = filters.commitCandidatePageAfterFetch(
    () => olderPage.promise,
    (data) => committed.push(data),
    (error) => reported.push(error.message),
    () => currentRequestId === 1,
  );
  currentRequestId = 2;
  const latestRequest = filters.commitCandidatePageAfterFetch(
    async () => { throw new Error("latest failed"); },
    (data) => committed.push(data),
    (error) => reported.push(error.message),
    () => currentRequestId === 2,
  );

  assert.equal(await latestRequest, false);
  olderPage.reject(new Error("stale failed"));
  assert.equal(await olderRequest, false);
  assert.deepEqual(committed, []);
  assert.deepEqual(reported, ["latest failed"]);
});

test("候选人时间以北京时间格式化", () => {
  const formatted = filters.formatCandidateTime("2026-08-17T16:30:00Z");

  assert.match(formatted, /2026\/8\/18/);
  assert.match(formatted, /00:30:00/);
  assert.equal(filters.formatCandidateTime(""), "—");
  assert.equal(filters.formatCandidateTime("not-a-date"), "not-a-date");
});

test("无时区时间作为北京时间墙上时间解析", () => {
  const previousTimeZone = process.env.TZ;
  let utc;
  let losAngeles;
  try {
    process.env.TZ = "UTC";
    utc = filters.formatCandidateTime("2026-08-18T00:30:00");
    process.env.TZ = "America/Los_Angeles";
    losAngeles = filters.formatCandidateTime("2026-08-18T00:30:00");
  } finally {
    if (previousTimeZone === undefined) delete process.env.TZ;
    else process.env.TZ = previousTimeZone;
  }

  assert.equal(utc, losAngeles);
  assert.match(utc, /2026\/8\/18/);
  assert.match(utc, /00:30:00/);
  assert.equal(filters.beijingDateString("2026-08-18T00:30:00"), "2026-08-18");
});

test("无秒本地时间同样作为北京时间墙上时间解析", () => {
  const previousTimeZone = process.env.TZ;
  let utc;
  let losAngeles;
  try {
    process.env.TZ = "UTC";
    utc = filters.formatCandidateTime("2026-08-18 00:30");
    process.env.TZ = "America/Los_Angeles";
    losAngeles = filters.formatCandidateTime("2026-08-18 00:30");
  } finally {
    if (previousTimeZone === undefined) delete process.env.TZ;
    else process.env.TZ = previousTimeZone;
  }

  assert.equal(utc, losAngeles);
  assert.match(utc, /2026\/8\/18/);
  assert.match(utc, /00:30:00/);
  assert.equal(filters.beijingDateString("2026-08-18T00:30"), "2026-08-18");
});

test("无参北京时间日期使用当前时间", () => {
  assert.match(filters.beijingDateString(), /^\d{4}-\d{2}-\d{2}$/);
});
