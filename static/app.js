const state = {
  view: "overview",
  manualMode: "search",
  selectedCandidate: null,
  candidatePagination: { offset: 0, limit: 50, total: 0 },
  archivedPagination: { offset: 0, limit: 50, total: 0 },
};

const viewMeta = {
  overview: ["总览", "人才采集与审核状态"],
  manual: ["手动采集", "按条件搜索或分析公开链接"],
  schedule: ["每周任务", "配置自动采集计划"],
  candidates: ["人才池", "筛选、核验和审核候选人"],
  jobs: ["任务日志", "查看采集进度与失败原因"],
  data: ["数据管理", "归档、备份和清理本地数据"],
};

const sourceNames = {
  github: "GitHub",
  gitee: "Gitee",
  gitlab: "GitLab",
  huggingface: "Hugging Face",
  stackoverflow: "Stack Overflow",
};

const sourceHealthStatusMeta = {
  available: { tone: "success", label: "可访问" },
  not_checked: { tone: "neutral", label: "待检测" },
  not_implemented: { tone: "warning", label: "规划中" },
  manual_only: { tone: "neutral", label: "人工链接" },
  network_unavailable: { tone: "error", label: "网络不可用" },
  rate_limited: { tone: "warning", label: "频率受限" },
  auth_required: { tone: "warning", label: "需要授权" },
  proxy_auth_required: { tone: "warning", label: "代理需授权" },
  access_blocked: { tone: "error", label: "访问被拒" },
  challenge: { tone: "error", label: "挑战页/验证码" },
  upstream_error: { tone: "error", label: "上游异常" },
  http_error: { tone: "error", label: "接口异常" },
};

const contactLevelMeta = {
  A: { label: "已核验邮箱", tone: "success" },
  B: { label: "公开邮箱待复核", tone: "warning" },
  C: { label: "其他公开入口待核验", tone: "neutral" },
  D: { label: "仅公开主页", tone: "neutral" },
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `请求失败 (${response.status})`);
  return payload;
}

function showToast(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("show");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("show"), 3600);
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function formatCoverage(value, count, total) {
  let percentage = value !== undefined && value !== null && value !== "" ? Number(value) : NaN;
  if (!Number.isFinite(percentage)) percentage = total > 0 ? (count / total) * 100 : 0;
  return `${Math.max(0, Math.min(100, percentage)).toFixed(0)}%`;
}

function statusClass(status) {
  if (["已完成", "优先联系", "已回复", "进入面试", "已发 Offer", "已录用"].includes(status)) return "success";
  if (["部分完成", "需要核验", "API 限流", "请求取消", "已联系"].includes(status)) return "warning";
  if (["执行失败", "网络不可用", "执行中断", "不符合", "不再推进"].includes(status)) return "error";
  return "neutral";
}

function badge(status) {
  const span = document.createElement("span");
  span.className = `badge ${statusClass(status)}`;
  span.textContent = status;
  return span;
}

function candidateContactLevel(candidate) {
  const supplied = String(candidate.contact_level || "").toUpperCase();
  if (contactLevelMeta[supplied]) return supplied;
  if (candidate.contact_email && candidate.contact_email_source_url && candidate.contact_email_verified_at) return "A";
  if (candidate.contact_email) return "B";
  const contactUrl = safeExternalUrl(candidate.contact_url);
  const profileUrl = safeExternalUrl(candidate.profile_url);
  return contactUrl && contactUrl !== profileUrl ? "C" : "D";
}

function contactLevelBadge(candidate) {
  const level = candidateContactLevel(candidate);
  const meta = contactLevelMeta[level];
  const span = document.createElement("span");
  span.className = `badge contact-level ${meta.tone} level-${level.toLowerCase()}`;
  span.textContent = `${level}级 · ${meta.label}`;
  span.title = `联系方式等级：${meta.label}`;
  return span;
}

function externalLink(label, href, className = "button secondary") {
  const safeHref = safeExternalUrl(href);
  if (!safeHref) return null;
  const link = document.createElement("a");
  link.className = className;
  link.textContent = `${label} ↗`;
  link.href = safeHref;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
}

function safeExternalUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch (_error) {
    return "";
  }
}

async function copyText(value) {
  const text = String(value || "").trim();
  if (!text) return;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const input = document.createElement("textarea");
      input.value = text;
      input.setAttribute("readonly", "");
      input.style.position = "fixed";
      input.style.opacity = "0";
      document.body.append(input);
      input.select();
      if (!document.execCommand("copy")) throw new Error("copy unavailable");
      input.remove();
    }
    showToast("邮箱已复制");
  } catch (_error) {
    showToast("复制失败，请手动选择邮箱", true);
  }
}

function updatePagination(prefix, pagination) {
  const totalPages = Math.max(1, Math.ceil(pagination.total / pagination.limit));
  const currentPage = Math.min(totalPages, Math.floor(pagination.offset / pagination.limit) + 1);
  $(`#${prefix}PageInfo`).textContent = `第 ${currentPage} / ${totalPages} 页`;
  $(`#${prefix}PrevPage`).disabled = pagination.offset <= 0;
  $(`#${prefix}NextPage`).disabled = pagination.offset + pagination.limit >= pagination.total;
}

function setView(view) {
  state.view = view;
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  $$(".view").forEach((section) => section.classList.toggle("active", section.id === `view-${view}`));
  $("#pageTitle").textContent = viewMeta[view][0];
  $("#pageSubtitle").textContent = viewMeta[view][1];
  if (view === "overview") loadOverview();
  if (view === "schedule") loadSchedule();
  if (view === "candidates") loadCandidates();
  if (view === "jobs") loadJobs();
  if (view === "data") loadDataManagement();
}

async function loadHealth() {
  try {
    const health = await api("/api/health");
    $("#serviceDot").className = "status-dot ok";
    $("#serviceStatus").textContent = health.github_token_configured ? "服务正常 · GitHub额度已配置" : "服务正常 · 公共额度";
  } catch (error) {
    $("#serviceDot").className = "status-dot error";
    $("#serviceStatus").textContent = "服务连接失败";
  }
}

async function loadOverview() {
  try {
    const data = await api("/api/overview");
    $("#metricTotal").textContent = data.total;
    $("#metricPending").textContent = data.pending;
    $("#metricPriority").textContent = data.priority;
    $("#metricContactable").textContent = data.contactable;
    const directContact = Number(data.direct_contactable ?? data.direct_contact_count ?? data.contactable ?? 0);
    const coverage = data.contact_coverage ?? data.contact_coverage_rate;
    $("#metricDirectContact").textContent = directContact;
    $("#metricContactCoverage").textContent = `覆盖率 ${formatCoverage(coverage, directContact, Number(data.total || 0))}`;

    const schedule = data.schedule;
    const scheduleState = $("#overviewScheduleState");
    scheduleState.textContent = schedule.enabled ? "已启用" : "未启用";
    scheduleState.className = `badge ${schedule.enabled ? "success" : "neutral"}`;
    $("#overviewNextRun").textContent = schedule.enabled ? formatTime(schedule.retry_at || schedule.next_run_at) : "—";
    $("#overviewTarget").textContent = `${schedule.config.target || 30} 人`;
    $("#overviewSources").textContent = (schedule.config.sources || []).map((item) => sourceNames[item] || item).join("、") || "—";
    const contactPriority = schedule.config.prefer_contactable !== false;
    $("#overviewContactPriority").textContent = contactPriority ? "已开启" : "未开启";
    $("#overviewContactPriority").className = `badge ${contactPriority ? "success" : "neutral"}`;

    renderDistribution(data.roles);
    renderJobs(data.recent_jobs, $("#overviewJobsBody"), true);
    loadSourceHealth(false);
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderSourceHealth(items) {
  const container = $("#sourceHealthList");
  container.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "暂无来源信息";
    container.append(empty);
    return;
  }
  items.forEach((item) => {
    const card = document.createElement("article");
    card.className = "source-health-item";
    const header = document.createElement("header");
    const name = document.createElement("strong");
    name.textContent = item.label;
    const statusMeta = sourceHealthStatusMeta[item.status] || { tone: "neutral", label: item.status_label || "未知" };
    const status = document.createElement("span");
    status.className = `badge ${statusMeta.tone}`;
    status.textContent = statusMeta.label;
    header.append(name, status);
    const detail = document.createElement("p");
    detail.textContent = item.detail || item.description || "—";
    const next = document.createElement("p");
    next.className = "source-health-next";
    next.textContent = item.next_step || "—";
    card.append(header, detail, next);
    container.append(card);
  });
}

async function loadSourceHealth(probe = false) {
  const button = $("#sourceHealthButton");
  if (probe) {
    button.disabled = true;
    button.textContent = "检测中…";
  }
  try {
    const data = await api(`/api/source-health${probe ? "?probe=1" : ""}`);
    renderSourceHealth(data.items || []);
    $("#sourceHealthCheckedAt").textContent = data.checked_at
      ? `最近检测 ${formatTime(data.checked_at)}`
      : "尚未检测公开接口";
  } catch (error) {
    showToast(error.message, true);
  } finally {
    if (probe) {
      button.disabled = false;
      button.textContent = "检测来源";
    }
  }
}

function renderDistribution(items) {
  const container = $("#distributionList");
  container.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "暂无候选人";
    container.append(empty);
    return;
  }
  const max = Math.max(...items.map((item) => item.count), 1);
  items.forEach((item) => {
    const row = document.createElement("div");
    row.className = "distribution-row";
    const label = document.createElement("span");
    label.textContent = item.suggested_role;
    const bar = document.createElement("div");
    bar.className = "bar";
    const fill = document.createElement("span");
    fill.style.width = `${Math.max(6, Math.round(item.count / max * 100))}%`;
    bar.append(fill);
    const count = document.createElement("strong");
    count.textContent = item.count;
    row.append(label, bar, count);
    container.append(row);
  });
}

function renderJobs(items, body, compact = false) {
  body.replaceChildren();
  if (!items.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = compact ? 5 : 8;
    cell.className = "empty-cell";
    cell.textContent = "暂无任务";
    row.append(cell);
    body.append(row);
    return;
  }
  items.forEach((job) => {
    const row = document.createElement("tr");
    if (!compact) {
      const id = document.createElement("td"); id.textContent = `#${job.id}`; row.append(id);
    }
    const kind = document.createElement("td"); kind.textContent = job.kind; row.append(kind);
    const status = document.createElement("td"); status.append(badge(job.status)); row.append(status);
    const progressCell = document.createElement("td");
    const progress = document.createElement("div"); progress.className = "progress";
    const fill = document.createElement("span"); fill.style.width = `${job.progress}%`; progress.append(fill);
    progressCell.append(progress); row.append(progressCell);
    const result = document.createElement("td"); result.textContent = `${job.result_count} 人`; row.append(result);
    if (!compact) {
      const message = document.createElement("td"); message.textContent = job.error || job.message || "—"; row.append(message);
    }
    const created = document.createElement("td"); created.textContent = formatTime(job.created_at); row.append(created);
    if (!compact) {
      const action = document.createElement("td");
      if (["等待执行", "正在采集", "正在分析"].includes(job.status)) {
        const cancel = document.createElement("button");
        cancel.className = "button danger"; cancel.type = "button"; cancel.textContent = "取消";
        cancel.addEventListener("click", () => cancelJob(job.id)); action.append(cancel);
      }
      row.append(action);
    }
    body.append(row);
  });
}

async function loadJobs() {
  try {
    const data = await api("/api/jobs?limit=60");
    renderJobs(data.items, $("#jobTableBody"));
  } catch (error) { showToast(error.message, true); }
}

async function cancelJob(jobId) {
  try {
    const result = await api(`/api/jobs/${jobId}/cancel`, { method: "POST", body: JSON.stringify({ confirm: true }) });
    showToast(result.cancel_requested ? "已请求取消任务" : "任务当前无法取消");
    loadJobs();
  } catch (error) { showToast(error.message, true); }
}

function selectedValues(selector) {
  return $$(selector).filter((input) => input.checked).map((input) => input.value);
}

async function submitManual(event) {
  event.preventDefault();
  const button = $("#manualSubmit");
  const sources = selectedValues(".manual-source");
  if (state.manualMode === "search" && !sources.length) {
    showToast("请至少选择一个数据来源", true); return;
  }
  const payload = {
    mode: state.manualMode,
    roles: [$("#manualRole").value],
    cities: [$("#manualCity").value],
    sources,
    target: Number($("#manualTarget").value),
    keywords: $("#manualKeywords").value.trim(),
    url: $("#manualUrl").value.trim(),
    prefer_contactable: $("#manualPreferContactable").checked,
  };
  button.disabled = true; button.textContent = "创建任务…";
  try {
    const result = await api("/api/jobs", { method: "POST", body: JSON.stringify(payload) });
    showToast(`任务 #${result.job_id} 已创建`);
    setView("jobs");
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; button.textContent = "立即采集"; }
}

async function loadSchedule() {
  try {
    const schedule = await api("/api/schedule");
    $("#scheduleEnabled").checked = schedule.enabled;
    $("#scheduleWeekday").value = String(schedule.weekday);
    $("#scheduleTime").value = `${String(schedule.hour).padStart(2, "0")}:${String(schedule.minute).padStart(2, "0")}`;
    $("#scheduleTarget").value = schedule.config.target || 30;
    $("#scheduleKeywords").value = schedule.config.keywords || "";
    $("#schedulePreferContactable").checked = schedule.config.prefer_contactable !== false;
    $$(".schedule-role").forEach((input) => { input.checked = (schedule.config.roles || []).includes(input.value); });
    $$(".schedule-city").forEach((input) => { input.checked = (schedule.config.cities || []).includes(input.value); });
    $$(".schedule-source").forEach((input) => { input.checked = (schedule.config.sources || []).includes(input.value); });
  $("#scheduleNextRun").textContent = schedule.enabled ? formatTime(schedule.retry_at || schedule.next_run_at) : "未启用";
  } catch (error) { showToast(error.message, true); }
}

async function saveSchedule(event) {
  event.preventDefault();
  const roles = selectedValues(".schedule-role");
  const cities = selectedValues(".schedule-city");
  const sources = selectedValues(".schedule-source");
  if (!roles.length || !cities.length || !sources.length) {
    showToast("岗位、城市和来源均需至少选择一项", true); return;
  }
  const [hour, minute] = $("#scheduleTime").value.split(":").map(Number);
  const payload = {
    enabled: $("#scheduleEnabled").checked,
    weekday: Number($("#scheduleWeekday").value),
    hour,
    minute,
    config: {
      roles,
      cities,
      sources,
      target: Number($("#scheduleTarget").value),
      keywords: $("#scheduleKeywords").value.trim(),
      prefer_contactable: $("#schedulePreferContactable").checked,
    },
  };
  try {
    const schedule = await api("/api/schedule", { method: "PUT", body: JSON.stringify(payload) });
    $("#scheduleNextRun").textContent = schedule.enabled ? formatTime(schedule.retry_at || schedule.next_run_at) : "未启用";
    showToast("每周任务已保存");
  } catch (error) { showToast(error.message, true); }
}

async function loadDataManagement(resetPage = false) {
  if (resetPage) state.archivedPagination.offset = 0;
  const pagination = state.archivedPagination;
  try {
    const [stats, archived] = await Promise.all([
      api("/api/data-management"),
      api(`/api/candidates?archived=only&limit=${pagination.limit}&offset=${pagination.offset}`),
    ]);
    if (archived.total > 0 && pagination.offset >= archived.total) {
      pagination.offset = Math.floor((archived.total - 1) / pagination.limit) * pagination.limit;
      return loadDataManagement();
    }
    pagination.total = archived.total;
    $("#dataActiveCandidates").textContent = stats.active_candidates;
    $("#dataArchivedCandidates").textContent = stats.archived_candidates;
    $("#dataDatabaseSize").textContent = formatBytes(stats.database_bytes);
    $("#dataBackupCount").textContent = stats.backup_count;
    $("#dataEvidenceCount").textContent = `${stats.evidence_count} 条`;
    $("#dataJobCount").textContent = `${stats.job_count} 条`;
    $("#dataLogSize").textContent = formatBytes(stats.log_bytes);
    $("#dataLatestBackup").textContent = stats.latest_backup
      ? `${formatTime(stats.latest_backup.created_at)} · ${formatBytes(stats.latest_backup.size_bytes)}`
      : "尚未备份";
    $("#archivedCandidateCount").textContent = `${archived.total} 人`;
    renderArchivedCandidates(archived.items);
    updatePagination("archived", pagination);
  } catch (error) { showToast(error.message, true); }
}

function renderArchivedCandidates(items) {
  const body = $("#archivedCandidateBody");
  body.replaceChildren();
  if (!items.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td"); cell.colSpan = 6; cell.className = "empty-cell"; cell.textContent = "暂无已归档候选人";
    row.append(cell); body.append(row); return;
  }
  items.forEach((candidate) => {
    const row = document.createElement("tr");
    const name = document.createElement("td");
    const strong = document.createElement("strong"); strong.textContent = candidate.display_name;
    const username = document.createElement("div"); username.className = "muted"; username.textContent = candidate.username;
    name.append(strong, username); row.append(name);
    const source = document.createElement("td"); source.textContent = sourceNames[candidate.source] || candidate.source.toUpperCase(); row.append(source);
    const city = document.createElement("td"); city.textContent = candidate.city; row.append(city);
    const status = document.createElement("td"); status.append(badge(candidate.review_status)); row.append(status);
    const archivedAt = document.createElement("td"); archivedAt.textContent = formatTime(candidate.archived_at); row.append(archivedAt);
    const actions = document.createElement("td"); actions.className = "management-actions";
    const restore = document.createElement("button"); restore.className = "button secondary"; restore.type = "button"; restore.textContent = "恢复";
    restore.addEventListener("click", () => restoreCandidate(candidate.id));
    const remove = document.createElement("button"); remove.className = "button danger"; remove.type = "button"; remove.textContent = "永久删除";
    remove.addEventListener("click", () => permanentlyDeleteCandidate(candidate));
    actions.append(restore, remove); row.append(actions); body.append(row);
  });
}

async function archiveSelectedCandidate() {
  if (!state.selectedCandidate) return;
  if (!window.confirm(`确认归档“${state.selectedCandidate.display_name}”吗？归档后可以在数据管理中恢复。`)) return;
  try {
    await api(`/api/candidates/${state.selectedCandidate.id}/archive`, { method: "POST", body: JSON.stringify({}) });
    $("#candidateDialog").close();
    state.selectedCandidate = null;
    showToast("候选人已归档");
    loadCandidates();
    loadOverview();
  } catch (error) { showToast(error.message, true); }
}

async function restoreCandidate(candidateId) {
  try {
    await api(`/api/candidates/${candidateId}/restore`, { method: "POST", body: JSON.stringify({}) });
    showToast("候选人已恢复到人才池");
    loadDataManagement();
  } catch (error) { showToast(error.message, true); }
}

async function permanentlyDeleteCandidate(candidate) {
  const confirmation = window.prompt(`永久删除“${candidate.display_name}”及其项目证据。系统会先自动备份数据库。请输入：永久删除`);
  if (confirmation === null) return;
  if (confirmation !== "永久删除") {
    showToast("确认文字不正确，未执行删除", true); return;
  }
  try {
    const result = await api(`/api/candidates/${candidate.id}`, {
      method: "DELETE",
      body: JSON.stringify({ confirmation }),
    });
    showToast(`已永久删除，删除前备份：${result.backup.filename}`);
    loadDataManagement();
  } catch (error) { showToast(error.message, true); }
}

async function backupDatabase() {
  const button = $("#dataBackupButton");
  button.disabled = true; button.textContent = "正在备份…";
  try {
    const result = await api("/api/data-management/backup", { method: "POST", body: JSON.stringify({}) });
    showToast(`备份已完成：${result.backup.filename}`);
    loadDataManagement();
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; button.textContent = "立即备份"; }
}

async function archiveNonmatching() {
  if (!window.confirm("将所有审核状态为“不符合”的候选人归档吗？此操作可以恢复。")) return;
  try {
    const result = await api("/api/data-management/archive-nonmatching", { method: "POST", body: JSON.stringify({}) });
    showToast(`已归档 ${result.archived_count} 名候选人`);
    loadDataManagement();
    loadOverview();
  } catch (error) { showToast(error.message, true); }
}

async function cleanupJobLogs() {
  if (!window.confirm("清理90天前已经结束的任务日志吗？候选人数据不会被删除。")) return;
  try {
    const result = await api("/api/data-management/cleanup-jobs", {
      method: "POST",
      body: JSON.stringify({ days: 90 }),
    });
    showToast(`已清理 ${result.deleted_count} 条任务日志`);
    loadDataManagement();
  } catch (error) { showToast(error.message, true); }
}

async function vacuumDatabase() {
  if (!window.confirm("整理数据库空间时页面可能短暂停顿，是否继续？")) return;
  try {
    const result = await api("/api/data-management/vacuum", { method: "POST", body: JSON.stringify({}) });
    showToast(`数据库整理完成，回收 ${formatBytes(result.reclaimed_bytes)}`);
    loadDataManagement();
  } catch (error) { showToast(error.message, true); }
}

async function loadCandidates(resetPage = false) {
  if (resetPage) state.candidatePagination.offset = 0;
  const pagination = state.candidatePagination;
  const params = new URLSearchParams({
    search: $("#candidateSearch").value.trim(),
    status: $("#candidateStatus").value,
    city: $("#candidateCity").value,
    source: $("#candidateSource").value,
    contactability: $("#candidateContactability").value,
    contact_stage: $("#candidateContactStage").value,
    limit: String(pagination.limit),
    offset: String(pagination.offset),
  });
  try {
    const data = await api(`/api/candidates?${params}`);
    if (data.total > 0 && pagination.offset >= data.total) {
      pagination.offset = Math.floor((data.total - 1) / pagination.limit) * pagination.limit;
      return loadCandidates();
    }
    pagination.total = data.total;
    $("#candidateCount").textContent = `${data.total} 人`;
    renderCandidates(data.items);
    updatePagination("candidate", pagination);
  } catch (error) { showToast(error.message, true); }
}

function renderCandidates(items) {
  const body = $("#candidateTableBody");
  body.replaceChildren();
  if (!items.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td"); cell.colSpan = 9; cell.className = "empty-cell"; cell.textContent = "暂无候选人";
    row.append(cell); body.append(row); return;
  }
  items.forEach((candidate) => {
    const row = document.createElement("tr");
    const name = document.createElement("td"); name.className = "candidate-name";
    const strong = document.createElement("strong"); strong.textContent = candidate.display_name;
    const user = document.createElement("span"); user.textContent = `${sourceNames[candidate.source] || candidate.source.toUpperCase()} · ${candidate.username}`;
    name.append(strong, user); row.append(name);
    const city = document.createElement("td"); city.textContent = candidate.city; row.append(city);
    const role = document.createElement("td"); role.textContent = candidate.suggested_role; row.append(role);
    const score = document.createElement("td"); score.className = "score"; score.textContent = candidate.match_score; row.append(score);
    const collection = document.createElement("td"); collection.className = "collection-time";
    const latest = document.createElement("strong"); latest.textContent = formatTime(candidate.last_seen_at);
    const first = document.createElement("small"); first.textContent = `首次 ${formatTime(candidate.first_seen_at)}`;
    collection.append(latest, first); row.append(collection);
    const contact = document.createElement("td"); contact.className = "contact-cell";
    contact.append(contactLevelBadge(candidate));
    if (candidate.contact_email) {
      const email = document.createElement("span"); email.className = "contact-email"; email.textContent = candidate.contact_email;
      const actions = document.createElement("span"); actions.className = "contact-actions";
      const copy = document.createElement("button"); copy.className = "text-button"; copy.type = "button"; copy.textContent = "复制"; copy.title = "复制邮箱";
      copy.addEventListener("click", () => copyText(candidate.contact_email));
      const mail = document.createElement("a"); mail.className = "text-button"; mail.href = `mailto:${candidate.contact_email}`; mail.textContent = "发邮件";
      actions.append(copy, mail); contact.append(email, actions);
    } else if (candidateContactLevel(candidate) === "C") {
      const contactLink = externalLink("打开入口", candidate.contact_url, "text-button");
      if (contactLink) contact.append(contactLink);
    } else {
      const profileOnly = document.createElement("span"); profileOnly.className = "contact-placeholder"; profileOnly.textContent = "暂无直接联系方式"; contact.append(profileOnly);
    }
    row.append(contact);
    const contactStage = document.createElement("td"); contactStage.append(badge(candidate.contact_stage || "未联系")); row.append(contactStage);
    const status = document.createElement("td"); status.append(badge(candidate.review_status)); row.append(status);
    const action = document.createElement("td");
    const view = document.createElement("button"); view.className = "button secondary"; view.type = "button"; view.textContent = "查看";
    view.addEventListener("click", () => openCandidate(candidate.id)); action.append(view); row.append(action);
    body.append(row);
  });
}

function addDetail(container, label, value) {
  const wrapper = document.createElement("div");
  const dt = document.createElement("dt"); dt.textContent = label;
  const dd = document.createElement("dd"); dd.textContent = value || "—";
  wrapper.append(dt, dd); container.append(wrapper);
}

function addDetailNode(container, label, node) {
  const wrapper = document.createElement("div");
  const dt = document.createElement("dt"); dt.textContent = label;
  const dd = document.createElement("dd"); dd.append(node);
  wrapper.append(dt, dd); container.append(wrapper);
}

async function openCandidate(candidateId) {
  try {
    const candidate = await api(`/api/candidates/${candidateId}`);
    state.selectedCandidate = candidate;
    $("#dialogName").textContent = candidate.display_name;
    $("#dialogMeta").textContent = `${sourceNames[candidate.source] || candidate.source.toUpperCase()} · ${candidate.username} · ${candidate.city}`;
    const links = $("#dialogLinks"); links.replaceChildren();
    const profileLink = externalLink("公开主页", candidate.profile_url, "button primary");
    if (profileLink) links.append(profileLink);
    if (candidate.contact_url && candidate.contact_url !== candidate.profile_url) {
      const contactLink = externalLink("联系入口", candidate.contact_url);
      if (contactLink) links.append(contactLink);
    }
    if (candidate.contact_email) {
      const copy = document.createElement("button"); copy.className = "button secondary"; copy.type = "button"; copy.textContent = "复制邮箱";
      copy.addEventListener("click", () => copyText(candidate.contact_email));
      const mail = document.createElement("a"); mail.className = "button secondary"; mail.href = `mailto:${candidate.contact_email}`; mail.textContent = "发送邮件"; links.append(mail);
      links.prepend(copy);
    }
    if (candidate.contact_email_source_url) {
      const emailSource = externalLink("邮箱来源", candidate.contact_email_source_url);
      if (emailSource) links.append(emailSource);
    }
    const details = $("#dialogDetails"); details.replaceChildren();
    addDetail(details, "建议岗位", candidate.suggested_role);
    addDetail(details, "匹配评分", `${candidate.match_score}/100`);
    addDetail(details, "最近采集时间", formatTime(candidate.last_seen_at));
    addDetail(details, "首次采集时间", formatTime(candidate.first_seen_at));
    addDetail(details, "来源更新时间", formatTime(candidate.source_updated_at));
    addDetail(details, "公司/机构", candidate.company);
    addDetail(details, "公开学历线索", candidate.education_status);
    addDetail(details, "学历核验", candidate.education_verification);
    addDetail(details, "年龄核验", candidate.age_status);
    addDetail(details, "工作地点核验", candidate.work_location_status);
    addDetail(details, "Agent 项目核验", candidate.agent_experience_status);
    addDetailNode(details, "联系方式等级", contactLevelBadge(candidate));
    addDetail(details, "公开邮箱", candidate.contact_email);
    addDetail(details, "邮箱核验时间", formatTime(candidate.contact_email_verified_at));
    addDetail(details, "联系进度更新时间", formatTime(candidate.contact_updated_at));
    addDetail(details, "公开简介", candidate.bio);
    const evidence = $("#dialogEvidence"); evidence.replaceChildren();
    if (!candidate.evidence.length) {
      const empty = document.createElement("p"); empty.className = "empty"; empty.textContent = "暂无公开项目证据"; evidence.append(empty);
    } else {
      candidate.evidence.forEach((item) => {
        const card = document.createElement("div"); card.className = "evidence-item";
        const safeEvidenceUrl = safeExternalUrl(item.url);
        const link = document.createElement(safeEvidenceUrl ? "a" : "strong");
        if (safeEvidenceUrl) { link.href = safeEvidenceUrl; link.target = "_blank"; link.rel = "noopener noreferrer"; }
        link.textContent = safeEvidenceUrl ? `${item.title} ↗` : item.title;
        const meta = document.createElement("p"); meta.textContent = `${item.language || "未知语言"} · ${item.stars} Stars${item.is_fork ? " · Fork" : " · 原创仓库"}`;
        const description = document.createElement("p"); description.textContent = item.description || "无公开说明";
        card.append(link, meta, description); evidence.append(card);
      });
    }
    $("#dialogEducationVerification").value = candidate.education_verification || "待本人确认";
    $("#dialogAgeStatus").value = candidate.age_status || "待本人确认";
    $("#dialogWorkLocationStatus").value = candidate.work_location_status || "待本人确认";
    $("#dialogAgentExperienceStatus").value = candidate.agent_experience_status || "待人工核验";
    $("#dialogContactStage").value = candidate.contact_stage || "未联系";
    $("#dialogReviewStatus").value = candidate.review_status;
    $("#dialogReviewNote").value = candidate.review_note || "";
    $("#candidateDialog").showModal();
  } catch (error) { showToast(error.message, true); }
}

async function saveCandidateReview() {
  if (!state.selectedCandidate) return;
  try {
    await api(`/api/candidates/${state.selectedCandidate.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        education_verification: $("#dialogEducationVerification").value,
        age_status: $("#dialogAgeStatus").value,
        work_location_status: $("#dialogWorkLocationStatus").value,
        agent_experience_status: $("#dialogAgentExperienceStatus").value,
        contact_stage: $("#dialogContactStage").value,
        review_status: $("#dialogReviewStatus").value,
        review_note: $("#dialogReviewNote").value.trim(),
      }),
    });
    $("#candidateDialog").close();
    showToast("核验结果与联系进度已保存");
    loadCandidates();
  } catch (error) { showToast(error.message, true); }
}

function bindEvents() {
  $$(".nav-item").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  $$('[data-go]').forEach((button) => button.addEventListener("click", () => setView(button.dataset.go)));
  $$(".segment").forEach((button) => button.addEventListener("click", () => {
    state.manualMode = button.dataset.mode;
    $("#manualMode").value = state.manualMode;
    $$(".segment").forEach((item) => item.classList.toggle("active", item === button));
    $("#searchFields").classList.toggle("hidden", state.manualMode !== "search");
    $("#urlFields").classList.toggle("hidden", state.manualMode !== "url");
  }));
  $("#manualForm").addEventListener("submit", submitManual);
  $("#scheduleForm").addEventListener("submit", saveSchedule);
  $("#candidateFilterButton").addEventListener("click", () => loadCandidates(true));
  $("#candidateSearch").addEventListener("keydown", (event) => { if (event.key === "Enter") loadCandidates(true); });
  $("#candidatePrevPage").addEventListener("click", () => {
    state.candidatePagination.offset = Math.max(0, state.candidatePagination.offset - state.candidatePagination.limit);
    loadCandidates();
  });
  $("#candidateNextPage").addEventListener("click", () => {
    state.candidatePagination.offset += state.candidatePagination.limit;
    loadCandidates();
  });
  $("#archivedPrevPage").addEventListener("click", () => {
    state.archivedPagination.offset = Math.max(0, state.archivedPagination.offset - state.archivedPagination.limit);
    loadDataManagement();
  });
  $("#archivedNextPage").addEventListener("click", () => {
    state.archivedPagination.offset += state.archivedPagination.limit;
    loadDataManagement();
  });
  $("#jobRefreshButton").addEventListener("click", loadJobs);
  $("#refreshButton").addEventListener("click", () => setView(state.view));
  $("#sourceHealthButton").addEventListener("click", () => loadSourceHealth(true));
  $("#dialogSave").addEventListener("click", saveCandidateReview);
  $("#dialogArchive").addEventListener("click", archiveSelectedCandidate);
  $("#dataBackupButton").addEventListener("click", backupDatabase);
  $("#dataVacuumButton").addEventListener("click", vacuumDatabase);
  $("#archiveNonmatchingButton").addEventListener("click", archiveNonmatching);
  $("#cleanupJobsButton").addEventListener("click", cleanupJobLogs);
}

if (window.location.protocol === "file:") {
  $("#serviceDot").className = "status-dot error";
  $("#serviceStatus").textContent = "请通过本地服务打开";
  showToast("当前打开的是源文件，请使用 http://127.0.0.1:8765/", true);
} else {
  bindEvents();
  loadHealth();
  loadOverview();
  window.setInterval(() => {
    if (state.view === "jobs") loadJobs();
    if (state.view === "overview") loadOverview();
  }, 8000);
}
