#!/usr/bin/env python3
"""
Relatório semanal de vendas do Mercado Livre.

Gera, a partir da API oficial do Mercado Livre:
  - resumo da semana (pedidos, unidades, faturamento, tarifas, lucro estimado)
  - comparação com a semana anterior
  - ranking dos produtos mais vendidos
  - vendas por dia
  - arquivos: relatorio_AAAA-MM-DD.html e vendas_AAAA-MM-DD.csv

Só usa a biblioteca padrão do Python 3.8+ (nenhuma instalação necessária).

------------------------------------------------------------------
CONFIGURAÇÃO (uma vez só)
------------------------------------------------------------------
1) Crie um app em https://developers.mercadolivre.com.br/devcenter
   - Defina uma "URL de redirecionamento" (ex.: https://localhost/callback)
   - Anote o App ID e a Secret Key

2) Defina as variáveis de ambiente:
     export ML_APP_ID="123456789"
     export ML_CLIENT_SECRET="sua_secret"
     export ML_REDIRECT_URI="https://localhost/callback"

3) Autorize o app abrindo no navegador (troque APP_ID e a URI):
     https://auth.mercadolivre.com.br/authorization?response_type=code&client_id=APP_ID&redirect_uri=URI
   Depois de aceitar, o navegador redireciona para a URI com ?code=TG-xxxx
   Copie esse código e rode (ele vale por poucos minutos):
     python3 relatorio_semanal_ml.py --auth TG-xxxx
   Isso salva os tokens em tokens.json.

------------------------------------------------------------------
USO
------------------------------------------------------------------
  python3 relatorio_semanal_ml.py                 # última semana fechada (seg a dom)
  python3 relatorio_semanal_ml.py --semanas-atras 2
  python3 relatorio_semanal_ml.py --ultimos-7-dias
  python3 relatorio_semanal_ml.py --custos custos.csv
  python3 relatorio_semanal_ml.py --custos custos.csv --posts --email voce@exemplo.com

  --posts  gera posts_AAAA-MM-DD.txt com textos prontos (top 3 vendidos) para Instagram/Facebook
  --email  envia o relatório por e-mail. Requer variáveis:
             SMTP_HOST (ex.: smtp.gmail.com), SMTP_USER, SMTP_PASS
             opcionais: SMTP_PORT (465 ou 587), EMAIL_FROM
           No Gmail use uma "Senha de app" (Conta Google > Segurança > Verificação em 2 etapas).

Agendamento semanal: veja AGENDAMENTO.md.

Arquivo opcional de custos (custos.csv), para calcular lucro estimado:
  item_id,custo_unitario
  MLB1234567890,25.90
  MLB9876543210,110.00

Obs.: o lucro estimado = faturamento - tarifas do ML - custo dos produtos.
O frete e os impostos NÃO entram no cálculo.
"""

import argparse
import csv
import html
import json
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone

API = "https://api.mercadolibre.com"
TZ = timezone(timedelta(hours=-3))  # horário de Brasília
TOKENS_FILE = "tokens.json"
# Status que não contam como venda
STATUS_IGNORADOS = {"cancelled", "invalid"}


# ----------------------------------------------------------------------
# API / autenticação
# ----------------------------------------------------------------------
def chamar_api(metodo, caminho, params=None, token=None, dados=None):
    url = API + caminho
    if params:
        url += "?" + urllib.parse.urlencode(params)
    corpo = None
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if dados is not None:
        corpo = urllib.parse.urlencode(dados).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=corpo, headers=headers, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode(errors="replace")
        sys.exit(f"Erro {e.code} em {caminho}: {detalhe}")
    except urllib.error.URLError as e:
        sys.exit(f"Falha de conexão com a API do Mercado Livre: {e.reason}")


def config_env():
    faltando = [v for v in ("ML_APP_ID", "ML_CLIENT_SECRET") if not os.environ.get(v)]
    if faltando:
        sys.exit("Defina as variáveis de ambiente: " + ", ".join(faltando))
    return os.environ["ML_APP_ID"], os.environ["ML_CLIENT_SECRET"], os.environ.get("ML_REDIRECT_URI", "")


def salvar_tokens(tokens):
    with open(TOKENS_FILE, "w") as f:
        json.dump(tokens, f, indent=2)
    try:
        os.chmod(TOKENS_FILE, 0o600)
    except OSError:
        pass


def autorizar(codigo):
    app_id, secret, redirect = config_env()
    if not redirect:
        sys.exit("Defina ML_REDIRECT_URI (a mesma cadastrada no app).")
    t = chamar_api("POST", "/oauth/token", dados={
        "grant_type": "authorization_code",
        "client_id": app_id,
        "client_secret": secret,
        "code": codigo,
        "redirect_uri": redirect,
    })
    salvar_tokens({"refresh_token": t["refresh_token"], "user_id": t.get("user_id")})
    print("Autorizado! Tokens salvos em", TOKENS_FILE)


def obter_access_token():
    """Renova o access token. O refresh token muda a cada uso, então é regravado."""
    app_id, secret, _ = config_env()
    if os.path.exists(TOKENS_FILE):
        with open(TOKENS_FILE) as f:
            salvo = json.load(f)
    elif os.environ.get("ML_REFRESH_TOKEN"):
        # Execução em nuvem (GitHub Actions): o token vem de um secret
        salvo = {"refresh_token": os.environ["ML_REFRESH_TOKEN"].strip()}
    else:
        sys.exit("tokens.json não encontrado. Rode primeiro: --auth CODIGO (veja o topo do script), "
                 "ou defina ML_REFRESH_TOKEN.")
    t = chamar_api("POST", "/oauth/token", dados={
        "grant_type": "refresh_token",
        "client_id": app_id,
        "client_secret": secret,
        "refresh_token": salvo["refresh_token"],
    })
    salvo["refresh_token"] = t["refresh_token"]
    salvo["user_id"] = t.get("user_id", salvo.get("user_id"))
    salvar_tokens(salvo)
    return t["access_token"], salvo["user_id"]


# ----------------------------------------------------------------------
# Coleta de pedidos
# ----------------------------------------------------------------------
def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000-03:00")


def buscar_pedidos(token, vendedor, inicio, fim):
    """Retorna todos os pedidos entre inicio (inclusive) e fim (exclusive)."""
    pedidos, offset, limite = [], 0, 50
    while True:
        r = chamar_api("GET", "/orders/search", token=token, params={
            "seller": vendedor,
            "order.date_created.from": iso(inicio),
            "order.date_created.to": iso(fim - timedelta(milliseconds=1)),
            "sort": "date_asc",
            "offset": offset,
            "limit": limite,
        })
        lote = r.get("results", [])
        pedidos.extend(lote)
        offset += limite
        if not lote or offset >= r.get("paging", {}).get("total", 0):
            break
    return [p for p in pedidos if p.get("status") not in STATUS_IGNORADOS]


# ----------------------------------------------------------------------
# Cálculos
# ----------------------------------------------------------------------
def carregar_custos(caminho):
    custos = {}
    if not caminho:
        return custos
    with open(caminho, newline="", encoding="utf-8-sig") as f:
        for linha in csv.DictReader(f):
            try:
                custos[linha["item_id"].strip()] = float(str(linha["custo_unitario"]).replace(",", "."))
            except (KeyError, ValueError):
                print(f"Aviso: linha de custo ignorada: {linha}", file=sys.stderr)
    return custos


def analisar(pedidos, custos):
    resumo = {"pedidos": len(pedidos), "unidades": 0, "faturamento": 0.0,
              "tarifas": 0.0, "custo": 0.0, "itens_sem_custo": set()}
    produtos = defaultdict(lambda: {"titulo": "", "qtd": 0, "receita": 0.0, "tarifas": 0.0, "custo": 0.0})
    por_dia = defaultdict(lambda: {"pedidos": 0, "faturamento": 0.0})
    linhas = []

    for p in pedidos:
        data = datetime.fromisoformat(p["date_created"]).astimezone(TZ).date()
        por_dia[data]["pedidos"] += 1
        for it in p.get("order_items", []):
            item_id = it["item"]["id"]
            qtd = it.get("quantity", 0)
            receita = it.get("unit_price", 0) * qtd
            tarifa = (it.get("sale_fee") or 0) * qtd
            custo_unit = custos.get(item_id)
            custo = (custo_unit or 0) * qtd
            if custos and custo_unit is None:
                resumo["itens_sem_custo"].add(item_id)

            resumo["unidades"] += qtd
            resumo["faturamento"] += receita
            resumo["tarifas"] += tarifa
            resumo["custo"] += custo
            por_dia[data]["faturamento"] += receita

            pr = produtos[item_id]
            pr["titulo"] = it["item"].get("title", item_id)
            pr["qtd"] += qtd
            pr["receita"] += receita
            pr["tarifas"] += tarifa
            pr["custo"] += custo

            linhas.append([p["id"], data.isoformat(), p.get("status"), item_id,
                           it["item"].get("title", ""), qtd, it.get("unit_price", 0),
                           round(receita, 2), round(tarifa, 2), round(custo, 2)])

    resumo["lucro"] = resumo["faturamento"] - resumo["tarifas"] - resumo["custo"]
    resumo["ticket_medio"] = resumo["faturamento"] / resumo["pedidos"] if resumo["pedidos"] else 0.0
    ranking = sorted(produtos.items(), key=lambda kv: kv[1]["receita"], reverse=True)
    return resumo, ranking, dict(sorted(por_dia.items())), linhas


# ----------------------------------------------------------------------
# Saída
# ----------------------------------------------------------------------
def brl(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def variacao(atual, anterior):
    if not anterior:
        return "—"
    pct = (atual - anterior) / anterior * 100
    return f"{pct:+.1f}%"


def gerar_html(inicio, fim, r, ant, ranking, por_dia, tem_custos):
    h = html.escape
    periodo = f"{inicio:%d/%m/%Y} a {(fim - timedelta(days=1)):%d/%m/%Y}"

    def card(titulo, valor, var=None):
        v = f'<div class="var">{h(var)} vs. semana anterior</div>' if var else ""
        return f'<div class="card"><div class="t">{h(titulo)}</div><div class="v">{h(valor)}</div>{v}</div>'

    cards = [
        card("Faturamento", brl(r["faturamento"]), variacao(r["faturamento"], ant["faturamento"])),
        card("Pedidos", str(r["pedidos"]), variacao(r["pedidos"], ant["pedidos"])),
        card("Unidades vendidas", str(r["unidades"]), variacao(r["unidades"], ant["unidades"])),
        card("Ticket médio", brl(r["ticket_medio"]), variacao(r["ticket_medio"], ant["ticket_medio"])),
        card("Tarifas do ML", brl(r["tarifas"])),
    ]
    if tem_custos:
        cards.append(card("Lucro estimado", brl(r["lucro"]), variacao(r["lucro"], ant["lucro"])))

    cab_lucro = "<th>Lucro est.</th>" if tem_custos else ""
    linhas_rank = ""
    for i, (item_id, p) in enumerate(ranking[:15], 1):
        lucro = f"<td>{brl(p['receita'] - p['tarifas'] - p['custo'])}</td>" if tem_custos else ""
        linhas_rank += (f"<tr><td>{i}</td><td>{h(p['titulo'])}<br><small>{h(item_id)}</small></td>"
                        f"<td>{p['qtd']}</td><td>{brl(p['receita'])}</td>{lucro}</tr>")

    linhas_dia = "".join(
        f"<tr><td>{d:%a %d/%m}</td><td>{v['pedidos']}</td><td>{brl(v['faturamento'])}</td></tr>"
        for d, v in por_dia.items())

    aviso = ""
    if tem_custos and r["itens_sem_custo"]:
        aviso = ('<p class="aviso">Itens sem custo cadastrado (lucro superestimado): '
                 + h(", ".join(sorted(r["itens_sem_custo"]))) + "</p>")

    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Relatório semanal — {h(periodo)}</title>
<style>
body{{font-family:system-ui,sans-serif;margin:24px auto;max-width:900px;padding:0 16px;color:#222}}
h1{{font-size:1.4rem}} h2{{font-size:1.1rem;margin-top:32px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}}
.card{{background:#f4f6f8;border-radius:10px;padding:14px}}
.t{{font-size:.8rem;color:#666}} .v{{font-size:1.3rem;font-weight:700;margin-top:4px}}
.var{{font-size:.75rem;color:#555;margin-top:4px}}
table{{border-collapse:collapse;width:100%}} th,td{{text-align:left;padding:8px;border-bottom:1px solid #e3e6e9}}
th{{background:#f4f6f8}} small{{color:#888}} .aviso{{color:#b45309;font-size:.85rem}}
</style></head><body>
<h1>Relatório semanal — Mercado Livre</h1>
<p>Período: {h(periodo)}</p>
<div class="cards">{''.join(cards)}</div>
{aviso}
<h2>Produtos mais vendidos (por faturamento)</h2>
<table><tr><th>#</th><th>Produto</th><th>Qtd</th><th>Faturamento</th>{cab_lucro}</tr>{linhas_rank}</table>
<h2>Vendas por dia</h2>
<table><tr><th>Dia</th><th>Pedidos</th><th>Faturamento</th></tr>{linhas_dia}</table>
</body></html>"""


def para_jpeg(url):
    """O Instagram só aceita JPEG. O ML costuma servir .webp; tenta a versão .jpg da mesma imagem."""
    if url.lower().endswith(".webp"):
        return url[:-5] + ".jpg"
    return url


def detalhes_itens(item_ids):
    """Busca permalink e fotos dos itens (endpoint público). Falha silenciosa."""
    info = {}
    ids = list(item_ids)
    for i in range(0, len(ids), 20):
        try:
            r = chamar_api("GET", "/items", params={
                "ids": ",".join(ids[i:i + 20]), "attributes": "id,permalink,thumbnail,pictures"})
        except SystemExit:
            continue
        for e in r:
            if e.get("code") == 200:
                b = e["body"]
                fotos = [para_jpeg(f["secure_url"]) for f in b.get("pictures", []) if f.get("secure_url")]
                if not fotos and b.get("thumbnail"):
                    fotos = [para_jpeg(b["thumbnail"].replace("http://", "https://"))]
                info[b["id"]] = {"link": b.get("permalink", ""), "fotos": fotos}
    return info


def montar_posts(inicio, fim, ranking, qtd_posts=3):
    """Monta os posts (estruturados) com os mais vendidos. Cada post tem legenda p/ Instagram e Facebook."""
    top = ranking[:qtd_posts]
    detalhes = detalhes_itens([i for i, _ in top])
    periodo = f"{inicio:%d/%m} a {(fim - timedelta(days=1)):%d/%m}"
    hashtags = os.environ.get("POST_HASHTAGS", "#mercadolivre #maisvendidos #ofertas #compreonline")
    medalhas = ["🥇", "🥈", "🥉"]
    posts = []

    linhas = [f"🔥 OS MAIS VENDIDOS DA SEMANA ({periodo})", ""]
    imagens_resumo = []
    for n, (item_id, p) in enumerate(top):
        preco = p["receita"] / p["qtd"] if p["qtd"] else 0
        linhas.append(f"{medalhas[n] if n < 3 else '•'} {p['titulo']} — {brl(preco)}")
        fotos = detalhes.get(item_id, {}).get("fotos", [])
        if fotos:
            imagens_resumo.append(fotos[0])
    posts.append({
        "tipo": "resumo", "item_id": None,
        "legenda_ig": "\n".join(linhas + ["", "Link na bio! 🛒", hashtags]),
        "legenda_fb": "\n".join(linhas + ["", "Confira no Mercado Livre! 🛒", hashtags]),
        "imagens": imagens_resumo,
    })

    for n, (item_id, p) in enumerate(top, 1):
        d = detalhes.get(item_id, {})
        preco = p["receita"] / p["qtd"] if p["qtd"] else 0
        corpo = (f"⭐ Campeão de vendas da semana!\n{p['titulo']}\n"
                 f"Por apenas {brl(preco)}\n"
                 f"{p['qtd']} unidades vendidas nos últimos dias — garanta a sua antes que acabe! 🚀")
        link = d.get("link", "")
        posts.append({
            "tipo": "produto", "item_id": item_id, "link": link,
            "legenda_ig": f"{corpo}\n👉 Link na bio\n{hashtags}",
            "legenda_fb": f"{corpo}\n👉 {link or '[cole o link do anúncio]'}\n{hashtags}",
            "imagens": d.get("fotos", [])[:1],
        })
    return posts


def posts_em_texto(posts):
    """Versão legível (para revisar antes de publicar)."""
    blocos = []
    for n, p in enumerate(posts):
        nome = "POST RESUMO (carrossel)" if p["tipo"] == "resumo" else f"POST {n}: {p['item_id']}"
        imgs = "\n".join(f"  {u}" for u in p["imagens"]) or "  [sem imagem: use sua foto]"
        blocos.append(f"=== {nome} ===\n--- Instagram ---\n{p['legenda_ig']}\n"
                      f"--- Facebook ---\n{p['legenda_fb']}\n--- Imagens ---\n{imgs}")
    return "\n\n".join(blocos) + "\n"


def enviar_email(destinos, inicio, fim, r, arq_html, anexos):
    """Envia o relatório por SMTP. Gmail: use uma 'senha de app' em SMTP_PASS."""
    faltando = [v for v in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS") if not os.environ.get(v)]
    if faltando:
        sys.exit("Para enviar e-mail defina: " + ", ".join(faltando)
                 + " (opcionais: SMTP_PORT=465, EMAIL_FROM)")
    remetente = os.environ.get("EMAIL_FROM", os.environ["SMTP_USER"])
    porta = int(os.environ.get("SMTP_PORT", "465"))
    periodo = f"{inicio:%d/%m} a {(fim - timedelta(days=1)):%d/%m}"

    msg = EmailMessage()
    msg["Subject"] = f"Relatório semanal ML ({periodo}) — {brl(r['faturamento'])} em {r['pedidos']} pedidos"
    msg["From"] = remetente
    msg["To"] = ", ".join(destinos)
    msg.set_content(f"Faturamento: {brl(r['faturamento'])}\nPedidos: {r['pedidos']}\n"
                    f"Unidades: {r['unidades']}\nVeja o relatório completo em HTML.")
    with open(arq_html, encoding="utf-8") as f:
        msg.add_alternative(f.read(), subtype="html")
    for caminho in anexos:
        with open(caminho, "rb") as f:
            msg.add_attachment(f.read(), maintype="application", subtype="octet-stream",
                               filename=os.path.basename(caminho))

    contexto = ssl.create_default_context()
    try:
        if porta == 465:
            with smtplib.SMTP_SSL(os.environ["SMTP_HOST"], porta, context=contexto, timeout=30) as s:
                s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
                s.send_message(msg)
        else:  # 587 = STARTTLS
            with smtplib.SMTP(os.environ["SMTP_HOST"], porta, timeout=30) as s:
                s.starttls(context=contexto)
                s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
                s.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        sys.exit(f"Falha ao enviar e-mail: {e}")
    print("E-mail enviado para:", ", ".join(destinos))


def main():
    ap = argparse.ArgumentParser(description="Relatório semanal de vendas do Mercado Livre")
    ap.add_argument("--auth", metavar="CODIGO", help="troca o código de autorização por tokens (1ª vez)")
    ap.add_argument("--semanas-atras", type=int, default=1,
                    help="1 = última semana fechada (seg-dom), 2 = a anterior, etc. (padrão: 1)")
    ap.add_argument("--ultimos-7-dias", action="store_true", help="usa os últimos 7 dias corridos")
    ap.add_argument("--custos", metavar="CSV", help="CSV com colunas item_id,custo_unitario")
    ap.add_argument("--saida", default=".", help="pasta de saída (padrão: atual)")
    ap.add_argument("--email", metavar="DEST", help="envia o relatório por e-mail (separe vários com vírgula)")
    ap.add_argument("--posts", action="store_true",
                    help="gera posts_AAAA-MM-DD.txt (para revisar) e .json (para publicar com postar_redes.py)")
    args = ap.parse_args()

    if args.auth:
        autorizar(args.auth)
        return

    hoje = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    if args.ultimos_7_dias:
        fim = hoje
        inicio = fim - timedelta(days=7)
    else:
        segunda_atual = hoje - timedelta(days=hoje.weekday())
        inicio = segunda_atual - timedelta(weeks=args.semanas_atras)
        fim = inicio + timedelta(days=7)
    inicio_ant, fim_ant = inicio - timedelta(days=7), inicio

    custos = carregar_custos(args.custos)
    token, vendedor = obter_access_token()

    print(f"Buscando pedidos de {inicio:%d/%m} a {(fim - timedelta(days=1)):%d/%m}...")
    pedidos = buscar_pedidos(token, vendedor, inicio, fim)
    pedidos_ant = buscar_pedidos(token, vendedor, inicio_ant, fim_ant)

    r, ranking, por_dia, linhas = analisar(pedidos, custos)
    ant, _, _, _ = analisar(pedidos_ant, custos)

    os.makedirs(args.saida, exist_ok=True)
    marca = f"{inicio:%Y-%m-%d}"
    arq_html = os.path.join(args.saida, f"relatorio_{marca}.html")
    arq_csv = os.path.join(args.saida, f"vendas_{marca}.csv")

    with open(arq_html, "w", encoding="utf-8") as f:
        f.write(gerar_html(inicio, fim, r, ant, ranking, por_dia, bool(custos)))
    with open(arq_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["pedido", "data", "status", "item_id", "produto", "qtd",
                    "preco_unit", "receita", "tarifa_ml", "custo"])
        w.writerows(linhas)

    print(f"\nFaturamento: {brl(r['faturamento'])} ({variacao(r['faturamento'], ant['faturamento'])} vs. semana anterior)")
    print(f"Pedidos: {r['pedidos']} | Unidades: {r['unidades']} | Ticket médio: {brl(r['ticket_medio'])}")
    if custos:
        print(f"Lucro estimado: {brl(r['lucro'])}")
    print("\nTop 5 produtos:")
    for i, (item_id, p) in enumerate(ranking[:5], 1):
        print(f"  {i}. {p['titulo'][:60]} — {p['qtd']} un. — {brl(p['receita'])}")
    print(f"\nArquivos gerados:\n  {arq_html}\n  {arq_csv}")

    anexos = [arq_csv]
    if args.posts:
        arq_posts = os.path.join(args.saida, f"posts_{marca}.txt")
        arq_posts_json = os.path.join(args.saida, f"posts_{marca}.json")
        posts = montar_posts(inicio, fim, ranking)
        with open(arq_posts, "w", encoding="utf-8") as f:
            f.write(posts_em_texto(posts))
        with open(arq_posts_json, "w", encoding="utf-8") as f:
            json.dump(posts, f, ensure_ascii=False, indent=2)
        anexos.append(arq_posts)
        print(f"  {arq_posts}\n  {arq_posts_json}")

    if args.email:
        destinos = [d.strip() for d in args.email.split(",") if d.strip()]
        enviar_email(destinos, inicio, fim, r, arq_html, anexos)


if __name__ == "__main__":
    main()
