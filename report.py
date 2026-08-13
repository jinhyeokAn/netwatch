import os
import smtplib
import threading
import time
from datetime import datetime
from email.mime.text import MIMEText

import models

SMTP_HOST = os.environ.get("EMAIL_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("EMAIL_SMTP_PORT", "587"))
EMAIL_FROM = os.environ.get("EMAIL_FROM", "")
EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD", "")
EMAIL_TO_DEFAULT = os.environ.get("EMAIL_TO", "")  # fallback when no DB setting saved yet


def get_email_to():
    """DB-saved recipient (set via the 설정 page) wins over the .env default."""
    return models.get_setting("email_to", EMAIL_TO_DEFAULT)

REPORT_ENABLED = os.environ.get("EMAIL_REPORT_ENABLED", "false").lower() == "true"
REPORT_HOUR = int(os.environ.get("EMAIL_REPORT_HOUR", "9"))


def build_report_text(days=1):
    stats = models.uptime_stats(days=days)
    lines = [f"NetWatch 관제 리포트 (최근 {days}일)", ""]

    if not stats:
        lines.append("등록된 장비가 없습니다.")
    for s in stats:
        d = s["device"]
        uptime = f"{s['uptime_pct']}%" if s["uptime_pct"] is not None else "데이터 없음"
        mttr = f"{s['mttr_minutes']}분" if s["mttr_minutes"] is not None else "-"
        lines.append(f"- {d['name']} ({d['ip']}): 가동률 {uptime}, 장애 {s['incident_count']}회, MTTR {mttr}")

    lines.append("")
    lines.append("최근 장애 이력 (최대 10건):")
    incidents = models.incident_history(limit=10)
    if not incidents:
        lines.append("없음")
    for i in incidents:
        lines.append(f"- {i['checked_at']} {i['name']} ({i['ip']}) DOWN")

    return "\n".join(lines)


def send_report_email(days=1):
    """Returns (ok: bool, message: str)."""
    email_to = get_email_to()
    if not (EMAIL_FROM and EMAIL_APP_PASSWORD and email_to):
        return False, "이메일 설정 안 됨 - .env에 EMAIL_FROM/EMAIL_APP_PASSWORD, '설정' 메뉴에 받는 주소 확인"

    body = build_report_text(days=days)
    msg = MIMEText(body)
    msg["Subject"] = f"[NetWatch] 관제 리포트 (최근 {days}일)"
    msg["From"] = EMAIL_FROM
    msg["To"] = email_to

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(EMAIL_FROM, EMAIL_APP_PASSWORD)
            server.send_message(msg)
        return True, f"{email_to}로 발송 완료"
    except Exception as exc:
        return False, f"발송 실패: {exc}"


def start_daily_report_scheduler():
    if not REPORT_ENABLED:
        return

    sent_dates = set()

    def loop():
        while True:
            now = datetime.now()
            today_key = now.date().isoformat()
            if now.hour == REPORT_HOUR and today_key not in sent_dates:
                send_report_email(days=1)
                sent_dates.add(today_key)
            time.sleep(60)

    threading.Thread(target=loop, daemon=True).start()
