import html
from typing import Any, Dict, List

from contactability import (
    MATCH_HIGH_SCORE,
    MATCH_MEDIUM_SCORE,
    contact_label,
    derive_contact_level,
)


SOURCE_NAMES = {
    "github": "GitHub",
    "gitee": "Gitee",
    "gitlab": "GitLab",
    "huggingface": "Hugging Face",
    "stackoverflow": "Stack Overflow",
}


def safe_url(value: str) -> str:
    value = (value or "").strip()
    if value.startswith("https://") or value.startswith("http://"):
        return html.escape(value, quote=True)
    return "#"


def display_timestamp(value: Any) -> str:
    text = str(value or "尚未采集").strip()
    return html.escape(text.replace("T", " ", 1))


def contact_actions(candidate: Dict[str, Any]) -> str:
    actions = []
    level = str(candidate.get("contact_level") or derive_contact_level(candidate)).upper()
    level_text = "{}级 · {}".format(level, contact_label(level))
    email = (candidate.get("contact_email") or "").strip()
    if email:
        escaped_email = html.escape(email, quote=True)
        actions.append('<a class="button primary" href="mailto:{}">邮件</a>'.format(escaped_email))
    profile_url = (candidate.get("profile_url") or "").strip()
    if profile_url:
        actions.append(
            '<a class="button" target="_blank" rel="noopener noreferrer" href="{}">公开主页 ↗</a>'.format(
                safe_url(profile_url)
            )
        )
    contact_url = (candidate.get("contact_url") or "").strip()
    if contact_url and contact_url != profile_url:
        actions.append(
            '<a class="button" target="_blank" rel="noopener noreferrer" href="{}">其他公开入口 ↗</a>'.format(
                safe_url(contact_url)
            )
        )
    email_text = '<span class="email">{}</span>'.format(html.escape(email)) if email else ""
    return '<span class="contact-grade level-{level}">{level_text}</span><div class="actions">{actions}</div>{email}'.format(
        level=html.escape(level.lower()),
        level_text=html.escape(level_text),
        actions="".join(actions),
        email=email_text,
    )


def generate_report(candidates: List[Dict[str, Any]]) -> bytes:
    rows = []
    for candidate in candidates:
        score = int(candidate["match_score"])
        if score >= MATCH_HIGH_SCORE:
            match_class = "match-high"
            match_level = "高匹配"
        elif score >= MATCH_MEDIUM_SCORE:
            match_class = "match-medium"
            match_level = "中匹配"
        else:
            match_class = "match-review"
            match_level = "待核验"
        rows.append(
            """
            <tr>
              <td><span class="match-level {match_class}">{match_level}</span></td>
              <td><strong>{display_name}</strong><br><span class="muted">{username}</span></td>
              <td><a class="button" target="_blank" rel="noopener noreferrer" href="{profile_url}">{source} ↗</a></td>
              <td>{city}</td>
              <td>{role}</td>
              <td>{score}/100</td>
              <td>{last_seen_at}</td>
              <td>{bio}</td>
              <td>{company}</td>
              <td>{education_verification}</td>
              <td>{age_status}</td>
              <td>{work_location_status}</td>
              <td>{agent_experience_status}</td>
              <td>{contact_stage}<br><span class="muted">{contact_updated_at}</span></td>
              <td>{contact}</td>
              <td>{review_status}<br><span class="muted">{review_note}</span></td>
            </tr>
            """.format(
                match_class=match_class,
                match_level=match_level,
                display_name=html.escape(candidate["display_name"]),
                username=html.escape(candidate["username"]),
                profile_url=safe_url(candidate["profile_url"]),
                source=html.escape(SOURCE_NAMES.get(candidate["source"], candidate["source"].upper())),
                city=html.escape(candidate["city"]),
                role=html.escape(candidate["suggested_role"]),
                score=int(candidate["match_score"]),
                last_seen_at=display_timestamp(candidate.get("last_seen_at")),
                bio=html.escape((candidate.get("bio") or "无公开简介")[:500]),
                company=html.escape(candidate.get("company") or "待核验"),
                education_verification=html.escape(candidate.get("education_verification") or "待本人确认"),
                age_status=html.escape(candidate.get("age_status") or "待本人确认"),
                work_location_status=html.escape(candidate.get("work_location_status") or "待本人确认"),
                agent_experience_status=html.escape(candidate.get("agent_experience_status") or "待人工核验"),
                contact_stage=html.escape(candidate.get("contact_stage") or "未联系"),
                contact_updated_at=html.escape(candidate.get("contact_updated_at") or "尚未更新"),
                contact=contact_actions(candidate),
                review_status=html.escape(candidate["review_status"]),
                review_note=html.escape(candidate.get("review_note") or "无备注"),
            )
        )

    document = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 人才周报</title>
<style>
:root{{--bg:#f5f7f8;--surface:#fff;--text:#172126;--muted:#68767e;--line:#d9e0e3;--accent:#087f73;--warm:#a85612}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
main{{width:min(1480px,calc(100% - 28px));margin:auto;padding:30px 0 50px}}h1{{font-size:26px;margin:0 0 6px;letter-spacing:0}}.meta,.muted{{color:var(--muted)}}
.table-wrap{{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:6px;margin-top:22px}}table{{width:100%;min-width:2100px;border-collapse:collapse}}th,td{{padding:12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{background:#edf2f3;white-space:nowrap;font-size:13px}}tr:last-child td{{border:0}}
.match-level,.contact-grade{{display:inline-flex;min-height:27px;align-items:center;padding:3px 7px;border-radius:4px;font-size:12px;font-weight:700;white-space:nowrap}}.match-high,.level-a{{background:#eaf8f4;color:#08665d}}.match-medium,.level-b{{background:#fff3df;color:var(--warm)}}.match-review,.level-c,.level-d{{background:#edf2f3;color:#53646c}}
.actions{{display:flex;gap:6px;flex-wrap:wrap}}a.button{{display:inline-flex;min-height:33px;align-items:center;padding:5px 9px;border:1px solid var(--accent);border-radius:5px;color:var(--accent);background:#fff;text-decoration:none;font-weight:600;white-space:nowrap}}a.primary{{color:#fff;background:var(--accent)}}.email{{display:block;margin-top:5px;color:var(--muted);font-size:12px}}
.contact-grade{{margin-bottom:7px}}
@media(max-width:760px){{main{{width:calc(100% - 18px)}}h1{{font-size:22px}}}}
</style></head><body><main>
<h1>北京 / 重庆 AI 人才周报</h1><p class="meta">由 AI 人才雷达本地生成 · 共 {count} 人 · 联系前请再次核验公开资料</p>
<div class="table-wrap"><table><thead><tr><th>匹配等级</th><th>候选人</th><th>来源</th><th>城市</th><th>建议岗位</th><th>评分</th><th>最近采集</th><th>公开简介</th><th>单位/院校</th><th>学历核验</th><th>年龄核验</th><th>工作地点</th><th>Agent 项目</th><th>联系进度</th><th>联系入口</th><th>审核状态/备注</th></tr></thead>
<tbody>{rows}</tbody></table></div>
</main></body></html>""".format(count=len(candidates), rows="".join(rows))
    return document.encode("utf-8")
