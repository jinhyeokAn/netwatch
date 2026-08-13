import ipaddress
import os

from dotenv import load_dotenv

load_dotenv()  # must run before importing modules that read env vars at import time

from flask import Flask, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import models
import monitor
import report
from monitor import CHECK_INTERVAL_SEC, start_background_monitor

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")


@app.before_request
def require_login():
    if request.endpoint in ("login", "static") or request.endpoint is None:
        return
    if "username" not in session:
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        user = models.get_user_by_username(username)
        if user and check_password_hash(user["password_hash"], password):
            session["username"] = username
            models.log_action(username, "로그인", "")
            return redirect(url_for("dashboard"))
        flash("아이디 또는 비밀번호가 틀렸습니다.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    username = session.pop("username", None)
    if username:
        models.log_action(username, "로그아웃", "")
    return redirect(url_for("login"))


@app.route("/users", methods=["GET", "POST"])
def users():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        if not username or not password:
            flash("아이디/비밀번호를 입력하세요.")
        elif models.get_user_by_username(username):
            flash(f"이미 존재하는 아이디입니다: {username}")
        else:
            models.create_user(username, generate_password_hash(password))
            models.log_action(session["username"], "사용자 추가", username)
            flash(f"사용자 추가됨: {username}")
        return redirect(url_for("users"))
    return render_template("users.html", users=models.list_users())


@app.route("/users/<int:user_id>/delete", methods=["POST"])
def delete_user(user_id):
    target = models.get_user_by_id(user_id)
    if not target:
        flash("존재하지 않는 사용자입니다.")
    elif models.count_users() <= 1:
        flash("마지막 남은 계정은 삭제할 수 없습니다.")
    else:
        models.delete_user(user_id)
        models.log_action(session["username"], "사용자 삭제", target["username"])
        flash(f"사용자 삭제됨: {target['username']}")
    return redirect(url_for("users"))


@app.route("/audit")
def audit():
    return render_template("audit.html", logs=models.list_audit_log())


@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        email_to = request.form.get("email_to", "").strip()
        models.set_setting("email_to", email_to)
        models.log_action(session["username"], "설정 변경", f"email_to={email_to}")
        flash("설정 저장됨")
        return redirect(url_for("settings"))

    return render_template("settings.html", email_to=report.get_email_to())


@app.route("/")
def dashboard():
    devices = models.list_devices()
    latest = models.latest_status_by_device()
    under_maintenance = models.devices_under_maintenance()

    device_rows = []
    for d in devices:
        status = latest.get(d["id"])
        device_rows.append(
            {
                "device": d,
                "is_up": bool(status["is_up"]) if status else None,
                "latency_ms": status["latency_ms"] if status else None,
                "checked_at": status["checked_at"] if status else None,
                "under_maintenance": d["id"] in under_maintenance,
                "flapping": monitor.is_flapping(d["id"]),
                "latency_anomaly": monitor.is_latency_anomaly(d["id"]),
                "ssl_days_left": monitor.ssl_days_left(d["id"]),
            }
        )

    up_count = sum(1 for r in device_rows if r["is_up"])
    down_count = sum(1 for r in device_rows if r["is_up"] is False)
    latencies = [r["latency_ms"] for r in device_rows if r["latency_ms"] is not None]
    avg_latency = round(sum(latencies) / len(latencies), 1) if latencies else None
    uptime_pct = round(up_count / len(device_rows) * 100, 1) if device_rows else None

    return render_template(
        "dashboard.html",
        rows=device_rows,
        refresh_sec=CHECK_INTERVAL_SEC,
        up_count=up_count,
        down_count=down_count,
        avg_latency=avg_latency,
        uptime_pct=uptime_pct,
    )


@app.route("/devices", methods=["GET", "POST"])
def devices():
    if request.method == "POST":
        models.add_device(
            name=request.form["name"],
            ip=request.form["ip"],
            subnet=request.form.get("subnet", ""),
            location=request.form.get("location", ""),
            owner=request.form.get("owner", ""),
            ssl_host=request.form.get("ssl_host", ""),
        )
        models.log_action(session["username"], "장비 추가", f"{request.form['name']} ({request.form['ip']})")
        return redirect(url_for("devices"))

    return render_template("devices.html", devices=models.list_devices())


@app.route("/devices/<int:device_id>/edit", methods=["GET", "POST"])
def edit_device(device_id):
    if request.method == "POST":
        models.update_device(
            device_id,
            name=request.form["name"],
            ip=request.form["ip"],
            subnet=request.form.get("subnet", ""),
            location=request.form.get("location", ""),
            owner=request.form.get("owner", ""),
            ssl_host=request.form.get("ssl_host", ""),
        )
        models.log_action(session["username"], "장비 수정", f"id={device_id} {request.form['name']}")
        return redirect(url_for("devices"))

    device = models.get_device(device_id)
    return render_template("device_edit.html", device=device)


@app.route("/devices/<int:device_id>/delete", methods=["POST"])
def delete_device(device_id):
    device = models.get_device(device_id)
    models.delete_device(device_id)
    if device:
        models.log_action(session["username"], "장비 삭제", f"{device['name']} ({device['ip']})")
    return redirect(url_for("devices"))


@app.route("/incidents")
def incidents():
    search = request.args.get("q", "").strip()
    return render_template("incidents.html", incidents=models.incident_history(search=search), q=search)


@app.route("/maintenance", methods=["GET", "POST"])
def maintenance():
    if request.method == "POST":
        models.add_maintenance(
            device_id=request.form["device_id"],
            title=request.form["title"],
            start_at=request.form["start_at"],
            end_at=request.form["end_at"],
            note=request.form.get("note", ""),
        )
        models.log_action(session["username"], "작업일정 추가", request.form["title"])
        return redirect(url_for("maintenance"))

    return render_template(
        "maintenance.html",
        windows=models.list_maintenance(),
        devices=models.list_devices(),
    )


@app.route("/maintenance/<int:window_id>/edit", methods=["GET", "POST"])
def edit_maintenance(window_id):
    if request.method == "POST":
        models.update_maintenance(
            window_id,
            device_id=request.form["device_id"],
            title=request.form["title"],
            start_at=request.form["start_at"],
            end_at=request.form["end_at"],
            note=request.form.get("note", ""),
        )
        models.log_action(session["username"], "작업일정 수정", f"id={window_id} {request.form['title']}")
        return redirect(url_for("maintenance"))

    window = models.get_maintenance(window_id)
    return render_template("maintenance_edit.html", window=window, devices=models.list_devices())


@app.route("/stats")
def stats():
    days = request.args.get("days", "30")
    days = int(days) if days.isdigit() else 30
    return render_template("stats.html", stats=models.uptime_stats(days=days), days=days)


@app.route("/stats/send-report", methods=["POST"])
def send_report():
    days = int(request.form.get("days", "1"))
    ok, message = report.send_report_email(days=days)
    models.log_action(session["username"], "리포트 수동발송", f"{days}일 - {message}")
    flash(message)
    return redirect(url_for("stats"))


@app.route("/tools/subnet", methods=["GET", "POST"])
def subnet_tool():
    result = None
    error = None
    cidr_input = request.form.get("cidr", "").strip() if request.method == "POST" else ""

    if request.method == "POST":
        try:
            network = ipaddress.ip_network(cidr_input, strict=False)
            hosts = list(network.hosts())
            result = {
                "network": str(network.network_address),
                "broadcast": str(network.broadcast_address),
                "netmask": str(network.netmask),
                "prefixlen": network.prefixlen,
                "total_addresses": network.num_addresses,
                "usable_hosts": len(hosts),
                "first_host": str(hosts[0]) if hosts else "-",
                "last_host": str(hosts[-1]) if hosts else "-",
            }
        except ValueError as exc:
            error = str(exc)

    return render_template("subnet.html", result=result, error=error, cidr_input=cidr_input)


models.init_db()

admin_username = os.environ.get("ADMIN_USERNAME", "admin")
admin_password = os.environ.get("ADMIN_PASSWORD", "changeme123")
models.ensure_default_admin(admin_username, generate_password_hash(admin_password))

start_background_monitor()
report.start_daily_report_scheduler()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
