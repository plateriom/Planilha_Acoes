import gspread
import pandas as pd
import numpy as np
import time
import random
import json
import threading
import os
import re
import html as html_lib
import requests
import math

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from google.oauth2.service_account import Credentials

try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None

SHEET_ID = "1saHSvkcUV7FUbYaJWJUtC6LBH2svMBOs-5kd8TMGpFU"
TICKERS_SHEET = "FIIs"
DATA_SHEET = "FIIs Dados"

MAX_WORKERS = 3
MAX_RETRIES = 4
RATE_LIMIT = 2.2

CACHE_FILE = "cache_fiis.json"
CACHE_TTL = 60 * 60 * 6
CACHE_SCHEMA_VERSION = 100
CLEAR_RANGE = "A1:AB1000"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

COLUMNS = [
    "Ticker",
    "Preço Atual",
    "Dividend Yield 12M (%)",
    "Rendimentos 12M",
    "P/VP",
    "Último Rendimento",
    "Fonte",
    "Atualizado em",
    "Status",
    "Erro",
]


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def log_ticker(ticker, fonte, status, detalhe=""):
    extra = f" | {detalhe}" if detalhe else ""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {ticker} | {fonte} | {status}{extra}")


def clean_for_json(value):
    if value is None or value is pd.NA:
        return None
    if isinstance(value, dict):
        return {str(k): clean_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_for_json(v) for v in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        value = float(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return value


def validate_json_safe(data):
    json.dumps(data, allow_nan=False)


creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
client = gspread.authorize(creds)
spreadsheet = client.open_by_key(SHEET_ID)
sheet_fiis = spreadsheet.worksheet(TICKERS_SHEET)
sheet_dados = spreadsheet.worksheet(DATA_SHEET)

rate_lock = threading.Lock()
last_call = [0.0]
cache_lock = threading.Lock()


def rate_limiter():
    with rate_lock:
        elapsed = time.time() - last_call[0]
        if elapsed < RATE_LIMIT:
            time.sleep(RATE_LIMIT - elapsed)
        last_call[0] = time.time()


def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"CACHE | ERRO AO LER CACHE | {e}")
        return {}


def save_cache(cache_data):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(clean_for_json(cache_data), f, ensure_ascii=False, indent=2, allow_nan=False)
    except Exception as e:
        log(f"CACHE | ERRO AO SALVAR CACHE | {e}")


cache = load_cache()


def normalize_result(data):
    return clean_for_json({col: data.get(col) for col in COLUMNS})


def get_cached(ticker):
    ticker = ticker.upper().strip()
    with cache_lock:
        item = cache.get(ticker)
    if not item:
        return None
    if item.get("schema_version", 0) < CACHE_SCHEMA_VERSION:
        log_ticker(ticker, "CACHE", "IGNORADO", "schema antigo")
        return None
    if time.time() - item.get("timestamp", 0) > CACHE_TTL:
        log_ticker(ticker, "CACHE", "EXPIRADO")
        return None
    payload = item.get("payload")
    if not payload:
        return None
    result = dict(payload)
    result["Status"] = f"CACHE_{str(result.get('Fonte', 'DESCONHECIDA')).upper()}"
    return normalize_result(result)


def set_cache(ticker, payload):
    ticker = ticker.upper().strip()
    with cache_lock:
        cache[ticker] = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "timestamp": time.time(),
            "payload": clean_for_json(dict(payload)),
        }
        save_cache(cache)


def sanitize_ticker(raw):
    if raw is None:
        return ""
    ticker = str(raw).strip().upper()
    if re.match(r"^[A-Z]{4}!!$", ticker):
        ticker = ticker.replace("!!", "11")
    return re.sub(r"[^A-Z0-9]", "", ticker)


def parse_br_number(value):
    if value is None:
        return None
    s = str(value).strip()
    if not s or s in ["-", "--", "N/A", "n/a", "None", "nan", "NaN"]:
        return None
    s = s.replace("%", "").replace("R$", "").replace("R", "")
    s = s.replace(" ", "").replace("\xa0", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    elif s.count(".") == 1:
        left, right = s.split(".")
        if len(right) == 3 and len(left) <= 3:
            s = s.replace(".", "")
    try:
        number = float(s)
        if math.isnan(number) or math.isinf(number):
            return None
        return round(number, 6)
    except Exception:
        return None


def plausible(value, minimum=None, maximum=None):
    value = parse_br_number(value)
    if value is None:
        return None
    if minimum is not None and value < minimum:
        return None
    if maximum is not None and value > maximum:
        return None
    return value


def html_to_text(raw_html):
    if BeautifulSoup:
        soup = BeautifulSoup(raw_html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
    else:
        text = re.sub(r"<script.*?</script>", " ", raw_html, flags=re.I | re.S)
        text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def build_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    }


def page_has_ticker(ticker, text):
    ticker = ticker.upper().strip()
    upper = str(text or "").upper()
    checks = [
        ticker,
        f"COTAÇÃO DO {ticker}",
        f"DIVIDENDOS DO {ticker}",
        f"HOME FUNDOS IMOBILIÁRIOS {ticker}",
    ]
    return any(check in upper for check in checks)


NUM = r"(-?\d[\d\.\,]*)"


def first_number(text, patterns, minimum=None, maximum=None):
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I | re.S)
        if match:
            value = plausible(match.group(1), minimum, maximum)
            if value is not None:
                return value
    return None


def extract_price(text, ticker):
    ticker_esc = re.escape(ticker.upper().strip())
    patterns = [
        rf"{ticker_esc}\s+Cotação\s+R\$\s*{NUM}",
        rf"{ticker_esc}.{{0,160}}?Valor atual\s+R\$\s*{NUM}",
        rf"Valor atual\s+R\$\s*{NUM}",
        rf"Cotação\s+R\$\s*{NUM}",
        rf"Preço Atual\s+R\$\s*{NUM}",
    ]
    return first_number(text, patterns, 0.01, 10000)


def extract_dy_12m(text):
    patterns = [
        rf"Dividend Yield\s*(?:help_outline)?\s*{NUM}\s*%",
        rf"Dividend Yield.{{0,80}}?{NUM}\s*%",
        rf"DY\s*12M\s*{NUM}\s*%",
    ]
    return first_number(text, patterns, 0.01, 100)


def extract_rendimentos_12m(text):
    patterns = [
        rf"Últimos 12 meses\s*R\$\s*{NUM}",
        rf"Ultimos 12 meses\s*R\$\s*{NUM}",
        rf"Rendimentos 12M\s*R\$\s*{NUM}",
        rf"Rendimento 12M\s*R\$\s*{NUM}",
    ]
    return first_number(text, patterns, 0, 1000)


def extract_pvp(text):
    patterns = [
        rf"P/VP\s*{NUM}",
        rf"P\/VP\s*[:\-]?\s*{NUM}",
    ]
    return first_number(text, patterns, 0.01, 20)


def extract_ultimo_rendimento(text, ticker):
    ticker_esc = re.escape(ticker.upper().strip())
    patterns = [
        rf"Último rendimento\s*R\$\s*{NUM}",
        rf"Ultimo rendimento\s*R\$\s*{NUM}",
        rf"O último rendimento do\s+{ticker_esc}\s+foi de\s+R\$?\s*{NUM}",
        rf"O ultimo rendimento do\s+{ticker_esc}\s+foi de\s+R\$?\s*{NUM}",
    ]
    return first_number(text, patterns, 0, 100)


def validate_core_data(data):
    return (
        data.get("Preço Atual") is not None and data.get("Preço Atual") > 0 and
        data.get("Dividend Yield 12M (%)") is not None and data.get("Dividend Yield 12M (%)") > 0 and
        data.get("P/VP") is not None and data.get("P/VP") > 0
    )


def count_valid_fields(data):
    ignored = {"Ticker", "Fonte", "Atualizado em", "Status", "Erro"}
    return sum(1 for key, value in data.items() if key not in ignored and value not in [None, ""])


def empty_result(ticker, status="ERRO", erro=None, fonte=None):
    return normalize_result({
        "Ticker": ticker,
        "Fonte": fonte,
        "Atualizado em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "Status": status,
        "Erro": erro,
    })


def load_tickers():
    values = sheet_fiis.get_all_values()
    tickers = []
    for row in values[1:]:
        if not row:
            continue
        ticker = sanitize_ticker(row[0])
        if ticker and len(ticker) > 1:
            tickers.append(ticker)
    return list(dict.fromkeys(tickers))


def parse_source(ticker, text, fonte):
    data = {
        "Ticker": ticker,
        "Preço Atual": extract_price(text, ticker),
        "Dividend Yield 12M (%)": extract_dy_12m(text),
        "Rendimentos 12M": extract_rendimentos_12m(text),
        "P/VP": extract_pvp(text),
        "Último Rendimento": extract_ultimo_rendimento(text, ticker),
        "Fonte": fonte,
        "Atualizado em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "Status": "OK",
        "Erro": None,
    }
    data = clean_for_json(data)
    if validate_core_data(data):
        data["Status"] = "OK"
        data["Erro"] = None
    elif count_valid_fields(data) > 0:
        data["Status"] = "PARCIAL"
        data["Erro"] = f"{fonte} retornou dados parciais; campos essenciais incompletos"
    else:
        data["Status"] = "ERRO"
        data["Erro"] = f"{fonte} não retornou indicadores úteis"
    return data


def fetch_source(ticker, fonte, url):
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            rate_limiter()
            log_ticker(ticker, fonte.upper(), f"TENTATIVA {attempt + 1}/{MAX_RETRIES}", url)
            response = requests.get(url, headers=build_headers(), timeout=25)
            log_ticker(ticker, fonte.upper(), f"HTTP {response.status_code}")
            if response.status_code in [403, 429]:
                raise Exception(f"bloqueio/rate limit HTTP {response.status_code}")
            if response.status_code == 404:
                raise Exception(f"FII não encontrado no {fonte}")
            if response.status_code == 410:
                raise Exception(f"FII indisponível/removido no {fonte} HTTP 410")
            response.raise_for_status()
            text = html_to_text(response.text)
            if not page_has_ticker(ticker, text):
                raise Exception(f"HTML do {fonte} não corresponde ao ticker solicitado")
            data = parse_source(ticker, text, fonte)
            valid_count = count_valid_fields(data)
            if data["Status"] == "OK":
                log_ticker(ticker, fonte.upper(), "OK", f"preço={data.get('Preço Atual')} | DY={data.get('Dividend Yield 12M (%)')} | P/VP={data.get('P/VP')}")
                return data, valid_count, None
            if data["Status"] == "PARCIAL":
                log_ticker(ticker, fonte.upper(), "PARCIAL", f"{valid_count} campos capturados")
                return data, valid_count, data.get("Erro")
            raise Exception(data.get("Erro") or "nenhum indicador útil capturado")
        except Exception as e:
            last_error = str(e)
            if "HTTP 410" in last_error or "não encontrado" in last_error:
                log_ticker(ticker, fonte.upper(), "FALHOU SEM RETRY", last_error)
                break
            if attempt < MAX_RETRIES - 1:
                sleep_time = (3 ** attempt) + random.uniform(1, 2)
                log_ticker(ticker, fonte.upper(), "RETRY", f"{last_error} | aguardando {round(sleep_time, 1)}s")
                time.sleep(sleep_time)
            else:
                log_ticker(ticker, fonte.upper(), "FALHOU", last_error)
    return None, 0, last_error


def fetch_investidor10(ticker):
    return fetch_source(ticker, "Investidor10", f"https://investidor10.com.br/fiis/{ticker.lower()}/")


def fetch_statusinvest(ticker):
    return fetch_source(ticker, "StatusInvest", f"https://statusinvest.com.br/fundos-imobiliarios/{ticker.lower()}")


def fetch_ticker(ticker):
    ticker = ticker.upper().strip()
    cached = get_cached(ticker)
    if cached:
        log_ticker(ticker, "CACHE", "OK", f"fonte original: {cached.get('Fonte')} | preço={cached.get('Preço Atual')} | DY={cached.get('Dividend Yield 12M (%)')} | P/VP={cached.get('P/VP')}")
        return normalize_result(cached)
    best_partial = None
    best_partial_count = 0
    errors = []
    i10_data, i10_count, i10_error = fetch_investidor10(ticker)
    if i10_data and i10_data.get("Status") == "OK":
        set_cache(ticker, i10_data)
        return normalize_result(i10_data)
    if i10_data and i10_count > best_partial_count:
        best_partial = i10_data
        best_partial_count = i10_count
    if i10_error:
        errors.append(f"Investidor10: {i10_error}")
    log_ticker(ticker, "FALLBACK", "ACIONANDO STATUSINVEST", "Investidor10 insuficiente ou indisponível")
    si_data, si_count, si_error = fetch_statusinvest(ticker)
    if si_data and si_data.get("Status") == "OK":
        set_cache(ticker, si_data)
        return normalize_result(si_data)
    if si_data and si_count > best_partial_count:
        best_partial = si_data
        best_partial_count = si_count
    if si_error:
        errors.append(f"StatusInvest: {si_error}")
    if best_partial and best_partial_count > 0:
        best_partial["Status"] = "PARCIAL"
        best_partial["Erro"] = " | ".join(errors) if errors else "dados parciais"
        best_partial = clean_for_json(best_partial)
        log_ticker(ticker, "FINAL", "PARCIAL", f"{best_partial_count} campos reais capturados; sem dados inventados")
        set_cache(ticker, best_partial)
        return normalize_result(best_partial)
    error_msg = " | ".join(errors) if errors else "nenhuma fonte retornou dados reais"
    log_ticker(ticker, "FINAL", "ERRO", error_msg)
    return empty_result(ticker=ticker, status="ERRO", erro=error_msg, fonte=None)


def fetch_all(tickers):
    results = [None] * len(tickers)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_index = {executor.submit(fetch_ticker, ticker): index for index, ticker in enumerate(tickers)}
        total = len(tickers)
        completed = 0
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            ticker_original = tickers[index]
            try:
                result = clean_for_json(future.result())
                results[index] = result
                completed += 1
                log(f"PROGRESSO [{completed}/{total}] {result.get('Ticker', ticker_original)} | posição original: {index + 1} | {result.get('Status')} | {result.get('Fonte')} | preço={result.get('Preço Atual')} | DY={result.get('Dividend Yield 12M (%)')} | P/VP={result.get('P/VP')}")
            except Exception as e:
                completed += 1
                log_ticker(ticker_original, "FINAL", "ERRO", f"falha crítica no future: {e}")
                results[index] = empty_result(ticker=ticker_original, status="ERRO", erro=f"falha crítica no future: {e}", fonte=None)
    for i, result in enumerate(results):
        if result is None:
            results[i] = empty_result(ticker=tickers[i], status="ERRO", erro="resultado ausente após execução paralela", fonte=None)
    return results


def write_sheet(df):
    df = df.reindex(columns=COLUMNS)
    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.astype(object).where(pd.notnull(df), None)
    data = clean_for_json([df.columns.tolist()] + df.values.tolist())
    log("PLANILHA | validando JSON antes de limpar a aba")
    validate_json_safe(data)
    log("PLANILHA | JSON validado com sucesso")
    log(f"PLANILHA | limpando intervalo {CLEAR_RANGE}")
    sheet_dados.batch_clear([CLEAR_RANGE])
    log(f"PLANILHA | gravando {len(df)} linhas e {len(COLUMNS)} colunas")
    sheet_dados.update(range_name="A1", values=data, value_input_option="RAW")


def main():
    start = time.time()
    log("INÍCIO | carregando FIIs da planilha")
    tickers = load_tickers()
    if not tickers:
        log("FINALIZADO | nenhum FII encontrado na aba FIIs")
        return
    log(f"FIIs | {len(tickers)} ativos encontrados")
    log("ORDEM | a ordem da aba FIIs será preservada na aba FIIs Dados")
    log("ORDEM DE FONTES | 1º Investidor10 | 2º Status Invest | 3º ERRO/PARCIAL")
    log("COLUNAS | versão simplificada: Ticker, Preço, DY, Rendimentos 12M, P/VP, Último Rendimento")
    log(f"CACHE | arquivo: {CACHE_FILE}")
    log(f"CACHE | TTL configurado: {round(CACHE_TTL / 3600, 1)} horas")
    log(f"CACHE | schema version exigido: {CACHE_SCHEMA_VERSION}")
    log(f"RATE LIMIT | intervalo global mínimo: {RATE_LIMIT}s")
    log(f"THREADS | max_workers: {MAX_WORKERS}")
    results = clean_for_json(fetch_all(tickers))
    df = pd.DataFrame(results).reindex(columns=COLUMNS)
    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.astype(object).where(pd.notnull(df), None)
    try:
        ordem_tickers = {ticker: index for index, ticker in enumerate(tickers)}
        df["_ordem_original"] = df["Ticker"].map(ordem_tickers)
        df = df.sort_values("_ordem_original", kind="stable").drop(columns=["_ordem_original"])
        log("ORDEM | DataFrame reordenado conforme aba FIIs")
    except Exception as e:
        log(f"ORDEM | falha ao reforçar ordenação: {e}")
    try:
        for status, qtd in df["Status"].value_counts(dropna=False).to_dict().items():
            log(f"STATUS | {status}: {qtd}")
    except Exception as e:
        log(f"STATUS | erro ao gerar resumo: {e}")
    try:
        sem_preco = df[df["Preço Atual"].isna()]["Ticker"].tolist()
        sem_dy = df[df["Dividend Yield 12M (%)"].isna()]["Ticker"].tolist()
        sem_pvp = df[df["P/VP"].isna()]["Ticker"].tolist()
        if sem_preco:
            log(f"VALIDAÇÃO | FIIs sem preço capturado: {', '.join(sem_preco)}")
        if sem_dy:
            log(f"VALIDAÇÃO | FIIs sem DY 12M capturado: {', '.join(sem_dy)}")
        if sem_pvp:
            log(f"VALIDAÇÃO | FIIs sem P/VP capturado: {', '.join(sem_pvp)}")
        if not sem_preco and not sem_dy and not sem_pvp:
            log("VALIDAÇÃO | todos os FIIs possuem Preço Atual, DY 12M e P/VP")
    except Exception as e:
        log(f"VALIDAÇÃO | erro ao validar campos mínimos: {e}")
    log("PLANILHA | gravando dados")
    write_sheet(df)
    save_cache(cache)
    log(f"FINALIZADO | tempo total: {round(time.time() - start, 2)}s")


if __name__ == "__main__":
    main()
