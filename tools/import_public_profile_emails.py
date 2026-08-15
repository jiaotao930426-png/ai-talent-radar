"""Re-scan existing GitHub/Gitee profiles for one explicit public email.

The command only updates the local SQLite database and prints aggregate counts.
It does not print email addresses or candidate profile contents.
"""

import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Tuple


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import collectors
import db


def scan_profile(row: Any) -> Tuple[Any, str, str]:
    try:
        email = collectors.public_profile_email(row["profile_url"], timeout=10)
        return row, email, "updated" if email else "no_email"
    except collectors.CollectorError:
        return row, "", "failed"


def main() -> None:
    db.init_db()
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, source, profile_url
            FROM candidates
            WHERE archived_at IS NULL AND source IN ('github', 'gitee')
            ORDER BY id
            """
        ).fetchall()

    result: Dict[str, Any] = {
        "total": len(rows),
        "updated": 0,
        "no_email": 0,
        "ambiguous_shared_email": 0,
        "failed": 0,
    }
    by_source: Dict[str, Dict[str, int]] = {}
    for row in rows:
        by_source.setdefault(
            row["source"],
            {"total": 0, "updated": 0, "no_email": 0, "ambiguous_shared_email": 0, "failed": 0},
        )["total"] += 1

    scans = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(scan_profile, row) for row in rows]
        for future in as_completed(futures):
            scans.append(future.result())

    email_counts = Counter(email for _row, email, status in scans if status == "updated")
    for row, email, status in scans:
        source_stats = by_source[row["source"]]
        if status == "updated":
            if email_counts[email] > 1:
                result["ambiguous_shared_email"] += 1
                source_stats["ambiguous_shared_email"] += 1
            else:
                db.set_public_email(int(row["id"]), email, row["profile_url"])
                result["updated"] += 1
                source_stats["updated"] += 1
        elif status == "no_email":
            result["no_email"] += 1
            source_stats["no_email"] += 1
        else:
            result["failed"] += 1
            source_stats["failed"] += 1

    result["by_source"] = by_source
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
