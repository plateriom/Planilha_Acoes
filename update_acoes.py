import yfinance as yf
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
TICKERS_SHEET = "AÇÕES"
DATA_SHEET = "Dados"

MAX_WORKERS = 3
MAX_RETRIES = 4
RATE_LIMIT = 2.2

CACHE_FILE = "cache_fundamentals.json"
CACHE_TTL = 60 * 60 * 6  # 6 horas
CACHE_SCHEMA_VERSION = 2  # versão com Preço Atual

MIN_VALID_FIELDS = 4

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

COLUMNS = [
    "Ticker",
    "Preço Atual",
    "Margem Líquida (%)",
    "ROE (%)",
    "P/VP",
    "Div Yield 12M (%)",
    "Payout (%)",
    "ROIC (%)",
    "ROA (%)",
    "Liquidez Corrente",
    "CAGR Lucros 5 anos (%)",
    "Fonte",
    "Atualizado em",
    "Status",
    "Erro"
]


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


# ================== AUTH ==================

creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
client = gspread.authorize(creds)

spreadsheet = client.open_by_key(SHEET_ID)
sheet_acoes = spreadsheet.worksheet(TICKERS_SHEET)
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

    schema_version = data.get("schema_version", 1)

    if schema_version < CACHE_SCHEMA_VERSION:
        log_ticker(ticker, "CACHE", "IGNORADO", "cache antigo sem Preço Atual")
        return None

    timestamp = data.get("timestamp", 0)

    if time.time() - timestamp > CACHE_TTL:
        log_ticker(ticker, "CACHE", "EXPIRADO")
        return None

    payload = data.get("payload")

    if not payload:
        return None

    if "Preço Atual" not in payload or payload.get("Preço Atual") is None:
        log_ticker(ticker, "CACHE", "IGNORADO", "payload sem Preço Atual")
        return None

    result = dict(payload)
    fonte = result.get("Fonte", "DESCONHECIDA")
    result["Status"] = f"CACHE_{fonte.upper()}"

    return clean_for_json(result)


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

def safe_percent(v):
    if isinstance(v, (int, float, np.integer, np.floating)):
        v = float(v)
        if math.isnan(v) or math.isinf(v):
            return None
        return round(v * 100, 2)
    return None


def safe_round(v):
    if isinstance(v, (int, float, np.integer, np.floating)):
        v = float(v)
        if math.isnan(v) or math.isinf(v):
            return None
        return round(v, 2)
    return None


def sanitize_df(df):
    df = df.copy()
    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.astype(object)
    df = df.where(pd.notnull(df), None)
    return df


def parse_br_number(value):
    if value is None:
        return None

    s = str(value).strip()

    if not s or s in ["-", "--", "N/A", "n/a", "None", "nan", "NaN"]:
        return None

    s = s.replace("%", "")
    s = s.replace("R$", "")
    s = s.replace(" ", "")
    s = s.replace("\xa0", "")

    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")

    try:
        number = float(s)

        if math.isnan(number) or math.isinf(number):
            return None

        return round(number, 2)
    except Exception:
        return None


def count_valid_fields(data):
    ignored = {"Ticker", "Fonte", "Atualizado em", "Status", "Erro"}

    return sum(
        1 for k, v in data.items()
        if k not in ignored and v is not None
    )


def normalize_result(data):
    result = {}

    for col in COLUMNS:
        result[col] = data.get(col)

    return clean_for_json(result)


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


def extract_metric(text, label):
    pattern = (
        re.escape(label)
        + r"\s*[:\-]?\s*"
        + r"(-?\d{1,3}(?:\.\d{3})*,\d+%?|"
        + r"-?\d+,\d+%?|"
        + r"-?\d+(?:\.\d+)?%?|"
        + r"--|-)"
    )

    match = re.search(pattern, text, flags=re.I)

    if not match:
        return None

    return parse_br_number(match.group(1))


def extract_price_investidor10(text, ticker):
    ticker = ticker.upper().strip()

    number_pattern = r"(-?\d{1,3}(?:\.\d{3})*,\d+|-?\d+,\d+|-?\d+(?:\.\d+)?)"

    patterns = [
        rf"{re.escape(ticker)}\s+Cotação\s+R\$\s*{number_pattern}",
        rf"Cotação\s+R\$\s*{number_pattern}",
        rf"COTAÇÃO\s+{re.escape(ticker)}.*?R\$\s*{number_pattern}",
        rf"Preço Atual\s+R\$\s*{number_pattern}",
        rf"Preço\s+Atual\s+R\$\s*{number_pattern}"
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I | re.S)
        if match:
            value = parse_br_number(match.group(1))
            if value is not None and value > 0:
                return value

    return None


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


# ================== LOAD TICKERS ==================

def load_tickers():
    values = sheet_acoes.get_all_values()

    tickers = [
        str(row[0]).strip().upper()
        for row in values[1:]
        if row and str(row[0]).strip()
    ]

    tickers = list(dict.fromkeys([t for t in tickers if len(t) > 1]))
    return tickers


# ================== INVESTIDOR10 ==================

def fetch_investidor10(ticker):
    url = f"https://investidor10.com.br/acoes/{ticker.lower()}/"
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
                raise Exception("ticker não encontrado no Investidor10")

            if response.status_code == 410:
                raise Exception("ativo indisponível/removido no Investidor10 HTTP 410")

            response.raise_for_status()

            text = html_to_text(response.text)

            data = {
                "Ticker": ticker,
                "Preço Atual": extract_price_investidor10(text, ticker),
                "Margem Líquida (%)": extract_metric(text, "Margem Líquida"),
                "ROE (%)": extract_metric(text, "ROE"),
                "P/VP": extract_metric(text, "P/VP"),
                "Div Yield 12M (%)": extract_metric(text, "Dividend Yield"),
                "Payout (%)": extract_metric(text, "Payout"),
                "ROIC (%)": extract_metric(text, "ROIC"),
                "ROA (%)": extract_metric(text, "ROA"),
                "Liquidez Corrente": extract_metric(text, "Liquidez Corrente"),
                "CAGR Lucros 5 anos (%)": extract_metric(text, "CAGR Lucros 5 anos"),
                "Fonte": "Investidor10",
                "Atualizado em": datetime.now().strftime("%d/%m/%Y %H:%M"),
                "Status": "OK",
                "Erro": None
            }

            data = clean_for_json(data)

            valid_count = count_valid_fields(data)

            found_fields = [
                k for k, v in data.items()
                if k not in ["Ticker", "Fonte", "Atualizado em", "Status", "Erro"]
                and v is not None
            ]

            missing_fields = [
                k for k, v in data.items()
                if k not in ["Ticker", "Fonte", "Atualizado em", "Status", "Erro"]
                and v is None
            ]

            if valid_count >= MIN_VALID_FIELDS:
                log_ticker(
                    ticker,
                    "INVESTIDOR10",
                    "OK",
                    f"{valid_count} campos capturados: {', '.join(found_fields)}"
                )
                return data, valid_count, None

            if valid_count > 0:
                data["Status"] = "PARCIAL"
                log_ticker(
                    ticker,
                    "INVESTIDOR10",
                    "PARCIAL",
                    f"{valid_count} campos capturados; faltando: {', '.join(missing_fields)}"
                )
                return data, valid_count, None

            raise Exception("nenhum indicador capturado no HTML")

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


# ================== YAHOO FALLBACK ==================

def get_yahoo_price(ticker_obj, info):
    candidates = [
        info.get("currentPrice"),
        info.get("regularMarketPrice"),
        info.get("previousClose")
    ]

    for value in candidates:
        parsed = safe_round(value)
        if parsed is not None and parsed > 0:
            return parsed

    try:
        fast_info = ticker_obj.fast_info

        for key in ["last_price", "lastPrice", "regular_market_price"]:
            try:
                value = fast_info.get(key)
            except Exception:
                value = None

            parsed = safe_round(value)
            if parsed is not None and parsed > 0:
                return parsed

    except Exception:
        pass

    return None


def fetch_yahoo(ticker):
    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            rate_limiter()

            log_ticker(
                ticker,
                "YAHOO",
                f"TENTATIVA {attempt + 1}/{MAX_RETRIES}",
                f"{ticker}.SA"
            )

            t = yf.Ticker(f"{ticker}.SA")
            info = t.info

            if not isinstance(info, dict) or not info:
                raise Exception("Yahoo retornou info vazio")

            div_yield = info.get("trailingAnnualDividendYield") or info.get("dividendYield")

            data = {
                "Ticker": ticker,
                "Preço Atual": get_yahoo_price(t, info),
                "Margem Líquida (%)": safe_percent(info.get("profitMargins")),
                "ROE (%)": safe_percent(info.get("returnOnEquity")),
                "P/VP": safe_round(info.get("priceToBook")),
                "Div Yield 12M (%)": safe_percent(div_yield),
                "Payout (%)": safe_percent(info.get("payoutRatio")),
                "ROIC (%)": None,
                "ROA (%)": safe_percent(info.get("returnOnAssets")),
                "Liquidez Corrente": safe_round(info.get("currentRatio")),
                "CAGR Lucros 5 anos (%)": None,
                "Fonte": "Yahoo",
                "Atualizado em": datetime.now().strftime("%d/%m/%Y %H:%M"),
                "Status": "OK",
                "Erro": None
            }

            data = clean_for_json(data)

            valid_count = count_valid_fields(data)

            found_fields = [
                k for k, v in data.items()
                if k not in ["Ticker", "Fonte", "Atualizado em", "Status", "Erro"]
                and v is not None
            ]

            missing_fields = [
                k for k, v in data.items()
                if k not in ["Ticker", "Fonte", "Atualizado em", "Status", "Erro"]
                and v is None
            ]

            if valid_count >= MIN_VALID_FIELDS:
                log_ticker(
                    ticker,
                    "YAHOO",
                    "OK",
                    f"{valid_count} campos capturados: {', '.join(found_fields)}"
                )
                return data, valid_count, None

            if valid_count > 0:
                data["Status"] = "PARCIAL"
                log_ticker(
                    ticker,
                    "YAHOO",
                    "PARCIAL",
                    f"{valid_count} campos capturados; faltando: {', '.join(missing_fields)}"
                )
                return data, valid_count, None

            raise Exception("nenhum indicador útil retornado pelo Yahoo")

        except Exception as e:
            last_error = str(e)

            if attempt < MAX_RETRIES - 1:
                sleep_time = (3 ** attempt) + random.uniform(1, 2)
                log_ticker(
                    ticker,
                    "YAHOO",
                    "RETRY",
                    f"{last_error} | aguardando {round(sleep_time, 1)}s"
                )
                time.sleep(sleep_time)
            else:
                log_ticker(
                    ticker,
                    "YAHOO",
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
            f"fonte original: {cached.get('Fonte')} | preço: {cached.get('Preço Atual')}"
        )
        return normalize_result(cached)

    best_partial = None
    best_partial_count = 0
    errors = []

    i10_data, i10_count, i10_error = fetch_investidor10(ticker)

    if i10_data and i10_count >= MIN_VALID_FIELDS:
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
        "ACIONANDO YAHOO",
        "Investidor10 insuficiente ou indisponível"
    )

    yahoo_data, yahoo_count, yahoo_error = fetch_yahoo(ticker)

    if yahoo_data and yahoo_count >= MIN_VALID_FIELDS:
        set_cache(ticker, yahoo_data)
        return normalize_result(yahoo_data)

    if yahoo_data and yahoo_count > best_partial_count:
        best_partial = yahoo_data
        best_partial_count = yahoo_count

    if yahoo_error:
        errors.append(f"Yahoo: {yahoo_error}")

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

    error_msg = " | ".join(errors) if errors else "nenhuma fonte retornou dados reais"

    log_ticker(
        ticker,
        "FINAL",
        "ERRO",
        error_msg
    )

    return normalize_result({
        "Ticker": ticker,
        "Preço Atual": None,
        "Fonte": None,
        "Atualizado em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "Status": "ERRO",
        "Erro": error_msg
    })

# ================== PARALLEL ==================

def fetch_all(tickers):
    # Mantém a ordem original da aba AÇÕES
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

                # Garante que o resultado volte para a posição original
                results[index] = result

                completed += 1

                ticker = result.get("Ticker", ticker_original)
                status = result.get("Status", "SEM_STATUS")
                fonte = result.get("Fonte", "SEM_FONTE")
                preco = result.get("Preço Atual", None)

                log(
                    f"PROGRESSO [{completed}/{total}] "
                    f"{ticker} | posição original: {index + 1} | "
                    f"{status} | {fonte} | preço: {preco}"
                )

            except Exception as e:
                completed += 1

                log_ticker(
                    ticker_original,
                    "FINAL",
                    "ERRO",
                    f"falha crítica no future: {e}"
                )

                # Mesmo em erro, preserva a posição original
                results[index] = normalize_result({
                    "Ticker": ticker_original,
                    "Preço Atual": None,
                    "Fonte": None,
                    "Atualizado em": datetime.now().strftime("%d/%m/%Y %H:%M"),
                    "Status": "ERRO",
                    "Erro": f"falha crítica no future: {e}"
                })

    # Remove qualquer None residual, mas sem alterar a ordem dos existentes
    results = [
        result if result is not None else normalize_result({
            "Ticker": tickers[i],
            "Preço Atual": None,
            "Fonte": None,
            "Atualizado em": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "Status": "ERRO",
            "Erro": "resultado ausente após execução paralela"
        })
        for i, result in enumerate(results)
    ]

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

    log("PLANILHA | limpando intervalo A1:Z1000")
    sheet_dados.batch_clear(["A1:Z1000"])

    log(f"PLANILHA | gravando {len(df)} linhas e {len(COLUMNS)} colunas")
    sheet_dados.update(
        range_name="A1",
        values=data,
        value_input_option="RAW"
    )


# ================== MAIN ==================

def main():
    start = time.time()

    log("INÍCIO | carregando tickers da planilha")

    tickers = load_tickers()

    if not tickers:
        log("FINALIZADO | nenhum ticker encontrado")
        return

    log(f"TICKERS | {len(tickers)} ativos encontrados")
    log("ORDEM DE FONTES | 1º Investidor10 | 2º Yahoo | 3º ERRO/PARCIAL fail-safe")
    log("NOVA COLUNA | Preço Atual será buscado junto dos fundamentos")
    log(f"CACHE | TTL configurado: {round(CACHE_TTL / 3600, 1)} horas")
    log(f"CACHE | schema version exigido: {CACHE_SCHEMA_VERSION}")
    log(f"RATE LIMIT | intervalo global mínimo: {RATE_LIMIT}s")
    log(f"THREADS | max_workers: {MAX_WORKERS}")

    results = fetch_all(tickers)

    results = clean_for_json(results)

    df = pd.DataFrame(results)
    df = df.reindex(columns=COLUMNS)
    df = sanitize_df(df)

    log("DATAFRAME | resumo de status:")

    try:
        status_counts = df["Status"].value_counts(dropna=False).to_dict()
        for status, qtd in status_counts.items():
            log(f"STATUS | {status}: {qtd}")
    except Exception as e:
        log(f"STATUS | erro ao gerar resumo: {e}")

    try:
        sem_preco = df[df["Preço Atual"].isna()]["Ticker"].tolist()
        if sem_preco:
            log(f"PREÇO | ativos sem preço capturado: {', '.join(sem_preco)}")
        else:
            log("PREÇO | todos os ativos com preço capturado")
    except Exception as e:
        log(f"PREÇO | erro ao validar preços: {e}")

    log("PLANILHA | gravando dados")
    write_sheet(df)

    save_cache(cache)

    elapsed = round(time.time() - start, 2)
    log(f"FINALIZADO | tempo total: {elapsed}s")


# ================== RUN ==================

if __name__ == "__main__":
    main()
