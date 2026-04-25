import os
import json
import smtplib
import requests
import anthropic
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

ANTHROPIC_API_KEY   = os.environ["ANTHROPIC_API_KEY"]
GMAIL_USER          = os.environ["GMAIL_USER"]
GMAIL_APP_PASSWORD  = os.environ["GMAIL_APP_PASSWORD"]
ALERT_EMAIL         = os.environ["ALERT_EMAIL"]
SITE_URL            = os.environ["SITE_URL"]

RANK_DROP_THRESHOLD  = 3
SLOW_RESPONSE_MS     = 3000
LCP_THRESHOLD_SEC    = 2.5

def check_uptime(url):
    try:
        resp = requests.get(url, timeout=10)
        return {"status": "up" if resp.status_code < 400 else "error",
                "status_code": resp.status_code,
                "response_ms": round(resp.elapsed.total_seconds() * 1000)}
    except requests.exceptions.Timeout:
        return {"status": "timeout", "status_code": None, "response_ms": None}
    except Exception as e:
        return {"status": "down", "status_code": None, "response_ms": None, "error": str(e)}

def check_pagespeed(url):
    try:
        resp = requests.get(
            "https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
            params={"url": url, "strategy": "mobile"}, timeout=30)
        data = resp.json()
        audits = data.get("lighthouseResult", {}).get("audits", {})
        cats   = data.get("lighthouseResult", {}).get("categories", {})
        return {
            "performance_score": round((cats.get("performance", {}).get("score", 0) or 0) * 100),
            "lcp_sec": round(audits.get("largest-contentful-paint", {}).get("numericValue", 0) / 1000, 2),
            "cls":     round(audits.get("cumulative-layout-shift", {}).get("numericValue", 0), 3),
            "tbt_ms":  round(audits.get("total-blocking-time", {}).get("numericValue", 0)),
            "fcp_sec": round(audits.get("first-contentful-paint", {}).get("numericValue", 0) / 1000, 2),
        }
    except Exception as e:
        return {"error": str(e)}

def analyze_with_claude(uptime, pagespeed, site_url):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    data_summary = {
        "site": site_url,
        "checked_at": datetime.utcnow().isoformat() + "Z",
        "uptime": uptime,
        "core_web_vitals": pagespeed,
        "thresholds": {
            "slow_response_alert_ms": SLOW_RESPONSE_MS,
            "lcp_alert_if_above_sec": LCP_THRESHOLD_SEC,
        }
    }
    prompt = f"""Analyze this SEO monitoring data and respond with JSON only:
{{
  "should_alert": true or false,
  "alert_level": "critical" or "warning" or "ok",
  "subject": "short email subject (max 60 chars)",
  "summary": "2-3 sentence plain english summary",
  "issues": [
    {{"severity": "critical or warning", "message": "specific issue", "action": "what to do"}}
  ],
  "wins": ["any positive metrics"],
  "next_check": "no action needed or monitor closely or fix urgently"
}}

Data:
{json.dumps(data_summary, indent=2)}

Rules:
- should_alert = true if site is down, response > {SLOW_RESPONSE_MS}ms, or LCP > {LCP_THRESHOLD_SEC}s
- Be specific with actual numbers"""

    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())

def build_email_html(analysis, uptime, pagespeed, site_url):
    level_colors = {"critical": "#dc2626", "warning": "#d97706", "ok": "#16a34a"}
    level = analysis.get("alert_level", "ok")
    color = level_colors.get(level, "#16a34a")
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    issues_html = ""
    for issue in analysis.get("issues", []):
        sev_color = level_colors.get(issue.get("severity", "ok"), "#16a34a")
        issues_html += f"<tr><td style='padding:8px 12px;border-bottom:1px solid #f3f4f6'><span style='color:{sev_color};font-weight:600'>{issue.get('severity','').upper()}</span> — {issue.get('message','')}<br><small style='color:#6b7280'>Action: {issue.get('action','')}</small></td></tr>"

    wins_html = "".join(f"<li style='color:#166534;margin-bottom:4px'>{w}</li>" for w in analysis.get("wins", []))

    ps = pagespeed if "error" not in pagespeed else {}
    up = uptime.get("status", "unknown")
    up_color = "#16a34a" if up == "up" else "#dc2626"
    resp_ms = uptime.get("response_ms")
    resp_str = f"{resp_ms}ms" if resp_ms else "N/A"
    resp_color = "#16a34a" if (resp_ms and resp_ms < SLOW_RESPONSE_MS) else "#d97706"

    return f"""<!DOCTYPE html><html><body style="margin:0;padding:0;background:#f9fafb;font-family:sans-serif">
<div style="max-width:600px;margin:24px auto;background:#fff;border-radius:12px;border:1px solid #e5e7eb;overflow:hidden">
  <div style="background:{color};padding:20px 24px">
    <div style="color:#fff;font-size:11px;font-weight:600;opacity:.85">SEO AGENT REPORT</div>
    <div style="color:#fff;font-size:22px;font-weight:700;margin-top:4px">{analysis.get('subject','SEO Status Update')}</div>
    <div style="color:#fff;font-size:13px;opacity:.8;margin-top:4px">{site_url} · {now}</div>
  </div>
  <div style="padding:20px 24px;border-bottom:1px solid #f3f4f6">
    <p style="margin:0;color:#374151;line-height:1.6">{analysis.get('summary','')}</p>
  </div>
  <div style="padding:16px 24px;display:flex;gap:12px;border-bottom:1px solid #f3f4f6;flex-wrap:wrap">
    <div style="flex:1;min-width:100px;background:#f9fafb;border-radius:8px;padding:12px">
      <div style="font-size:11px;color:#6b7280;font-weight:600">UPTIME</div>
      <div style="font-size:20px;font-weight:700;color:{up_color}">{up.upper()}</div>
    </div>
    <div style="flex:1;min-width:100px;background:#f9fafb;border-radius:8px;padding:12px">
      <div style="font-size:11px;color:#6b7280;font-weight:600">RESPONSE</div>
      <div style="font-size:20px;font-weight:700;color:{resp_color}">{resp_str}</div>
    </div>
    <div style="flex:1;min-width:100px;background:#f9fafb;border-radius:8px;padding:12px">
      <div style="font-size:11px;color:#6b7280;font-weight:600">PERF SCORE</div>
      <div style="font-size:20px;font-weight:700;color:#111827">{ps.get('performance_score','N/A')}</div>
    </div>
    <div style="flex:1;min-width:100px;background:#f9fafb;border-radius:8px;padding:12px">
      <div style="font-size:11px;color:#6b7280;font-weight:600">LCP</div>
      <div style="font-size:20px;font-weight:700;color:#111827">{ps.get('lcp_sec','N/A')}s</div>
    </div>
  </div>
  {'<div style="padding:16px 24px;border-bottom:1px solid #f3f4f6"><div style="font-size:13px;font-weight:600;margin-bottom:8px">Issues Found</div><table style="width:100%;border-collapse:collapse">' + issues_html + '</table></div>' if issues_html else ''}
  {'<div style="padding:16px 24px;border-bottom:1px solid #f3f4f6"><div style="font-size:13px;font-weight:600;margin-bottom:8px">Wins</div><ul style="margin:0;padding-left:18px">' + wins_html + '</ul></div>' if wins_html else ''}
  <div style="padding:16px 24px;background:#f9fafb">
    <div style="font-size:12px;color:#6b7280">Next action: <strong style="color:#111827">{analysis.get('next_check','').title()}</strong></div>
    <div style="font-size:12px;color:#9ca3af;margin-top:4px">Powered by Claude AI · GitHub Actions · Free SEO Agent</div>
  </div>
</div></body></html>"""

def send_email(subject, html_body, is_alert=False):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{'🚨 ' if is_alert else '📊 '}[SEO Agent] {subject}"
    msg["From"]    = GMAIL_USER
    msg["To"]      = ALERT_EMAIL
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, ALERT_EMAIL, msg.as_string())
    print(f"Email sent: {subject}")

def run():
    print(f"SEO Agent starting at {datetime.utcnow().isoformat()}Z")
    print(f"Site: {SITE_URL}")

    print("Checking uptime...")
    uptime = check_uptime(SITE_URL)
    print(f"  Status: {uptime['status']} | Response: {uptime.get('response_ms')}ms")

    print("Checking PageSpeed...")
    pagespeed = check_pagespeed(SITE_URL)
    if "error" not in pagespeed:
        print(f"  Performance: {pagespeed.get('performance_score')} | LCP: {pagespeed.get('lcp_sec')}s")
    else:
        print(f"  PageSpeed error: {pagespeed['error']}")

    print("Analyzing with Claude AI...")
    analysis = analyze_with_claude(uptime, pagespeed, SITE_URL)
    print(f"  Alert level: {analysis.get('alert_level')} | Should alert: {analysis.get('should_alert')}")

    html = build_email_html(analysis, uptime, pagespeed, SITE_URL)
    send_email(analysis.get("subject", "SEO Status Update"), html, analysis.get("should_alert", False))
    print("Done!")

if __name__ == "__main__":
    run()
