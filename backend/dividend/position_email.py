"""Small SMTP sender and renderers for a completed dividend position report."""
from __future__ import annotations

import html
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any


GRADE_ORDER = {"S": 0, "A": 1, "B": 2, None: 3}
POSITION_HINTS = {"S": "目标仓位上限约 10%", "A": "目标仓位上限约 5%", "B": "目标仓位上限约 2%"}


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    username: str
    password: str
    email_from: str
    email_to: str

    @classmethod
    def from_env(cls) -> "SmtpSettings":
        values = {name: os.getenv(name, "").strip() for name in (
            "DIVIDEND_SMTP_HOST", "DIVIDEND_SMTP_PORT", "DIVIDEND_SMTP_USERNAME",
            "DIVIDEND_SMTP_PASSWORD", "DIVIDEND_EMAIL_FROM", "DIVIDEND_EMAIL_TO",
        )}
        for name, value in values.items():
            if not value:
                raise ValueError(f"{name} not configured")
        try:
            port = int(values["DIVIDEND_SMTP_PORT"])
        except ValueError as exc:
            raise ValueError("DIVIDEND_SMTP_PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise ValueError("DIVIDEND_SMTP_PORT must be between 1 and 65535")
        return cls(values["DIVIDEND_SMTP_HOST"], port, values["DIVIDEND_SMTP_USERNAME"],
                   values["DIVIDEND_SMTP_PASSWORD"], values["DIVIDEND_EMAIL_FROM"], values["DIVIDEND_EMAIL_TO"])


def _items(report: dict[str, Any], status: str) -> list[dict[str, Any]]:
    return sorted(report.get(status, []), key=lambda item: (
        GRADE_ORDER[item.get("grade")], -float(item["three_year_average_yield_pct"]), item["symbol"]
    ))


def email_subject(report: dict[str, Any]) -> str:
    return f"【AuroraAI 红利机会】{len(report.get('heavy', []))}只重仓 / {len(report.get('add', []))}只加仓"


def _value(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}%"


def _table(items: list[dict[str, Any]], status: str) -> str:
    badge = "#c0392b" if status == "heavy" else "#d97706"
    rows = []
    for item in items:
        grade = item.get("grade") or "未设置"
        hint = POSITION_HINTS.get(item.get("grade"), "")
        rows.append(
            "<tr><td><span style='background:%s;color:#fff;border-radius:10px;padding:2px 7px'>%s</span></td>"
            "<td>%s</td><td>%s<br><span style='color:#666'>%s</span></td><td>%.2f</td><td>%.4f</td>"
            "<td><b>%.2f%%</b></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                badge, "重仓" if status == "heavy" else "加仓", html.escape(grade),
                html.escape(item["symbol"]), html.escape(item["name"]), item["close"], item["avg_dps_3y"],
                item["three_year_average_yield_pct"], _value(item.get("entry_yield")), _value(item.get("add_yield")),
                _value(item.get("heavy_yield")), html.escape(hint),
            )
        )
    return "".join(rows)


def render_email(report: dict[str, Any]) -> tuple[str, str, str]:
    heavy, add = _items(report, "heavy"), _items(report, "add")
    subject = email_subject(report)
    text_lines = ["AuroraAI 红利观察池", f"数据日期：{report['trade_date']}", f"重仓：{len(heavy)}", f"加仓：{len(add)}"]
    for status, items in (("重仓", heavy), ("加仓", add)):
        text_lines.append(f"\n{status}")
        for item in items:
            text_lines.append(f"{item['symbol']} {item['name']} | {item.get('grade') or '未设置'} | 收盘价 {item['close']:.2f} | 3年股息率 {item['three_year_average_yield_pct']:.2f}%")
    table_head = "<tr><th>状态</th><th>等级</th><th>股票</th><th>收盘价</th><th>avg_dps_3y</th><th>3年股息率</th><th>建仓</th><th>加仓</th><th>重仓</th><th>仓位提示</th></tr>"
    body = "<html><body style='font-family:Arial,sans-serif;color:#1f2937'><h2>AuroraAI 红利观察池</h2>"
    body += f"<p>数据日期：{html.escape(report['trade_date'])}<br>扫描结果：重仓 {len(heavy)}；加仓 {len(add)}</p>"
    for title, status, items in (("重仓", "heavy", heavy), ("加仓", "add", add)):
        body += f"<h3>{title}</h3><table style='border-collapse:collapse;font-size:13px' border='1' cellpadding='6'>{table_head}{_table(items, status)}</table>"
    return subject, "\n".join(text_lines), body + "</body></html>"


def send_email(settings: SmtpSettings, subject: str, text_body: str, html_body: str) -> None:
    message = EmailMessage()
    message["Subject"], message["From"], message["To"] = subject, settings.email_from, settings.email_to
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    context = ssl.create_default_context()
    if settings.port == 465:
        with smtplib.SMTP_SSL(settings.host, settings.port, context=context, timeout=30) as client:
            client.login(settings.username, settings.password)
            client.send_message(message)
    else:
        with smtplib.SMTP(settings.host, settings.port, timeout=30) as client:
            client.starttls(context=context)
            client.login(settings.username, settings.password)
            client.send_message(message)
