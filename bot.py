#!/usr/bin/env python3
"""Bot de alertas de promoção (Telegram e/ou Discord). Roda em lote (GitHub Actions):
1) lê comandos novos enviados ao bot, 2) busca ofertas, 3) avisa só as que casam
com os seus interesses. Estado (interesses, ofertas já vistas) fica em state.json.
Sem dependências externas (só biblioteca padrão)."""
import json, os, re, sys, unicodedata, urllib.parse, urllib.request, urllib.error
from pathlib import Path

STATE_FILE = Path(os.environ.get("STATE_FILE", "state.json"))
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
DC_TOKEN = os.environ.get("DISCORD_TOKEN", "")
DC_CHANNEL = os.environ.get("DISCORD_CHANNEL_ID", "")
DC_ENABLED = bool(DC_TOKEN and DC_CHANNEL)
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
MAX_SEEN = 3000

AJUDA = (
    "Comandos (no Discord pode usar ! no lugar de /):\n"
    "/adicionar creatina\n"
    "/adicionar ssd 1tb ate 400  (preço máximo)\n"
    "/adicionar galaxy s24 -capa -pelicula  (palavras a excluir)\n"
    "/remover creatina\n"
    "/lista\n"
    "/ajuda\n\n"
    "Todas as palavras do termo precisam aparecer no título da oferta. "
    "Os comandos são processados a cada rodada do agendador (pode levar alguns minutos)."
)

# ---------- estado ----------
def default_state():
    return {"telegram": {"offset": 0, "owner": None}, "discord": {"last_id": None, "owner": None},
            "interests": [], "seen": []}

def load_state():
    st = default_state()
    if STATE_FILE.exists():
        st.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    return st

def save_state(st):
    st["seen"] = st["seen"][-MAX_SEEN:]
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

# ---------- texto / filtro ----------
def norm(s):
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")

def parse_interest(args):
    """'ssd 1tb ate 400 -capa' -> {'termo':'ssd 1tb','preco_max':400.0,'excluir':['capa']}"""
    text = args.strip()
    preco = None
    # preço pode vir em qualquer posição (antes ou depois das exclusões)
    m = re.search(r"\b(?:ate|até|max|máx)\s*(?:r\$\s*)?(\d+(?:[.,]\d+)?)", text, re.I)
    if m:
        preco = float(m.group(1).replace(",", "."))
        text = (text[:m.start()] + " " + text[m.end():])
    excluir = [norm(w[1:]) for w in text.split() if w.startswith("-") and len(w) > 1]
    termo = " ".join(w for w in text.split() if not w.startswith("-"))
    return {"termo": termo.strip(), "preco_max": preco, "excluir": excluir}

def matches(interest, offer):
    title = norm(offer["title"])
    words = norm(interest["termo"]).split()
    if not words or not all(w in title for w in words):
        return False
    if any(x in title for x in interest.get("excluir", [])):
        return False
    pm = interest.get("preco_max")
    if pm is not None and (offer.get("price") is None or offer["price"] > pm):
        return False
    return True

def brl(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def fmt_interest(i):
    s = i["termo"]
    if i.get("preco_max") is not None: s += f" (até {brl(i['preco_max'])})"
    if i.get("excluir"): s += " sem: " + ", ".join(i["excluir"])
    return s

def fmt_offer(o, interest):
    lines = [f"🔥 {o['title']}"]
    if o.get("price") is not None:
        p = brl(o["price"])
        if o.get("old_price") and o["old_price"] > o["price"]:
            p += f"  (antes {brl(o['old_price'])}"
            if o.get("discount"): p += f", -{o['discount']:.0f}%"
            p += ")"
        lines.append("💰 " + p)
    if o.get("coupon"): lines.append(f"🎟️ Cupom: {o['coupon']}")
    if o.get("store"): lines.append(f"🏬 {o['store']}  •  via {o['source']}")
    lines.append(f"🔎 Interesse: {interest['termo']}")
    lines.append(o["url"])
    return "\n".join(lines)

# ---------- canais ----------
def tg(method, **params):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TG_TOKEN}/{method}",
        data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)["result"]

def tg_send(chat_id, text):
    try:
        tg("sendMessage", chat_id=chat_id, text=text)
    except Exception as e:
        print("erro ao enviar (telegram):", e, file=sys.stderr)

def dc(method, path, body=None):
    req = urllib.request.Request(
        "https://discord.com/api/v10" + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bot {DC_TOKEN}", "Content-Type": "application/json",
                 "User-Agent": "DiscordBot (https://github.com/promo-bot, 1.0)"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode("utf-8", "replace")[:300]
        dica = {401: "token inválido", 403: "bot sem permissão no canal (Ver canal / Ler histórico)",
                404: "canal não encontrado: confira DISCORD_CHANNEL_ID (precisa ser o ID do canal, não do servidor)"}.get(e.code, "")
        raise RuntimeError(f"HTTP {e.code} {dica} | {detalhe}") from None

def dc_send(text):
    try:
        dc("POST", f"/channels/{DC_CHANNEL}/messages", {"content": text[:2000]})
    except Exception as e:
        print("erro ao enviar (discord):", e, file=sys.stderr)

def notify(st, text):
    """Envia o alerta para todos os canais ativos."""
    if TG_TOKEN and st["telegram"]["owner"]:
        tg_send(st["telegram"]["owner"], text)
    if DC_ENABLED:
        dc_send(text)

# ---------- comandos ----------
def handle_command(st, chan, sender, text):
    """chan: 'telegram' | 'discord'. Retorna a resposta (str) ou None para ignorar."""
    ch = st[chan]
    if ch["owner"] is None:
        ch["owner"] = sender  # o primeiro a falar com o bot vira o dono (por canal)
    if sender != ch["owner"]:
        return None
    parts = text.strip().split(None, 1)
    cmd = "/" + parts[0].lstrip("/!").split("@")[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    if cmd in ("/start", "/ajuda", "/help"):
        return "Olá! Eu aviso quando surgir promoção dos seus interesses.\n\n" + AJUDA
    if cmd == "/adicionar":
        it = parse_interest(args)
        if not it["termo"]:
            return "Diga o que quer monitorar. Ex.: /adicionar creatina"
        if any(norm(x["termo"]) == norm(it["termo"]) for x in st["interests"]):
            st["interests"] = [x for x in st["interests"] if norm(x["termo"]) != norm(it["termo"])]
            st["interests"].append(it)
            return f"✏️ Interesse atualizado: {fmt_interest(it)}"
        st["interests"].append(it)
        return f"✅ Adicionado: {fmt_interest(it)}"
    if cmd == "/remover":
        alvo = norm(args.strip())
        antes = len(st["interests"])
        st["interests"] = [x for x in st["interests"] if norm(x["termo"]) != alvo]
        return "🗑️ Removido." if len(st["interests"]) < antes else "Não encontrei esse interesse. Veja /lista."
    if cmd == "/lista":
        if not st["interests"]:
            return "Nenhum interesse ainda. Use /adicionar creatina"
        return "📋 Seus interesses:\n" + "\n".join(f"• {fmt_interest(i)}" for i in st["interests"])
    return "Comando não reconhecido.\n\n" + AJUDA

def process_telegram(st):
    if not TG_TOKEN:
        return 0
    try:
        updates = tg("getUpdates", offset=st["telegram"]["offset"], timeout=0, allowed_updates=["message"])
    except Exception as e:
        print("[telegram] getUpdates falhou:", e, file=sys.stderr)
        return 0
    for u in updates:
        st["telegram"]["offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        text = msg.get("text", "")
        if not text.startswith("/"):
            continue
        reply = handle_command(st, "telegram", msg["chat"]["id"], text)
        if reply:
            tg_send(msg["chat"]["id"], reply)
    return len(updates)

def process_discord(st):
    if not DC_ENABLED:
        return 0
    d = st["discord"]
    path = f"/channels/{DC_CHANNEL}/messages?limit=50" + (f"&after={d['last_id']}" if d["last_id"] else "")
    try:
        msgs = dc("GET", path) or []
    except Exception as e:
        print("[discord] leitura falhou:", e, file=sys.stderr)
        return 0
    msgs.sort(key=lambda m: int(m["id"]))
    for m in msgs:
        d["last_id"] = m["id"]
        if m["author"].get("bot"):
            continue
        text = m.get("content", "")
        if not text:
            print("[discord] mensagem sem texto: ative 'Message Content Intent' no portal do bot", file=sys.stderr)
            continue
        if not text.startswith(("/", "!")):
            continue
        reply = handle_command(st, "discord", m["author"]["id"], text)
        if reply:
            dc_send(reply)
    return len(msgs)

# ---------- fontes ----------
def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")

def next_data(html):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("__NEXT_DATA__ não encontrado (layout mudou ou bloqueio)")
    return json.loads(m.group(1))

def parse_promobit(html):
    pp = next_data(html)["props"]["pageProps"]
    raw = list(pp.get("serverFeaturedOffers") or []) + list((pp.get("serverOffers") or {}).get("offers") or [])
    out, ids = [], set()
    for o in raw:
        oid = o.get("offerId")
        if not oid or oid in ids or o.get("offerStatusName") == "EXPIRED":
            continue
        ids.add(oid)
        price = o.get("offerPrice")
        old = o.get("offerOldPrice")
        out.append({
            "id": f"promobit:{oid}", "source": "Promobit", "title": o.get("offerTitle", ""),
            "price": float(price) if price and price > 0.5 else None,
            "old_price": float(old) if old and old > 1 else None,
            "discount": o.get("offerDiscontPercentage") or None,
            "coupon": o.get("offerCoupon"), "store": o.get("storeName"),
            "url": f"https://www.promobit.com.br/oferta/{o['offerSlug']}/",
        })
    return out

def fetch_promobit():
    out = []
    for u in ("https://www.promobit.com.br/promocoes/recentes/", "https://www.promobit.com.br/"):
        try:
            out += parse_promobit(http_get(u))
        except Exception as e:
            print(f"[promobit] {u}: {e}", file=sys.stderr)
    return {o["id"]: o for o in out}.values()

def fetch_pelando():
    # O Pelando fica atrás de Cloudflare e costuma devolver 403 para scripts.
    # Tentamos; se falhar, só registramos no log e seguimos com as outras fontes.
    try:
        html = http_get("https://www.pelando.com.br/recentes")
    except urllib.error.HTTPError as e:
        print(f"[pelando] bloqueado (HTTP {e.code}); ignorando nesta rodada", file=sys.stderr)
        return []
    try:
        data = next_data(html)
    except Exception as e:
        print(f"[pelando] {e}", file=sys.stderr)
        return []
    out = []
    def walk(o):
        if isinstance(o, dict):
            if o.get("title") and o.get("id") and (o.get("price") is not None or o.get("currentPrice") is not None) and (o.get("url") or o.get("slug")):
                price = o.get("price", o.get("currentPrice"))
                url = o.get("url") or f"https://www.pelando.com.br/d/{o['slug']}"
                if url.startswith("/"): url = "https://www.pelando.com.br" + url
                out.append({"id": f"pelando:{o['id']}", "source": "Pelando", "title": o["title"],
                            "price": float(price) if price else None, "old_price": None, "discount": None,
                            "coupon": o.get("couponCode"), "store": (o.get("store") or {}).get("name") if isinstance(o.get("store"), dict) else None,
                            "url": url})
            for v in o.values(): walk(v)
        elif isinstance(o, list):
            for v in o: walk(v)
    walk(data)
    return out

SOURCES = [fetch_promobit, fetch_pelando]

def scan(st, fetchers=None):
    """Busca ofertas, avisa as novas que casam, marca todas como vistas."""
    seen = set(st["seen"])
    first_run = not st["seen"]
    sent = 0
    for fetch in (fetchers or SOURCES):
        try:
            offers = list(fetch())
        except Exception as e:
            print(f"[{fetch.__name__}] falhou: {e}", file=sys.stderr)
            continue
        for o in offers:
            if o["id"] in seen:
                continue
            seen.add(o["id"]); st["seen"].append(o["id"])
            if first_run:
                continue  # 1ª rodada só "aprende" o que já existe, sem spam
            for it in st["interests"]:
                if matches(it, o):
                    notify(st, fmt_offer(o, it)); sent += 1
                    break  # uma mensagem por oferta
    return sent

def main():
    if not (TG_TOKEN or DC_ENABLED):
        sys.exit("Defina TELEGRAM_TOKEN e/ou DISCORD_TOKEN + DISCORD_CHANNEL_ID")
    st = load_state()
    n = process_telegram(st) + process_discord(st)
    sent = scan(st)
    save_state(st)
    canais = [c for c, on in (("telegram", bool(TG_TOKEN)), ("discord", DC_ENABLED)) if on]
    print(f"canais: {','.join(canais)} | mensagens lidas: {n} | alertas: {sent} | "
          f"interesses: {len(st['interests'])} | vistos: {len(st['seen'])}")

if __name__ == "__main__":
    main()
