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


# ================== CONFIG ==================

SHEET_ID = "1saHSvkcUV7FUbYaJWJUtC6LBH2svMBOs-5kd8TMGpFU"

TICKERS_SHEET = "FIIs"
DATA_SHEET = "FIIs Dados"

MAX_WORKERS = 3
MAX_RETRIES = 4
RATE_LIMIT = 2.2

CACHE_FILE = "cache_fiis.json"
CACHE_TTL = 60 * 60 * 6
CACHE_SCHEMA_VERSION = 3

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
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
    "Erro"
]

CLEAR_RANGE = "A1:AB1000"


# ================== LOG ==================

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def log_ticker(ticker, fonte, status, detalhe=""):
    detalhe_txt = f" | {detalhe}" if detalhe else ""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {ticker} | {fonte} | {status}{detalhe_txt}")


# ================== JSON / SHEETS SAFETY ==================

def clean_for_json(value):
    if value is None:
        return None

    if value is pd.NA:
        return None

    if isinstance(value, dict):
        return {str(k): clean_for_json(v) for k, v in value.items()}

    if isinstance(value, list):
        return [clean_for_json(v) for v in value]

    if isinstance(value, tuple):
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

def validate_json_safe(data):
    json.dumps(data, allow_nan=False)


# ================== AUTH ==================

creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
client = gspread.authorize(creds)

spreadsheet = client.open_by_key(SHEET_ID)
sheet_fiis = spreadsheet.worksheet(TICKERS_SHEET)
sheet_dados = spreadsheet.worksheet(DATA_SHEET)


# ================== RATE LIMIT GLOBAL ==================

rate_lock = threading.Lock()
last_call = [0]


def rate_limiter():
    with rate_lock:
        now = time.time()
        elapsed = now - last_call[0]

        if elapsed < RATE_LIMIT:
            time.sleep(RATE_LIMIT - elapsed)

        last_call[0] = time.time()


# ================== CACHE ==================

cache_lock = threading.Lock()


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
        cache_data = clean_for_json(cache_data)

        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                cache_data,
                f,
                ensure_ascii=False,
                indent=2,
                allow_nan=False
            )
    except Exception as e:
        log(f"CACHE | ERRO AO SALVAR CACHE | {e}")


cache = load_cache()


def get_cached(ticker):
    ticker = ticker.upper().strip()

    with cache_lock:
        data = cache.get(ticker)

    if not data:
        return None

    schema_version = data.get("schema_version", 0)

    if schema_version < CACHE_SCHEMA_VERSION:
        log_ticker(ticker, "CACHE", "IGNORADO", "schema antigo")
        return None

    timestamp = data.get("timestamp", 0)

    if time.time() - timestamp > CACHE_TTL:
        log_ticker(ticker, "CACHE", "EXPIRADO")
        return None

    payload = data.get("payload")

    if not payload:
        return None

    result = dict(payload)
    fonte = result.get("Fonte", "DESCONHECIDA")
    result["Status"] = f"CACHE_{fonte.upper()}"

    return normalize_result(result)


def set_cache(ticker, payload):
    ticker = ticker.upper().strip()
    payload_to_save = clean_for_json(dict(payload))

    with cache_lock:
        cache[ticker] = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "timestamp": time.time(),
            "payload": payload_to_save
        }
        save_cache(cache)


# ================== UTILS ==================

def sanitize_ticker(raw):
    if raw is None:
        return ""

    s = str(raw).strip().upper()

    # Correção defensiva para digitação tipo HGRE!!
    if re.match(r"^[A-Z]{4}!!$", s):
        s = s.replace("!!", "11")

    s = re.sub(r"[^A-Z0-9]", "", s)

    return s


def parse_br_number(value):
    if value is None:
        return None

    s = str(value).strip()

    if not s or s in ["-", "--", "N/A", "n/a", "None", "nan", "NaN"]:
        return None

    s = s.replace("%", "")
    s = s.replace("R$", "")
    s = s.replace("R", "")
    s = s.replace(" ", "")
    s = s.replace("\xa0", "")

    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    elif s.count(".") == 1:
        partes = s.split(".")
        if len(partes) == 2 and len(partes[1]) == 3 and len(partes[0]) <= 3:
            s = s.replace(".", "")

    try:
        number = float(s)

        if math.isnan(number) or math.isinf(number):
            return None

        return round(number, 6)
    except Exception:
        return None


def plausible(value, min_value=None, max_value=None):
    if value is None:
        return None

    try:
        v = float(value)
    except Exception:
        return None

    if math.isnan(v) or math.isinf(v):
        return None

    if min_value is not None and v < min_value:
        return None

    if max_value is not None and v > max_value:
        return None

    return round(v, 6)


def sanitize_df(df):
    df = df.copy()
    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.astype(object)
    df = df.where(pd.notnull(df), None)
    return df


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
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def build_headers():
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive"
    }


def number_pattern():
    return r"(-?\d[\d\.\,]*)"


def validate_page_for_ticker(ticker, text, fonte):
    ticker = ticker.upper().strip()
    text_upper = str(text or "").upper()

    evidencias = [
        ticker,
        f"COTAÇÃO DO {ticker}",
        f"DIVIDENDOS DO {ticker}",
        f"{ticker}:",
        f"{ticker} COTAÇÃO",
        f"FUNDOS IMOBILIÁRIOS {ticker}",
        f"HOME FUNDOS IMOBILIÁRIOS {ticker}",
    ]

    if any(e in text_upper for e in evidencias):
        return True

    log_ticker(
        ticker,
        fonte,
        "HTML SUSPEITO",
        "ticker não encontrado no conteúdo da página"
    )

    return False


def extract_first_match(text, patterns):
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I | re.S)

        if match:
            value = parse_br_number(match.group(1))

            if value is not None:
                return value

    return None


def extract_price(text, ticker):
    ticker = ticker.upper().strip()
    num = number_pattern()

    patterns = [
        rf"{re.escape(ticker)}\s+Cotação\s+R\$\s*{num}",
        rf"{re.escape(ticker)}.*?Valor atual\s+R\$\s*{num}",
        rf"Cotação\s+R\$\s*{num}",
        rf"Preço Atual\s+R\$\s*{num}",
        rf"Valor Atual\s+R\$\s*{num}",
        rf"Valor atual\s+R\$\s*{num}",
    ]

    value = extract_first_match(text, patterns)
    return plausible(value, 0.01, 10000)


def extract_dy_12m(text):
    num = number_pattern()

    patterns = [
        rf"Dividend Yield\s*(?:help_outline)?\s*{num}\s*%",
        rf"Dividend Yield.*?{num}\s*%",
        rf"DY\s*12M\s*{num}\s*%",
        rf"DY\s*{num}\s*%",
    ]

    value = extract_first_match(text, patterns)
    return plausible(value, 0, 100)


def extract_rendimentos_12m(text):
    num = number_pattern()

    patterns = [
        rf"Últimos 12 meses\s*R\$\s*{num}",
        rf"Ultimos 12 meses\s*R\$\s*{num}",
        rf"Rendimentos 12M\s*R\$\s*{num}",
        rf"Rendimento 12M\s*R\$\s*{num}",
        rf"12 meses\s*R\$\s*{num}",
    ]

    value = extract_first_match(text, patterns)
    return plausible(value, 0, 1000)


def extract_pvp(text):
    num = number_pattern()

    patterns = [
        rf"P/VP\s*{num}",
        rf"P\/VP\s*[:\-]?\s*{num}",
    ]

    value = extract_first_match(text, patterns)
    return plausible(value, 0.01, 20)


def extract_ultimo_rendimento(text, ticker):
    ticker = ticker.upper().strip()
    num = number_pattern()

    patterns = [
        rf"Último rendimento\s*R\$\s*{num}",
        rf"Ultimo rendimento\s*R\$\s*{num}",
        rf"O último rendimento do\s+{re.escape(ticker)}\s+foi de\s+R\$?\s*{num}",
        rf"O ultimo rendimento do\s+{re.escape(ticker)}\s+foi de\s+R\$?\s*{num}",
        rf"Rendimento\s+\d{{2}}/\d{{2}}/\d{{4}}\s+\d{{2}}/\d{{2}}/\d{{4}}\s+{num}",
    ]

    value = extract_first_match(text, patterns)
    return plausible(value, 0, 100)


def validate_core_data(data):
    preco = data.get("Preço Atual")
    dy = data.get("Dividend Yield 12M (%)")
    pvp = data.get("P/VP")

    return (
        preco is not None and preco > 0 and
        dy is not None and dy > 0 and
        pvp is not None and pvp > 0
    )


def count_valid_fields(data):
    ignored = {"Ticker", "Fonte", "Atualizado em", "Status", "Erro"}

    return sum(
        1 for k, v in data.items()
        if k not in ignored and v not in [None, ""]
    )


def normalize_result(data):
    result = {}

    for col in COLUMNS:
        result[col] = data.get(col)

    return clean_for_json(result)


def empty_result(ticker, status="ERRO", erro=None, fonte=None):
    return normalize_result({
        "Ticker": ticker,
        "Fonte": fonte,
        "Atualizado em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "Status": status,
        "Erro": erro
    })


# ================== LOAD TICKERS ==================

def load_tickers():
    values = sheet_fiis.get_all_values()

    tickers = []

    for row in values[1:]:
        if not row:
            continue

        raw = row[0]
        ticker = sanitize_ticker(raw)

        if ticker and len(ticker) > 1:
            tickers.append(ticker)

    tickers = list(dict.fromkeys(tickers))

    return tickers


# ================== PARSE FONTE ==================

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
        "Erro": None
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


# ================== FETCH INVESTIDOR10 ==================

def fetch_investidor10(ticker):
    url = f"https://investidor10.com.br/fiis/{ticker.lower()}/"
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            rate_limiter()

            log_ticker(
                ticker,
                "INVESTIDOR10",
                f"TENTATIVA {attempt + 1}/{MAX_RETRIES}",
                url
            )

            response = requests.get(
                url,
                headers=build_headers(),
                timeout=25
            )

            log_ticker(
                ticker,
                "INVESTIDOR10",
                f"HTTP {response.status_code}"
            )

            if response.status_code in [403, 429]:
                raise Exception(f"bloqueio/rate limit HTTP {response.status_code}")

            if response.status_code == 404:
                raise Exception("FII não encontrado no Investidor10")

            if response.status_code == 410:
                raise Exception("FII indisponível/removido no Investidor10 HTTP 410")

            response.raise_for_status()

            text = html_to_text(response.text)

            if not validate_page_for_ticker(ticker, text, "INVESTIDOR10"):
                raise Exception("HTML do Investidor10 não corresponde ao ticker solicitado")

            data = parse_source(ticker, text, "Investidor10")
            valid_count = count_valid_fields(data)

            if data["Status"] == "OK":
                log_ticker(
                    ticker,
                    "INVESTIDOR10",
                    "OK",
                    f"preço={data.get('Preço Atual')} | DY={data.get('Dividend Yield 12M (%)')} | P/VP={data.get('P/VP')}"
                )
                return data, valid_count, None

            if data["Status"] == "PARCIAL":
                log_ticker(
                    ticker,
                    "INVESTIDOR10",
                    "PARCIAL",
                    f"{valid_count} campos capturados; fallback será acionado"
                )
                return data, valid_count, data.get("Erro")

            raise Exception(data.get("Erro") or "nenhum indicador útil capturado")

        except Exception as e:
            last_error = str(e)

            if "HTTP 410" in last_error or "não encontrado" in last_error:
                log_ticker(
                    ticker,
                    "INVESTIDOR10",
                    "FALHOU SEM RETRY",
                    last_error
                )
                break

            if attempt < MAX_RETRIES - 1:
                sleep_time = (3 ** attempt) + random.uniform(1, 2)

                log_ticker(
                    ticker,
                    "INVESTIDOR10",
                    "RETRY",
                    f"{last_error} | aguardando {round(sleep_time, 1)}s"
                )

                time.sleep(sleep_time)
            else:
                log_ticker(
                    ticker,
                    "INVESTIDOR10",
                    "FALHOU",
                    last_error
                )

    return None, 0, last_error


# ================== FETCH STATUS INVEST ==================

def fetch_statusinvest(ticker):
    url = f"https://statusinvest.com.br/fundos-imobiliarios/{ticker.lower()}"
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            rate_limiter()

            log_ticker(
                ticker,
                "STATUSINVEST",
                f"TENTATIVA {attempt + 1}/{MAX_RETRIES}",
                url
            )

            response = requests.get(
                url,
                headers=build_headers(),
                timeout=25
            )

            log_ticker(
                ticker,
                "STATUSINVEST",
                f"HTTP {response.status_code}"
            )

            if response.status_code in [403, 429]:
                raise Exception(f"bloqueio/rate limit HTTP {response.status_code}")

            if response.status_code == 404:
                raise Exception("FII não encontrado no Status Invest")

            if response.status_code == 410:
                raise Exception("FII indisponível/removido no Status Invest HTTP 410")

            response.raise_for_status()

            text = html_to_text(response.text)

            if not validate_page_for_ticker(ticker, text, "STATUSINVEST"):
                raise Exception("HTML do Status Invest não corresponde ao ticker solicitado")

            data = parse_source(ticker, text, "StatusInvest")
            valid_count = count_valid_fields(data)

            if data["Status"] == "OK":
                log_ticker(
                    ticker,
                    "STATUSINVEST",
                    "OK",
                    f"preço={data.get('Preço Atual')} | DY={data.get('Dividend Yield 12M (%)')} | P/VP={data.get('P/VP')}"
                )
                return data, valid_count, None

            if data["Status"] == "PARCIAL":
                log_ticker(
                    ticker,
                    "STATUSINVEST",
                    "PARCIAL",
                    f"{valid_count} campos capturados"
                )
                return data, valid_count, data.get("Erro")

            raise Exception(data.get("Erro") or "nenhum indicador útil capturado")

        except Exception as e:
            last_error = str(e)

            if "HTTP 410" in last_error or "não encontrado" in last_error:
                log_ticker(
                    ticker,
                    "STATUSINVEST",
                    "FALHOU SEM RETRY",
                    last_error
                )
                break

            if attempt < MAX_RETRIES - 1:
                sleep_time = (3 ** attempt) + random.uniform(1, 2)

                log_ticker(
                    ticker,
                    "STATUSINVEST",
                    "RETRY",
                    f"{last_error} | aguardando {round(sleep_time, 1)}s"
                )

                time.sleep(sleep_time)
            else:
                log_ticker(
                    ticker,
                    "STATUSINVEST",
                    "FALHOU",
                    last_error
                )

    return None, 0, last_error


# ================== FETCH TICKER ==================

def fetch_ticker(ticker):
    ticker = ticker.upper().strip()

    cached = get_cached(ticker)

    if cached:
        log_ticker(
            ticker,
            "CACHE",
            "OK",
            f"fonte original: {cached.get('Fonte')} | preço={cached.get('Preço Atual')} | DY={cached.get('Dividend Yield 12M (%)')} | P/VP={cached.get('P/VP')}"
        )
        return normalize_result(cached)

    best_partial = None
    best_partial_count = 0
    errors = []

    # 1) Fonte principal: Investidor10
    i10_data, i10_count, i10_error = fetch_investidor10(ticker)

    if i10_data and i10_data.get("Status") == "OK":
        set_cache(ticker, i10_data)
        return normalize_result(i10_data)

    if i10_data and i10_count > best_partial_count:
        best_partial = i10_data
        best_partial_count = i10_count

    if i10_error:
        errors.append(f"Investidor10: {i10_error}")

    log_ticker(
        ticker,
        "FALLBACK",
        "ACIONANDO STATUSINVEST",
        "Investidor10 insuficiente ou indisponível"
    )

    # 2) Fallback: Status Invest
    si_data, si_count, si_error = fetch_statusinvest(ticker)

    if si_data and si_data.get("Status") == "OK":
        set_cache(ticker, si_data)
        return normalize_result(si_data)

    if si_data and si_count > best_partial_count:
        best_partial = si_data
        best_partial_count = si_count

    if si_error:
        errors.append(f"StatusInvest: {si_error}")

    # 3) Se houver dados reais parciais, grava PARCIAL
    if best_partial and best_partial_count > 0:
        best_partial["Status"] = "PARCIAL"
        best_partial["Erro"] = " | ".join(errors) if errors else "dados parciais"

        best_partial = clean_for_json(best_partial)

        log_ticker(
            ticker,
            "FINAL",
            "PARCIAL",
            f"{best_partial_count} campos reais capturados; sem dados inventados"
        )

        set_cache(ticker, best_partial)
        return normalize_result(best_partial)

    # 4) Se nada deu certo, retorna ERRO
    error_msg = " | ".join(errors) if errors else "nenhuma fonte retornou dados reais"

    log_ticker(
        ticker,
        "FINAL",
        "ERRO",
        error_msg
    )

    return empty_result(
        ticker=ticker,
        status="ERRO",
        erro=error_msg,
        fonte=None
    )


# ================== PARALLEL COM ORDEM PRESERVADA ==================

def fetch_all(tickers):
    results = [None] * len(tickers)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_index = {
            executor.submit(fetch_ticker, ticker): index
            for index, ticker in enumerate(tickers)
        }

        total = len(tickers)
        completed = 0

        for future in as_completed(future_to_index):
            index = future_to_index[future]
            ticker_original = tickers[index]

            try:
                result = future.result()
                result = clean_for_json(result)

                results[index] = result
                completed += 1

                ticker = result.get("Ticker", ticker_original)
                status = result.get("Status", "SEM_STATUS")
                fonte = result.get("Fonte", "SEM_FONTE")
                preco = result.get("Preço Atual", None)
                dy = result.get("Dividend Yield 12M (%)", None)
                pvp = result.get("P/VP", None)

                log(
                    f"PROGRESSO [{completed}/{total}] "
                    f"{ticker} | posição original: {index + 1} | "
                    f"{status} | {fonte} | preço={preco} | DY={dy} | P/VP={pvp}"
                )

            except Exception as e:
                completed += 1

                log_ticker(
                    ticker_original,
                    "FINAL",
                    "ERRO",
                    f"falha crítica no future: {e}"
                )

                results[index] = empty_result(
                    ticker=ticker_original,
                    status="ERRO",
                    erro=f"falha crítica no future: {e}",
                    fonte=None
                )

    for i, result in enumerate(results):
        if result is None:
            ticker_original = tickers[i]

            results[i] = empty_result(
                ticker=ticker_original,
                status="ERRO",
                erro="resultado ausente após execução paralela",
                fonte=None
            )

    return results


# ================== WRITE ==================

def write_sheet(df):
    df = df.reindex(columns=COLUMNS)
    df = sanitize_df(df)

    raw_data = [df.columns.tolist()] + df.values.tolist()
    data = clean_for_json(raw_data)

    log("PLANILHA | validando JSON antes de limpar a aba")
    validate_json_safe(data)

    log("PLANILHA | JSON validado com sucesso")

    log(f"PLANILHA | limpando intervalo {CLEAR_RANGE}")
    sheet_dados.batch_clear([CLEAR_RANGE])

    log(f"PLANILHA | gravando {len(df)} linhas e {len(COLUMNS)} colunas")
    sheet_dados.update(
        range_name="A1",
        values=data,
        value_input_option="RAW"
    )


# ================== MAIN ==================

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
    log("FAIL-SAFE | OK exige: Preço Atual + Dividend Yield 12M (%) + P/VP")
    log("VALIDAÇÃO HTML | ativa: ticker precisa aparecer no conteúdo da página")

    results = fetch_all(tickers)
    results = clean_for_json(results)

    df = pd.DataFrame(results)
    df = df.reindex(columns=COLUMNS)
    df = sanitize_df(df)

    # Reforço de ordem
    try:
        ordem_tickers = {ticker: index for index, ticker in enumerate(tickers)}

        df["_ordem_original"] = df["Ticker"].map(ordem_tickers)
        df = df.sort_values("_ordem_original", kind="stable")
        df = df.drop(columns=["_ordem_original"])

        log("ORDEM | DataFrame reordenado conforme aba FIIs")
    except Exception as e:
        log(f"ORDEM | falha ao reforçar ordenação: {e}")

    log("DATAFRAME | resumo de status:")

    try:
        status_counts = df["Status"].value_counts(dropna=False).to_dict()

        for status, qtd in status_counts.items():
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

    elapsed = round(time.time() - start, 2)
    log(f"FINALIZADO | tempo total: {elapsed}s")


# ================== RUN ==================

if __name__ == "__main__":
    main()
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


# ================== CONFIG ==================

SHEET_ID = "1saHSvkcUV7FUbYaJWJUtC6LBH2svMBOs-5kd8TMGpFU"

TICKERS_SHEET = "FIIs"
DATA_SHEET = "FIIs Dados"

MAX_WORKERS = 3
MAX_RETRIES = 4
RATE_LIMIT = 2.2

CACHE_FILE = "cache_fiis.json"
CACHE_TTL = 60 * 60 * 6
CACHE_SCHEMA_VERSION = 3  # versão simplificada e mais rígida

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
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
    "Erro"
]

# Importante:
# Como a versão anterior gravava até AB, esta limpeza remove colunas antigas contaminadas.
# Quando reconstruirmos o Apps Script novo, podemos trocar para A1:J1000 se quiser preservar scores.
CLEAR_RANGE = "A1:AB1000"


# ================== LOG ==================

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def log_ticker(ticker, fonte, status, detalhe=""):
    detalhe_txt = f" | {detalhe}" if detalhe else ""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {ticker} | {fonte} | {status}{detalhe_txt}")


# ================== JSON / SHEETS SAFETY ==================

def clean_for_json(value):
    if value is None:
        return None

    if value is pd.NA:
        return None

    if isinstance(value, dict):
        return {str(k): clean_for_json(v) for k, v in value.items()}

    if isinstance(value, list):
        return [clean_for_json(v) for v in value]

    if isinstance(value, tuple):
        return [clean_for_json(v) for v in value]

    if isinstance(value, np.integer):
        return int(value)

    if isinstance(value, np.floating):
        value = float(value)

    if isinstance(value, float):
