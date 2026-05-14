"""
Asaas Payment Checker — heartbeat handler (in-process, no Claude CLI).

Runs every 5 minutes. Four flows:
  0. PENDING payments due in 5 days → upcoming reminder to customer.
  1. RECEIVED/CONFIRMED payments → thank-you message to customer.
  2. PENDING payments due TODAY → due-date reminder to customer.
  3. OVERDUE payments → D+2 and D+5 reminders to customer; after D+5, alert to owner.

State: ADWs/logs/asaas_payment_checker_state.json
  {
    "processed_ids":         [...],  # flow 1
    "notified_upcoming_ids": [...],  # flow 0 — 5 days before
    "notified_due_ids":      [...],  # flow 2
    "notified_overdue_d2":   [...],  # flow 3 — D+2
    "notified_overdue_d5":   [...],  # flow 3 — D+5
    "notified_admin":        [...],  # flow 3 — owner alert
  }
"""

import json
import os
import smtplib
import traceback
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

# ── Config ──────────────────────────────────────────────────────────────────

ASAAS_API_KEY    = os.environ.get("ASAAS_API_KEY", "")
ASAAS_BASE_URL   = "https://api.asaas.com/v3"

EVOLUTION_API_URL  = os.environ.get("EVOLUTION_API_URL", "")
EVOLUTION_INSTANCE = os.environ.get("ASAAS_CHECKER_WHATSAPP_INSTANCE", "COGNITIVA-AI")
_instance_key_var  = "EVOLUTION_API_KEY_" + EVOLUTION_INSTANCE.upper().replace("-", "_")
EVOLUTION_API_KEY  = os.environ.get(_instance_key_var) or os.environ.get("EVOLUTION_API_KEY", "")
OWNER_WHATSAPP     = os.environ.get("ASAAS_CHECKER_OWNER_WHATSAPP", "5531982302185")

SMTP_HOST = os.environ.get("SMTP_HOST", "mail.cognitivaai.com.br")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")


STATE_FILE = Path(__file__).resolve().parents[2] / "ADWs" / "logs" / "asaas_payment_checker_state.json"

WHATSAPP_TEMPLATE = (
    "Olá, *{name}*! 👋\n\n"
    "Confirmamos o recebimento do seu pagamento de *R$ {value}* "
    "referente a _{description}_. Muito obrigado pela confiança!\n\n"
    "Acesse seu comprovante: {receipt_url}\n\n"
    "Qualquer dúvida, estamos à disposição. 😊"
)

EMAIL_SUBJECT  = "Pagamento recebido — obrigado, {name}!"
EMAIL_TEMPLATE = """\
<html><body style="font-family:Arial,sans-serif;color:#333">
<p>Olá, <strong>{name}</strong>,</p>
<p>Confirmamos o recebimento do seu pagamento no valor de <strong>R$ {value}</strong>,
referente a <em>{description}</em>, processado em <strong>{payment_date}</strong>.</p>
<p><a href="{receipt_url}" style="background:#00FFA7;color:#000;padding:8px 16px;
border-radius:4px;text-decoration:none;font-weight:bold">Acessar comprovante</a></p>
<p>Agradecemos pela pontualidade e pela confiança nos nossos serviços.<br>
Estamos sempre à disposição.</p>
<p>Atenciosamente,<br><strong>Cognitiva AI</strong></p>
</body></html>
"""

WHATSAPP_DUE_TEMPLATE = (
    "Olá, *{name}*! 👋\n\n"
    "Passamos para lembrar que você tem um pagamento de *R$ {value}* "
    "referente a _{description}_ com vencimento *hoje, {due_date}*.\n\n"
    "Acesse o link para pagar: {invoice_url}\n\n"
    "Qualquer dúvida, estamos à disposição. 😊"
)

EMAIL_DUE_SUBJECT  = "Lembrete: pagamento vence hoje, {name}!"
EMAIL_DUE_TEMPLATE = """\
<html><body style="font-family:Arial,sans-serif;color:#333">
<p>Olá, <strong>{name}</strong>,</p>
<p>Este é um lembrete de que você tem um pagamento no valor de <strong>R$ {value}</strong>,
referente a <em>{description}</em>, com vencimento <strong>hoje, {due_date}</strong>.</p>
<p><a href="{invoice_url}" style="background:#FFA700;color:#000;padding:8px 16px;
border-radius:4px;text-decoration:none;font-weight:bold">Pagar agora</a></p>
<p>Caso já tenha efetuado o pagamento, por favor desconsidere este aviso.<br>
Estamos sempre à disposição para qualquer dúvida.</p>
<p>Atenciosamente,<br><strong>Cognitiva AI</strong></p>
</body></html>
"""

WHATSAPP_UPCOMING_TEMPLATE = (
    "Olá, *{name}*! 👋\n\n"
    "Passamos para avisar que você tem um pagamento de *R$ {value}* "
    "referente a _{description}_ com vencimento em *{due_date}* (daqui a 5 dias).\n\n"
    "Acesse o link para pagar com antecedência: {invoice_url}\n\n"
    "Qualquer dúvida, estamos à disposição. 😊"
)

EMAIL_UPCOMING_SUBJECT  = "Lembrete: pagamento vence em 5 dias, {name}"
EMAIL_UPCOMING_TEMPLATE = """\
<html><body style="font-family:Arial,sans-serif;color:#333">
<p>Olá, <strong>{name}</strong>,</p>
<p>Este é um aviso amigável: você tem um pagamento no valor de <strong>R$ {value}</strong>,
referente a <em>{description}</em>, com vencimento em <strong>{due_date}</strong> — daqui a 5 dias.</p>
<p><a href="{invoice_url}" style="background:#00FFA7;color:#000;padding:8px 16px;
border-radius:4px;text-decoration:none;font-weight:bold">Pagar com antecedência</a></p>
<p>Caso já tenha providenciado o pagamento, por favor desconsidere este aviso.<br>
Estamos sempre à disposição para qualquer dúvida.</p>
<p>Atenciosamente,<br><strong>Cognitiva AI</strong></p>
</body></html>
"""

WHATSAPP_OVERDUE_D2_TEMPLATE = (
    "Olá, *{name}*! 👋\n\n"
    "Identificamos que o seu pagamento de *R$ {value}* referente a _{description}_, "
    "com vencimento em *{due_date}*, ainda não foi realizado.\n\n"
    "Regularize agora para evitar juros: {invoice_url}\n\n"
    "Qualquer dúvida, estamos à disposição. 😊"
)

EMAIL_OVERDUE_D2_SUBJECT  = "Pagamento em atraso — {name}, regularize agora"
EMAIL_OVERDUE_D2_TEMPLATE = """\
<html><body style="font-family:Arial,sans-serif;color:#333">
<p>Olá, <strong>{name}</strong>,</p>
<p>Identificamos que o seu pagamento no valor de <strong>R$ {value}</strong>,
referente a <em>{description}</em>, com vencimento em <strong>{due_date}</strong>,
ainda não foi realizado.</p>
<p><a href="{invoice_url}" style="background:#FF6B00;color:#fff;padding:8px 16px;
border-radius:4px;text-decoration:none;font-weight:bold">Regularizar agora</a></p>
<p>Caso já tenha efetuado o pagamento, por favor desconsidere este aviso.<br>
Estamos à disposição para qualquer dúvida.</p>
<p>Atenciosamente,<br><strong>Cognitiva AI</strong></p>
</body></html>
"""

WHATSAPP_OVERDUE_D5_TEMPLATE = (
    "Olá, *{name}*! 👋\n\n"
    "Seu pagamento de *R$ {value}* referente a _{description}_ está em atraso "
    "há *5 dias* (vencimento: {due_date}).\n\n"
    "Por favor, regularize o quanto antes para evitar a suspensão do serviço: {invoice_url}\n\n"
    "Se precisar de ajuda ou quiser negociar, entre em contato conosco. 🙏"
)

EMAIL_OVERDUE_D5_SUBJECT  = "Urgente: pagamento {days} dias em atraso — {name}"
EMAIL_OVERDUE_D5_TEMPLATE = """\
<html><body style="font-family:Arial,sans-serif;color:#333">
<p>Olá, <strong>{name}</strong>,</p>
<p>Seu pagamento no valor de <strong>R$ {value}</strong>, referente a <em>{description}</em>,
está em atraso há <strong>{days} dias</strong> (vencimento: <strong>{due_date}</strong>).</p>
<p>Para evitar a suspensão do serviço, regularize o quanto antes:</p>
<p><a href="{invoice_url}" style="background:#CC0000;color:#fff;padding:8px 16px;
border-radius:4px;text-decoration:none;font-weight:bold">Regularizar agora</a></p>
<p>Se desejar negociar ou precisar de apoio, responda este e-mail ou entre em contato conosco.<br>
Estamos à disposição.</p>
<p>Atenciosamente,<br><strong>Cognitiva AI</strong></p>
</body></html>
"""

WHATSAPP_ADMIN_TEMPLATE = (
    "⚠️ *Inadimplente — ação necessária*\n\n"
    "*Cliente:* {name}\n"
    "*Valor:* R$ {value}\n"
    "*Descrição:* {description}\n"
    "*Vencimento:* {due_date}\n"
    "*Dias em atraso:* {days}\n"
    "*Telefone:* {phone}\n"
    "*E-mail:* {email}\n\n"
    "D+2 e D+5 já enviados. Entre em contato para negociar."
)

# ── State ────────────────────────────────────────────────────────────────────

def _load_state() -> tuple[set, set, set, set, set, set]:
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text())
            return (
                set(data.get("processed_ids", [])),
                set(data.get("notified_upcoming_ids", [])),
                set(data.get("notified_due_ids", [])),
                set(data.get("notified_overdue_d2", [])),
                set(data.get("notified_overdue_d5", [])),
                set(data.get("notified_admin", [])),
            )
    except Exception:
        pass
    return set(), set(), set(), set(), set(), set()


def _save_state(processed_ids: set, notified_upcoming_ids: set, notified_due_ids: set,
                notified_overdue_d2: set, notified_overdue_d5: set,
                notified_admin: set) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({
        "processed_ids":         sorted(processed_ids),
        "notified_upcoming_ids": sorted(notified_upcoming_ids),
        "notified_due_ids":      sorted(notified_due_ids),
        "notified_overdue_d2":   sorted(notified_overdue_d2),
        "notified_overdue_d5":   sorted(notified_overdue_d5),
        "notified_admin":        sorted(notified_admin),
    }, indent=2))


# ── Asaas ────────────────────────────────────────────────────────────────────

def _asaas_get(path: str, params: dict = None) -> dict:
    resp = requests.get(
        f"{ASAAS_BASE_URL}{path}",
        headers={"access_token": ASAAS_API_KEY},
        params=params or {},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _fetch_new_payments() -> list:
    """Return payments RECEIVED or CONFIRMED in the last 7 days."""
    since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    results = []
    for status in ("RECEIVED", "CONFIRMED"):
        data = _asaas_get("/payments", {"status": status, "paymentDate[ge]": since, "limit": 50})
        results.extend(data.get("data", []))
    return results


def _fetch_receipt_url(payment_id: str) -> str:
    try:
        data = _asaas_get(f"/payments/{payment_id}/transactionReceiptUrl")
        return data.get("transactionReceiptUrl") or data.get("url") or ""
    except Exception:
        return ""


def _fetch_customer(customer_id: str) -> dict:
    try:
        return _asaas_get(f"/customers/{customer_id}")
    except Exception:
        return {}


def _fetch_due_today_payments() -> list:
    """Return PENDING payments with dueDate = today."""
    today = datetime.now().strftime("%Y-%m-%d")
    data = _asaas_get("/payments", {
        "status": "PENDING",
        "dueDate[ge]": today,
        "dueDate[le]": today,
        "limit": 50,
    })
    return data.get("data", [])


def _fetch_upcoming_payments() -> list:
    """Return PENDING payments due exactly 5 days from today."""
    target = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
    data = _asaas_get("/payments", {
        "status": "PENDING",
        "dueDate[ge]": target,
        "dueDate[le]": target,
        "limit": 50,
    })
    return data.get("data", [])


def _fetch_overdue_payments() -> list:
    """Return OVERDUE payments from the last 90 days."""
    since = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    data = _asaas_get("/payments", {
        "status": "OVERDUE",
        "dueDate[ge]": since,
        "limit": 50,
    })
    return data.get("data", [])


# ── WhatsApp ─────────────────────────────────────────────────────────────────

def _normalize_phone(phone: str) -> str:
    """Normalize Brazilian phone to E.164 without '+': 5531999990000"""
    digits = "".join(c for c in phone if c.isdigit())
    if not digits.startswith("55"):
        digits = "55" + digits
    return digits


def _send_whatsapp(phone: str, message: str) -> bool:
    if not EVOLUTION_API_URL or not EVOLUTION_API_KEY:
        print("[asaas_checker] WhatsApp: EVOLUTION_API_URL or EVOLUTION_API_KEY not set")
        return False
    try:
        number = _normalize_phone(phone)
        resp = requests.post(
            f"{EVOLUTION_API_URL}/message/sendText/{EVOLUTION_INSTANCE}",
            headers={"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"},
            json={"number": number, "text": message},
            timeout=15,
        )
        resp.raise_for_status()
        print(f"[asaas_checker] WhatsApp sent to {number}")
        return True
    except Exception as exc:
        print(f"[asaas_checker] WhatsApp error: {exc}")
        return False


# ── Email ────────────────────────────────────────────────────────────────────

def _send_email(to_email: str, subject: str, html_body: str) -> bool:
    if not SMTP_USER or not SMTP_PASS:
        print("[asaas_checker] Email: SMTP_USER or SMTP_PASS not set — skipping email")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = to_email
        msg.attach(MIMEText(html_body, "html"))
        import base64 as _b64
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
            smtp.ehlo()
            # AUTH LOGIN manual — suporta senhas com caracteres não-ASCII (ex: £)
            smtp.docmd("AUTH", "LOGIN")
            smtp.docmd(_b64.b64encode(SMTP_USER.encode()).decode())
            smtp.docmd(_b64.b64encode(SMTP_PASS.encode("utf-8")).decode())
            smtp.sendmail(SMTP_USER, to_email, msg.as_string())
        print(f"[asaas_checker] Email sent to {to_email}")
        return True
    except Exception as exc:
        print(f"[asaas_checker] Email error: {exc}")
        return False


# ── Entry points ─────────────────────────────────────────────────────────────

def tick_received() -> dict:
    """Flow 1: pagamentos recebidos. Chamado a cada 5 minutos."""
    if not ASAAS_API_KEY:
        return {"status": "skip", "reason": "ASAAS_API_KEY not configured"}

    processed_ids, notified_upcoming_ids, notified_due_ids, notified_overdue_d2, notified_overdue_d5, notified_admin = _load_state()

    payments = _fetch_new_payments()
    new_payments = [p for p in payments if p["id"] not in processed_ids]
    sent, failed = [], []

    for payment in new_payments:
        pay_id   = payment["id"]
        value    = f"{payment.get('value', 0):.2f}".replace(".", ",")
        desc     = payment.get("description") or "serviços"
        pay_date = payment.get("paymentDate") or payment.get("clientPaymentDate") or ""
        if pay_date:
            try:
                pay_date = datetime.strptime(pay_date[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
            except Exception:
                pass

        customer    = _fetch_customer(payment.get("customer", ""))
        name        = customer.get("name") or "Cliente"
        phone       = customer.get("mobilePhone") or customer.get("phone") or ""
        email       = customer.get("email") or ""
        receipt_url = _fetch_receipt_url(pay_id) or payment.get("invoiceUrl") or "https://www.asaas.com"

        ctx      = {"name": name, "value": value, "description": desc,
                    "receipt_url": receipt_url, "payment_date": pay_date}
        wpp_ok   = _send_whatsapp(phone, WHATSAPP_TEMPLATE.format(**ctx)) if phone else False
        email_ok = _send_email(email, EMAIL_SUBJECT.format(name=name), EMAIL_TEMPLATE.format(**ctx)) if email else False

        processed_ids.add(pay_id)
        (sent if wpp_ok or email_ok else failed).append(
            {"id": pay_id, "customer": name, "value": value, "whatsapp": wpp_ok, "email": email_ok}
        )

    _save_state(processed_ids, notified_upcoming_ids, notified_due_ids, notified_overdue_d2, notified_overdue_d5, notified_admin)

    if new_payments:
        return {"status": "work", "new": len(new_payments), "sent": sent, "failed": failed}
    return {"status": "skip", "reason": "no new payments", "checked": len(payments)}


def tick_daily() -> dict:
    """Flows 0, 2 e 3: aviso antecipado + vencimentos hoje + inadimplência. Chamado às 08:00."""
    if not ASAAS_API_KEY:
        return {"status": "skip", "reason": "ASAAS_API_KEY not configured"}

    try:
        return _tick_daily_inner()
    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else "?"
        return {"status": "error", "reason": f"Asaas API HTTP {status_code}: {exc}"}
    except requests.exceptions.RequestException as exc:
        return {"status": "error", "reason": f"Asaas API network error: {exc}"}


def _tick_daily_inner() -> dict:
    processed_ids, notified_upcoming_ids, notified_due_ids, notified_overdue_d2, notified_overdue_d5, notified_admin = _load_state()

    # ── Flow 0: upcoming reminder (5 days before) ─────────────────────────────
    upcoming_payments = _fetch_upcoming_payments()
    new_upcoming = [p for p in upcoming_payments if p["id"] not in notified_upcoming_ids]
    sent_upcoming, failed_upcoming = [], []

    for payment in new_upcoming:
        pay_id      = payment["id"]
        value       = f"{payment.get('value', 0):.2f}".replace(".", ",")
        desc        = payment.get("description") or "serviços"
        raw_due     = payment.get("dueDate") or ""
        due_date    = datetime.strptime(raw_due[:10], "%Y-%m-%d").strftime("%d/%m/%Y") if raw_due else ""
        invoice_url = payment.get("invoiceUrl") or "https://www.asaas.com"

        customer = _fetch_customer(payment.get("customer", ""))
        name     = customer.get("name") or "Cliente"
        phone    = customer.get("mobilePhone") or customer.get("phone") or ""
        email    = customer.get("email") or ""

        ctx      = {"name": name, "value": value, "description": desc,
                    "due_date": due_date, "invoice_url": invoice_url}
        wpp_ok   = _send_whatsapp(phone, WHATSAPP_UPCOMING_TEMPLATE.format(**ctx)) if phone else False
        email_ok = _send_email(email, EMAIL_UPCOMING_SUBJECT.format(name=name), EMAIL_UPCOMING_TEMPLATE.format(**ctx)) if email else False

        notified_upcoming_ids.add(pay_id)
        (sent_upcoming if wpp_ok or email_ok else failed_upcoming).append(
            {"id": pay_id, "customer": name, "value": value, "whatsapp": wpp_ok, "email": email_ok}
        )

    # ── Flow 2: due today reminder ────────────────────────────────────────────
    due_payments = _fetch_due_today_payments()
    new_due = [p for p in due_payments if p["id"] not in notified_due_ids]
    sent_due, failed_due = [], []

    for payment in new_due:
        pay_id      = payment["id"]
        value       = f"{payment.get('value', 0):.2f}".replace(".", ",")
        desc        = payment.get("description") or "serviços"
        raw_due     = payment.get("dueDate") or ""
        due_date    = datetime.strptime(raw_due[:10], "%Y-%m-%d").strftime("%d/%m/%Y") if raw_due else ""
        invoice_url = payment.get("invoiceUrl") or "https://www.asaas.com"

        customer = _fetch_customer(payment.get("customer", ""))
        name     = customer.get("name") or "Cliente"
        phone    = customer.get("mobilePhone") or customer.get("phone") or ""
        email    = customer.get("email") or ""

        ctx      = {"name": name, "value": value, "description": desc,
                    "due_date": due_date, "invoice_url": invoice_url}
        wpp_ok   = _send_whatsapp(phone, WHATSAPP_DUE_TEMPLATE.format(**ctx)) if phone else False
        email_ok = _send_email(email, EMAIL_DUE_SUBJECT.format(name=name), EMAIL_DUE_TEMPLATE.format(**ctx)) if email else False

        notified_due_ids.add(pay_id)
        (sent_due if wpp_ok or email_ok else failed_due).append(
            {"id": pay_id, "customer": name, "value": value, "whatsapp": wpp_ok, "email": email_ok}
        )

    # ── Flow 3: overdue reminders (D+2, D+5) + owner alert ───────────────────
    overdue_payments = _fetch_overdue_payments()
    today = datetime.now().date()
    sent_overdue, failed_overdue, sent_admin = [], [], []

    for payment in overdue_payments:
        pay_id       = payment["id"]
        raw_due      = payment.get("dueDate") or ""
        if not raw_due:
            continue
        due_date_obj = datetime.strptime(raw_due[:10], "%Y-%m-%d").date()
        days_overdue = (today - due_date_obj).days
        if days_overdue < 2:
            continue

        value       = f"{payment.get('value', 0):.2f}".replace(".", ",")
        desc        = payment.get("description") or "serviços"
        due_date    = due_date_obj.strftime("%d/%m/%Y")
        invoice_url = payment.get("invoiceUrl") or "https://www.asaas.com"

        customer = _fetch_customer(payment.get("customer", ""))
        name     = customer.get("name") or "Cliente"
        phone    = customer.get("mobilePhone") or customer.get("phone") or ""
        email    = customer.get("email") or ""

        if pay_id not in notified_overdue_d2:
            ctx      = {"name": name, "value": value, "description": desc,
                        "due_date": due_date, "invoice_url": invoice_url}
            wpp_ok   = _send_whatsapp(phone, WHATSAPP_OVERDUE_D2_TEMPLATE.format(**ctx)) if phone else False
            email_ok = _send_email(email, EMAIL_OVERDUE_D2_SUBJECT.format(name=name),
                                   EMAIL_OVERDUE_D2_TEMPLATE.format(**ctx)) if email else False
            notified_overdue_d2.add(pay_id)
            (sent_overdue if wpp_ok or email_ok else failed_overdue).append(
                {"id": pay_id, "customer": name, "value": value, "stage": "D+2",
                 "whatsapp": wpp_ok, "email": email_ok}
            )

        if days_overdue >= 5 and pay_id not in notified_overdue_d5:
            ctx      = {"name": name, "value": value, "description": desc,
                        "due_date": due_date, "invoice_url": invoice_url, "days": days_overdue}
            wpp_ok   = _send_whatsapp(phone, WHATSAPP_OVERDUE_D5_TEMPLATE.format(**ctx)) if phone else False
            email_ok = _send_email(email, EMAIL_OVERDUE_D5_SUBJECT.format(name=name, days=days_overdue),
                                   EMAIL_OVERDUE_D5_TEMPLATE.format(**ctx)) if email else False
            notified_overdue_d5.add(pay_id)
            (sent_overdue if wpp_ok or email_ok else failed_overdue).append(
                {"id": pay_id, "customer": name, "value": value, "stage": "D+5",
                 "whatsapp": wpp_ok, "email": email_ok}
            )

            if pay_id not in notified_admin and OWNER_WHATSAPP:
                admin_ctx = {"name": name, "value": value, "description": desc,
                             "due_date": due_date, "days": days_overdue,
                             "phone": phone or "não informado", "email": email or "não informado"}
                admin_ok = _send_whatsapp(OWNER_WHATSAPP, WHATSAPP_ADMIN_TEMPLATE.format(**admin_ctx))
                notified_admin.add(pay_id)
                if admin_ok:
                    sent_admin.append({"id": pay_id, "customer": name, "value": value, "days": days_overdue})

    _save_state(processed_ids, notified_upcoming_ids, notified_due_ids, notified_overdue_d2, notified_overdue_d5, notified_admin)

    any_work = new_upcoming or new_due or sent_overdue or sent_admin
    if any_work:
        return {
            "status": "work",
            "upcoming":    {"new": len(new_upcoming), "sent": sent_upcoming, "failed": failed_upcoming},
            "due_today":   {"new": len(new_due), "sent": sent_due, "failed": failed_due},
            "overdue":     {"sent": sent_overdue, "failed": failed_overdue},
            "admin_alerts": sent_admin,
        }
    return {
        "status": "skip",
        "reason": "no new events",
        "checked": {"upcoming": len(upcoming_payments), "due": len(due_payments), "overdue": len(overdue_payments)},
    }


def run_watcher() -> dict:
    """Heartbeat entry point — chama tick_received (a cada 5min pelo heartbeat_runner)."""
    return tick_received()


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "received"
    result = tick_daily() if mode == "daily" else tick_received()
    print(json.dumps(result, indent=2, ensure_ascii=False))
