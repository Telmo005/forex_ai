"""
alerts.py
==========
Notificações (Telegram/email) para eventos de risco em produção
(ALERT-01, `docs/risk_engine_mql5_spec.md`): kill-switch acionado,
drawdown a aproximar-se do limite, ligação perdida. Este módulo NUNCA
decide risco — só informa; é sempre um observador, nunca um ator.

GARANTIA CRÍTICA: uma falha ao enviar um alerta (rede em baixo,
credenciais erradas, Telegram fora do ar) NUNCA pode interromper o
loop de negociação ao vivo. Todas as funções aqui engolem exceções e
devolvem `False` em caso de falha — nunca propagam.

Configuração via variáveis de ambiente (NUNCA hardcoded, NUNCA
versionado — `.gitignore` já protege `.env`):
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    ALERT_SMTP_HOST, ALERT_SMTP_PORT, ALERT_SMTP_USER, ALERT_SMTP_PASSWORD,
    ALERT_EMAIL_TO, ALERT_EMAIL_FROM

Se nenhum canal estiver configurado, `send_alert()` regista um único
aviso (não repete a cada chamada, para não poluir o log) e devolve
False — nunca bloqueia o chamador. Isto é intencional: o sistema deve
continuar a operar (com o registo em `output/live_hedge_loop.log`
sempre disponível) mesmo sem alertas configurados.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.text import MIMEText

log = logging.getLogger("alerts")

_warned_not_configured = False


def send_telegram_alert(message: str, bot_token: str | None = None, chat_id: str | None = None) -> bool:
    """Envia `message` via Telegram Bot API. `bot_token`/`chat_id`
    default às variáveis de ambiente `TELEGRAM_BOT_TOKEN`/
    `TELEGRAM_CHAT_ID` — nunca hardcoded. Devolve False (nunca levanta)
    se não configurado ou se o pedido falhar por qualquer razão."""
    bot_token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        return False

    try:
        import requests

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        resp = requests.post(url, data={"chat_id": chat_id, "text": message}, timeout=10)
        if resp.status_code != 200:
            log.warning("send_telegram_alert: Telegram devolveu status %d — alerta pode não ter chegado",
                        resp.status_code)
            return False
        return True
    except Exception as exc:
        log.warning("send_telegram_alert: falha ao enviar (%s) — alerta perdido, não bloqueia o chamador", exc)
        return False


def send_email_alert(subject: str, message: str) -> bool:
    """Envia `message` por email via SMTP, configurado inteiramente por
    variáveis de ambiente (`ALERT_SMTP_HOST`/`ALERT_SMTP_PORT`/
    `ALERT_SMTP_USER`/`ALERT_SMTP_PASSWORD`/`ALERT_EMAIL_TO`/
    `ALERT_EMAIL_FROM`). Devolve False (nunca levanta) se não
    configurado ou se o envio falhar."""
    host = os.environ.get("ALERT_SMTP_HOST")
    user = os.environ.get("ALERT_SMTP_USER")
    password = os.environ.get("ALERT_SMTP_PASSWORD")
    to_addr = os.environ.get("ALERT_EMAIL_TO")
    from_addr = os.environ.get("ALERT_EMAIL_FROM", user)
    port = int(os.environ.get("ALERT_SMTP_PORT", "587"))

    if not host or not user or not password or not to_addr:
        return False

    try:
        msg = MIMEText(message)
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to_addr

        with smtplib.SMTP(host, port, timeout=10) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(from_addr, [to_addr], msg.as_string())
        return True
    except Exception as exc:
        log.warning("send_email_alert: falha ao enviar (%s) — alerta perdido, não bloqueia o chamador", exc)
        return False


def send_alert(message: str, subject: str = "Forex AI — Alerta") -> bool:
    """Ponto de entrada único — tenta todos os canais configurados
    (Telegram e/ou email); devolve True se PELO MENOS um teve sucesso.
    Nunca levanta exceção, nunca bloqueia o chamador (mesma garantia
    de todas as funções deste módulo).

    Se nenhum canal estiver configurado, regista o aviso "não
    configurado" só na PRIMEIRA chamada desta sessão (evita poluir o
    log a cada evento enquanto o utilizador não configurar nada)."""
    global _warned_not_configured

    sent_telegram = send_telegram_alert(message)
    sent_email = send_email_alert(subject, message)

    if not sent_telegram and not sent_email:
        if not _warned_not_configured:
            log.warning(
                "send_alert: nenhum canal de alerta configurado (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID "
                "ou ALERT_SMTP_HOST/ALERT_SMTP_USER/ALERT_SMTP_PASSWORD/ALERT_EMAIL_TO) — "
                "alertas desativados até configurares um. Mensagem perdida: %s", message,
            )
            _warned_not_configured = True
        return False
    return True
