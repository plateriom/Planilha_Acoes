import yfinance as yf
import gspread
import pandas as pd
import numpy as np
import time
import random
import json
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from google.oauth2.service_account import Credentials
import os

# ================== CONFIG ==================
SHEET_ID = "1saHSvkcUV7FUbYaJWJUtC6LBH2svMBOs-5kd8TMGpFU"
TICKERS_SHEET = "AÇÕES"
DATA_SHEET = "Dados"

MAX_WORKERS = 3
MAX_RETRIES = 4
RATE_LIMIT = 2.2  # intervalo global entre chamadas
CACHE_FILE = "cache_fundamentals.json"
CACHE_TTL = 60 * 60 * 6  # 6h

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

# ================== LOG ==================
def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

# ================== AUTH ==================
creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
client = gspread.authorize(creds)

spreadsheet = client.open_by_key(SHEET_ID)
sheet_acoes = spreadsheet.worksheet(TICKERS_SHEET)
sheet_dados = spreadsheet.worksheet(DATA_SHEET)

# ================== RATE LIMIT GLOBAL ==================
lock = threading.Lock()
last_call = [0]

def rate_limiter():
    with lock:
        now = time.time()
        elapsed = now - last_call[0]

        if elapsed < RATE_LIMIT:
            time.sleep(RATE_LIMIT - elapsed)

        last_call[0] = time.time()

# ================== CACHE ==================
def load_cache():
    if not os.path.exists(CACHE_FILE):
        return {}
    with open(CACHE_FILE, "r") as f:
        return json.load(f)

def save_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f)

cache = load_cache()

def get_cached(ticker):
    data = cache.get(ticker)
    if not data:
        return None

    timestamp = data.get("timestamp", 0)
    if time.time() - timestamp > CACHE_TTL:
        return None

    return data.get("payload")

def set_cache(ticker, payload):
    cache[ticker] = {
        "timestamp": time.time(),
        "payload": payload
    }

# ================== UTILS ==================
def safe_percent(v):
    return round(v * 100, 2) if isinstance(v, (int, float)) else None

def safe_round(v):
    return round(v, 2) if isinstance(v, (int, float)) else None

def sanitize(df):
    return df.replace([np.inf, -np.inf, np.nan], None)

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

# ================== FETCH ==================
def fetch_ticker(ticker):
    # CACHE
    cached = get_cached(ticker)
    if cached:
        cached["Status"] = "CACHE"
        return cached

    for attempt in range(MAX_RETRIES):
        try:
            rate_limiter()

            t = yf.Ticker(f"{ticker}.SA")
            info = t.info

            div_yield = info.get('trailingAnnualDividendYield') or info.get('dividendYield')

            data = {
                "Ticker": ticker,
                "Margem Líquida (%)": safe_percent(info.get('profitMargins')),
                "ROE (%)": safe_percent(info.get('returnOnEquity')),
                "P/VP": safe_round(info.get('priceToBook')),
                "Div Yield 12M (%)": safe_percent(div_yield),
                "Fonte": "Yahoo",
                "Atualizado em": datetime.now().strftime("%d/%m/%Y %H:%M"),
                "Status": "OK"
            }

            set_cache(ticker, data)
            return data

        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                sleep_time = (3 ** attempt) + random.uniform(1, 2)
                log(f"{ticker} retry {attempt+1} em {round(sleep_time,1)}s")
                time.sleep(sleep_time)
            else:
                return {
                    "Ticker": ticker,
                    "Status": "ERRO",
                    "Erro": str(e)
                }

# ================== PARALLEL ==================
def fetch_all(tickers):
    results = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(fetch_ticker, t) for t in tickers]

        for i, future in enumerate(as_completed(futures)):
            try:
                result = future.result()
                results.append(result)
                log(f"[{i+1}/{len(tickers)}] {result['Ticker']} ✅")
            except Exception as e:
                log(f"Falha crítica: {e}")

    return results

# ================== WRITE ==================
def write_sheet(df):
    df = sanitize(df)

    data = [df.columns.tolist()] + df.values.tolist()

    # NÃO apaga tudo cegamente → mais seguro
    sheet_dados.batch_clear(["A1:Z1000"])

    sheet_dados.update(
        range_name="A1",
        values=data,
        value_input_option="RAW"
    )

# ================== MAIN ==================
def main():
    start = time.time()

    log("Carregando tickers...")
    tickers = load_tickers()

    if not tickers:
        log("Nenhum ticker encontrado")
        return

    log(f"{len(tickers)} ativos")

    results = fetch_all(tickers)

    df = pd.DataFrame(results)

    log("Gravando na planilha...")
    write_sheet(df)

    save_cache(cache)

    elapsed = round(time.time() - start, 2)
    log(f"Finalizado em {elapsed}s")

# ================== RUN ==================
if __name__ == "__main__":
    main()
