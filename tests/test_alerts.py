"""
test_alerts.py
================
Prova as garantias de segurança de `src/alerts.py`: nunca levanta
exceção, devolve False de forma previsível quando não configurado ou
quando o envio falha, e `send_alert` tenta todos os canais antes de
desistir.
"""

from __future__ import annotations

import src.alerts as alerts_module
from src.alerts import send_alert, send_email_alert, send_telegram_alert


def _clear_alert_env(monkeypatch):
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ALERT_SMTP_HOST", "ALERT_SMTP_PORT",
                "ALERT_SMTP_USER", "ALERT_SMTP_PASSWORD", "ALERT_EMAIL_TO", "ALERT_EMAIL_FROM"):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------
# send_telegram_alert
# ---------------------------------------------------------------------

def test_send_telegram_alert_returns_false_when_not_configured(monkeypatch):
    _clear_alert_env(monkeypatch)
    assert send_telegram_alert("teste") is False


def test_send_telegram_alert_success_calls_correct_endpoint(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

    def fake_post(url, data=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        return FakeResponse()

    monkeypatch.setattr("requests.post", fake_post)
    result = send_telegram_alert("mensagem de teste", bot_token="TOKEN123", chat_id="CHAT456")

    assert result is True
    assert "TOKEN123" in captured["url"]
    assert captured["data"]["chat_id"] == "CHAT456"
    assert captured["data"]["text"] == "mensagem de teste"


def test_send_telegram_alert_handles_non_200_status(monkeypatch):
    class FakeResponse:
        status_code = 401

    monkeypatch.setattr("requests.post", lambda *a, **k: FakeResponse())
    result = send_telegram_alert("teste", bot_token="T", chat_id="C")
    assert result is False


def test_send_telegram_alert_never_raises_on_network_failure(monkeypatch):
    def raise_connection_error(*args, **kwargs):
        raise ConnectionError("rede em baixo")

    monkeypatch.setattr("requests.post", raise_connection_error)
    result = send_telegram_alert("teste", bot_token="T", chat_id="C")
    assert result is False  # nunca propaga a exceção


# ---------------------------------------------------------------------
# send_email_alert
# ---------------------------------------------------------------------

def test_send_email_alert_returns_false_when_not_configured(monkeypatch):
    _clear_alert_env(monkeypatch)
    assert send_email_alert("assunto", "corpo") is False


def test_send_email_alert_success_sends_via_smtp(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent["host"] = host
            sent["port"] = port

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self):
            sent["starttls"] = True

        def login(self, user, password):
            sent["login"] = (user, password)

        def sendmail(self, from_addr, to_addrs, message):
            sent["from"] = from_addr
            sent["to"] = to_addrs
            sent["message"] = message

    monkeypatch.setenv("ALERT_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("ALERT_SMTP_USER", "bot@example.com")
    monkeypatch.setenv("ALERT_SMTP_PASSWORD", "secret")
    monkeypatch.setenv("ALERT_EMAIL_TO", "trader@example.com")
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)

    result = send_email_alert("Kill-switch ativo", "corpo do alerta")

    assert result is True
    assert sent["host"] == "smtp.example.com"
    assert sent["to"] == ["trader@example.com"]
    assert sent["login"] == ("bot@example.com", "secret")


def test_send_email_alert_never_raises_on_smtp_failure(monkeypatch):
    monkeypatch.setenv("ALERT_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("ALERT_SMTP_USER", "bot@example.com")
    monkeypatch.setenv("ALERT_SMTP_PASSWORD", "secret")
    monkeypatch.setenv("ALERT_EMAIL_TO", "trader@example.com")

    class FailingSMTP:
        def __init__(self, *args, **kwargs):
            raise OSError("não foi possível ligar ao servidor SMTP")

    monkeypatch.setattr("smtplib.SMTP", FailingSMTP)
    result = send_email_alert("assunto", "corpo")
    assert result is False


# ---------------------------------------------------------------------
# send_alert (ponto de entrada único)
# ---------------------------------------------------------------------

def test_send_alert_false_and_warns_once_when_nothing_configured(monkeypatch, caplog):
    _clear_alert_env(monkeypatch)
    alerts_module._warned_not_configured = False  # reset entre testes

    with caplog.at_level("WARNING"):
        result1 = send_alert("evento 1")
        result2 = send_alert("evento 2")

    assert result1 is False
    assert result2 is False
    warning_count = sum(1 for r in caplog.records if "nenhum canal de alerta configurado" in r.message)
    assert warning_count == 1  # avisa só uma vez, não a cada chamada


def test_send_alert_true_if_at_least_one_channel_succeeds(monkeypatch):
    _clear_alert_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TOKEN123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "CHAT456")

    class FakeResponse:
        status_code = 200

    monkeypatch.setattr("requests.post", lambda *a, **k: FakeResponse())
    result = send_alert("teste", subject="assunto")
    assert result is True  # Telegram configurado e a "responder" com sucesso
