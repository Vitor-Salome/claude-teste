#!/usr/bin/env python3
"""
Publica os posts gerados por relatorio_semanal_ml.py (arquivo posts_AAAA-MM-DD.json)
no Instagram e/ou na página do Facebook, via Meta Graph API.

Só usa a biblioteca padrão do Python 3.8+.

POR SEGURANÇA, o padrão é SIMULAR (dry-run): mostra o que seria publicado sem postar nada.
Para publicar de verdade, use --publicar.

Variáveis de ambiente:
  META_ACCESS_TOKEN   token de acesso da PÁGINA (veja LEIA-ME.md)
  IG_USER_ID          ID da conta profissional do Instagram  (omita para não postar no Instagram)
  FB_PAGE_ID          ID da página do Facebook               (omita para não postar no Facebook)
  META_API_VERSION    opcional, padrão v25.0

Uso:
  python3 postar_redes.py relatorios/posts_2026-11-02.json              # simulação
  python3 postar_redes.py relatorios/posts_2026-11-02.json --publicar   # publica
  python3 postar_redes.py posts.json --publicar --somente instagram --limite 2

Regras da API do Instagram:
  - a imagem precisa ser JPEG e estar numa URL pública;
  - limite de ~100 publicações por API a cada 24h (carrossel conta como 1).
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

VERSAO = os.environ.get("META_API_VERSION", "v25.0")
BASE = f"https://graph.facebook.com/{VERSAO}"


class ErroMeta(Exception):
    pass


def chamar(metodo, caminho, dados=None):
    token = os.environ["META_ACCESS_TOKEN"]
    dados = dict(dados or {})
    dados["access_token"] = token
    corpo = urllib.parse.urlencode(dados).encode()
    if metodo == "GET":
        req = urllib.request.Request(f"{BASE}{caminho}?{corpo.decode()}", method="GET")
    else:
        req = urllib.request.Request(f"{BASE}{caminho}", data=corpo, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode(errors="replace").replace(token, "***")
        try:
            msg = json.loads(detalhe).get("error", {}).get("message", detalhe)
        except ValueError:
            msg = detalhe
        raise ErroMeta(f"HTTP {e.code} em {caminho}: {msg}")
    except urllib.error.URLError as e:
        raise ErroMeta(f"Falha de conexão com a Meta: {e.reason}")


def aguardar_container(container_id, tentativas=12, espera=5):
    """Espera o Instagram terminar de processar a mídia (status FINISHED)."""
    for _ in range(tentativas):
        st = chamar("GET", f"/{container_id}", {"fields": "status_code"}).get("status_code")
        if st == "FINISHED":
            return
        if st in ("ERROR", "EXPIRED"):
            raise ErroMeta(f"Instagram recusou a mídia (status {st}). "
                           "Verifique se a imagem é JPEG e a URL é pública.")
        time.sleep(espera)
    raise ErroMeta("Tempo esgotado esperando o Instagram processar a mídia.")


def publicar_instagram(ig_id, legenda, imagens):
    if not imagens:
        raise ErroMeta("post sem imagem (o Instagram exige imagem)")
    if len(imagens) == 1:
        c = chamar("POST", f"/{ig_id}/media", {"image_url": imagens[0], "caption": legenda})["id"]
    else:  # carrossel (máx. 10)
        filhos = []
        for url in imagens[:10]:
            f = chamar("POST", f"/{ig_id}/media", {"image_url": url, "is_carousel_item": "true"})["id"]
            aguardar_container(f)
            filhos.append(f)
        c = chamar("POST", f"/{ig_id}/media", {
            "media_type": "CAROUSEL", "children": ",".join(filhos), "caption": legenda})["id"]
    aguardar_container(c)
    return chamar("POST", f"/{ig_id}/media_publish", {"creation_id": c})["id"]


def publicar_facebook(page_id, legenda, imagens):
    if not imagens:  # sem imagem: post de texto
        return chamar("POST", f"/{page_id}/feed", {"message": legenda})["id"]
    if len(imagens) == 1:
        r = chamar("POST", f"/{page_id}/photos", {"url": imagens[0], "caption": legenda})
        return r.get("post_id") or r["id"]
    anexos = {}
    for i, url in enumerate(imagens[:10]):
        f = chamar("POST", f"/{page_id}/photos", {"url": url, "published": "false"})["id"]
        anexos[f"attached_media[{i}]"] = json.dumps({"media_fbid": f})
    return chamar("POST", f"/{page_id}/feed", {"message": legenda, **anexos})["id"]


def main():
    ap = argparse.ArgumentParser(description="Publica os posts do relatório no Instagram/Facebook")
    ap.add_argument("arquivo", help="posts_AAAA-MM-DD.json gerado pelo relatório")
    ap.add_argument("--publicar", action="store_true", help="publica de verdade (padrão: só simula)")
    ap.add_argument("--somente", choices=["instagram", "facebook"], help="limita a uma rede")
    ap.add_argument("--limite", type=int, help="publica só os N primeiros posts")
    ap.add_argument("--pausa", type=int, default=10, help="segundos entre posts (padrão 10)")
    args = ap.parse_args()

    with open(args.arquivo, encoding="utf-8") as f:
        posts = json.load(f)
    if args.limite:
        posts = posts[:args.limite]

    ig_id = os.environ.get("IG_USER_ID") if args.somente != "facebook" else None
    fb_id = os.environ.get("FB_PAGE_ID") if args.somente != "instagram" else None
    if not ig_id and not fb_id:
        sys.exit("Defina IG_USER_ID e/ou FB_PAGE_ID.")
    if args.publicar and not os.environ.get("META_ACCESS_TOKEN"):
        sys.exit("Defina META_ACCESS_TOKEN para publicar.")

    modo = "PUBLICANDO" if args.publicar else "SIMULAÇÃO (nada será postado; use --publicar)"
    print(f"{modo} | Instagram: {'sim' if ig_id else 'não'} | Facebook: {'sim' if fb_id else 'não'}\n")

    falhas, feitos = 0, 0
    for n, p in enumerate(posts):
        rotulo = "resumo (carrossel)" if p["tipo"] == "resumo" else p["item_id"]
        for rede, rid, legenda, func in (
                ("Instagram", ig_id, p["legenda_ig"], publicar_instagram),
                ("Facebook", fb_id, p["legenda_fb"], publicar_facebook)):
            if not rid:
                continue
            if not args.publicar:
                print(f"[{rede}] {rotulo}: {len(p['imagens'])} imagem(ns)\n{legenda}\n")
                continue
            try:
                post_id = func(rid, legenda, p["imagens"])
                print(f"[{rede}] {rotulo}: publicado (id {post_id})")
                feitos += 1
            except ErroMeta as e:
                falhas += 1
                print(f"[{rede}] {rotulo}: FALHOU - {e}", file=sys.stderr)
        if args.publicar and n < len(posts) - 1:
            time.sleep(args.pausa)

    if args.publicar:
        print(f"\nResumo: {feitos} publicado(s), {falhas} falha(s).")
        if falhas:
            sys.exit(1)


if __name__ == "__main__":
    main()
