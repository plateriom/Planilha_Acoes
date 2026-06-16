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
import unicodedata

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
CACHE_SCHEMA_VERSION = 300
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

NUM = r"(-?\d[\d\.\,]*)"


# ================== LOG ==================

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def log_ticker(ticker, fonte, status, detalhe=""):
    detalhe_txt = f" | {detalhe}" if detalhe else ""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {ticker} | {fonte} | {status}{detalhe_txt}")


# ================== JSON SAFETY ==================

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


# ================== AUTH ==================

creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
client = gspread.authorize(creds)

spreadsheet = client.open_by_key(SHEET_ID)
sheet_fiis = spreadsheet.worksheet(TICKERS_SHEET)
sheet_dados = spreadsheet.worksheet(DATA_SHEET)


# ================== RATE LIMIT ==================

rate_lock = threading.Lock()
last_call = [0.0]


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
            json.dump(cache_data, f, ensure_ascii=False, indent=2, allow_nan=False)
    except Exception as e:
        log(f"CACHE | ERRO AO SALVAR CACHE | {e}")


cache = load_cache()


def normalize_result(data):
    result = {col: data.get(col) for col in COLUMNS}
    return clean_for_json(result)


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
    payload_to_save = clean_for_json(dict(payload))

    with cache_lock:
        cache[ticker] = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "timestamp": time.time(),
            "payload": payload_to_save,
        }
        save_cache(cache)


# ================== HELPERS ==================

def sanitize_ticker(raw):
    if raw is None:
        return ""

    ticker = str(raw).strip().upper()

    if re.match(r"^[A-Z]{4}!!$", ticker):
        ticker = ticker.replace("!!", "11")

    return re.sub(r"[^A-Z0-9]", "", ticker)


def normalize_label(value):
    value = str(value or "").strip().lower()
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = re.sub(r"\s+", " ", value)
    return value


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
        parts = s.split(".")
        if len(parts) == 2 and len(parts[1]) == 3 and len(parts[0]) <= 3:
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
        "Connection": "keep-alive",
    }


def page_has_ticker(ticker, text):
    ticker = ticker.upper().strip()
    text_upper = str(text or "").upper()

    checks = [
        ticker,
        f"COTAÇÃO DO {ticker}",
        f"DIVIDENDOS DO {ticker}",
        f"HOME FUNDOS IMOBILIÁRIOS {ticker}",
    ]

    return any(check in text_upper for check in checks)


def first_number(text, patterns, minimum=None, maximum=None):
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I | re.S)

        if match:
            value = plausible(match.group(1), minimum, maximum)

            if value is not None:
                return value

    return None


# ================== INVESTIDOR10 HTML EXTRACTORS ==================

def extract_investidor10_card_value(raw_html, wanted_labels):
    if not BeautifulSoup or not raw_html:
        return None

    if isinstance(wanted_labels, str):
        wanted_labels = [wanted_labels]

    wanted_labels_norm = [normalize_label(label) for label in wanted_labels]
    soup = BeautifulSoup(raw_html, "html.parser")

    headers = soup.select("._card-header")

    for header in headers:
        header_text = normalize_label(header.get_text(" ", strip=True))

        if not any(label in header_text for label in wanted_labels_norm):
            continue

        parent = header.parent

        if not parent:
            continue

        body = parent.select_one("._card-body")

        if not body:
            continue

        body_text = body.get_text(" ", strip=True)
        value = parse_br_number(body_text)

        if value is not None:
            return value

    return None


def extract_investidor10_info_item_value(raw_html, wanted_labels, prefer_money=False, prefer_percent=False):
    """
    Extrai valores de blocos do Investidor10 com estrutura parecida com:
    <span class="content--info--item--title">YIELD 12 MESES</span>

    Para Rendimentos 12M, use prefer_money=True.
    Para percentual, use prefer_percent=True.
    """
    if not BeautifulSoup or not raw_html:
        return None

    if isinstance(wanted_labels, str):
        wanted_labels = [wanted_labels]

    wanted_labels_norm = [normalize_label(label) for label in wanted_labels]
    soup = BeautifulSoup(raw_html, "html.parser")

    candidates = []
    candidates.extend(soup.select("span.content--info--item--title"))
    candidates.extend(soup.select(".content--info--item--title"))

    # Fallback: caso a classe venha composta/dinâmica, procura spans/divs cujo texto seja o label.
    for tag in soup.find_all(["span", "div", "p"]):
        tag_text_norm = normalize_label(tag.get_text(" ", strip=True))
        if any(label == tag_text_norm or label in tag_text_norm for label in wanted_labels_norm):
            candidates.append(tag)

    seen = set()

    for title_tag in candidates:
        tag_id = id(title_tag)
        if tag_id in seen:
            continue
        seen.add(tag_id)

        title_text_norm = normalize_label(title_tag.get_text(" ", strip=True))

        if not any(label in title_text_norm for label in wanted_labels_norm):
            continue

        search_blocks = []

        if title_tag.parent:
            search_blocks.append(title_tag.parent)
            if title_tag.parent.parent:
                search_blocks.append(title_tag.parent.parent)

        next_sibling = title_tag.find_next_sibling()
        if next_sibling:
            search_blocks.append(next_sibling)

        for block in search_blocks:
            block_text = block.get_text(" ", strip=True)
            title_text = title_tag.get_text(" ", strip=True)
            value_text = block_text.replace(title_text, " ").strip()

            if prefer_money:
                money_match = re.search(rf"R\$\s*{NUM}", value_text, flags=re.I | re.S)
                if money_match:
                    value = plausible(money_match.group(1), 0, 10000)
                    if value is not None:
                        return value

            if prefer_percent:
                percent_match = re.search(rf"{NUM}\s*%", value_text, flags=re.I | re.S)
                if percent_match:
                    value = plausible(percent_match.group(1), 0, 100)
                    if value is not None:
                        return value

            # Último fallback no bloco: primeiro número plausível.
            number_match = re.search(NUM, value_text, flags=re.I | re.S)
            if number_match:
                max_value = 100 if prefer_percent else 10000
                value = plausible(number_match.group(1), 0, max_value)
                if value is not None:
                    return value

    return None


# ================== EXTRACTORS ==================

def extract_price(text, ticker, raw_html=None, fonte=None):
    fonte_norm = str(fonte or "").strip().lower()

    if raw_html and "investidor10" in fonte_norm:
        card_value = extract_investidor10_card_value(raw_html, ["cotação", "preço atual", "valor atual"])
        card_value = plausible(card_value, 0.01, 10000)

        if card_value is not None:
            return card_value

    ticker_esc = re.escape(ticker.upper().strip())

    patterns = [
        rf"{ticker_esc}\s+Cotação\s+R\$\s*{NUM}",
        rf"{ticker_esc}.{{0,160}}?Valor atual\s+R\$\s*{NUM}",
        rf"Valor atual\s+R\$\s*{NUM}",
        rf"Cotação\s+R\$\s*{NUM}",
        rf"Preço Atual\s+R\$\s*{NUM}",
    ]

    return first_number(text, patterns, 0.01, 10000)


def extract_dy_12m(text, ticker=None, raw_html=None, fonte=None):
    fonte_norm = str(fonte or "").strip().lower()

    if raw_html and "investidor10" in fonte_norm:
        card_value = extract_investidor10_card_value(raw_html, ["dy (12m)", "dy 12m", "dividend yield"])
        card_value = plausible(card_value, 0.01, 100)

        if card_value is not None:
            return card_value

    patterns = []

    if ticker:
        ticker_esc = re.escape(str(ticker).upper().strip())
        patterns.extend([
            rf"{ticker_esc}\s+DY\s*\(12M\)\s*{NUM}\s*%",
            rf"{ticker_esc}\s+DY\s*12M\s*{NUM}\s*%",
            rf"{ticker_esc}.{{0,80}}?DY\s*\(12M\)\s*{NUM}\s*%",
        ])

    patterns.extend([
        rf"DY\s*\(12M\)\s*{NUM}\s*%",
        rf"DY\s*12M\s*{NUM}\s*%",
        rf"Dividend Yield\s*{NUM}\s*%",
        rf"Dividend Yield\s+{NUM}\s*%",
    ])

    return first_number(text, patterns, 0.01, 100)


def extract_rendimentos_12m(text, raw_html=None, fonte=None):
    fonte_norm = str(fonte or "").strip().lower()

    # Estratégia principal: Investidor10, item específico informado pelo usuário.
    if raw_html and "investidor10" in fonte_norm:
        item_value = extract_investidor10_info_item_value(
            raw_html,
            [
                "yield 12 meses",
                "yields 12 meses",
                "rendimentos 12 meses",
                "rendimento 12 meses",
                "ultimos 12 meses",
                "últimos 12 meses",
            ],
            prefer_money=True,
        )
        item_value = plausible(item_value, 0, 1000)

        if item_value is not None and item_value > 0:
            return item_value

    # Fallback textual conservador, sem Status Invest.
    patterns = [
        rf"YIELD\s*12\s*MESES.{{0,80}}?R\$\s*{NUM}",
        rf"YIELD\s*12\s*MESES.{{0,80}}?{NUM}",
        rf"Últimos 12 meses\s*R\$\s*{NUM}",
        rf"Ultimos 12 meses\s*R\$\s*{NUM}",
        rf"Últimos\s+12\s+meses\s*R\$\s*{NUM}",
        rf"Ultimos\s+12\s+meses\s*R\$\s*{NUM}",
        rf"Rendimentos\s*12M\s*R\$\s*{NUM}",
        rf"Rendimento\s*12M\s*R\$\s*{NUM}",
    ]

    return first_number(text, patterns, 0, 1000)


def extract_pvp(text, raw_html=None, fonte=None):
    fonte_norm = str(fonte or "").strip().lower()

    if raw_html and "investidor10" in fonte_norm:
        card_value = extract_investidor10_card_value(raw_html, ["p/vp"])
        card_value = plausible(card_value, 0.01, 20)

        if card_value is not None:
            return card_value

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
        rf"ÚLTIMO RENDIMENTO\s*R\$\s*{NUM}",
        rf"ULTIMO RENDIMENTO\s*R\$\s*{NUM}",
        rf"O último rendimento do\s+{ticker_esc}\s+foi de\s+R\$?\s*{NUM}",
        rf"O ultimo rendimento do\s+{ticker_esc}\s+foi de\s+R\$?\s*{NUM}",
    ]

    return first_number(text, patterns, 0, 100)


# ================== VALIDATION ==================

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
        1 for key, value in data.items()
        if key not in ignored and value not in [None, ""]
    )


def empty_result(ticker, status="ERRO", erro=None, fonte=None):
    return normalize_result({
        "Ticker": ticker,
        "Fonte": fonte,
        "Atualizado em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "Status": status,
        "Erro": erro,
    })


# ================== LOAD TICKERS ==================

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


# ================== PARSE ==================

def parse_source(ticker, text, fonte, raw_html=None):
    data = {
        "Ticker": ticker,
        "Preço Atual": extract_price(text, ticker, raw_html=raw_html, fonte=fonte),
        "Dividend Yield 12M (%)": extract_dy_12m(text, ticker=ticker, raw_html=raw_html, fonte=fonte),
        "Rendimentos 12M": extract_rendimentos_12m(text, raw_html=raw_html, fonte=fonte),
        "P/VP": extract_pvp(text, raw_html=raw_html, fonte=fonte),
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


# ================== FETCH SOURCE ==================

def fetch_investidor10(ticker):
    url = f"https://investidor10.com.br/fiis/{ticker.lower()}/"
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            rate_limiter()

            log_ticker(ticker, "INVESTIDOR10", f"TENTATIVA {attempt + 1}/{MAX_RETRIES}", url)

            response = requests.get(url, headers=build_headers(), timeout=25)

            log_ticker(ticker, "INVESTIDOR10", f"HTTP {response.status_code}")

            if response.status_code in [403, 429]:
                raise Exception(f"bloqueio/rate limit HTTP {response.status_code}")

            if response.status_code == 404:
                raise Exception("FII não encontrado no Investidor10")

            if response.status_code == 410:
                raise Exception("FII indisponível/removido no Investidor10 HTTP 410")

            response.raise_for_status()

            raw_html = response.text
            text = html_to_text(raw_html)

            if not page_has_ticker(ticker, text):
                raise Exception("HTML do Investidor10 não corresponde ao ticker solicitado")

            data = parse_source(ticker, text, "Investidor10", raw_html=raw_html)
            valid_count = count_valid_fields(data)

            if data["Status"] == "OK":
                log_ticker(
                    ticker,
                    "INVESTIDOR10",
                    "OK",
                    f"preço={data.get('Preço Atual')} | "
                    f"DY={data.get('Dividend Yield 12M (%)')} | "
                    f"R12M={data.get('Rendimentos 12M')} | "
                    f"P/VP={data.get('P/VP')} | "
                    f"último rendimento={data.get('Último Rendimento')}"
                )
                return data, valid_count, None

            if data["Status"] == "PARCIAL":
                log_ticker(ticker, "INVESTIDOR10", "PARCIAL", f"{valid_count} campos capturados")
                return data, valid_count, data.get("Erro")

            raise Exception(data.get("Erro") or "nenhum indicador útil capturado")

        except Exception as e:
            last_error = str(e)

            if "HTTP 410" in last_error or "não encontrado" in last_error:
                log_ticker(ticker, "INVESTIDOR10", "FALHOU SEM RETRY", last_error)
                break

            if attempt < MAX_RETRIES - 1:
                sleep_time = (3 ** attempt) + random.uniform(1, 2)
                log_ticker(ticker, "INVESTIDOR10", "RETRY", f"{last_error} | aguardando {round(sleep_time, 1)}s")
                time.sleep(sleep_time)
            else:
                log_ticker(ticker, "INVESTIDOR10", "FALHOU", last_error)

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
            f"fonte original: {cached.get('Fonte')} | "
            f"preço={cached.get('Preço Atual')} | "
            f"DY={cached.get('Dividend Yield 12M (%)')} | "
            f"R12M={cached.get('Rendimentos 12M')} | "
            f"P/VP={cached.get('P/VP')}"
        )
        return normalize_result(cached)

    data, count, error = fetch_investidor10(ticker)

    if data and data.get("Status") in ["OK", "PARCIAL"]:
        set_cache(ticker, data)
        return normalize_result(data)

    error_msg = f"Investidor10: {error}" if error else "Investidor10 não retornou dados reais"
    log_ticker(ticker, "FINAL", "ERRO", error_msg)

    return empty_result(ticker=ticker, status="ERRO", erro=error_msg, fonte=None)


# ================== PARALLEL ==================

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
                result = clean_for_json(future.result())
                results[index] = result
                completed += 1

                log(
                    f"PROGRESSO [{completed}/{total}] "
                    f"{result.get('Ticker', ticker_original)} | "
                    f"posição original: {index + 1} | "
                    f"{result.get('Status')} | {result.get('Fonte')} | "
                    f"preço={result.get('Preço Atual')} | "
                    f"DY={result.get('Dividend Yield 12M (%)')} | "
                    f"R12M={result.get('Rendimentos 12M')} | "
                    f"P/VP={result.get('P/VP')}"
                )

            except Exception as e:
                completed += 1
                log_ticker(ticker_original, "FINAL", "ERRO", f"falha crítica no future: {e}")
                results[index] = empty_result(
                    ticker=ticker_original,
                    status="ERRO",
                    erro=f"falha crítica no future: {e}",
                    fonte=None,
                )

    for i, result in enumerate(results):
        if result is None:
            results[i] = empty_result(
                ticker=tickers[i],
                status="ERRO",
                erro="resultado ausente após execução paralela",
                fonte=None,
            )

    return results


# ================== WRITE ==================

def write_sheet(df):
    df = df.reindex(columns=COLUMNS)
    df = sanitize_df(df)

    data = clean_for_json([df.columns.tolist()] + df.values.tolist())

    log("PLANILHA | validando JSON antes de limpar a aba")
    validate_json_safe(data)

    log("PLANILHA | JSON validado com sucesso")
    log(f"PLANILHA | limpando intervalo {CLEAR_RANGE}")
    sheet_dados.batch_clear([CLEAR_RANGE])

    log(f"PLANILHA | gravando {len(df)} linhas e {len(COLUMNS)} colunas")
    sheet_dados.update(range_name="A1", values=data, value_input_option="RAW")


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
    log("FONTE | Investidor10 somente; Status Invest desativado")
    log("COLUNAS | Ticker, Preço, DY, Rendimentos 12M, P/VP, Último Rendimento")
    log("R12M | estratégia Investidor10: span.content--info--item--title = YIELD 12 MESES")
    log(f"CACHE | arquivo: {CACHE_FILE}")
    log(f"CACHE | TTL configurado: {round(CACHE_TTL / 3600, 1)} horas")
    log(f"CACHE | schema version exigido: {CACHE_SCHEMA_VERSION}")
    log(f"RATE LIMIT | intervalo global mínimo: {RATE_LIMIT}s")
    log(f"THREADS | max_workers: {MAX_WORKERS}")

    results = fetch_all(tickers)
    results = clean_for_json(results)

    df = pd.DataFrame(results)
    df = df.reindex(columns=COLUMNS)
    df = sanitize_df(df)

    try:
        ordem_tickers = {ticker: index for index, ticker in enumerate(tickers)}
        df["_ordem_original"] = df["Ticker"].map(ordem_tickers)
        df = df.sort_values("_ordem_original", kind="stable")
        df = df.drop(columns=["_ordem_original"])
        log("ORDEM | DataFrame reordenado conforme aba FIIs")
    except Exception as e:
        log(f"ORDEM | falha ao reforçar ordenação: {e}")

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
        sem_r12m = df[df["Rendimentos 12M"].isna()]["Ticker"].tolist()

        if sem_preco:
            log(f"VALIDAÇÃO | FIIs sem preço capturado: {', '.join(sem_preco)}")
        if sem_dy:
            log(f"VALIDAÇÃO | FIIs sem DY 12M capturado: {', '.join(sem_dy)}")
        if sem_pvp:
            log(f"VALIDAÇÃO | FIIs sem P/VP capturado: {', '.join(sem_pvp)}")
        if sem_r12m:
            log(f"VALIDAÇÃO | FIIs sem Rendimentos 12M capturado: {', '.join(sem_r12m)}")
        if not sem_preco and not sem_dy and not sem_pvp:
            log("VALIDAÇÃO | todos os FIIs possuem Preço Atual, DY 12M e P/VP")
    except Exception as e:
        log(f"VALIDAÇÃO | erro ao validar campos mínimos: {e}")

    log("PLANILHA | gravando dados")
    write_sheet(df)

    save_cache(cache)

    elapsed = round(time.time() - start, 2)
    log(f"FINALIZADO | tempo total: {elapsed}s")


if __name__ == "__main__":
    main()
