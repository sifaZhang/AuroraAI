from __future__ import annotations

import pytest

from backend.dividend.position_email import SmtpSettings, email_subject, render_email, send_email


def _item(symbol, status, grade, yield_pct):
    return {"symbol": symbol, "name": symbol, "grade": grade, "status": status, "close": 10.0,
            "avg_dps_3y": 0.6, "three_year_average_yield_pct": yield_pct,
            "entry_yield": 5.0, "add_yield": 6.0, "heavy_yield": 7.0}


def _report():
    return {"trade_date": "2026-08-21", "report_status": "completed", "watch": [_item("watch", "watch", "S", 9)],
            "entry": [_item("entry", "entry", "S", 8)],
            "heavy": [_item("B", "heavy", "B", 9), _item("S", "heavy", "S", 7.1)],
            "add": [_item("A-low", "add", "A", 6.1), _item("A-high", "add", "A", 6.5), _item("unset", "add", None, 9)]}


def test_email_contains_only_heavy_and_add_in_required_order():
    subject, text, body = render_email(_report())
    assert subject == "【AuroraAI 红利机会】2只重仓 / 3只加仓"
    assert "watch" not in text and "entry" not in text
    assert text.index("S ") < text.index("B ") < text.index("A-high") < text.index("A-low") < text.index("unset")
    assert "收盘价" in body and "avg_dps_3y" in body and "仓位提示" in body
    assert "目标仓位上限约 10%" in body and "目标仓位上限约 5%" in body and "目标仓位上限约 2%" in body


def test_missing_smtp_environment_is_explicit(monkeypatch):
    for name in ("DIVIDEND_SMTP_HOST", "DIVIDEND_SMTP_PORT", "DIVIDEND_SMTP_USERNAME", "DIVIDEND_SMTP_PASSWORD", "DIVIDEND_EMAIL_FROM", "DIVIDEND_EMAIL_TO"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="DIVIDEND_SMTP_HOST not configured"):
        SmtpSettings.from_env()


def test_sender_uses_one_message_and_ssl_for_465(monkeypatch):
    calls = []
    class Client:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def login(self, user, password): calls.append(("login", user))
        def send_message(self, message): calls.append(("send", message["Subject"]))
    monkeypatch.setattr("backend.dividend.position_email.smtplib.SMTP_SSL", lambda *args, **kwargs: Client())
    settings = SmtpSettings("host", 465, "user", "password", "from@example.com", "to@example.com")
    send_email(settings, "subject", "plain", "<b>html</b>")
    assert calls == [("login", "user"), ("send", "subject")]


def test_workflow_has_manual_email_input_without_schedule():
    from backend.dividend.daily_position_report import PROJECT_ROOT
    workflow = (PROJECT_ROOT / ".github/workflows/dividend-daily-report.yml").read_text(encoding="utf-8")
    assert "send_email:" in workflow and "default: false" in workflow
    assert "--send-email" in workflow and "schedule:" not in workflow


def test_no_signals_never_configures_or_sends(monkeypatch):
    from backend.dividend import daily_position_report as scanner
    monkeypatch.setattr(scanner.SmtpSettings, "from_env", classmethod(lambda cls: pytest.fail("must not configure SMTP")))
    assert scanner.send_report_summary({"report_status": "completed", "heavy": [], "add": []}) is False


def test_many_signals_send_exactly_one_summary(monkeypatch):
    from backend.dividend import daily_position_report as scanner
    calls = []
    monkeypatch.setattr(scanner.SmtpSettings, "from_env", classmethod(lambda cls: object()))
    monkeypatch.setattr(scanner, "send_email", lambda *args: calls.append(args))
    assert scanner.send_report_summary(_report()) is True
    assert len(calls) == 1
