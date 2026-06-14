import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from _ml_shared.config import WORKSPACE, LOG_DIR


# ---------------------------------------------------------------------------
# Credenciais X — sempre lê do .env para pegar tokens renovados
# ---------------------------------------------------------------------------
def _notify_403(context: str = ""):
    """Envia WA uma vez por hora quando detecta 403 (cookies expirados)."""
    flag = LOG_DIR / "x_403_notified.json"
    try:
        if flag.exists():
            data = json.loads(flag.read_text())
            last = datetime.fromisoformat(data.get("ts", "2000-01-01"))
            if (datetime.now(timezone.utc) - last).total_seconds() < 3600:
                return
    except Exception:
        pass

    msg = (
        "⚠️ *VazouPromo — Cookies X expirados*\n\n"
        "Erro 403 detectado ao tentar postar.\n"
        + (f"Contexto: {context}\n" if context else "")
        + "Os cookies do browser precisam ser renovados.\n\n"
        f"_Horário: {datetime.now(timezone.utc).strftime('%d/%m %H:%M')} UTC_"
    )

    script = str(WORKSPACE / ".claude" / "skills" / "int-evolution-api" / "scripts" / "evolution_api_client.py")
    try:
        import subprocess
        subprocess.run(
            [sys.executable, script, "send_text", "COGNITIVA-AI", "5531982302185", msg],
            capture_output=True, timeout=20,
        )
        flag.write_text(json.dumps({"ts": datetime.now(timezone.utc).isoformat()}))
    except Exception as e:
        print(f"[x_poster] _notify_403 falhou: {e}", file=sys.stderr)


def _read_x_credentials() -> dict:
    result = {}
    env_path = WORKSPACE / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if k.startswith("X_VAZOUPROMO_"):
                result[k] = v
    return result


# ---------------------------------------------------------------------------
# Cache de falhas recentes — evita retentar produto com 226/344 por 2h
# ---------------------------------------------------------------------------
def _skip_cache_path() -> Path:
    return LOG_DIR / f"ml_skip_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"


def _load_skip_cache() -> dict:
    p = _skip_cache_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def _save_skip_cache(cache: dict):
    _skip_cache_path().write_text(json.dumps(cache, ensure_ascii=False))


def was_failed_recently(item_id: str, hours: int = 2) -> bool:
    """True se o produto falhou nas últimas `hours` horas.
    344 (rate limit): pula após 1 falha. 226 (automação): pula após 2 falhas.
    """
    cache = _load_skip_cache()
    entry = cache.get(item_id)
    if not entry:
        return False
    count = entry.get("count", 0)
    error_code = entry.get("error_code", 0)
    min_fails = 1 if error_code == 344 else 2
    if count < min_fails:
        return False
    try:
        from datetime import timedelta
        last_dt = datetime.fromisoformat(entry["last_failed"])
        return (datetime.now(timezone.utc) - last_dt) < timedelta(hours=hours)
    except Exception:
        return False


def record_failed_attempt(item_id: str, error_code: int):
    """Registra falha para item_id no cache diário."""
    cache = _load_skip_cache()
    entry = cache.get(item_id, {"count": 0, "last_failed": "", "error_code": 0})
    entry["count"] = entry.get("count", 0) + 1
    entry["last_failed"] = datetime.now(timezone.utc).isoformat()
    entry["error_code"] = error_code
    cache[item_id] = entry
    _save_skip_cache(cache)


# ---------------------------------------------------------------------------
# post_tweet — posta via API interna do X (cookies browser)
# ---------------------------------------------------------------------------
def post_tweet(text: str) -> dict:
    """Posta tweet via API interna do X (sem custo). Usa cookies do browser."""
    creds = _read_x_credentials()
    auth_token = creds.get("X_VAZOUPROMO_AUTH_TOKEN", "")
    ct0        = creds.get("X_VAZOUPROMO_CT0", "")
    twid       = creds.get("X_VAZOUPROMO_TWID", "")
    kdt        = creds.get("X_VAZOUPROMO_KDT", "")
    att        = creds.get("X_VAZOUPROMO_ATT", "")

    if not all([auth_token, ct0]):
        return {"ok": False, "error": "Credenciais browser não configuradas"}

    cookie_str = f"auth_token={auth_token}; ct0={ct0}; twid={twid}; kdt={kdt}; att={att}; lang=pt; dnt=1"
    headers = {
        "Authorization":             "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA",
        "Content-Type":              "application/json",
        "Cookie":                    cookie_str,
        "X-Csrf-Token":              ct0,
        "User-Agent":                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
        "x-twitter-active-user":     "yes",
        "x-twitter-auth-type":       "OAuth2Session",
        "x-twitter-client-language": "pt",
        "Accept-Language":           "pt-BR,pt;q=0.9",
        "Referer":                   "https://x.com/home",
        "Origin":                    "https://x.com",
        "Sec-Fetch-Site":            "same-origin",
        "Sec-Fetch-Mode":            "cors",
        "Sec-Fetch-Dest":            "empty",
    }
    url  = "https://x.com/i/api/graphql/oB-5XsHNAbjvARJEc8CZFw/CreateTweet"
    body = json.dumps({
        "variables": {
            "tweet_text":              text,
            "dark_request":            False,
            "media":                   {"media_entities": [], "possibly_sensitive": False},
            "semantic_annotation_ids": [],
            "disallowed_reply_options": None,
        },
        "features": {
            "articles_preview_enabled": True, "rweb_tipjar_consumption_enabled": True,
            "responsive_web_graphql_exclude_directive_enabled": True,
            "verified_phone_label_enabled": False,
            "creator_subscriptions_tweet_preview_api_enabled": True,
            "responsive_web_graphql_timeline_navigation_enabled": True,
            "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
            "communities_web_enable_tweet_community_results_fetch": True,
            "c9s_tweet_anatomy_moderator_badge_enabled": True, "articles_rtl_enabled": True,
            "responsive_web_edit_tweet_api_enabled": True,
            "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
            "view_counts_everywhere_api_enabled": True, "longform_notetweets_consumption_enabled": True,
            "responsive_web_twitter_article_tweet_consumption_enabled": True,
            "tweet_awards_web_tipping_enabled": False,
            "creator_subscriptions_quote_tweet_preview_enabled": False,
            "freedom_of_speech_not_reach_fetch_enabled": True,
            "standardized_nudges_misinfo": True,
            "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
            "rweb_video_timestamps_enabled": True, "longform_notetweets_rich_text_read_enabled": True,
            "longform_notetweets_inline_media_enabled": True, "responsive_web_enhance_cards_enabled": False,
        },
        "queryId": "oB-5XsHNAbjvARJEc8CZFw",
    }).encode()

    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
            tweet_id = (
                resp.get("data", {})
                    .get("create_tweet", {})
                    .get("tweet_results", {})
                    .get("result", {})
                    .get("rest_id", "")
            )
            if tweet_id:
                return {"ok": True, "tweet_id": tweet_id, "url": f"https://x.com/VAZOUPROMO/status/{tweet_id}"}
            errors = resp.get("errors", [])
            for err in errors:
                if err.get("code") == 344:
                    return {"ok": False, "error": "344 rate limit"}
            return {"ok": False, "error": f"Sem tweet_id: {str(resp)[:1000]}"}
    except urllib.request.HTTPError as e:
        if e.code == 403:
            _notify_403(context="post_tweet")
        return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode()[:1000]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# OAuth 1.0a helper (para upload de mídia)
# ---------------------------------------------------------------------------
def _oauth1_header(method: str, url: str, extra_params: dict = None) -> str:
    """Gera header OAuth 1.0a para qualquer endpoint."""
    creds = _read_x_credentials()
    api_key    = creds.get("X_VAZOUPROMO_API_KEY", "")
    api_secret = creds.get("X_VAZOUPROMO_API_SECRET", "")
    acc_token  = creds.get("X_VAZOUPROMO_ACCESS_TOKEN", "")
    acc_secret = creds.get("X_VAZOUPROMO_ACCESS_SECRET", "")

    nonce = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    ts    = str(int(time.time()))
    params = {
        "oauth_consumer_key":     api_key,
        "oauth_nonce":            nonce,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp":        ts,
        "oauth_token":            acc_token,
        "oauth_version":          "1.0",
    }
    if extra_params:
        params.update(extra_params)
    sorted_params = "&".join(
        f"{urllib.parse.quote(k, '')}={urllib.parse.quote(v, '')}"
        for k, v in sorted(params.items())
    )
    base_str = "&".join([method.upper(), urllib.parse.quote(url, ""), urllib.parse.quote(sorted_params, "")])
    signing_key = urllib.parse.quote(api_secret, "") + "&" + urllib.parse.quote(acc_secret, "")
    sig = base64.b64encode(
        hmac.new(signing_key.encode(), base_str.encode(), hashlib.sha1).digest()
    ).decode()
    params["oauth_signature"] = sig
    return "OAuth " + ", ".join(
        f'{urllib.parse.quote(k, "")}="{urllib.parse.quote(v, "")}"'
        for k, v in sorted(params.items())
    )


# ---------------------------------------------------------------------------
# Upload de mídia via v1.1 OAuth 1.0a
# ---------------------------------------------------------------------------
def upload_media_oauth(image_url: str) -> str | None:
    """
    Baixa imagem da URL pública e faz upload via Twitter v1.1 media/upload.json com OAuth 1.0a.
    Retorna media_id_string ou None se falhar.
    """
    try:
        req = urllib.request.Request(image_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            image_data = r.read()
        if len(image_data) < 1000:
            return None  # imagem muito pequena, provavelmente erro

        upload_url = "https://upload.twitter.com/1.1/media/upload.json"
        boundary = "----FormBoundary" + base64.b64encode(os.urandom(8)).decode().rstrip("=")
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="media"\r\n\r\n'
        ).encode() + image_data + f"\r\n--{boundary}--\r\n".encode()

        oauth_h = _oauth1_header("POST", upload_url)
        headers = {
            "Authorization": oauth_h,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        req2 = urllib.request.Request(upload_url, data=body, method="POST", headers=headers)
        with urllib.request.urlopen(req2, timeout=30) as r2:
            resp = json.loads(r2.read())
            return resp.get("media_id_string")
    except Exception as e:
        print(f"[x_poster] upload_media falhou: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# post_tweet_with_image — tenta com imagem, fallback sem
# ---------------------------------------------------------------------------
def post_tweet_with_image(text: str, image_url: str | None = None) -> dict:
    """
    Tenta postar tweet com imagem (se image_url fornecida).
    Fallback: post via cookies browser sem mídia.
    """
    media_id = None
    if image_url:
        media_id = upload_media_oauth(image_url)

    creds      = _read_x_credentials()
    auth_token = creds.get("X_VAZOUPROMO_AUTH_TOKEN", "")
    ct0        = creds.get("X_VAZOUPROMO_CT0", "")
    twid       = creds.get("X_VAZOUPROMO_TWID", "")
    kdt        = creds.get("X_VAZOUPROMO_KDT", "")
    att        = creds.get("X_VAZOUPROMO_ATT", "")

    if not all([auth_token, ct0]):
        return {"ok": False, "error": "Credenciais browser não configuradas"}

    cookie_str = f"auth_token={auth_token}; ct0={ct0}; twid={twid}; kdt={kdt}; att={att}; lang=pt; dnt=1"
    headers = {
        "Authorization":             "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA",
        "Content-Type":              "application/json",
        "Cookie":                    cookie_str,
        "X-Csrf-Token":              ct0,
        "User-Agent":                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
        "x-twitter-active-user":     "yes",
        "x-twitter-auth-type":       "OAuth2Session",
        "x-twitter-client-language": "pt",
        "Accept-Language":           "pt-BR,pt;q=0.9",
        "Referer":                   "https://x.com/home",
        "Origin":                    "https://x.com",
        "Sec-Fetch-Site":            "same-origin",
        "Sec-Fetch-Mode":            "cors",
        "Sec-Fetch-Dest":            "empty",
    }

    media_entities = []
    if media_id:
        media_entities = [{"media_id": media_id, "tagged_users": []}]

    url  = "https://x.com/i/api/graphql/oB-5XsHNAbjvARJEc8CZFw/CreateTweet"
    body = json.dumps({
        "variables": {
            "tweet_text":              text,
            "dark_request":            False,
            "media":                   {"media_entities": media_entities, "possibly_sensitive": False},
            "semantic_annotation_ids": [],
            "disallowed_reply_options": None,
        },
        "features": {
            "articles_preview_enabled": True, "rweb_tipjar_consumption_enabled": True,
            "responsive_web_graphql_exclude_directive_enabled": True,
            "verified_phone_label_enabled": False,
            "creator_subscriptions_tweet_preview_api_enabled": True,
            "responsive_web_graphql_timeline_navigation_enabled": True,
            "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
            "communities_web_enable_tweet_community_results_fetch": True,
            "c9s_tweet_anatomy_moderator_badge_enabled": True, "articles_rtl_enabled": True,
            "responsive_web_edit_tweet_api_enabled": True,
            "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
            "view_counts_everywhere_api_enabled": True, "longform_notetweets_consumption_enabled": True,
            "responsive_web_twitter_article_tweet_consumption_enabled": True,
            "tweet_awards_web_tipping_enabled": False,
            "creator_subscriptions_quote_tweet_preview_enabled": False,
            "freedom_of_speech_not_reach_fetch_enabled": True,
            "standardized_nudges_misinfo": True,
            "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
            "rweb_video_timestamps_enabled": True, "longform_notetweets_rich_text_read_enabled": True,
            "longform_notetweets_inline_media_enabled": True, "responsive_web_enhance_cards_enabled": False,
        },
        "queryId": "oB-5XsHNAbjvARJEc8CZFw",
    }).encode()

    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
            tweet_id = (
                resp.get("data", {})
                    .get("create_tweet", {})
                    .get("tweet_results", {})
                    .get("result", {})
                    .get("rest_id", "")
            )
            if tweet_id:
                return {
                    "ok": True,
                    "tweet_id": tweet_id,
                    "url": f"https://x.com/VAZOUPROMO/status/{tweet_id}",
                    "has_image": bool(media_id),
                    "posting_method": "browser_with_media" if media_id else "browser_text",
                }
            errors = resp.get("errors", [])
            for err in errors:
                if err.get("code") == 344:
                    return {"ok": False, "error": "344 rate limit"}
            return {"ok": False, "error": f"Sem tweet_id: {str(resp)[:1000]}"}
    except urllib.request.HTTPError as e:
        if e.code == 403:
            _notify_403(context="post_tweet_with_image")
        return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode()[:1000]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
