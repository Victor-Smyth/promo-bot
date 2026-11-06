import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["STATE_FILE"] = os.path.join(tempfile.mkdtemp(), "state.json")
import bot
REAL_DC, REAL_DC_SEND = bot.dc, bot.dc_send  # originais, pois alguns testes os substituem

SENT = []
bot.notify = lambda st, text: SENT.append(text)

def offer(i, title, price):
    return {"id": f"t:{i}", "source": "T", "title": title, "price": price, "old_price": None,
            "discount": None, "coupon": None, "store": "Loja", "url": f"http://x/{i}"}

def test_parse_interest():
    assert bot.parse_interest("creatina") == {"termo": "creatina", "preco_max": None, "excluir": []}
    assert bot.parse_interest("ssd 1tb ate 400") == {"termo": "ssd 1tb", "preco_max": 400.0, "excluir": []}
    r = bot.parse_interest("galaxy s24 -Capa -película até 3.500,50".replace("3.500,50", "3500"))
    assert r == {"termo": "galaxy s24", "preco_max": 3500.0, "excluir": ["capa", "pelicula"]}

def test_matches():
    it = bot.parse_interest("creatina 300g ate 80 -pote")
    assert bot.matches(it, offer(1, "Creatina Monohidratada 300g Max", 70))
    assert not bot.matches(it, offer(2, "Creatina 300g", 99))
    assert not bot.matches(it, offer(3, "Creatina 300g com pote", 50))
    assert not bot.matches(it, offer(4, "Creatina 1kg", 50))
    assert bot.matches(bot.parse_interest("pelicula"), offer(5, "Película de vidro", None))

def test_commands_and_owner():
    st = bot.default_state()
    assert "Adicionado" in bot.handle_command(st, "telegram", 10, "/adicionar creatina")
    assert st["telegram"]["owner"] == 10
    assert bot.handle_command(st, "telegram", 99, "/adicionar intruso") is None
    assert "creatina" in bot.handle_command(st, "telegram", 10, "/lista")
    assert "atualizado" in bot.handle_command(st, "telegram", 10, "/adicionar Creatina ate 90")
    assert len(st["interests"]) == 1
    assert "Removido" in bot.handle_command(st, "telegram", 10, "/remover creatina")
    assert st["interests"] == []

def test_scan_first_run_silent_then_alert():
    SENT.clear()
    st = bot.default_state(); st["interests"] = [bot.parse_interest("creatina")]
    f1 = lambda: [offer(1, "Creatina A", 50)]
    assert bot.scan(st, [f1]) == 0          # 1ª rodada: só aprende
    f2 = lambda: [offer(1, "Creatina A", 50), offer(2, "Creatina B", 40), offer(3, "Whey", 10)]
    assert bot.scan(st, [f2]) == 1          # só a nova que casa
    assert bot.scan(st, [f2]) == 0          # sem repetir
    assert len(SENT) == 1 and "Creatina B" in SENT[0]

def test_parse_promobit_fixture():
    nd = {"props": {"pageProps": {"serverFeaturedOffers": [], "serverOffers": {"offers": [
        {"offerId": 7, "offerTitle": "Creatina X", "offerPrice": 59.9, "offerOldPrice": 99.9,
         "offerDiscontPercentage": 40.0, "offerSlug": "creatina-x-7", "storeName": "Amazon",
         "offerStatusName": "APPROVED", "offerCoupon": None}]}}}}
    html = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(nd) + "</script>"
    o = bot.parse_promobit(html)[0]
    assert o["url"] == "https://www.promobit.com.br/oferta/creatina-x-7/" and o["price"] == 59.9
    assert "R$ 59,90" in bot.fmt_offer(o, {"termo": "creatina"})


def test_discord_flow():
    """Simula a API do Discord: comandos com ! e /, ignora bots e outros usuários."""
    bot.DC_ENABLED = True; bot.DC_CHANNEL = "123"
    msgs = [
        {"id": "3", "author": {"id": "u1"}, "content": "!adicionar ssd 1tb ate 400"},
        {"id": "1", "author": {"id": "u1"}, "content": "oi, conversa normal"},
        {"id": "2", "author": {"id": "bot", "bot": True}, "content": "/adicionar bot"},
        {"id": "4", "author": {"id": "intruso"}, "content": "!adicionar hack"},
        {"id": "5", "author": {"id": "u1"}, "content": "/lista"},
    ]
    calls, replies = [], []
    def fake_dc(method, path, body=None):
        calls.append((method, path))
        return list(msgs) if method == "GET" else None
    bot.dc = fake_dc
    bot.dc_send = lambda text: replies.append(text)
    st = bot.default_state()
    assert bot.process_discord(st) == 5
    assert st["discord"]["last_id"] == "5" and st["discord"]["owner"] == "u1"
    assert [i["termo"] for i in st["interests"]] == ["ssd 1tb"]
    assert "Adicionado" in replies[0] and "ssd 1tb" in replies[1]
    msgs.clear()
    bot.process_discord(st)
    assert calls[-1][1].endswith("after=5")  # só lê mensagens novas
    bot.DC_ENABLED = False
    bot.dc, bot.dc_send = REAL_DC, REAL_DC_SEND


def test_retry_on_429():
    """Discord devolve 429 duas vezes e depois aceita: a mensagem não pode ser perdida."""
    import io, urllib.error
    bot.DC_TOKEN = "x"; bot.DC_CHANNEL = "1"
    bot.time.sleep = lambda s: None
    estados = {"n": 0}
    class R:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return b'{"id":"9"}'
    def fake_urlopen(req, timeout=0):
        estados["n"] += 1
        if estados["n"] <= 2:
            raise urllib.error.HTTPError(req.full_url, 429, "RL", {}, io.BytesIO(b'{"retry_after":0.3}'))
        return R()
    orig = bot.urllib.request.urlopen
    bot.urllib.request.urlopen = fake_urlopen
    try:
        assert bot.dc("POST", "/channels/1/messages", {"content": "x"}) == {"id": "9"}
        assert estados["n"] == 3
        # 404 continua dando mensagem clara e sem repetir
        estados["n"] = 0
        def fake404(req, timeout=0):
            estados["n"] += 1
            raise urllib.error.HTTPError(req.full_url, 404, "NF", {}, io.BytesIO(b'{"code":10003}'))
        bot.urllib.request.urlopen = fake404
        try:
            bot.dc("GET", "/x"); assert False
        except RuntimeError as e:
            assert "DISCORD_CHANNEL_ID" in str(e) and estados["n"] == 1
    finally:
        bot.urllib.request.urlopen = orig


def test_liga_desliga():
    SENT.clear()
    st = bot.default_state(); st["interests"] = [bot.parse_interest("creatina")]
    f1 = lambda: [offer(1, "Creatina A", 50)]
    bot.scan(st, [f1])                                    # aprende
    assert "desligado" in bot.handle_command(st, "telegram", 10, "/desligar")
    assert st["paused"] is True
    assert "já está desligado" in bot.handle_command(st, "telegram", 10, "!pausar")
    f2 = lambda: [offer(2, "Creatina B", 40)]
    assert bot.scan(st, [f2]) == 0 and SENT == []         # desligado: não avisa
    assert "desligado" in bot.handle_command(st, "telegram", 10, "/status")
    assert "adicionado" in bot.handle_command(st, "telegram", 10, "/adicionar whey").lower()  # comandos seguem
    assert "ligado" in bot.handle_command(st, "telegram", 10, "/ligar") and st["paused"] is False
    assert "já está ligado" in bot.handle_command(st, "telegram", 10, "/ligar")
    f3 = lambda: [offer(2, "Creatina B", 40), offer(3, "Creatina C", 30)]
    assert bot.scan(st, [f3]) == 1                        # B foi visto durante a pausa; só C avisa
    assert "Creatina C" in SENT[0]
    old = {"offset": 0, "owner": None}
    import json, tempfile, os
    p = os.path.join(tempfile.mkdtemp(), "s.json"); open(p, "w").write(json.dumps({"interests": [], "seen": ["x"]}))
    bot.STATE_FILE = bot.Path(p); assert bot.load_state()["paused"] is False  # estado antigo sem a chave
