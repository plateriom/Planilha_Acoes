import yfinance as yf
import gspread
import pandas as pd
import numpy as np
import time
import random
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from google.oauth2.service_account import Credentials

# ================== CONFIG ==================
SHEET_ID = "1saHSvkcUV7FUbYaJWJUtC6LBH2svMBOs-5kd8TMGpFU"
TICKERS_SHEET = "AÇÕES"
DATA_SHEET = "Dados"

MAX_WORKERS = 5
MAX_RETRIES = 3

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

print("🚀 Inicializando engine institucional...")

# ================== AUTH ==================
creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
client = gspread.authorize(creds)

spreadsheet = client.open_by_key(SHEET_ID)
sheet_acoes = spreadsheet.worksheet(TICKERS_SHEET)
sheet_dados = spreadsheet.worksheet(DATA_SHEET)

# ================== UTILS ==================
def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def safe_percent(value):
    return round(value * 100, 2) if isinstance(value, (int, float)) else None

def safe_round(value):
    return round(value, 2) if isinstance(value, (int, float)) else None

def sanitize(df):
    return df.replace([np.inf, -np.inf, np.nan], None)

# ================== LOAD TICKERS ==================
def load_tickers():
    log("Carregando tickers...")
    values = sheet_acoes.get_all_values()

    tickers = [
        str(row[0]).strip().upper()
        for row in values[1:]
        if row and str(row[0]).strip()
    ]

    tickers = list(dict.fromkeys([t for t in tickers if len(t) > 1]))

    log(f"{len(tickers)} tickers válidos carregados")
    return tickers

# ================== FETCH ==================
def fetch_ticker(ticker):
    for attempt in range(MAX_RETRIES):
        try:
            t = yf.Ticker(f"{ticker}.SA")
            info = t.info

            div_yield = info.get('trailingAnnualDividendYield') or info.get('dividendYield')

            return {
                "Ticker": ticker,
                "Margem Líquida (%)": safe_percent(info.get('profitMargins')),
                "ROE (%)": safe_percent(info.get('returnOnEquity')),
                "P/VP": safe_round(info.get('priceToBook')),
                "Div Yield 12M (%)": safe_percent(div_yield),
                "Fonte": "Yahoo",
                "Atualizado em": datetime.now().strftime("%d/%m/%Y %H:%M"),
                "Status": "OK"
            }

        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                sleep_time = (2 ** attempt) + random.uniform(0.5, 1.5)
                log(f"{ticker} retry {attempt+1} em {round(sleep_time,1)}s")
                time.sleep(sleep_time)
            else:
                return {
                    "Ticker": ticker,
                    "Status": "ERRO",
                    "Erro": str(e)
                }

# ================== PARALLEL ENGINE ==================
def fetch_all(tickers):
    log("Iniciando coleta paralela...")
    results = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_ticker, t): t for t in tickers}

        for i, future in enumerate(as_completed(futures)):
            ticker = futures[future]
            try:
                data = future.result()
                results.append(data)
                log(f"[{i+1}/{len(tickers)}] {ticker} ✅")
            except Exception as e:
                log(f"{ticker} falhou geral: {e}")
                results.append({"Ticker": ticker, "Status": "FALHA CRÍTICA"})

    return results

# ================== WRITE ==================
def write_sheet(df):
    log("Sanitizando dados...")
    df = sanitize(df)

    log("Escrevendo no Google Sheets...")

    data = [df.columns.tolist()] + df.values.tolist()

    # limpa somente o necessário (mais seguro)
    sheet_dados.batch_clear(["A:Z"])

    sheet_dados.update(
        range_name="A1",
        values=data,
        value_input_option="RAW"
    )

    log("✅ Escrita concluída")

# ================== MAIN ==================
def main():
    start = time.time()

    tickers = load_tickers()

    if not tickers:
        log("⚠️ Nenhum ticker encontrado")
        return

    results = fetch_all(tickers)

    df = pd.DataFrame(results)

    write_sheet(df)

    elapsed = round(time.time() - start, 2)
    log(f"🏁 Finalizado em {elapsed}s")

# ================== RUN ==================
if __name__ == "__main__":
    main()
