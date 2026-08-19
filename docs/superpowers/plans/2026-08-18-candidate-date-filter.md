# 候选人最近采集日期筛选 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在人才池中按候选人的最近采集日期筛选，并保证列表、计数、分页和 Excel 导出使用同一套北京时间日期边界。

**Architecture:** 后端在 `db.py` 中集中解析日期并生成北京时间的开始包含、结束次日排除边界，列表和导出使用参数化 SQLite `julianday()` 条件。前端区分草稿筛选与已应用筛选，只有请求成功才提交页面、分页和导出状态；纯日期计算放入一个无依赖的小型 JavaScript 模块并用 Node 内置测试验证。

**Tech Stack:** Python 3.11 标准库、SQLite、原生 HTML/CSS/JavaScript、`unittest`、Node.js 内置 `node:test`、现有 `openpyxl`、Playwright 浏览器验收。

---

## 文件结构

- Create: `static/candidate_date_filters.js` - 北京日期、快捷范围、前端日期校验、导出 URL 和候选人时间格式化的纯函数。
- Create: `tests/test_candidate_date_filters.js` - 不依赖 DOM 的确定性 JavaScript 日期测试。
- Modify: `static/index.html` - 日期控件、Excel 按钮 ID、日期工具脚本入口。
- Modify: `static/styles.css` - 独立日期筛选行及桌面/手机布局。
- Modify: `static/app.js` - 草稿/已应用筛选状态、请求提交、分页、空状态和候选人北京时间显示。
- Modify: `db.py` - 日期参数校验、列表条件和导出条件。
- Modify: `app.py` - 将导出查询参数传给数据库层。
- Modify: `tests/test_core.py` - 数据库、HTTP、真实 XLSX 与前端结构测试。
- Reference only: `excel_export.py` - 保持现有生成接口，不修改。

## 工作区保护

当前 `app.py`、`db.py`、`static/app.js`、`static/index.html`、`static/styles.css` 和测试中已有未提交修改。实施时必须保留这些修改，不执行 reset/checkout，不读取本地候选人数据库。由于修改文件与既有变更重叠，本计划不自动创建 Git commit；每个任务以限定路径的 `git diff --check` 和测试作为检查点，最终由用户决定何时提交。

### Task 1: 建立基线与测试夹具

**Files:**
- Modify: `tests/test_core.py:514-550`
- Modify: `tests/test_core.py:1447-1480`

- [ ] **Step 1: 运行现有基线测试**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
node --check static/app.js
```

Expected: 现有测试通过；如有既存失败，记录测试名和输出，在本功能修改前单独说明。

- [ ] **Step 2: 为数据库测试增加合成候选人时间夹具**

在 `DatabaseTests` 中 `candidate()` 之后加入：

```python
    def add_candidate_at(self, external_id, collected_at, **overrides):
        candidate = self.candidate(external_id)
        candidate.update(
            {
                "external_id": external_id,
                "username": external_id,
                "display_name": external_id,
                "profile_url": "https://profiles.example.test/{}".format(external_id),
                "contact_url": "https://profiles.example.test/{}".format(external_id),
                "contact_email": "{}@example.test".format(external_id),
                "evidence": [
                    {
                        "title": "{}-project".format(external_id),
                        "url": "https://code.example.test/{}/project".format(external_id),
                        "description": "synthetic agent project",
                        "stars": 1,
                        "is_fork": False,
                    }
                ],
            }
        )
        candidate.update(overrides)
        with patch.object(db, "now_iso", return_value=collected_at):
            candidate_id, _ = db.upsert_candidate(candidate)
        return candidate_id
```

- [ ] **Step 3: 为 HTTP 测试增加二进制响应读取方法**

将 `AppHandlerTests.request()` 拆为：

```python
    def request_raw(self, method: str, path: str, payload=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=3
        )
        headers = {}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = response.read()
        response_headers = dict(response.getheaders())
        status = response.status
        connection.close()
        return status, response_headers, result

    def request(self, method: str, path: str, payload=None):
        status, headers, raw = self.request_raw(method, path, payload)
        text = raw.decode("utf-8")
        parsed = (
            json.loads(text)
            if text and headers.get("Content-Type", "").startswith("application/json")
            else text
        )
        return status, headers, parsed
```

同时在 `AppHandlerTests` 中加入只使用合成数据的候选人帮助方法：

```python
    def add_candidate_at(self, external_id, collected_at):
        candidate = {
            "source": "github",
            "external_id": external_id,
            "username": external_id,
            "display_name": external_id,
            "city": "北京",
            "profile_url": "https://profiles.example.test/{}".format(external_id),
            "contact_url": "https://profiles.example.test/{}".format(external_id),
            "suggested_role": "AI Agent 工程师",
            "match_score": 80,
            "evidence": [
                {
                    "title": "{}-evidence".format(external_id),
                    "url": "https://code.example.test/{}/evidence".format(external_id),
                }
            ],
        }
        with patch.object(db, "now_iso", return_value=collected_at):
            db.upsert_candidate(candidate)
```

- [ ] **Step 4: 确认测试夹具没有改变既有行为**

Run:

```bash
python3 -m unittest tests.test_core.DatabaseTests tests.test_core.AppHandlerTests -v
```

Expected: PASS。

- [ ] **Step 5: 检查限定 diff**

Run:

```bash
git diff --check -- tests/test_core.py
```

Expected: 无输出。

### Task 2: 用失败测试驱动服务端日期校验

**Files:**
- Modify: `tests/test_core.py` in `DatabaseTests`
- Modify: `db.py:1-45`
- Modify: `db.py:897-950`

- [ ] **Step 1: 写日期格式、范围和倒序范围的失败测试**

在 `DatabaseTests` 中加入：

```python
    def test_candidate_date_filter_validates_exact_supported_dates(self) -> None:
        cases = [
            ("last_seen_from", "2026-8-18", "开始日期格式无效，请使用 YYYY-MM-DD"),
            ("last_seen_from", "2026-02-30", "开始日期格式无效，请使用 YYYY-MM-DD"),
            ("last_seen_from", " 2026-08-18", "开始日期格式无效，请使用 YYYY-MM-DD"),
            ("last_seen_from", "２０２６-０８-１８", "开始日期格式无效，请使用 YYYY-MM-DD"),
            ("last_seen_from", "1999-12-31", "日期必须在 2000-01-01 至 2100-12-31 之间"),
            ("last_seen_to", "2101-01-01", "日期必须在 2000-01-01 至 2100-12-31 之间"),
        ]
        for key, value, message in cases:
            with self.subTest(key=key, value=value):
                with self.assertRaisesRegex(ValueError, re.escape(message)):
                    db.list_candidates({key: value})

    def test_candidate_date_filter_rejects_reversed_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "开始日期不能晚于结束日期"):
            db.list_candidates(
                {"last_seen_from": "2026-08-19", "last_seen_to": "2026-08-18"}
            )
```

在 `AppHandlerTests` 中加入：

```python
    def test_invalid_candidate_date_returns_400_on_list(self) -> None:
        status, _, payload = self.request(
            "GET", "/api/candidates?last_seen_from=2026-02-30"
        )
        self.assertEqual(status, 400)
        self.assertIn("error", payload)
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
python3 -m unittest \
  tests.test_core.DatabaseTests.test_candidate_date_filter_validates_exact_supported_dates \
  tests.test_core.DatabaseTests.test_candidate_date_filter_rejects_reversed_range \
  tests.test_core.AppHandlerTests.test_invalid_candidate_date_returns_400_on_list -v
```

Expected: FAIL，因为当前查询忽略日期参数，不抛出 `ValueError`。

- [ ] **Step 3: 增加最小日期解析与边界函数**

在 `db.py` 中导入并定义：

```python
import re
from datetime import date, datetime, timedelta, timezone

CANDIDATE_FILTER_DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
CANDIDATE_FILTER_MIN_DATE = date(2000, 1, 1)
CANDIDATE_FILTER_MAX_DATE = date(2100, 12, 31)
BEIJING_TIMEZONE = timezone(timedelta(hours=8))


def _parse_candidate_filter_date(value: Any, label: str) -> Optional[date]:
    raw = "" if value is None else str(value)
    if raw == "":
        return None
    if not CANDIDATE_FILTER_DATE_PATTERN.fullmatch(raw):
        raise ValueError("{}格式无效，请使用 YYYY-MM-DD".format(label))
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("{}格式无效，请使用 YYYY-MM-DD".format(label)) from exc
    if not CANDIDATE_FILTER_MIN_DATE <= parsed <= CANDIDATE_FILTER_MAX_DATE:
        raise ValueError("日期必须在 2000-01-01 至 2100-12-31 之间")
    return parsed


def _candidate_date_bounds(
    filters: Dict[str, Any],
) -> Tuple[Optional[str], Optional[str]]:
    start_date = _parse_candidate_filter_date(
        filters.get("last_seen_from"), "开始日期"
    )
    end_date = _parse_candidate_filter_date(filters.get("last_seen_to"), "结束日期")
    if start_date and end_date and start_date > end_date:
        raise ValueError("开始日期不能晚于结束日期")
    start = (
        datetime.combine(start_date, datetime.min.time(), tzinfo=BEIJING_TIMEZONE)
        if start_date
        else None
    )
    end = (
        datetime.combine(
            end_date + timedelta(days=1),
            datetime.min.time(),
            tzinfo=BEIJING_TIMEZONE,
        )
        if end_date
        else None
    )
    return (
        start.isoformat(timespec="seconds") if start else None,
        end.isoformat(timespec="seconds") if end else None,
    )
```

在 `list_candidates()` 开头保存 `start_boundary, end_boundary = _candidate_date_bounds(filters)`；此任务先只建立校验，不加入 SQL 条件。

- [ ] **Step 4: 运行测试并确认 GREEN**

Run:

```bash
python3 -m unittest \
  tests.test_core.DatabaseTests.test_candidate_date_filter_validates_exact_supported_dates \
  tests.test_core.DatabaseTests.test_candidate_date_filter_rejects_reversed_range \
  tests.test_core.AppHandlerTests.test_invalid_candidate_date_returns_400_on_list -v
```

Expected: PASS。

- [ ] **Step 5: 检查限定 diff**

Run:

```bash
git diff --check -- db.py tests/test_core.py
```

Expected: 无输出。

### Task 3: 用失败测试驱动候选人列表日期查询

**Files:**
- Modify: `tests/test_core.py` in `DatabaseTests`
- Modify: `db.py:897-950`

- [ ] **Step 1: 写北京时间边界、单边范围和组合分页测试**

加入：

```python
    def test_candidate_date_filter_uses_inclusive_beijing_boundaries(self) -> None:
        self.add_candidate_at("before", "2026-08-17T15:59:59+00:00")
        self.add_candidate_at("start", "2026-08-17T16:00:00+00:00")
        self.add_candidate_at("late", "2026-08-18T23:59:59.999999+08:00")
        self.add_candidate_at("next-day", "2026-08-19T00:00:00+08:00")

        result = db.list_candidates(
            {"last_seen_from": "2026-08-18", "last_seen_to": "2026-08-18"}
        )

        self.assertEqual(
            {candidate["external_id"] for candidate in result["items"]},
            {"start", "late"},
        )
        self.assertEqual(result["total"], 2)

    def test_candidate_date_filter_supports_open_ended_ranges(self) -> None:
        self.add_candidate_at("old", "2026-08-10T10:00:00+08:00")
        self.add_candidate_at("new", "2026-08-20T10:00:00+08:00")
        from_result = db.list_candidates({"last_seen_from": "2026-08-18"})
        to_result = db.list_candidates({"last_seen_to": "2026-08-18"})
        self.assertEqual(
            {item["external_id"] for item in from_result["items"]}, {"new"}
        )
        self.assertEqual(
            {item["external_id"] for item in to_result["items"]}, {"old"}
        )

    def test_candidate_date_filter_combines_with_filters_and_pagination(self) -> None:
        self.add_candidate_at("beijing-a", "2026-08-18T09:00:00+08:00")
        self.add_candidate_at("beijing-b", "2026-08-18T10:00:00+08:00")
        self.add_candidate_at(
            "chongqing", "2026-08-18T11:00:00+08:00", city="重庆"
        )
        self.add_candidate_at("outside", "2026-08-17T11:00:00+08:00")
        wrong_status_id = self.add_candidate_at(
            "wrong-status", "2026-08-18T12:00:00+08:00"
        )
        archived_id = self.add_candidate_at(
            "archived", "2026-08-18T13:00:00+08:00"
        )
        with db.connect() as connection:
            connection.execute(
                "UPDATE candidates SET review_status = '人才储备' WHERE id = ?",
                (wrong_status_id,),
            )
            connection.execute(
                "UPDATE candidates SET archived_at = ? WHERE id = ?",
                ("2026-08-18T14:00:00+08:00", archived_id),
            )
        result = db.list_candidates(
            {
                "last_seen_from": "2026-08-18",
                "last_seen_to": "2026-08-18",
                "city": "北京",
                "source": "github",
                "status": "待审核",
                "contactability": "email",
                "limit": 1,
                "offset": 1,
            }
        )
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(result["items"]), 1)
```

在 `AppHandlerTests` 中加入：

```python
    def test_candidate_date_query_filters_list_endpoint(self) -> None:
        self.add_candidate_at("in-range", "2026-08-18T09:00:00+08:00")
        self.add_candidate_at("outside", "2026-08-17T09:00:00+08:00")
        status, _, payload = self.request(
            "GET",
            "/api/candidates?last_seen_from=2026-08-18&last_seen_to=2026-08-18",
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["external_id"], "in-range")
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
python3 -m unittest \
  tests.test_core.DatabaseTests.test_candidate_date_filter_uses_inclusive_beijing_boundaries \
  tests.test_core.DatabaseTests.test_candidate_date_filter_supports_open_ended_ranges \
  tests.test_core.DatabaseTests.test_candidate_date_filter_combines_with_filters_and_pagination \
  tests.test_core.AppHandlerTests.test_candidate_date_query_filters_list_endpoint -v
```

Expected: FAIL，因为日期已校验但尚未进入 SQL WHERE。

- [ ] **Step 3: 将日期边界加入列表的参数化条件**

在 `list_candidates()` 中复用已经解析的边界：

```python
    start_boundary, end_boundary = _candidate_date_bounds(filters)
    if start_boundary:
        clauses.append("julianday(last_seen_at) >= julianday(?)")
        params.append(start_boundary)
    if end_boundary:
        clauses.append("julianday(last_seen_at) < julianday(?)")
        params.append(end_boundary)
```

该代码必须在创建 `where` 之前执行；count 和分页 rows 继续共享同一个 `where` 与 `params`。

- [ ] **Step 4: 运行新增数据库测试并确认 GREEN**

Run:

```bash
python3 -m unittest \
  tests.test_core.DatabaseTests.test_candidate_date_filter_uses_inclusive_beijing_boundaries \
  tests.test_core.DatabaseTests.test_candidate_date_filter_supports_open_ended_ranges \
  tests.test_core.DatabaseTests.test_candidate_date_filter_combines_with_filters_and_pagination \
  tests.test_core.AppHandlerTests.test_candidate_date_query_filters_list_endpoint -v
```

Expected: PASS。

- [ ] **Step 5: 回归全部 DatabaseTests**

Run:

```bash
python3 -m unittest tests.test_core.DatabaseTests -v
```

Expected: PASS。

### Task 4: 用失败测试驱动日期过滤的数据库导出

**Files:**
- Modify: `tests/test_core.py` in `DatabaseTests`
- Modify: `db.py:1457-1482`

- [ ] **Step 1: 写导出候选人和证据一致性测试**

加入：

```python
    def test_export_candidates_applies_date_filter_and_matching_evidence(self) -> None:
        self.add_candidate_at("in-range", "2026-08-18T09:00:00+08:00")
        self.add_candidate_at("out-of-range", "2026-08-17T09:00:00+08:00")

        exported = db.export_candidates(
            {"last_seen_from": "2026-08-18", "last_seen_to": "2026-08-18"}
        )

        self.assertEqual([item["external_id"] for item in exported], ["in-range"])
        self.assertEqual(
            [item["title"] for item in exported[0]["evidence"]],
            ["in-range-project"],
        )

    def test_export_candidates_without_filters_remains_backward_compatible(self) -> None:
        self.add_candidate_at("first", "2026-08-17T09:00:00+08:00")
        self.add_candidate_at("second", "2026-08-18T09:00:00+08:00")
        self.assertEqual(len(db.export_candidates()), 2)
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
python3 -m unittest \
  tests.test_core.DatabaseTests.test_export_candidates_applies_date_filter_and_matching_evidence \
  tests.test_core.DatabaseTests.test_export_candidates_without_filters_remains_backward_compatible -v
```

Expected: ERROR/FAIL，因为 `export_candidates()` 还不接受筛选参数。

- [ ] **Step 3: 为数据库导出加入同一日期边界**

将函数签名和查询改为：

```python
def export_candidates(
    filters: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    active_filters = filters or {}
    start_boundary, end_boundary = _candidate_date_bounds(active_filters)
    candidate_clauses = ["archived_at IS NULL"]
    evidence_clauses = ["c.archived_at IS NULL"]
    params: List[Any] = []
    if start_boundary:
        candidate_clauses.append("julianday(last_seen_at) >= julianday(?)")
        evidence_clauses.append("julianday(c.last_seen_at) >= julianday(?)")
        params.append(start_boundary)
    if end_boundary:
        candidate_clauses.append("julianday(last_seen_at) < julianday(?)")
        evidence_clauses.append("julianday(c.last_seen_at) < julianday(?)")
        params.append(end_boundary)
    candidate_where = " WHERE " + " AND ".join(candidate_clauses)
    evidence_where = " WHERE " + " AND ".join(evidence_clauses)
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM candidates{}
            ORDER BY {}
            """.format(candidate_where, candidate_order_sql()),
            params,
        ).fetchall()
        candidates = [_candidate_dict(row) for row in rows]
        evidence_rows = connection.execute(
            """
            SELECT e.*, c.display_name AS candidate_name
            FROM evidence e
            JOIN candidates c ON c.id = e.candidate_id
            {}
            ORDER BY e.candidate_id, e.is_fork, e.stars DESC, e.id
            """.format(evidence_where),
            params,
        ).fetchall()
    evidence_by_candidate: Dict[int, List[Dict[str, Any]]] = {}
    for row in evidence_rows:
        item = dict(row)
        evidence_by_candidate.setdefault(int(item["candidate_id"]), []).append(item)
    for candidate in candidates:
        candidate["evidence"] = evidence_by_candidate.get(int(candidate["id"]), [])
    return candidates
```

- [ ] **Step 4: 运行导出测试并确认 GREEN**

Run:

```bash
python3 -m unittest \
  tests.test_core.DatabaseTests.test_export_candidates_applies_date_filter_and_matching_evidence \
  tests.test_core.DatabaseTests.test_export_candidates_without_filters_remains_backward_compatible -v
```

Expected: PASS。

- [ ] **Step 5: 检查限定 diff**

Run:

```bash
git diff --check -- db.py tests/test_core.py
```

Expected: 无输出。

### Task 5: 用失败测试驱动 HTTP 与真实 XLSX 导出

**Files:**
- Modify: `tests/test_core.py` in `AppHandlerTests`
- Modify: `app.py:69-109`

- [ ] **Step 1: 写非法导出参数、过滤导出和空导出测试**

在 `AppHandlerTests` 中加入测试：

```python
    def test_invalid_candidate_date_returns_400_on_export(self) -> None:
        status, headers, raw = self.request_raw(
            "GET", "/export/candidates.xlsx?last_seen_to=2101-01-01"
        )
        self.assertEqual(status, 400)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        self.assertIn("error", json.loads(raw.decode("utf-8")))

    @unittest.skipUnless(importlib.util.find_spec("openpyxl"), "openpyxl is not installed")
    def test_candidate_excel_export_applies_date_query(self) -> None:
        from openpyxl import load_workbook

        self.add_candidate_at("in-range", "2026-08-18T09:00:00+08:00")
        self.add_candidate_at("outside", "2026-08-17T09:00:00+08:00")
        status, headers, raw = self.request_raw(
            "GET",
            "/export/candidates.xlsx?last_seen_from=2026-08-18&last_seen_to=2026-08-18",
        )
        self.assertEqual(status, 200)
        self.assertIn("spreadsheetml", headers["Content-Type"])
        workbook = load_workbook(io.BytesIO(raw), data_only=False)
        self.addCleanup(workbook.close)
        values = [cell.value for cell in workbook["候选人总表"]["B"]]
        evidence_values = [cell.value for cell in workbook["项目证据"]["C"]]
        self.assertIn("in-range", values)
        self.assertNotIn("outside", values)
        self.assertIn("in-range-evidence", evidence_values)
        self.assertNotIn("outside-evidence", evidence_values)

    @unittest.skipUnless(importlib.util.find_spec("openpyxl"), "openpyxl is not installed")
    def test_candidate_excel_export_supports_empty_date_result(self) -> None:
        from openpyxl import load_workbook

        self.add_candidate_at("outside", "2026-08-17T09:00:00+08:00")
        status, _, raw = self.request_raw(
            "GET",
            "/export/candidates.xlsx?last_seen_from=2026-08-18&last_seen_to=2026-08-18",
        )
        self.assertEqual(status, 200)
        workbook = load_workbook(io.BytesIO(raw), data_only=False)
        self.addCleanup(workbook.close)
        sheet = workbook["候选人总表"]
        self.assertEqual(sheet["B6"].value, "候选人")
        self.assertIsNone(sheet["B7"].value)
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
python3 -m unittest \
  tests.test_core.AppHandlerTests.test_invalid_candidate_date_returns_400_on_export \
  tests.test_core.AppHandlerTests.test_candidate_excel_export_applies_date_query \
  tests.test_core.AppHandlerTests.test_candidate_excel_export_supports_empty_date_result -v
```

Expected: 全部 FAIL，因为导出路由尚未传递 query。

- [ ] **Step 3: 将导出查询传给数据库层**

在 `app.py` 的导出分支改为：

```python
        elif path == "/export/candidates.xlsx":
            try:
                content = generate_excel(db.export_candidates(query))
                self.send_bytes(
                    content,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    content_disposition="attachment; filename*=UTF-8''AI-Talent-Candidates.xlsx",
                )
            except RuntimeError:
                self.send_json(
                    {"error": "Excel 导出生成失败，请检查本机导出运行环境"},
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
```

外层 `do_GET()` 已将 `ValueError` 映射为 HTTP 400，不增加重复异常处理。

- [ ] **Step 4: 运行 HTTP/XLSX 测试并确认 GREEN**

Run: 与 Step 2 相同。

Expected: PASS。

- [ ] **Step 5: 回归全部 AppHandlerTests**

Run:

```bash
python3 -m unittest tests.test_core.AppHandlerTests -v
```

Expected: PASS。

### Task 6: 用 Node 失败测试驱动纯前端日期工具

**Files:**
- Create: `tests/test_candidate_date_filters.js`
- Create: `static/candidate_date_filters.js`

- [ ] **Step 1: 创建纯函数的失败测试**

创建 `tests/test_candidate_date_filters.js`：

```javascript
const test = require("node:test");
const assert = require("node:assert/strict");

const filters = require("../static/candidate_date_filters.js");

test("quick ranges use the Beijing calendar at a UTC day boundary", () => {
  const mondayInBeijing = new Date("2026-08-16T16:30:00Z");
  assert.deepEqual(filters.candidateDateRangeForPreset("today", mondayInBeijing), {
    date_range: "today",
    last_seen_from: "2026-08-17",
    last_seen_to: "2026-08-17",
  });
  assert.deepEqual(filters.candidateDateRangeForPreset("this_week", mondayInBeijing), {
    date_range: "this_week",
    last_seen_from: "2026-08-17",
    last_seen_to: "2026-08-17",
  });
  assert.deepEqual(filters.candidateDateRangeForPreset("last_7_days", mondayInBeijing), {
    date_range: "last_7_days",
    last_seen_from: "2026-08-11",
    last_seen_to: "2026-08-17",
  });
});

test("quick ranges cross month and year boundaries deterministically", () => {
  const newYearInBeijing = new Date("2025-12-31T16:30:00Z");
  assert.deepEqual(filters.candidateDateRangeForPreset("last_7_days", newYearInBeijing), {
    date_range: "last_7_days",
    last_seen_from: "2025-12-26",
    last_seen_to: "2026-01-01",
  });
  assert.deepEqual(filters.candidateDateRangeForPreset("this_month", newYearInBeijing), {
    date_range: "this_month",
    last_seen_from: "2026-01-01",
    last_seen_to: "2026-01-01",
  });
});

test("custom validation and export use only applied date boundaries", () => {
  assert.equal(
    filters.candidateDateValidationMessage({
      last_seen_from: "2026-08-19",
      last_seen_to: "2026-08-18",
    }),
    "开始日期不能晚于结束日期",
  );
  assert.equal(
    filters.candidateExportUrl({
      last_seen_from: "2026-08-12",
      last_seen_to: "2026-08-18",
    }),
    "/export/candidates.xlsx?last_seen_from=2026-08-12&last_seen_to=2026-08-18",
  );
  assert.deepEqual(
    filters.normalizeCandidateDateRange({ date_range: "custom" }),
    { date_range: "all", last_seen_from: "", last_seen_to: "" },
  );
});

test("a failed page request never commits candidate state", async () => {
  const oldState = { page: 2, total: 50, exportUrl: "/export/candidates.xlsx" };
  let reported = "";
  const succeeded = await filters.commitCandidatePageAfterFetch(
    async () => { throw new Error("日期无效"); },
    (nextState) => Object.assign(oldState, nextState),
    (error) => { reported = error.message; },
  );
  assert.equal(succeeded, false);
  assert.deepEqual(oldState, {
    page: 2,
    total: 50,
    exportUrl: "/export/candidates.xlsx",
  });
  assert.equal(reported, "日期无效");
});

test("a successful page request commits once", async () => {
  const commits = [];
  const succeeded = await filters.commitCandidatePageAfterFetch(
    async () => ({ page: 1, total: 8 }),
    (nextState) => commits.push(nextState),
    () => assert.fail("success must not report an error"),
  );
  assert.equal(succeeded, true);
  assert.deepEqual(commits, [{ page: 1, total: 8 }]);
});

test("candidate timestamps display in Beijing time", () => {
  const rendered = filters.formatCandidateTime("2026-08-17T16:30:00Z");
  assert.match(rendered, /2026\/8\/18/);
  assert.match(rendered, /00:30:00/);
});
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
node --test tests/test_candidate_date_filters.js
```

Expected: FAIL，提示 `static/candidate_date_filters.js` 不存在。

- [ ] **Step 3: 创建无依赖日期工具模块**

创建 `static/candidate_date_filters.js`：

```javascript
(function exposeCandidateDateFilters(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.CandidateDateFilters = api;
})(typeof globalThis === "undefined" ? this : globalThis, function createCandidateDateFilters() {
  "use strict";

  const BEIJING_TIME_ZONE = "Asia/Shanghai";

  function beijingDateString(now = new Date()) {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: BEIJING_TIME_ZONE,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(now);
    const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    return `${values.year}-${values.month}-${values.day}`;
  }

  function addDaysToDate(dateText, days) {
    const [year, month, day] = dateText.split("-").map(Number);
    const shifted = new Date(Date.UTC(year, month - 1, day + days));
    return [
      shifted.getUTCFullYear(),
      String(shifted.getUTCMonth() + 1).padStart(2, "0"),
      String(shifted.getUTCDate()).padStart(2, "0"),
    ].join("-");
  }

  function candidateDateRangeForPreset(preset, now = new Date()) {
    const today = beijingDateString(now);
    if (preset === "all") {
      return { date_range: "all", last_seen_from: "", last_seen_to: "" };
    }
    if (preset === "today") {
      return { date_range: preset, last_seen_from: today, last_seen_to: today };
    }
    if (preset === "this_week") {
      const [year, month, day] = today.split("-").map(Number);
      const weekday = new Date(Date.UTC(year, month - 1, day)).getUTCDay();
      const daysSinceMonday = (weekday + 6) % 7;
      return {
        date_range: preset,
        last_seen_from: addDaysToDate(today, -daysSinceMonday),
        last_seen_to: today,
      };
    }
    if (preset === "last_7_days" || preset === "last_30_days") {
      const days = preset === "last_7_days" ? 6 : 29;
      return {
        date_range: preset,
        last_seen_from: addDaysToDate(today, -days),
        last_seen_to: today,
      };
    }
    if (preset === "this_month") {
      return {
        date_range: preset,
        last_seen_from: `${today.slice(0, 7)}-01`,
        last_seen_to: today,
      };
    }
    throw new Error("未知的日期范围");
  }

  function normalizeCandidateDateRange(range = {}) {
    const from = String(range.last_seen_from || "");
    const to = String(range.last_seen_to || "");
    if (!from && !to) {
      return { date_range: "all", last_seen_from: "", last_seen_to: "" };
    }
    return {
      date_range: String(range.date_range || "custom"),
      last_seen_from: from,
      last_seen_to: to,
    };
  }

  function candidateDateValidationMessage(range = {}) {
    const normalized = normalizeCandidateDateRange(range);
    if (
      normalized.last_seen_from
      && normalized.last_seen_to
      && normalized.last_seen_from > normalized.last_seen_to
    ) return "开始日期不能晚于结束日期";
    return "";
  }

  function candidateExportUrl(range = {}) {
    const normalized = normalizeCandidateDateRange(range);
    const params = new URLSearchParams();
    if (normalized.last_seen_from) params.set("last_seen_from", normalized.last_seen_from);
    if (normalized.last_seen_to) params.set("last_seen_to", normalized.last_seen_to);
    const query = params.toString();
    return `/export/candidates.xlsx${query ? `?${query}` : ""}`;
  }

  async function commitCandidatePageAfterFetch(fetchPage, commitPage, handleError) {
    try {
      const page = await fetchPage();
      commitPage(page);
      return true;
    } catch (error) {
      handleError(error);
      return false;
    }
  }

  function formatCandidateTime(value) {
    if (!value) return "—";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return value;
    return parsed.toLocaleString("zh-CN", {
      timeZone: BEIJING_TIME_ZONE,
      hourCycle: "h23",
    });
  }

  return {
    BEIJING_TIME_ZONE,
    addDaysToDate,
    beijingDateString,
    commitCandidatePageAfterFetch,
    candidateDateRangeForPreset,
    candidateDateValidationMessage,
    candidateExportUrl,
    formatCandidateTime,
    normalizeCandidateDateRange,
  };
});
```

- [ ] **Step 4: 运行 Node 测试并确认 GREEN**

Run:

```bash
node --test tests/test_candidate_date_filters.js
node --check static/candidate_date_filters.js
```

Expected: 6 tests PASS；语法检查无输出。

### Task 7: 用失败结构测试驱动日期控件和响应式布局

**Files:**
- Modify: `tests/test_core.py:25-110`
- Modify: `static/index.html:38-41`
- Modify: `static/index.html:258-267`
- Modify: `static/index.html` before closing `body`
- Modify: `static/styles.css:208-210`
- Modify: `static/styles.css:280-310`

- [ ] **Step 1: 写日期控件与布局的失败测试**

在 `FrontendStructureTests` 中加入：

```python
    def test_candidate_pool_exposes_date_filter_controls_and_responsive_row(self) -> None:
        project_dir = Path(__file__).resolve().parents[1]
        html = (project_dir / "static" / "index.html").read_text(encoding="utf-8")
        styles = (project_dir / "static" / "styles.css").read_text(encoding="utf-8")
        for element_id in (
            "candidateDateRange",
            "candidateDateFrom",
            "candidateDateTo",
            "candidateDateClearButton",
            "candidateExportButton",
        ):
            self.assertEqual(html.count('id="{}"'.format(element_id)), 1)
        for value in (
            "all",
            "today",
            "this_week",
            "last_7_days",
            "last_30_days",
            "this_month",
            "custom",
        ):
            self.assertIn('value="{}"'.format(value), html)
        self.assertIn('id="candidateDateFrom" type="date"', html)
        self.assertIn('id="candidateDateTo" type="date"', html)
        self.assertIn('src="candidate_date_filters.js"', html)
        self.assertIn(".candidate-date-filter", styles)
        tablet = styles[
            styles.index("@media (max-width: 980px)"):
            styles.index("@media (max-width: 640px)")
        ]
        mobile = styles[styles.index("@media (max-width: 640px)"):]
        self.assertIn(".candidate-date-filter", tablet)
        self.assertIn("repeat(2, minmax(0, 1fr))", tablet)
        self.assertIn(".candidate-date-filter", mobile)
        self.assertIn("grid-template-columns: 1fr", mobile)
        self.assertIn("min-width: 0", styles)
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
python3 -m unittest \
  tests.test_core.FrontendStructureTests.test_candidate_pool_exposes_date_filter_controls_and_responsive_row -v
```

Expected: FAIL，因为日期控件和样式尚不存在。

- [ ] **Step 3: 增加日期筛选行并标记 Excel 按钮**

将顶部导出链接改为：

```html
<a id="candidateExportButton" class="button secondary" href="/export/candidates.xlsx">导出 Excel</a>
```

保留现有 `.filter-bar` 的六个筛选字段，将 `candidateFilterButton` 移到新日期行：

```html
<div class="candidate-date-filter" aria-label="按最近采集时间筛选">
  <label><span>最近采集</span><select id="candidateDateRange"><option value="all">全部</option><option value="today">今天</option><option value="this_week">本周</option><option value="last_7_days">最近 7 天</option><option value="last_30_days">最近 30 天</option><option value="this_month">本月</option><option value="custom">自定义</option></select></label>
  <label><span>开始日期</span><input id="candidateDateFrom" type="date" min="2000-01-01" max="2100-12-31"></label>
  <label><span>结束日期</span><input id="candidateDateTo" type="date" min="2000-01-01" max="2100-12-31"></label>
  <button id="candidateDateClearButton" class="button secondary" type="button">清除日期</button>
  <button id="candidateFilterButton" class="button primary" type="button">筛选</button>
</div>
```

在 `app.js` 之前加载纯函数脚本：

```html
<script src="candidate_date_filters.js" defer></script>
<script src="app.js" defer></script>
```

- [ ] **Step 4: 增加稳定响应式样式**

加入：

```css
.filter-bar {
  grid-template-columns: minmax(180px, 2fr) repeat(5, minmax(120px, 1fr));
}
.candidate-date-filter {
  display: grid;
  grid-template-columns: repeat(3, minmax(150px, 1fr)) auto auto;
  align-items: end;
  gap: 10px;
  margin: -4px 0 14px;
}
.candidate-date-filter label { display: grid; min-width: 0; gap: 5px; }
.candidate-date-filter label > span { color: #46565e; font-size: 12px; font-weight: 700; }
.candidate-date-filter .button { min-height: 38px; }
```

在现有媒体查询中加入：

```css
@media (max-width: 980px) {
  .candidate-date-filter { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 640px) {
  .candidate-date-filter { grid-template-columns: 1fr; }
}
```

- [ ] **Step 5: 运行结构测试并确认 GREEN**

Run:

```bash
python3 -m unittest \
  tests.test_core.FrontendStructureTests.test_candidate_pool_exposes_date_filter_controls_and_responsive_row \
  tests.test_core.FrontendStructureTests.test_javascript_id_references_exist_once_in_html -v
```

Expected: PASS。

### Task 8: 用失败结构测试驱动草稿/已应用筛选状态

**Files:**
- Modify: `tests/test_core.py` in `FrontendStructureTests`
- Modify: `static/app.js:1-12`
- Modify: `static/app.js:1113-1185`
- Modify: `static/app.js:1276-1310`
- Modify: `static/app.js:1365-1393`

- [ ] **Step 1: 写前端状态、查询、空状态和时区接线的失败测试**

加入：

```python
    def test_candidate_filter_commits_only_successful_applied_state(self) -> None:
        script = (
            Path(__file__).resolve().parents[1] / "static" / "app.js"
        ).read_text(encoding="utf-8")
        for function_name in (
            "defaultCandidateFilters",
            "readCandidateFilterControls",
            "buildCandidateListParams",
            "fetchCandidatePage",
            "commitCandidatePage",
            "applyCandidateFilters",
            "updateCandidateExportButton",
            "hasActiveCandidateFilters",
        ):
            self.assertIn("function {}(".format(function_name), script)
        self.assertIn("candidateFilters:", script)
        self.assertIn("draft:", script)
        self.assertIn("applied:", script)
        self.assertIn("last_seen_from", script)
        self.assertIn("last_seen_to", script)
        self.assertIn("当前筛选条件下暂无候选人", script)
        self.assertIn("formatCandidateTime(candidate.last_seen_at)", script)
        self.assertIn("formatCandidateTime(candidate.first_seen_at)", script)

    def test_candidate_export_uses_applied_date_boundaries(self) -> None:
        script = (
            Path(__file__).resolve().parents[1] / "static" / "app.js"
        ).read_text(encoding="utf-8")
        self.assertIn("candidateExportUrl(state.candidateFilters.applied)", script)
        self.assertIn("commitCandidatePageAfterFetch(", script)
        self.assertIn("state.candidateFilters.applied = { ...filters }", script)
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
python3 -m unittest \
  tests.test_core.FrontendStructureTests.test_candidate_filter_commits_only_successful_applied_state \
  tests.test_core.FrontendStructureTests.test_candidate_export_uses_applied_date_boundaries -v
```

Expected: FAIL，因为应用状态函数尚不存在。

- [ ] **Step 3: 初始化筛选状态并接入日期工具**

在 `app.js` 顶部加入：

```javascript
const {
  candidateDateRangeForPreset,
  candidateDateValidationMessage,
  candidateExportUrl,
  commitCandidatePageAfterFetch,
  formatCandidateTime,
  normalizeCandidateDateRange,
} = window.CandidateDateFilters;

function defaultCandidateFilters() {
  return {
    search: "",
    status: "全部",
    city: "全部",
    source: "全部",
    contactability: "all",
    contact_stage: "全部",
    date_range: "all",
    last_seen_from: "",
    last_seen_to: "",
  };
}
```

在 `state` 中加入两个独立对象：

```javascript
  candidateFilters: {
    draft: defaultCandidateFilters(),
    applied: defaultCandidateFilters(),
  },
```

- [ ] **Step 4: 增加读取、归一化、查询和导出帮助函数**

在 `loadCandidates()` 前加入：

```javascript
function readCandidateFilterControls() {
  return {
    search: $("#candidateSearch").value.trim(),
    status: $("#candidateStatus").value,
    city: $("#candidateCity").value,
    source: $("#candidateSource").value,
    contactability: $("#candidateContactability").value,
    contact_stage: $("#candidateContactStage").value,
    date_range: $("#candidateDateRange").value,
    last_seen_from: $("#candidateDateFrom").value,
    last_seen_to: $("#candidateDateTo").value,
  };
}

function normalizeCandidateFilters(filters = {}) {
  const merged = { ...defaultCandidateFilters(), ...filters };
  const dates = normalizeCandidateDateRange(merged);
  return { ...merged, search: String(merged.search || "").trim(), ...dates };
}

function writeCandidateDateControls(filters) {
  $("#candidateDateRange").value = filters.date_range;
  $("#candidateDateFrom").value = filters.last_seen_from;
  $("#candidateDateTo").value = filters.last_seen_to;
}

function buildCandidateListParams(filters, limit, offset) {
  const params = new URLSearchParams({
    search: filters.search,
    status: filters.status,
    city: filters.city,
    source: filters.source,
    contactability: filters.contactability,
    contact_stage: filters.contact_stage,
    limit: String(limit),
    offset: String(offset),
  });
  if (filters.last_seen_from) params.set("last_seen_from", filters.last_seen_from);
  if (filters.last_seen_to) params.set("last_seen_to", filters.last_seen_to);
  return params;
}

function hasActiveCandidateFilters(filters) {
  const defaults = defaultCandidateFilters();
  return Object.keys(defaults).some((key) => filters[key] !== defaults[key]);
}

function updateCandidateExportButton() {
  $("#candidateExportButton").href = candidateExportUrl(state.candidateFilters.applied);
}

function syncCandidateDraft() {
  state.candidateFilters.draft = { ...readCandidateFilterControls() };
}
```

- [ ] **Step 5: 以成功后提交替换现有 `loadCandidates()`**

使用以下完整控制流：

```javascript
async function fetchCandidatePage(filters, requestedOffset) {
  const pagination = state.candidatePagination;
  let offset = Math.max(0, requestedOffset);
  let data = await api(`/api/candidates?${buildCandidateListParams(filters, pagination.limit, offset)}`);
  if (data.total > 0 && offset >= data.total) {
    offset = Math.floor((data.total - 1) / pagination.limit) * pagination.limit;
    data = await api(`/api/candidates?${buildCandidateListParams(filters, pagination.limit, offset)}`);
  }
  return { data, offset };
}

function commitCandidatePage(page, filters) {
  const pagination = state.candidatePagination;
  state.candidateFilters.applied = { ...filters };
  pagination.offset = page.offset;
  pagination.total = page.data.total;
  $("#candidateCount").textContent = `${page.data.total} 人`;
  renderCandidates(page.data.items, filters);
  updatePagination("candidate", pagination);
  updateCandidateExportButton();
}

async function loadCandidatePage(filters, requestedOffset, commitDraft = false) {
  return commitCandidatePageAfterFetch(
    () => fetchCandidatePage(filters, requestedOffset),
    (page) => {
      if (commitDraft) {
        state.candidateFilters.draft = { ...filters };
        writeCandidateDateControls(filters);
      }
      commitCandidatePage(page, filters);
    },
    (error) => showToast(error.message, true),
  );
}

async function loadCandidates() {
  return loadCandidatePage(
    state.candidateFilters.applied,
    state.candidatePagination.offset,
  );
}

async function applyCandidateFilters() {
  const draft = readCandidateFilterControls();
  state.candidateFilters.draft = { ...draft };
  const filters = normalizeCandidateFilters(draft);
  const validationMessage = candidateDateValidationMessage(filters);
  if (validationMessage) {
    showToast(validationMessage, true);
    return false;
  }
  return loadCandidatePage(filters, 0, true);
}
```

因为 `commitCandidatePage()` 只在 `await fetchCandidatePage()` 成功后调用，请求失败不会改变旧表格、计数、分页、已应用条件或 Excel URL。

- [ ] **Step 6: 让空状态和候选人采集时间使用已应用条件**

将渲染签名及空状态改为：

```javascript
function renderCandidates(items, filters = state.candidateFilters.applied) {
  const body = $("#candidateTableBody");
  body.replaceChildren();
  if (!items.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 9;
    cell.className = "empty-cell";
    cell.textContent = hasActiveCandidateFilters(filters)
      ? "当前筛选条件下暂无候选人"
      : "暂无候选人";
    row.append(cell);
    body.append(row);
    return;
  }
}
```

在现有非空行构造中只替换采集时间格式化调用：

```javascript
const latest = document.createElement("strong");
latest.textContent = formatCandidateTime(candidate.last_seen_at);
const first = document.createElement("small");
first.textContent = `首次 ${formatCandidateTime(candidate.first_seen_at)}`;
```

候选人详情中的三个采集来源时间字段改为：

```javascript
addDetail(details, "最近采集时间", formatCandidateTime(candidate.last_seen_at));
addDetail(details, "首次采集时间", formatCandidateTime(candidate.first_seen_at));
addDetail(details, "来源更新时间", formatCandidateTime(candidate.source_updated_at));
```

- [ ] **Step 7: 接入草稿控件、筛选和不提前变更的分页事件**

在 `bindEvents()` 中替换/增加：

```javascript
  $("#candidateDateRange").addEventListener("change", () => {
    const preset = $("#candidateDateRange").value;
    if (preset !== "custom") writeCandidateDateControls(candidateDateRangeForPreset(preset));
    syncCandidateDraft();
  });
  [$("#candidateDateFrom"), $("#candidateDateTo")].forEach((input) => {
    input.addEventListener("change", () => {
      $("#candidateDateRange").value = "custom";
      syncCandidateDraft();
    });
  });
  $("#candidateDateClearButton").addEventListener("click", () => {
    writeCandidateDateControls(candidateDateRangeForPreset("all"));
    syncCandidateDraft();
  });
  [
    $("#candidateStatus"),
    $("#candidateCity"),
    $("#candidateSource"),
    $("#candidateContactability"),
    $("#candidateContactStage"),
  ].forEach((control) => control.addEventListener("change", syncCandidateDraft));
  $("#candidateSearch").addEventListener("input", syncCandidateDraft);
  $("#candidateFilterButton").addEventListener("click", applyCandidateFilters);
  $("#candidateSearch").addEventListener("keydown", (event) => {
    if (event.key === "Enter") applyCandidateFilters();
  });
  $("#candidatePrevPage").addEventListener("click", () => {
    const target = Math.max(
      0,
      state.candidatePagination.offset - state.candidatePagination.limit,
    );
    loadCandidatePage(state.candidateFilters.applied, target);
  });
  $("#candidateNextPage").addEventListener("click", () => {
    const target = state.candidatePagination.offset + state.candidatePagination.limit;
    loadCandidatePage(state.candidateFilters.applied, target);
  });
```

删除旧的 `loadCandidates(true)` 事件和翻页前直接写 `pagination.offset` 的代码。

- [ ] **Step 8: 运行前端测试并确认 GREEN**

Run:

```bash
python3 -m unittest tests.test_core.FrontendStructureTests -v
node --test tests/test_candidate_date_filters.js
node --check static/candidate_date_filters.js
node --check static/app.js
```

Expected: PASS；两个语法检查无输出。

### Task 9: 全量验证和无真实候选人浏览器验收

**Files:**
- Verify: 所有本计划涉及的文件
- Modify: 仅当测试暴露缺陷时修改对应的最小生产文件及其测试

- [ ] **Step 1: 运行完整自动测试**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/test_candidate_date_filters.js
node --check static/candidate_date_filters.js
node --check static/app.js
```

Expected: 全部 PASS，无警告或语法错误。

- [ ] **Step 2: 启动使用临时空数据库的验证服务**

使用 `mktemp -d` 创建临时目录，并在同一独立终端会话中运行：

```bash
validation_dir="$(mktemp -d /private/tmp/ai-talent-radar-date-filter.XXXXXX)"
TALENT_RADAR_DB="$validation_dir/talent_radar.db" TALENT_RADAR_PORT=8876 python3 app.py
```

执行前确保目录是本任务新建的明确临时路径；不要连接或复制本机真实数据库。Expected: 输出 `AI Talent Radar running at http://127.0.0.1:8876`。

- [ ] **Step 3: 使用 Playwright 验证真实 UI 流程**

按照 `playwright` skill 操作 `http://127.0.0.1:8876/`，仅使用空数据库或路由注入的合成候选人。逐项验证：

```text
日期选项：全部、今天、本周、最近 7 天、最近 30 天、本月、自定义
日期边界：北京时间周一、月初和跨年测试时刻
应用规则：改动草稿后 Excel URL 不变，点击筛选成功后才更新
失败规则：模拟 400 后旧表格、总数、页码和 Excel URL 不变
空状态：有筛选时显示“当前筛选条件下暂无候选人”
分页规则：分页请求沿用已应用的绝对日期
布局：1440x900、1024x768、390x844 均无重叠、截断或横向溢出
下载：日期已应用时触发 .xlsx 下载
```

Expected: 所有检查通过，控制台无新增错误。只保存不包含个人信息的最小区域截图；若不需要交付截图，则验证后不保留截图。

- [ ] **Step 4: 停止验证服务并保留可审计结果**

向测试服务会话发送 Ctrl-C，确认端口 8876 不再响应。临时数据库只包含空数据或合成记录，不提交到 Git。

- [ ] **Step 5: 检查完整 diff 与敏感文件**

Run:

```bash
git diff --check
git status --short
git diff -- static/index.html static/styles.css static/app.js db.py app.py tests/test_core.py
sed -n '1,260p' static/candidate_date_filters.js
sed -n '1,240p' tests/test_candidate_date_filters.js
```

Expected: 无 whitespace 错误；显式审阅两个未跟踪新文件的完整内容；变更仅覆盖本计划和先前已存在的用户修改；`.env`、数据库、日志、导出文件、候选人信息和凭证均未新增到版本控制。

- [ ] **Step 6: 最终交付检查**

报告：功能行为、测试命令与结果、浏览器视口、未提交文件、任何既存失败，以及下一步是否同步到 GitHub。不得在用户未明确要求时发布、推送或创建 PR。
