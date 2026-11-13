#!/usr/bin/env python3
"""
Radar semanal de mercado do Mercado Livre (Brasil).

Descobre o que mais vende e quais nichos (categorias) estão mais fortes, usando a API oficial:
  - /highlights/MLB/category/<id>  -> os 20 mais vendidos de cada categoria (ranking do Mercado Livre)
  - /items                         -> preço, vendas acumuladas, frete, Full, loja oficial
  - /trends/MLB                    -> buscas em alta (se disponível)

Compara com a semana anterior (arquivos em dados/) para estimar:
  - vendas da semana por produto  = diferença das "vendas acumuladas" entre uma semana e a outra
  - produtos novos no ranking e quem subiu/caiu

Gera: radar_AAAA-MM-DD.html (relatório completo), radar_AAAA-MM-DD.csv e envia por e-mail.
Só usa a biblioteca padrão do Python 3.8+.

NÃO usa suas vendas e não exige conta de vendedor: precisa só de um app (App ID e Secret Key)
criado em https://developers.mercadolivre.com.br/devcenter

Variáveis de ambiente:
  ML_APP_ID, ML_CLIENT_SECRET            (obrigatórias)
  SMTP_HOST, SMTP_USER, SMTP_PASS        (para --email; opcionais SMTP_PORT=465, EMAIL_FROM)

Uso:
  python3 radar_mercado_ml.py                       # categorias principais (nível 1)
  python3 radar_mercado_ml.py --nivel 2             # nichos mais específicos (subcategorias)
  python3 radar_mercado_ml.py --categorias MLB1055,MLB1648
  python3 radar_mercado_ml.py --email voce@exemplo.com

Limitações importantes:
  - Os números de vendas vêm do próprio Mercado Livre e podem ser aproximados (faixas).
  - "Vendas da semana" só aparece a partir da 2ª execução (precisa do histórico da semana anterior).
  - O ranking é do Mercado Livre: ele não informa unidades vendidas por categoria.
"""

import argparse
import csv
import glob
import html
import json
import os
import smtplib
import ssl
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

API = "https://api.mercadolibre.com"
SITE = "MLB"
TZ = timezone(timedelta(hours=-3))  # Brasília
PAUSA = 0.2  # segundos entre chamadas (educação com a API)
CAMPOS_ITEM = ("id,title,price,sold_quantity,permalink,thumbnail,seller_id,"
               "shipping,official_store_id,condition,category_id")


class ErroAPI(Exception):
    def __init__(self, status, msg):
        super().__init__(f"HTTP {status}: {msg}")
        self.status = status


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------
def http(metodo, caminho, params=None, token=None, dados=None, tolerante=False):
    """Chama a API com repetição em erros temporários.
    tolerante=True devolve None em 400/404 (ex.: categoria sem ranking). 401/403 sempre geram erro."""
    url = API + caminho + ("?" + urllib.parse.urlencode(params) if params else "")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    corpo = None
    if dados is not None:
        corpo = urllib.parse.urlencode(dados).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    for tentativa in range(4):
        try:
            time.sleep(PAUSA)
            req = urllib.request.Request(url, data=corpo, headers=headers, method=metodo)
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            detalhe = e.read().decode(errors="replace")[:300]
            if e.code in (429, 500, 502, 503, 504) and tentativa < 3:
                time.sleep(2 ** (tentativa + 1))
                continue
            if tolerante and e.code in (400, 404):
                return None
            raise ErroAPI(e.code, detalhe)
        except urllib.error.URLError as e:
            if tentativa < 3:
                time.sleep(2 ** (tentativa + 1))
                continue
            raise ErroAPI(0, f"falha de conexão: {e.reason}")


def obter_token():
    app, segredo = os.environ.get("ML_APP_ID"), os.environ.get("ML_CLIENT_SECRET")
    if not app or not segredo:
        sys.exit("Defina ML_APP_ID e ML_CLIENT_SECRET (veja o topo do script).")
    try:
        r = http("POST", "/oauth/token", dados={
            "grant_type": "client_credentials", "client_id": app, "client_secret": segredo})
    except ErroAPI as e:
        sys.exit(f"Não consegui o token do Mercado Livre ({e}). Confira o App ID/Secret Key e se o "
                 "app está ativo no DevCenter.")
    return r["access_token"]


def listar_categorias(token, nivel, ids):
    """Retorna a lista de categorias a analisar: [{id, nome}]."""
    if ids:
        base = []
        for cid in ids:
            d = http("GET", f"/categories/{cid}", token=token, tolerante=True) or {}
            base.append({"id": cid, "nome": d.get("name", cid), "filhos": d.get("children_categories")})
    else:
        raiz = http("GET", f"/sites/{SITE}/categories", token=token)
        base = [{"id": c["id"], "nome": c["name"], "filhos": None} for c in raiz]
    if nivel < 2:
        return [{"id": b["id"], "nome": b["nome"]} for b in base]
    cats = []
    for b in base:
        filhos = b["filhos"]
        if filhos is None:
            d = http("GET", f"/categories/{b['id']}", token=token, tolerante=True) or {}
            filhos = d.get("children_categories", [])
        if not filhos:
            cats.append({"id": b["id"], "nome": b["nome"]})
        for f in filhos:
            cats.append({"id": f["id"], "nome": f"{b['nome']} › {f['name']}"})
    return cats


def buscar_itens(token, ids, cache):
    faltam = [i for i in ids if i not in cache]
    for k in range(0, len(faltam), 20):
        lote = faltam[k:k + 20]
        try:
            r = http("GET", "/items", params={"ids": ",".join(lote), "attributes": CAMPOS_ITEM},
                     token=token, tolerante=True) or []
        except ErroAPI:
            continue
        for e in r:
            if e.get("code") == 200 and e.get("body"):
                cache[e["body"]["id"]] = e["body"]


def buscar_produtos(token, ids, cache):
    for pid in ids:
        if pid in cache:
            continue
        try:
            cache[pid] = http("GET", f"/products/{pid}", token=token, tolerante=True) or {}
        except ErroAPI:
            cache[pid] = {}


def link_item(item_id):
    return f"https://produto.mercadolivre.com.br/{item_id[:3]}-{item_id[3:]}"


def coletar_categoria(token, cat, cache_itens, cache_prod):
    r = http("GET", f"/highlights/{SITE}/category/{cat['id']}", token=token, tolerante=True)
    if not r or not r.get("content"):
        return None
    conteudo = sorted(r["content"], key=lambda c: c.get("position", 99))
    buscar_itens(token, [c["id"] for c in conteudo if c.get("type") == "ITEM"], cache_itens)
    buscar_produtos(token, [c["id"] for c in conteudo if c.get("type") == "PRODUCT"], cache_prod)
    linhas = []
    for c in conteudo:
        base = {"posicao": c.get("position"), "tipo": c.get("type"), "id": c["id"],
                "categoria_id": cat["id"], "categoria": cat["nome"]}
        if c.get("type") == "ITEM":
            b = cache_itens.get(c["id"], {})
            envio = b.get("shipping") or {}
            base.update({
                "titulo": b.get("title") or c["id"], "preco": b.get("price"),
                "vendidos": b.get("sold_quantity"),
                "link": b.get("permalink") or link_item(c["id"]), "foto": b.get("thumbnail", ""),
                "vendedor": b.get("seller_id"), "frete_gratis": bool(envio.get("free_shipping")),
                "full": envio.get("logistic_type") == "fulfillment",
                "loja_oficial": bool(b.get("official_store_id"))})
        else:  # PRODUCT (catálogo): sem preço/vendas individuais
            p = cache_prod.get(c["id"], {})
            fotos = p.get("pictures") or []
            base.update({
                "titulo": p.get("name") or c["id"], "preco": None, "vendidos": None,
                "link": f"https://www.mercadolivre.com.br/p/{c['id']}",
                "foto": (fotos[0].get("url") if fotos else "") or "", "vendedor": None,
                "frete_gratis": False, "full": False, "loja_oficial": False})
        linhas.append(base)
    return linhas


def buscar_tendencias(token):
    try:
        r = http("GET", f"/trends/{SITE}", token=token, tolerante=True)
    except ErroAPI:
        return None
    return r if isinstance(r, list) else None


# ----------------------------------------------------------------------
# Histórico e análise
# ----------------------------------------------------------------------
def carregar_anterior(pasta, hoje):
    arqs = sorted(a for a in glob.glob(os.path.join(pasta, "snapshot_*.json"))
                  if os.path.basename(a) != f"snapshot_{hoje}.json")
    if not arqs:
        return None
    try:
        with open(arqs[-1], encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def comparar(linhas_por_cat, anterior):
    """Preenche pos_anterior, delta (vendas na semana) e novo em cada linha."""
    for cid, linhas in linhas_por_cat.items():
        cat_existia = bool(anterior) and cid in anterior.get("categorias", {})
        for l in linhas:
            l["pos_anterior"], l["delta"], l["novo"] = None, None, False
            if not anterior:
                continue
            ant = anterior["itens"].get(f"{cid}|{l['id']}")
            if ant:
                l["pos_anterior"] = ant.get("pos")
                if l["vendidos"] is not None and ant.get("vendidos") is not None:
                    d = l["vendidos"] - ant["vendidos"]
                    l["delta"] = d if d >= 0 else None
            elif cat_existia:
                l["novo"] = True


def metricas(linhas):
    precos = [l["preco"] for l in linhas if l["preco"] is not None]
    itens = [l for l in linhas if l["tipo"] == "ITEM"]
    n = len(itens) or 1
    vend = [l["vendidos"] for l in linhas if l["vendidos"] is not None]
    deltas = [l["delta"] for l in linhas if l["delta"] is not None]
    return {
        "n": len(linhas),
        "preco_mediano": statistics.median(precos) if precos else None,
        "vendidos_total": sum(vend) if vend else None,
        "vendas_semana": sum(deltas) if deltas else None,
        "vendedores": len({l["vendedor"] for l in itens if l["vendedor"]}),
        "pct_frete": 100 * sum(l["frete_gratis"] for l in itens) / n,
        "pct_full": 100 * sum(l["full"] for l in itens) / n,
        "novos": sum(1 for l in linhas if l["novo"]),
    }


def chave_nicho(m):
    return (m["vendas_semana"] if m["vendas_semana"] is not None else -1,
            m["vendidos_total"] or 0)


def chave_produto(l):
    return (l["delta"] if l["delta"] is not None else -1, l["vendidos"] or 0, -(l["posicao"] or 99))


# ----------------------------------------------------------------------
# Saída
# ----------------------------------------------------------------------
def brl(v):
    if v is None:
        return "—"
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def inteiro(v):
    return "—" if v is None else f"{int(v):,}".replace(",", ".")


def gerar_html(hoje, cats, linhas_por_cat, mets, tendencias, tem_anterior, erros, completo):
    h = html.escape
    ordem = sorted(mets, key=lambda c: chave_nicho(mets[c]), reverse=True)
    nomes = {c["id"]: c["nome"] for c in cats}
    todas = [l for ls in linhas_por_cat.values() for l in ls]
    # produtos únicos (mantém a melhor ocorrência)
    unicos = {}
    for l in todas:
        if l["id"] not in unicos or chave_produto(l) > chave_produto(unicos[l["id"]]):
            unicos[l["id"]] = l
    top_prod = sorted(unicos.values(), key=chave_produto, reverse=True)[:30]
    novos = [l for l in todas if l["novo"]][:20]

    def linha_prod(l, i=None):
        foto = f'<img src="{h(l["foto"])}" width="40" height="40" alt="">' if l.get("foto") else ""
        mov = ""
        if l.get("pos_anterior") and l.get("posicao"):
            dif = l["pos_anterior"] - l["posicao"]
            mov = f" ({'▲' if dif > 0 else '▼'}{abs(dif)})" if dif else ""
        elif l.get("novo"):
            mov = " 🆕"
        semana = inteiro(l["delta"]) if tem_anterior else "—"
        return (f"<tr>{'<td>' + str(i) + '</td>' if i else ''}<td>{foto}</td>"
                f'<td><a href="{h(l["link"])}">{h(l["titulo"][:90])}</a><br>'
                f"<small>{h(l['categoria'])}</small></td>"
                f"<td>{brl(l['preco'])}</td><td>{inteiro(l['vendidos'])}</td><td>{semana}</td>"
                f"<td>#{l['posicao']}{mov}</td></tr>")

    cab_prod = ("<th>Foto</th><th>Produto</th><th>Preço</th><th>Vendas acum.</th>"
                "<th>Vendas na semana*</th><th>Posição</th>")

    linhas_nicho = ""
    for i, cid in enumerate(ordem[:25 if not completo else 100], 1):
        m = mets[cid]
        semana = inteiro(m["vendas_semana"]) if tem_anterior else "—"
        linhas_nicho += (f"<tr><td>{i}</td><td>{h(nomes.get(cid, cid))}</td><td>{semana}</td>"
                         f"<td>{inteiro(m['vendidos_total'])}</td><td>{brl(m['preco_mediano'])}</td>"
                         f"<td>{m['vendedores']}</td><td>{m['pct_frete']:.0f}%</td>"
                         f"<td>{m['pct_full']:.0f}%</td><td>{m['novos'] if tem_anterior else '—'}</td></tr>")

    tend = ""
    if tendencias:
        itens = "".join(f'<li><a href="{h(t.get("url", "#"))}">{h(t.get("keyword", ""))}</a></li>'
                        for t in tendencias[:30])
        tend = f"<h2>Buscas em alta no Mercado Livre</h2><ol class='tend'>{itens}</ol>"

    sec_novos = ""
    if tem_anterior and novos:
        sec_novos = ("<h2>Novos no ranking esta semana</h2><table><tr><th>Foto</th><th>Produto</th>"
                     "<th>Preço</th><th>Vendas acum.</th><th>Vendas na semana*</th><th>Posição</th></tr>"
                     + "".join(linha_prod(l) for l in novos) + "</table>")

    detalhes = ""
    if completo:
        for cid in ordem:
            ls = linhas_por_cat[cid]
            detalhes += (f"<details><summary>{h(nomes.get(cid, cid))} — top {len(ls)}</summary>"
                         f"<table><tr><th>Foto</th><th>Produto</th><th>Preço</th><th>Vendas acum.</th>"
                         f"<th>Vendas na semana*</th><th>Posição</th></tr>"
                         + "".join(linha_prod(l) for l in ls) + "</table></details>")
        detalhes = "<h2>Ranking completo por nicho</h2>" + detalhes

    aviso_hist = ("" if tem_anterior else
                  "<p class='aviso'>Primeira execução: ainda não há semana anterior para comparar. "
                  "As colunas de vendas da semana e novidades aparecem a partir da próxima segunda.</p>")
    aviso_err = (f"<p class='aviso'>{len(erros)} categoria(s) sem ranking ou com falha: "
                 f"{h(', '.join(erros[:8]))}{'…' if len(erros) > 8 else ''}</p>") if erros else ""

    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Radar de mercado — Mercado Livre — {h(hoje)}</title>
<style>
body{{font-family:system-ui,sans-serif;margin:24px auto;max-width:980px;padding:0 16px;color:#222}}
h1{{font-size:1.4rem}} h2{{font-size:1.1rem;margin-top:32px}}
table{{border-collapse:collapse;width:100%;font-size:.9rem}} th,td{{text-align:left;padding:7px;border-bottom:1px solid #e3e6e9;vertical-align:middle}}
th{{background:#f4f6f8}} small{{color:#777}} .aviso{{color:#b45309;font-size:.85rem}}
a{{color:#0b5cad;text-decoration:none}} details{{margin:8px 0}} summary{{cursor:pointer;font-weight:600;padding:6px 0}}
.tend{{columns:2;font-size:.9rem}} .nota{{color:#666;font-size:.8rem;margin-top:24px}}
</style></head><body>
<h1>Radar de mercado — Mercado Livre</h1>
<p>Semana de {h(hoje)} · {len(mets)} nichos · {len(unicos)} produtos analisados</p>
{aviso_hist}{aviso_err}
<h2>Nichos mais fortes</h2>
<table><tr><th>#</th><th>Nicho</th><th>Vendas na semana*</th><th>Vendas acum. (top 20)</th>
<th>Preço mediano</th><th>Vendedores distintos</th><th>Frete grátis</th><th>Full</th><th>Novos</th></tr>
{linhas_nicho}</table>
<p class="nota">Poucos vendedores distintos no top 20 indica nicho concentrado (mais difícil de entrar);
muitos indica mercado pulverizado.</p>
<h2>Produtos mais vendidos (geral)</h2>
<table><tr><th>#</th>{cab_prod}</tr>{''.join(linha_prod(l, i) for i, l in enumerate(top_prod, 1))}</table>
{sec_novos}{tend}{detalhes}
<p class="nota">* Vendas na semana = diferença das vendas acumuladas informadas pelo Mercado Livre entre
duas execuções semanais (estimativa; o Mercado Livre pode arredondar esses números).
O ranking dos "mais vendidos" é calculado pelo próprio Mercado Livre.</p>
</body></html>"""


def enviar_email(destinos, assunto, html_corpo, anexos):
    faltam = [v for v in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS") if not os.environ.get(v)]
    if faltam:
        sys.exit("Para enviar e-mail defina: " + ", ".join(faltam))
    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = os.environ.get("EMAIL_FROM") or os.environ["SMTP_USER"]
    msg["To"] = ", ".join(destinos)
    msg.set_content("Seu e-mail não exibe HTML. Abra o relatório completo em anexo.")
    msg.add_alternative(html_corpo, subtype="html")
    for caminho in anexos:
        with open(caminho, "rb") as f:
            msg.add_attachment(f.read(), maintype="application", subtype="octet-stream",
                               filename=os.path.basename(caminho))
    porta = int(os.environ.get("SMTP_PORT") or "465")
    ctx = ssl.create_default_context()
    try:
        if porta == 465:
            with smtplib.SMTP_SSL(os.environ["SMTP_HOST"], porta, context=ctx, timeout=30) as s:
                s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(os.environ["SMTP_HOST"], porta, timeout=30) as s:
                s.starttls(context=ctx)
                s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
                s.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        sys.exit(f"Falha ao enviar e-mail: {e}")
    print("E-mail enviado para:", ", ".join(destinos))


def main():
    global PAUSA
    ap = argparse.ArgumentParser(description="Radar semanal de mercado do Mercado Livre")
    ap.add_argument("--nivel", type=int, choices=[1, 2], default=1,
                    help="1 = categorias principais; 2 = subcategorias (nichos), mais demorado")
    ap.add_argument("--categorias", help="IDs de categorias separados por vírgula (ex.: MLB1055,MLB1648)")
    ap.add_argument("--saida", default="relatorios", help="pasta dos relatórios (padrão: relatorios)")
    ap.add_argument("--dados", default="dados", help="pasta do histórico semanal (padrão: dados)")
    ap.add_argument("--email", metavar="DEST", help="envia por e-mail (vários: separe por vírgula)")
    ap.add_argument("--pausa", type=float, default=PAUSA, help="segundos entre chamadas à API")
    args = ap.parse_args()
    PAUSA = args.pausa

    hoje = datetime.now(TZ).strftime("%Y-%m-%d")
    ids = [c.strip() for c in args.categorias.split(",") if c.strip()] if args.categorias else None

    token = obter_token()
    try:
        cats = listar_categorias(token, args.nivel, ids)
    except ErroAPI as e:
        sys.exit(f"Não consegui listar as categorias ({e}).")
    print(f"Analisando {len(cats)} categorias...")

    cache_itens, cache_prod = {}, {}
    linhas_por_cat, erros, sucesso = {}, [], False
    for n, cat in enumerate(cats, 1):
        try:
            linhas = coletar_categoria(token, cat, cache_itens, cache_prod)
        except ErroAPI as e:
            if e.status in (401, 403) and not sucesso:
                sys.exit(
                    f"O Mercado Livre recusou o acesso ao ranking de mais vendidos ({e}).\n"
                    "Possíveis causas: (1) o token só de aplicativo não tem permissão para este "
                    "endpoint, caso em que é preciso autorizar com sua conta (peça o modo de "
                    "autorização por usuário); (2) o app não está ativo; (3) bloqueio de IP.")
            erros.append(cat["nome"])
            continue
        if linhas:
            linhas_por_cat[cat["id"]] = linhas
            sucesso = True
        else:
            erros.append(cat["nome"])
        if n % 10 == 0:
            print(f"  {n}/{len(cats)} categorias")
    if not linhas_por_cat:
        sys.exit("Nenhuma categoria retornou ranking. Rode de novo mais tarde ou use --categorias.")

    tendencias = buscar_tendencias(token)
    anterior = carregar_anterior(args.dados, hoje)
    comparar(linhas_por_cat, anterior)
    mets = {cid: metricas(ls) for cid, ls in linhas_por_cat.items()}

    os.makedirs(args.saida, exist_ok=True)
    os.makedirs(args.dados, exist_ok=True)
    arq_html = os.path.join(args.saida, f"radar_{hoje}.html")
    arq_csv = os.path.join(args.saida, f"radar_{hoje}.csv")
    with open(arq_html, "w", encoding="utf-8") as f:
        f.write(gerar_html(hoje, cats, linhas_por_cat, mets, tendencias, bool(anterior), erros, True))
    with open(arq_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["data", "categoria_id", "categoria", "posicao", "tipo", "id", "titulo", "preco",
                    "vendas_acumuladas", "vendas_semana_est", "posicao_anterior", "novo",
                    "frete_gratis", "full", "loja_oficial", "vendedor", "link"])
        for ls in linhas_por_cat.values():
            for l in ls:
                w.writerow([hoje, l["categoria_id"], l["categoria"], l["posicao"], l["tipo"], l["id"],
                            l["titulo"], l["preco"], l["vendidos"], l["delta"], l["pos_anterior"],
                            int(l["novo"]), int(l["frete_gratis"]), int(l["full"]),
                            int(l["loja_oficial"]), l["vendedor"], l["link"]])

    snapshot = {"data": hoje,
                "categorias": {cid: {"nome": linhas_por_cat[cid][0]["categoria"]} for cid in linhas_por_cat},
                "itens": {f"{cid}|{l['id']}": {"vendidos": l["vendidos"], "pos": l["posicao"],
                                               "titulo": l["titulo"]}
                          for cid, ls in linhas_por_cat.items() for l in ls}}
    with open(os.path.join(args.dados, f"snapshot_{hoje}.json"), "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False)

    ordem = sorted(mets, key=lambda c: chave_nicho(mets[c]), reverse=True)
    nome = {c["id"]: c["nome"] for c in cats}
    print(f"\nTop 5 nichos ({'vendas na semana' if anterior else 'vendas acumuladas'}):")
    for cid in ordem[:5]:
        m = mets[cid]
        v = m["vendas_semana"] if anterior else m["vendidos_total"]
        print(f"  {nome[cid]} — {inteiro(v)}")
    print(f"\nArquivos:\n  {arq_html}\n  {arq_csv}\n  {os.path.join(args.dados, f'snapshot_{hoje}.json')}")
    if erros:
        print(f"\n{len(erros)} categoria(s) sem ranking/dados.")

    if args.email:
        destinos = [d.strip() for d in args.email.split(",") if d.strip()]
        corpo = gerar_html(hoje, cats, linhas_por_cat, mets, tendencias, bool(anterior), erros, False)
        topo = nome[ordem[0]] if ordem else "—"
        enviar_email(destinos, f"Radar de mercado ML ({datetime.now(TZ):%d/%m}) — nicho em destaque: {topo}",
                     corpo, [arq_html, arq_csv])


if __name__ == "__main__":
    main()
