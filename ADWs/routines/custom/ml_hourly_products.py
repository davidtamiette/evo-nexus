#!/usr/bin/env python3
"""
ADW: ML Hourly Products — posta 4 produtos ML com desconto no X/@VAZOUPROMO a cada hora.

Fluxo:
  1. Busca produtos com desconto na página de ofertas do ML (scraping)
  2. Diversifica categorias — não posta 4 produtos da mesma categoria
  3. Verifica se o produto tem cupom ativo no banco ml_affiliate.db
  4. Se cupom disponível, gera o código #VAZOU automaticamente
  5. Monta tweet com produto + desconto (+ cupom se houver)
  6. Posta via Twitter API v2 (OAuth 1.0a)
  7. Registra no banco para evitar duplicatas

Preços e descontos: usados EXATAMENTE como o ML declara. Nunca modificados.
"""

import base64
import hashlib
import hmac
import json
import os
import random
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths — funciona tanto em routines/ quanto em routines/custom/
# ---------------------------------------------------------------------------
_ROUTINE_DIR = Path(__file__).resolve().parent
# Se estiver em custom/, sobe mais um nível para chegar no ADWs/
_ADW_DIR     = _ROUTINE_DIR.parent if _ROUTINE_DIR.name != "custom" else _ROUTINE_DIR.parent.parent
_WORKSPACE   = _ADW_DIR.parent
_LOG_DIR     = _ADW_DIR / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_DB_PATH     = _WORKSPACE / ".claude" / "skills" / "int-mercadolivre" / "ml_affiliate.db"
_SKILL_SCRIPTS = _WORKSPACE / ".claude" / "skills" / "int-mercadolivre" / "scripts"
sys.path.insert(0, str(_SKILL_SCRIPTS))
import ml_client  # noqa: E402


# ---------------------------------------------------------------------------
# .env
# ---------------------------------------------------------------------------
def _load_dotenv():
    env = _WORKSPACE / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if k and k not in os.environ:
            os.environ[k] = v

_load_dotenv()

# Twitter — credenciais do browser (sem custo de API)
X_AUTH_TOKEN   = os.environ.get("X_VAZOUPROMO_AUTH_TOKEN", "")
X_CT0          = os.environ.get("X_VAZOUPROMO_CT0", "")
X_TWID         = os.environ.get("X_VAZOUPROMO_TWID", "")
X_KDT          = os.environ.get("X_VAZOUPROMO_KDT", "")
X_ATT          = os.environ.get("X_VAZOUPROMO_ATT", "")

# Twitter OAuth 1.0a (fallback)
X_API_KEY      = os.environ.get("X_VAZOUPROMO_API_KEY", "")
X_API_SECRET   = os.environ.get("X_VAZOUPROMO_API_SECRET", "")
X_ACCESS_TOKEN = os.environ.get("X_VAZOUPROMO_ACCESS_TOKEN", "")
X_ACCESS_SECRET= os.environ.get("X_VAZOUPROMO_ACCESS_SECRET", "")

# ML Afiliados portal
ML_SSID        = os.environ.get("ML_SESSION_SSID", "")
ML_PUBLISHER   = os.environ.get("ML_PUBLISHER_ID", "58670462")
ML_AFF_WORD    = os.environ.get("ML_AFFILIATE_WORD", "vazoupromo")

TWEET_URL = "https://api.twitter.com/2/tweets"
AFFILIATE_BASE = "https://www.mercadolivre.com.br/affiliate-program/api/affiliates"

# WhatsApp
WA_GROUP_JID  = os.environ.get("WA_VAZOUPROMO_GROUP_JID", "")
WA_INSTANCE   = os.environ.get("WA_VAZOUPROMO_INSTANCE", "COGNITIVA-AI")


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
def _log(entry: dict):
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = _LOG_DIR / f"ml_hourly_{date_str}.jsonl"
    entry["ts"] = datetime.now(timezone.utc).isoformat()
    with open(log_path, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Cache de falhas recentes — evita retentar produto com 226/344 por 2h
# ---------------------------------------------------------------------------
def _skip_cache_path() -> Path:
    return _LOG_DIR / f"ml_skip_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"

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
# Categorias — diversificação
# ---------------------------------------------------------------------------
# Produtos permanentemente bloqueados (IDs do ML)
BLOCKED_IDS = {
    "MLB5298166742",  # Ar Condicionado Agratto 18000 BTUs — código 226 recorrente
}

CATEGORY_RULES = [
    (["creatina", "suplemento", "whey", "proteina", "fitoway", "darklab", "dark-lab",
      "dark lab", "capsulas", "vitamina", "aminoacido", "colagem"], "saude_fitness"),
    (["copa", "figurinha", "panini", "envelope", "cromo", "album"], "copa_colecao"),
    (["smart tv", "tv ", "televisao", "televisão", "qled", "dled", "oled", "philco",
      "samsung tv", "lg tv", "aoc tv"], "tv"),
    (["celular", "smartphone", "iphone", "galaxy", "moto g", "motorola", "redmi"], "celular"),
    (["notebook", "laptop", "computador"], "informatica"),
    (["camera", "câmera", "icsee", "seguranca", "segurança"], "cameras"),
    (["cadeira", "mesa", "sofa", "sofá", "movel", "móvel", "estante"], "moveis"),
    (["tenis", "tênis", "roupa", "camisa", "camiseta", "bermuda", "calca", "calça",
      "meia", "kit moda"], "moda"),
    (["ar condicionado", "geladeira", "fogão", "fogao", "lavadora", "microondas",
      "eletrodomestico"], "eletrodomesticos"),
    (["impressora", "monitor", "teclado", "mouse", "headset", "fone", "microfone"], "perifericos"),
    (["bicicleta", "esteira", "academia", "musculacao", "musculação"], "esportes"),
    (["perfume", "desodorante", "colonia", "colônia", "boticario", "boticário"], "beleza"),
    (["projetor", "caixa de som", "jbl", "speaker", "bluetooth"], "audio"),
    (["fritadeira", "air fryer", "cafeteira", "liquidificador"], "cozinha"),
]

def get_category(title: str) -> str:
    t = title.lower()
    for keywords, cat in CATEGORY_RULES:
        if any(k in t for k in keywords):
            return cat
    return "outros"


# ---------------------------------------------------------------------------
# Verificação de disponibilidade do produto no ML
# ---------------------------------------------------------------------------
ML_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

def is_product_available(permalink: str) -> bool:
    """
    Verifica disponibilidade do produto lendo o HTML da página.
    O ML retorna 200 mesmo para indisponíveis — precisa checar o conteúdo.
    """
    try:
        req = urllib.request.Request(permalink, headers=ML_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read(8192).decode("utf-8", errors="ignore")
        unavailable_signals = [
            "not_found",
            "publicação indisponível",
            "publicacao indisponivel",
            "produto indisponível",
            "esta publicação foi removida",
            '"status":"inactive"',
            'rel="canonical" href=""',
        ]
        html_lower = html.lower()
        return not any(s.lower() in html_lower for s in unavailable_signals)
    except urllib.request.HTTPError as e:
        return e.code not in (404, 410)
    except Exception:
        return True

def verify_price(permalink: str, scraped_price: float) -> bool:
    """
    Verifica se o preço da página do produto bate com o preço raspado.
    Aceita variação de até 10%. Rejeita se divergir mais (preço errado no scrape).
    """
    try:
        req = urllib.request.Request(permalink, headers=ML_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as r:
            html = r.read(32768).decode("utf-8", errors="ignore")
        # Busca preços no JSON da página
        import re as _re
        prices = _re.findall(r'"price"\s*:\s*([\d]+(?:\.\d+)?)', html)
        if not prices:
            return True  # não conseguiu verificar, deixa passar
        page_prices = [float(p) for p in prices if float(p) > 10]
        if not page_prices:
            return True
        # Verifica se o preço raspado está próximo de algum preço da página
        for pp in page_prices:
            if abs(pp - scraped_price) / max(pp, scraped_price) <= 0.10:
                return True  # preço confere
        # Nenhum preço da página bate — divergência > 10%
        print(f"[ml_hourly] ⚠️ Preço divergente: raspado=R${scraped_price:.2f} | página={page_prices[:5]}")
        return False
    except Exception:
        return True  # na dúvida, deixa passar


# ---------------------------------------------------------------------------
# Banco de dados
# ---------------------------------------------------------------------------
def db_connect():
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def was_posted_recently(item_id: str, hours: int = 48) -> bool:
    """Retorna True se o item foi postado nas últimas `hours` horas."""
    with db_connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM posted_tweets "
            "WHERE item_id = ? AND datetime(posted_at) > datetime('now', ?)",
            (item_id, f"-{hours} hours")
        ).fetchone()
        return row["cnt"] > 0

def record_post(tweet_id: str, item_id: str, title: str, price: float,
                discount: int, coupon_id: int | None, coupon_code: str | None,
                affiliate_link: str):
    with db_connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO posted_tweets "
            "(tweet_id, item_id, item_title, price, discount, coupon_id, coupon_code, affiliate_link) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (tweet_id, item_id, title, price, discount, coupon_id, coupon_code, affiliate_link)
        )
        conn.commit()

def get_coupons_by_seller(seller_keyword: str):
    """Retorna cupons cuja coluna seller contenha o keyword."""
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM coupons WHERE lower(seller) LIKE ? AND budget > 0 "
            "AND date(expires) >= date('now') ORDER BY budget DESC",
            (f"%{seller_keyword.lower()}%",)
        ).fetchall()
        return [dict(r) for r in rows]

def get_all_coupons():
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM coupons WHERE budget > 0 AND date(expires) >= date('now')"
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# ML Afiliados — obter CSRF e gerar/recuperar código de cupom
# ---------------------------------------------------------------------------
def _ml_portal_headers(csrf: str = "") -> dict:
    h = {
        "Cookie": f"ssid={ML_SSID}",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://www.mercadolivre.com.br/afiliados/coupons",
        "Origin": "https://www.mercadolivre.com.br",
    }
    if csrf:
        h["x-csrf-token"] = csrf
        h["Cookie"] += f"; _csrf={csrf}"
    return h

def _get_csrf() -> tuple[str, str]:
    """Retorna (csrf_token, csrf_cookie) da página do portal."""
    import http.cookiejar
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    req = urllib.request.Request(
        "https://www.mercadolivre.com.br/afiliados/coupons",
        headers={
            "Cookie": f"ssid={ML_SSID}",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "text/html",
        }
    )
    with opener.open(req, timeout=15) as r:
        html = r.read().decode("utf-8", errors="ignore")
    # Token da página
    m = re.search(r'"csrfToken"\s*:\s*"([^"]+)"', html)
    csrf_token = m.group(1) if m else ""
    # Cookie _csrf
    csrf_cookie = ""
    for cookie in cj:
        if cookie.name == "_csrf":
            csrf_cookie = cookie.value
            break
    return csrf_token, csrf_cookie

def ensure_coupon_code(coupon_id: int, code_suffix: str) -> str:
    """
    Garante que o código #VAZOU{suffix} existe para o cupom.
    Retorna o alias gerado (ex: '#VAZOUDARK').
    """
    if not ML_SSID:
        return ""
    try:
        csrf_token, csrf_cookie = _get_csrf()
        h = _ml_portal_headers(csrf_token)
        if csrf_cookie:
            h["Cookie"] += f"; _csrf={csrf_cookie}"
        h["Content-Type"] = "application/json"
        body = json.dumps({"couponId": coupon_id, "code": code_suffix[:4].upper()}).encode()
        req = urllib.request.Request(f"{AFFILIATE_BASE}/create-code", data=body, method="POST", headers=h)
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
            return resp.get("alias", f"#VAZOU{code_suffix[:4].upper()}")
    except urllib.request.HTTPError as e:
        if e.code == 409:  # já existe
            return f"#VAZOU{code_suffix[:4].upper()}"
        print(f"[ml_hourly] Erro ao gerar cupom {coupon_id}: {e.code}", file=sys.stderr)
        return ""
    except Exception as e:
        print(f"[ml_hourly] Erro cupom: {e}", file=sys.stderr)
        return ""


# ---------------------------------------------------------------------------
# Match produto → cupom
# ---------------------------------------------------------------------------
def find_coupon_for_product(title: str, url: str) -> dict | None:
    """
    Match seguro: verifica o seller na URL do produto usando apenas o
    SLUG (path), nunca o domínio — evita falsos positivos com "livr" em
    mercadolivre.com.br.
    Exige mínimo 5 chars do seller key para evitar matches acidentais.
    """
    all_coupons = get_all_coupons()
    title_lower = title.lower()

    # Extrai apenas o path do produto (sem o domínio)
    path_lower = url.lower().split("mercadolivre.com.br/")[-1] if "mercadolivre.com.br/" in url.lower() else url.lower()

    # Também checa a URL de produtos do cupom para ver se é o mesmo seller
    for c in all_coupons:
        prod_url = (c.get("products_url") or "").lower()
        if not prod_url:
            continue

        # Extrai o slug da loja a partir da URL de produtos do cupom
        # Ex: ".../loja/dark-lab/..." → "dark-lab"
        store_slug = ""
        loja_match = re.search(r'/loja/([^/?#]+)', prod_url)
        if loja_match:
            store_slug = loja_match.group(1).strip("/")

        if not store_slug or len(store_slug) < 4:
            continue

        # O slug da loja deve aparecer no PATH do produto
        store_norm = re.sub(r'[^a-z0-9]', '', store_slug)
        path_norm  = re.sub(r'[^a-z0-9]', '', path_lower)

        if store_norm in path_norm:
            return c

    return None


# ---------------------------------------------------------------------------
# Twitter OAuth 1.0a
# ---------------------------------------------------------------------------
def _oauth_header(method: str, url: str) -> str:
    nonce = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    ts = str(int(time.time()))
    params = {
        "oauth_consumer_key":     X_API_KEY,
        "oauth_nonce":            nonce,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp":        ts,
        "oauth_token":            X_ACCESS_TOKEN,
        "oauth_version":          "1.0",
    }
    sorted_params = "&".join(
        f"{urllib.parse.quote(k, '')}={urllib.parse.quote(v, '')}"
        for k, v in sorted(params.items())
    )
    base_str = "&".join([
        method.upper(),
        urllib.parse.quote(url, ""),
        urllib.parse.quote(sorted_params, ""),
    ])
    signing_key = urllib.parse.quote(X_API_SECRET, "") + "&" + urllib.parse.quote(X_ACCESS_SECRET, "")
    sig = base64.b64encode(
        hmac.new(signing_key.encode(), base_str.encode(), hashlib.sha1).digest()
    ).decode()
    params["oauth_signature"] = sig
    return "OAuth " + ", ".join(
        f'{urllib.parse.quote(k, "")}="{urllib.parse.quote(v, "")}"'
        for k, v in sorted(params.items())
    )

def _read_x_credentials() -> dict:
    """Lê credenciais X diretamente do .env — sempre fresco, ignora cache de os.environ."""
    result = {}
    env_path = _WORKSPACE / ".env"
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

def post_tweet(text: str) -> dict:
    """Posta tweet via API interna do X (sem custo). Usa cookies do browser."""
    # Sempre lê do .env para pegar cookies renovados pelo cookie_refresh_server
    creds = _read_x_credentials()
    auth_token = creds.get("X_VAZOUPROMO_AUTH_TOKEN") or X_AUTH_TOKEN
    ct0        = creds.get("X_VAZOUPROMO_CT0") or X_CT0
    twid       = creds.get("X_VAZOUPROMO_TWID") or X_TWID
    kdt        = creds.get("X_VAZOUPROMO_KDT") or X_KDT
    att        = creds.get("X_VAZOUPROMO_ATT") or X_ATT

    if not all([auth_token, ct0]):
        return {"ok": False, "error": "Credenciais do browser não configuradas"}

    cookie_str = f"auth_token={auth_token}; ct0={ct0}; twid={twid}; kdt={kdt}; att={att}; lang=pt; dnt=1"
    headers = {
        'Authorization': 'Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA',
        'Content-Type': 'application/json',
        'Cookie': cookie_str,
        'X-Csrf-Token': ct0,
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36',
        'x-twitter-active-user': 'yes',
        'x-twitter-auth-type': 'OAuth2Session',
        'x-twitter-client-language': 'pt',
        'Accept-Language': 'pt-BR,pt;q=0.9',
        'Referer': 'https://x.com/home',
        'Origin': 'https://x.com',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Dest': 'empty',
    }
    url = 'https://x.com/i/api/graphql/oB-5XsHNAbjvARJEc8CZFw/CreateTweet'
    body = json.dumps({
        "variables": {
            "tweet_text": text,
            "dark_request": False,
            "media": {"media_entities": [], "possibly_sensitive": False},
            "semantic_annotation_ids": [],
            "disallowed_reply_options": None
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
            "longform_notetweets_inline_media_enabled": True, "responsive_web_enhance_cards_enabled": False
        },
        "queryId": "oB-5XsHNAbjvARJEc8CZFw"
    }).encode()

    req = urllib.request.Request(url, data=body, method='POST', headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
            tweet_id = resp.get('data', {}).get('create_tweet', {}).get('tweet_results', {}).get('result', {}).get('rest_id', '')
            if tweet_id:
                return {"ok": True, "tweet_id": tweet_id, "url": f"https://x.com/VAZOUPROMO/status/{tweet_id}"}
            return {"ok": False, "error": f"Sem tweet_id: {str(resp)[:200]}"}
    except urllib.request.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Formatação do tweet
# ---------------------------------------------------------------------------
def _fmt_price(value: float) -> str:
    inteiro = int(value)
    centavos = round((value - inteiro) * 100)
    s = f"{inteiro:,}".replace(",", ".")
    return f"R${s},{centavos:02d}"

def _truncate(text: str, max_len: int) -> str:
    return text if len(text) <= max_len else text[:max_len - 3] + "..."

def format_tweet(product: dict, coupon: dict | None = None, coupon_code: str = "") -> str:
    title    = _truncate(product["title"], 55)
    price    = _fmt_price(product["price"])
    original = _fmt_price(product["original_price"])
    discount = product["discount_percentage"]
    link     = product["affiliate_link"]

    lines = [
        "VAZOU PROMO 🔥",
        "",
        title,
        f"De {original} por {price} ({discount}% OFF)",
    ]

    if coupon and coupon_code:
        lines.append(f"🎟️ Cupom {coupon['title']} extra: {coupon_code}")

    lines += ["", link, "", "#VAZOU #MercadoLivre #Oferta"]

    tweet = "\n".join(lines)
    return _truncate(tweet, 280)


def format_whatsapp_msg(product: dict, coupon: dict | None = None, coupon_code: str = "") -> str:
    """Mensagem para o grupo WhatsApp — sem limite de 280 chars, mais completa."""
    title    = product["title"][:80]
    price    = _fmt_price(product["price"])
    original = _fmt_price(product["original_price"])
    discount = product["discount_percentage"]
    # Garante link com matt_word=vazoupromo para rastreamento correto
    base_url = product["permalink"].split("?")[0].split("#")[0]
    link     = f"{base_url}?matt_tool={ML_PUBLISHER}&matt_word={ML_AFF_WORD}"

    lines = [
        "🔥 *VAZOU PROMO*",
        "",
        f"*{title}*",
        f"De {original} por *{price}* ({discount}% OFF)",
    ]
    if coupon and coupon_code:
        lines.append(f"🎟️ Cupom *{coupon_code}* → {coupon['title']} extra no checkout!")
    lines += ["", f"👉 {link}"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# WhatsApp — envio para grupo
# ---------------------------------------------------------------------------
def send_whatsapp_group(message: str) -> bool:
    """Envia mensagem para o grupo via Evolution API."""
    if not WA_GROUP_JID:
        return False
    try:
        import subprocess
        script = str(_WORKSPACE / ".claude" / "skills" / "int-evolution-api" / "scripts" / "evolution_api_client.py")
        result = subprocess.run(
            [sys.executable, script, "send_text", WA_INSTANCE, WA_GROUP_JID, message],
            capture_output=True, text=True, timeout=20
        )
        return result.returncode == 0
    except Exception as e:
        print(f"[ml_hourly] Erro WhatsApp: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Seleção diversificada de produtos
# ---------------------------------------------------------------------------
def select_diverse_products(products: list, limit: int = 4) -> list:
    """
    Seleciona até `limit` produtos priorizando variedade de categorias.
    Nunca escolhe mais de 2 produtos da mesma categoria.
    """
    category_count: dict[str, int] = {}
    selected = []

    # Primeiro passa: produtos com cupom têm prioridade
    sorted_prods = sorted(products, key=lambda p: (
        0 if find_coupon_for_product(p["title"], p["permalink"]) else 1,
        -p["discount_percentage"]
    ))

    for p in sorted_prods:
        if len(selected) >= limit:
            break
        if p["id"] in BLOCKED_IDS:
            continue
        if was_failed_recently(p["id"]):
            print(f"[ml_hourly] ⏭️ Falhou recentemente, pulando: {p['title'][:50]}")
            continue
        if was_posted_recently(p["id"], hours=48):
            continue
        cat = get_category(p["title"])
        if category_count.get(cat, 0) >= 1:  # máximo 1 por categoria
            continue
        if not is_product_available(p["permalink"]):
            print(f"[ml_hourly] ❌ Indisponível, pulando: {p['title'][:50]}")
            continue
        if not verify_price(p["permalink"], p["price"]):
            print(f"[ml_hourly] ❌ Preço divergente, pulando: {p['title'][:50]}")
            continue
        category_count[cat] = category_count.get(cat, 0) + 1
        selected.append(p)

    return selected


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print(f"[ml_hourly] Iniciando — {datetime.now().isoformat()}")
    _log({"event": "start"})

    # 1. Busca produtos
    try:
        all_products = ml_client.get_best_sellers_with_discount(limit=40, min_discount=20)
    except Exception as e:
        msg = f"Erro ao buscar produtos: {e}"
        print(f"[ml_hourly] {msg}", file=sys.stderr)
        _log({"event": "error", "stage": "fetch", "msg": msg})
        sys.exit(1)

    if not all_products:
        print("[ml_hourly] Nenhum produto encontrado.")
        _log({"event": "no_products"})
        return

    # 2. Seleciona 4 com diversidade de categoria
    products = select_diverse_products(all_products, limit=4)
    if not products:
        print("[ml_hourly] Todos os produtos já postados recentemente.")
        _log({"event": "all_recently_posted"})
        return

    print(f"[ml_hourly] {len(products)} produto(s) selecionados.")

    # Pausa inicial para evitar detecção de automação
    print("[ml_hourly] Aguardando 15 segundos antes do primeiro post...")
    time.sleep(15)

    # 3. Posta cada produto
    for idx, product in enumerate(products):
        cat = get_category(product["title"])

        # Re-verifica disponibilidade e preço no momento do post (produto pode cair entre seleção e post)
        if not is_product_available(product["permalink"]):
            print(f"[ml_hourly] ❌ Produto caiu antes de postar, pulando: {product['title'][:50]}")
            _log({"event": "skip_unavailable_at_post", "title": product["title"]})
            continue
        if not verify_price(product["permalink"], product["price"]):
            print(f"[ml_hourly] ❌ Preço divergente no post, pulando: {product['title'][:50]}")
            _log({"event": "skip_price_mismatch_at_post", "title": product["title"]})
            continue

        # Verifica cupom
        coupon      = find_coupon_for_product(product["title"], product["permalink"])
        coupon_code = ""
        if coupon:
            seller_key = re.sub(r'[^a-z0-9]', '', coupon["seller"].lower())[:4].upper()
            coupon_code = coupon.get("code") or ensure_coupon_code(coupon["id"], seller_key)
            if coupon_code:
                print(f"[ml_hourly] Cupom encontrado: {coupon_code} ({coupon['title']})")

        tweet = format_tweet(product, coupon if coupon_code else None, coupon_code)

        print(f"[ml_hourly] [{idx+1}/4] {cat} | {product['title'][:50]}")
        print(f"[ml_hourly] Tweet ({len(tweet)} chars):\n{tweet}\n")

        result = post_tweet(tweet)

        if result.get("ok"):
            record_post(
                tweet_id     = result["tweet_id"],
                item_id      = product["id"],
                title        = product["title"],
                price        = product["price"],
                discount     = product["discount_percentage"],
                coupon_id    = coupon["id"] if coupon and coupon_code else None,
                coupon_code  = coupon_code or None,
                affiliate_link = product["affiliate_link"],
            )
            print(f"[ml_hourly] ✅ X: {result['url']}")
            main._consecutive_344 = 0  # reset contador de 344 consecutivos
            # Envia para grupo WhatsApp apenas entre 07:00 e 21:00
            hora_atual = datetime.now().hour
            if 7 <= hora_atual < 21:
                wa_msg = format_whatsapp_msg(product, coupon if coupon_code else None, coupon_code)
                wa_ok = send_whatsapp_group(wa_msg)
                print(f"[ml_hourly] {'✅' if wa_ok else '❌'} WhatsApp grupo")
            else:
                print(f"[ml_hourly] ⏸️ WhatsApp silenciado ({hora_atual}h — fora do horário 07-21)")
        else:
            err_str = result.get("error", "")
            print(f"[ml_hourly] ❌ Falha X: {err_str}", file=sys.stderr)
            # Registra falha para skip nas próximas 2h
            for code in (226, 344):
                if str(code) in err_str:
                    record_failed_attempt(product["id"], code)
                    break
            # 344 = rate limit da conta — aguarda 3 min e tenta o próximo produto
            # Só aborta se dois 344 consecutivos (evita loop infinito)
            if "344" in err_str:
                consecutive_344 = getattr(main, "_consecutive_344", 0) + 1
                main._consecutive_344 = consecutive_344
                if consecutive_344 >= 2:
                    print("[ml_hourly] ⛔ Dois rate limits 344 consecutivos — abortando run.", file=sys.stderr)
                    _log({"event": "abort_rate_limit", "product_title": product["title"], "consecutive": consecutive_344})
                    break
                print(f"[ml_hourly] ⚠️ Rate limit 344 (tentativa {consecutive_344}/2) — aguardando 3 min...", file=sys.stderr)
                _log({"event": "rate_limit_retry", "product_title": product["title"], "consecutive": consecutive_344})
                time.sleep(180)
                continue

        _log({
            "event":         "tweet_posted" if result.get("ok") else "tweet_failed",
            "product_title": product["title"],
            "category":      cat,
            "discount":      product["discount_percentage"],
            "price":         product["price"],
            "coupon_code":   coupon_code or None,
            "tweet_chars":   len(tweet),
            "result":        result,
        })

        # Intervalo entre posts (exceto o último) — jitter aleatório 45-120s
        if idx < len(products) - 1:
            wait = random.uniform(45, 120)
            print(f"[ml_hourly] Aguardando {wait:.0f} segundos...")
            time.sleep(wait)

    _log({"event": "done", "total": len(products)})
    print(f"[ml_hourly] Concluído — {datetime.now().isoformat()}")


if __name__ == "__main__":
    main()
