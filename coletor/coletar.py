"""Robô de preços: lê config/produtos.json, coleta o preço de cada produto em cada loja
e acrescenta uma linha por produto e loja em data/precos.csv.

Só usa a biblioteca padrão do Python, para rodar de graça no GitHub Actions.
Uma loja que falhar não derruba as outras: a linha fica com status de erro.
"""

import csv
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime
from zoneinfo import ZoneInfo

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(RAIZ, "config", "produtos.json")
CSV_PATH = os.path.join(RAIZ, "data", "precos.csv")
ULTIMO_PATH = os.path.join(RAIZ, "data", "ultimo.json")

CAMPOS = [
    "data_hora", "produto_id", "produto", "loja", "preco_pix", "preco_cheio",
    "disponivel", "vendedor", "titulo_encontrado", "url", "status", "fonte", "tipo_oferta", "preco_de",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}


def baixar(url, tentativas=2):
    """Retorna (status_http, texto). status 0 = erro de rede."""
    ultimo_erro = ""
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=40) as r:
                corpo = r.read()
                enc = r.headers.get("Content-Encoding", "")
                if enc == "gzip":
                    corpo = gzip.decompress(corpo)
                elif enc == "deflate":
                    corpo = zlib.decompress(corpo)
                return r.status, corpo.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, ""
        except Exception as e:  # rede, timeout, TLS
            ultimo_erro = str(e)
            time.sleep(3 * (i + 1))
    return 0, ultimo_erro


def brl(texto):
    """'4.999,00' -> 4999.0"""
    if texto is None:
        return None
    if isinstance(texto, (int, float)):
        return float(texto)
    t = re.sub(r"[^\d,\.]", "", str(texto))
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def procurar_chave(obj, chaves):
    """Procura recursivamente o primeiro dicionário que tenha todas as chaves."""
    if isinstance(obj, dict):
        if all(k in obj for k in chaves):
            return obj
        for v in obj.values():
            achado = procurar_chave(v, chaves)
            if achado is not None:
                return achado
    elif isinstance(obj, list):
        for v in obj:
            achado = procurar_chave(v, chaves)
            if achado is not None:
                return achado
    return None


def json_ld_ofertas(html):
    """Lê os blocos schema.org (JSON-LD) e devolve (preço, disponível) se houver."""
    for bloco in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            dados = json.loads(bloco.strip())
        except Exception:
            continue
        oferta = procurar_chave(dados, ["price"]) or procurar_chave(dados, ["lowPrice"])
        if oferta:
            preco = brl(oferta.get("price") or oferta.get("lowPrice"))
            disp = oferta.get("availability", "")
            disponivel = None
            if disp:
                disponivel = "InStock" in disp
            return preco, disponivel
    return None, None


def texto_limpo(html):
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t)


def coletar_kabum(cfg):
    url = f"https://www.kabum.com.br/produto/{cfg['codigo']}"
    status, html = baixar(url)
    linha = {"url": url, "fonte": "kabum-pagina"}
    if status != 200 or not html:
        linha["status"] = f"erro_http_{status}"
        return linha

    texto = texto_limpo(html)
    titulo = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S)
    linha["titulo_encontrado"] = re.sub(r"<[^>]+>|\s+", " ", titulo.group(1)).strip() if titulo else ""
    vend = re.search(r"Vendido e entregue por:\s*([^\n<]{2,60}?)\s+(?:Sobre o produto|Ver descri|Produto Esgotado|R\$)", texto)
    linha["vendedor"] = vend.group(1).strip() if vend else ""

    esgotado = "Produto Esgotado" in texto
    pix = cheio = None

    # 1) dados do Next.js
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if m:
        try:
            dados = json.loads(m.group(1))
            bloco = procurar_chave(dados, ["priceWithDiscount"])
            if bloco:
                pix = brl(bloco.get("priceWithDiscount"))
                linha["preco_de"] = brl(bloco.get("oldPrice")) or None
                cheio = brl(bloco.get("price") or bloco.get("oldPrice"))
                if "available" in bloco and isinstance(bloco["available"], bool):
                    esgotado = esgotado or not bloco["available"]
        except Exception:
            pass

    # 2) texto da página
    if pix is None:
        mp = re.search(r"R\$\s*([\d\.]+,\d{2})\s*(?:-\d+%)?\s*à vista no PIX", texto)
        if mp:
            pix = brl(mp.group(1))
            antes = texto[: mp.start()]
            mc = re.findall(r"R\$\s*([\d\.]+,\d{2})", antes[-200:])
            if mc:
                cheio = brl(mc[-1])

    # 3) JSON-LD
    if pix is None and not esgotado:
        pix, disp = json_ld_ofertas(html)
        if disp is False:
            esgotado = True

    linha["preco_pix"] = pix
    linha["preco_cheio"] = cheio
    linha["disponivel"] = "nao" if esgotado else ("sim" if pix else "")
    if esgotado:
        linha["status"] = "esgotado"
    elif pix:
        linha["status"] = "ok"
    else:
        linha["status"] = "sem_preco_na_pagina"
    return linha


def filtrar(itens, cfg):
    """itens: lista de dicts com 'titulo' e 'preco'. Aplica as regras do produto."""
    deve = [p.lower() for p in cfg.get("deve_conter", [])]
    nao = [p.lower() for p in cfg.get("nao_pode_conter", [])]
    minimo = cfg.get("min_preco", 0)
    ok = []
    for it in itens:
        t = (it.get("titulo") or "").lower()
        if not it.get("preco") or it["preco"] < minimo:
            continue
        if any(p not in t for p in deve) or any(p in t for p in nao):
            continue
        ok.append(it)
    return ok


def duas_ofertas(candidatos, base, eh_da_loja):
    """Devolve até duas linhas: a melhor oferta da própria loja/loja oficial e a melhor de qualquer vendedor."""
    linhas = []
    proprios = [c for c in candidatos if eh_da_loja(c)]
    for tipo, grupo in (("loja", proprios), ("qualquer_vendedor", candidatos)):
        if not grupo:
            linhas.append({**base, "tipo_oferta": tipo, "status": "nenhum_resultado_valido"})
            continue
        m = min(grupo, key=lambda c: c["preco"])
        linhas.append({**base, "tipo_oferta": tipo, "preco_pix": m["preco"], "preco_cheio": m.get("cheio"),
                       "disponivel": "sim", "vendedor": m.get("vendedor", ""),
                       "titulo_encontrado": m.get("titulo", ""), "url": m.get("url") or base.get("url"),
                       "status": "ok"})
    return linhas


def coletar_mercadolivre(cfg):
    q = urllib.parse.quote(cfg["busca"])
    api = f"https://api.mercadolibre.com/sites/MLB/search?q={q}&condition=new&limit=50"
    base = {"fonte": "mercadolivre-api", "url": api}
    status, corpo = baixar(api)
    if status != 200:
        return [{**base, "status": f"erro_http_{status}"}]
    try:
        resultados = json.loads(corpo).get("results", [])
    except Exception:
        return [{**base, "status": "erro_json"}]
    itens = [{"titulo": r.get("title", ""), "preco": float(r["price"]) if r.get("price") else None,
              "cheio": float(r["original_price"]) if r.get("original_price") else None,
              "url": r.get("permalink"), "oficial": bool(r.get("official_store_id")),
              "vendedor": "loja oficial" if r.get("official_store_id") else "vendedor comum"} for r in resultados]
    return duas_ofertas(filtrar(itens, cfg), base, lambda c: c["oficial"])


def numero(v):
    if isinstance(v, dict):
        for k in ("bestPrice", "price", "value", "fullPrice"):
            if k in v:
                return numero(v[k])
        return None
    return brl(v)


def coletar_magalu(cfg):
    q = urllib.parse.quote(cfg["busca"])
    url = f"https://www.magazineluiza.com.br/busca/{q}/"
    base = {"fonte": "magalu-busca", "url": url}
    status, html = baixar(url)
    if status != 200 or not html:
        return [{**base, "status": f"erro_http_{status}"}]
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return [{**base, "status": "sem_dados_na_pagina"}]
    dados = json.loads(m.group(1))
    achados = []

    def andar(o):
        if isinstance(o, dict):
            if "title" in o and "price" in o:
                preco = o.get("price")
                pix = numero(preco.get("bestPrice")) if isinstance(preco, dict) and "bestPrice" in preco else numero(preco)
                cheio = numero(preco.get("fullPrice")) if isinstance(preco, dict) and "fullPrice" in preco else None
                seller = o.get("seller") or {}
                vend = (seller.get("description") or seller.get("id") or "") if isinstance(seller, dict) else str(seller)
                path = o.get("url") or o.get("path") or ""
                achados.append({"titulo": o.get("title", ""), "preco": pix, "cheio": cheio, "vendedor": vend,
                                "url": ("https://www.magazineluiza.com.br/" + path.lstrip("/")) if path and not path.startswith("http") else (path or url)})
            for v in o.values():
                andar(v)
        elif isinstance(o, list):
            for v in o:
                andar(v)

    andar(dados)
    if not achados:
        print(f"[magalu] nenhum produto reconhecido em {url}; chaves do topo: {list(dados.get('props', {}).keys())[:10]}")
        return [{**base, "status": "formato_desconhecido"}]
    eh_magalu = lambda c: "magazine" in (c.get("vendedor") or "").lower() or (c.get("vendedor") or "").lower() == "magalu"
    return duas_ofertas(filtrar(achados, cfg), base, eh_magalu)


COLETORES = {
    "kabum": coletar_kabum,
    "mercadolivre": coletar_mercadolivre,
    "magalu": coletar_magalu,
}


def lojas_desligadas():
    """Lojas que ainda não liberaram acesso ao robô. Ligam quando a credencial existir."""
    desligadas = set()
    if not os.environ.get("ML_ACCESS_TOKEN"):
        desligadas.add("mercadolivre")  # busca da API exige app de desenvolvedor desde 2025
    if not os.environ.get("MAGALU_ATIVO"):
        desligadas.add("magalu")  # bloqueia acessos vindos dos servidores do GitHub
    return desligadas


def main():
    with open(CONFIG, encoding="utf-8") as f:
        produtos = json.load(f)["produtos"]

    agora = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%Y-%m-%d %H:%M")
    linhas = []
    for p in produtos:
        for loja, cfg in p.get("lojas", {}).items():
            coletor = COLETORES.get(loja)
            if not coletor or loja in lojas_desligadas():
                continue
            try:
                r = coletor(cfg)
            except Exception as e:
                r = {"status": f"erro_{type(e).__name__}"}
            for item in (r if isinstance(r, list) else [{**r, "tipo_oferta": "loja"}]):
                item.update({"data_hora": agora, "produto_id": p["id"], "produto": p["nome"], "loja": loja})
                linhas.append(item)
            time.sleep(2)  # educado com as lojas

    novo = not os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS, extrasaction="ignore")
        if novo:
            w.writeheader()
        for l in linhas:
            w.writerow({k: ("" if l.get(k) is None else l.get(k)) for k in CAMPOS})

    with open(ULTIMO_PATH, "w", encoding="utf-8") as f:
        json.dump({"atualizado_em": agora, "linhas": linhas}, f, ensure_ascii=False, indent=1)

    # resumo para o log do GitHub
    resumo = ["| Produto | Loja | Oferta | Pix | Vendedor | Status |", "| --- | --- | --- | --- | --- | --- |"]
    for l in linhas:
        preco = f"R$ {l['preco_pix']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if l.get("preco_pix") else "-"
        resumo.append(f"| {l['produto']} | {l['loja']} | {l.get('tipo_oferta','')} | {preco} | {l.get('vendedor','')} | {l.get('status')} |")
    texto = "\n".join(resumo)
    print(texto)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(f"## Coleta {agora}\n\n{texto}\n")

    ok = sum(1 for l in linhas if l.get("status") in ("ok", "esgotado"))
    print(f"\n{ok} de {len(linhas)} coletas com resposta válida.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
