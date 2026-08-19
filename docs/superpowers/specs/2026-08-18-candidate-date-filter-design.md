# Candidate Recent-Collection Date Filter Design

Status: Draft for written-spec approval
Date: 2026-08-18
Scope: Talent-pool page, candidate-list API, and candidate Excel export

## 1. Goal

Let recruiters filter talent-pool candidates by the date on which each candidate was most recently collected. The result must be accurate across pagination and must use Beijing time (UTC+08:00).

The feature must provide both:

- quick ranges: All, Today, This week, Last 7 days, Last 30 days, and This month;
- a custom inclusive start and end date.

## 2. Confirmed Product Decisions

- The filter uses `candidates.last_seen_at`, not first collection time or source update time.
- The default is All, so the existing candidate list is unchanged until a date filter is applied.
- All date semantics use Beijing time (UTC+08:00), independent of the host computer's timezone.
- Candidate first-collection and recent-collection timestamps are displayed in Beijing time so the visible date agrees with the filter result even when the browser runs in another timezone.
- Both the start date and end date include the complete selected calendar day.
- The candidate list, result count, pagination, and Excel export all apply the same selected date range.
- The date filter combines with all existing talent-pool filters using logical AND.
- No candidate-table migration is required because `last_seen_at` already exists.

## 3. Non-Goals

- Filtering by `first_seen_at`, `source_updated_at`, or database insertion time.
- Filtering archived candidates in the talent-pool view.
- Changing candidate matching, AI analysis, collection rules, or scheduled jobs.
- Changing the behavior of the HTML report.
- Making all existing non-date talent-pool filters affect Excel export. This feature only guarantees that Excel export inherits the active date range; other export behavior remains unchanged.

## 4. Current System Context

- `static/index.html` contains the talent-pool filter bar and the global Excel export link.
- `static/app.js` builds the `/api/candidates` query and manages candidate pagination.
- `app.py` forwards candidate-list query parameters and serves `/export/candidates.xlsx`.
- `db.py` applies parameterized candidate filters in `list_candidates()` and currently exports all active candidates through `export_candidates()`.
- Candidate rows already display both `last_seen_at` and `first_seen_at`.

The existing filter bar is dense on desktop, so the date controls should use a dedicated second row rather than compressing the current controls.

## 5. User Interface Design

### 5.1 Controls

Add a date-filter group below the existing talent-pool filters:

- `Date range` select menu with: All, Today, This week, Last 7 days, Last 30 days, This month, Custom.
- `Start date` native date input.
- `End date` native date input.
- `Clear dates` button.
- Reuse the existing `Filter` button to apply both existing filters and date controls.

Stable control identifiers should be used so UI tests can target them, for example:

- `candidateDateRange`
- `candidateDateFrom`
- `candidateDateTo`
- `candidateDateClearButton`
- `candidateExportButton`

### 5.2 Quick-Range Semantics

Quick ranges are calculated from the current Beijing calendar date:

- All: both inputs are empty.
- Today: start and end are today.
- This week: Monday of the current week through today.
- Last 7 days: today and the preceding six calendar days.
- Last 30 days: today and the preceding 29 calendar days.
- This month: the first day of the current month through today.

Selecting a quick range updates both date inputs. Manually editing either date changes the range select to Custom. Either custom endpoint may be empty:

- start only means that date and later;
- end only means that date and earlier.

Clicking Clear dates resets the select to All and empties both inputs. The user then applies the change with the existing Filter button, consistent with current filter behavior.

If Custom is selected while both date inputs are empty, treat it as All and return the select to All when the filter is successfully applied.

### 5.3 Visible States

- While a date range is active, pagination retains that range.
- Candidate `最近采集` and `首次采集` values in the list and detail dialog use explicit `Asia/Shanghai` formatting; unrelated job and system timestamps are outside this feature's scope.
- If any filter is active and no candidate matches, show `当前筛选条件下暂无候选人`; retain the generic empty state when no filter is active.
- If the server rejects the range, preserve the current table and show the returned validation message as an error toast.
- Controls remain one column wide on small screens and must not overflow or overlap.

## 6. API Contract

### 6.1 Candidate List

Extend `GET /api/candidates` with optional query parameters:

- `last_seen_from=YYYY-MM-DD`
- `last_seen_to=YYYY-MM-DD`

Omitted or empty values mean no boundary. Existing parameters remain unchanged.

Examples:

```text
/api/candidates?last_seen_from=2026-08-12&last_seen_to=2026-08-18&limit=50&offset=0
/api/candidates?last_seen_from=2026-08-18
/api/candidates?last_seen_to=2026-08-18
```

### 6.2 Excel Export

Extend `GET /export/candidates.xlsx` with the same optional parameters:

```text
/export/candidates.xlsx?last_seen_from=2026-08-12&last_seen_to=2026-08-18
```

When the user activates the export button, its download URL must be generated from the currently applied date range. Editing date controls without clicking Filter must not silently change the export range.

### 6.3 Validation and Errors

The server is authoritative and must validate both routes:

- accept only the exact `YYYY-MM-DD` shape and a real calendar date;
- accept ASCII digits only, reject surrounding whitespace, and support dates from `2000-01-01` through `2100-12-31`;
- reject a start date later than the end date;
- return HTTP 400 with a concise Chinese message for invalid input;
- never interpolate query values into SQL.

Recommended messages:

- `开始日期格式无效，请使用 YYYY-MM-DD`
- `结束日期格式无效，请使用 YYYY-MM-DD`
- `开始日期不能晚于结束日期`
- `日期必须在 2000-01-01 至 2100-12-31 之间`

## 7. Date and Database Semantics

Convert calendar dates into Beijing-time boundaries on the server:

- start: selected date at `00:00:00+08:00`, inclusive;
- end: the day after the selected end date at `00:00:00+08:00`, exclusive.

Conceptually:

```sql
julianday(last_seen_at) >= julianday(?)
julianday(last_seen_at) < julianday(?)
```

The end-exclusive form avoids fragile `23:59:59` logic and includes timestamps with fractional seconds. Using SQLite time conversion also keeps comparisons correct if an existing timestamp carries a different UTC offset.

All values remain bound parameters. At the expected collection rate, an expression-based scan is acceptable and avoids a schema change. Indexing can be revisited only if measured candidate volume makes this query slow.

## 8. Data Flow

1. The user selects a quick range or enters custom dates; these values remain draft state.
2. Clicking Filter validates obvious client-side ordering and sends the draft range with a requested offset of zero, without changing the currently applied state yet.
3. After a successful response, the page commits the applied range, resets the stored offset, updates rows/count/pagination, and rebuilds the Excel export URL.
4. If the request fails, the previous applied range, offset, total, rows, and export URL remain unchanged.
5. `app.py` passes the query to the database layer.
6. `db.list_candidates()` validates the range, adds parameterized clauses, and applies the same clauses to count and item queries.
7. Pagination reuses the committed applied range rather than rereading unsubmitted date inputs.
8. Excel export uses the committed applied date range, and `db.export_candidates()` applies equivalent date clauses to both candidate and evidence selection.

## 9. Failure and Edge Cases

- Invalid or impossible dates return HTTP 400; the browser displays the message.
- Out-of-range dates return HTTP 400 rather than overflowing into an HTTP 500 response.
- A reversed range is rejected rather than silently swapped.
- A start-only or end-only range is valid.
- Candidate timestamps exactly at the start boundary are included.
- Candidate timestamps exactly at the next-day end boundary are excluded.
- Equivalent timestamps expressed with another UTC offset are compared by instant, not raw text.
- Empty results do not reset the date controls.
- Moving between pages does not clear or recalculate a relative quick range during the same applied filter session.
- Export uses the last applied range, preventing a mismatch caused by unsubmitted form edits.
- A failed filter request preserves the prior page, offset, total, applied range, and export URL.
- Custom with both endpoints empty is equivalent to All.

## 10. Acceptance Checks

### Automated

- Database tests cover start-only, end-only, two-sided, exact boundaries, another UTC offset, invalid format, impossible date, and reversed range.
- Database tests confirm date filters combine with city/status/source/contact filters and active-only behavior.
- API tests confirm valid queries return 200 and invalid or out-of-range queries return 400 on both `/api/candidates` and `/export/candidates.xlsx`.
- Export tests confirm candidates and evidence outside the selected range are excluded and an empty result still produces a valid zero-row workbook.
- Frontend tests confirm the controls, query parameters, pending-versus-applied state, failed-request preservation, Beijing timestamp display, and date-aware export URL.
- Quick-range tests use an injected/frozen instant and cover a Beijing day boundary, Monday/week behavior, and month/year rollover.

### Browser

- All six quick options produce the expected Beijing dates.
- Custom one-sided and two-sided ranges produce the correct count and rows.
- Filtered pagination remains stable.
- Clear dates restores the full active talent pool.
- Excel download contains exactly the candidates in the applied date range.
- Desktop and mobile layouts have no overlap, clipped text, or horizontal control overflow.
- Invalid and empty-result states are understandable.
- A rejected request leaves the previously displayed result, page, and export range unchanged.

## 11. Implementation Boundaries

Expected implementation files:

- `static/index.html`
- `static/styles.css`
- `static/app.js`
- `app.py`
- `db.py`
- focused tests under `tests/`

Existing unrelated working-tree changes must be preserved. No candidate records, credentials, environment files, or local database contents are needed to implement or test this feature.
