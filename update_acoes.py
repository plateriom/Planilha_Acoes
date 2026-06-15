import yfinance as yf
import gspread
import pandas as pd
import numpy as np
import time
import random
import json
import os
import requests
from datetime import datetime
from google.oauth2.service_account import Credentials

# ================== CONFIG ==================
SHEET_ID = "1saHSvkcUV7FUbYaJWJUtC6LBH2svMBOs-5kd8TMGpFU"
TICKERS_SHEET = "AÇÕES"
DATA_SHEET = "Dados"

MAX_RETRIES = 3
CACHE_FILE = "cache_fundamentals.json"
CACHE_TTL = 60 * 60 * 6  # 6h

SCOPES = [
    'https://googleapis.com',
    'https://googleapis.com'
]

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

# ================== CONFIG SESSÃO HTTP (ANTI-BLOQUEIO) ==================
# Configura uma sessão que imita um navegador real Chrome
session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Connection': 'keep-alive'
})

# ================== AUTH ==================
creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
client = gspread.authorize(creds)

spreadsheet = client.open_by_key(SHEET_ID)
sheet_acoes = spreadsheet.worksheet(TICKERS_SHEET)
sheet_dados = spreadsheet.worksheet(DATA_SHEET)

# ================== CACHE ==================
def load_cache():
    if not os.path.exists(CACHE_FILE): return {}
    with open(CACHE_FILE, "r") as f: return json.load(f)

def save_cache(cache):
    with open(CACHE_FILE, "w") as f: json.dump(cache, f)

cache = load_cache()

def get_cached(ticker):
    data = cache.get(ticker)
    if not data: return None
    if time.time() - data.get("timestamp", 0) > CACHE_TTL: return None
    return data.get("payload")

def set_cache(ticker, payload):
    cache[ticker] = {"timestamp": time.time(), "payload": payload}
    save_cache(cache)

# ================== BUSCA DE DADOS (SEQUENCIAL) ==================
def fetch_ticker_data(ticker):
    cached_data = get_cached(ticker)
    if cached_data:
        log(f"[{ticker}] Dados recuperados do cache.")
        return cached_data

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Pausa aleatória entre 3 a 6 segundos para simular comportamento humano
            wait_time = random.uniform(3.0, 6.0)
            time.sleep(wait_time)
            
            log(f"[{ticker}] Buscando... (Tentativa {attempt}/{MAX_RETRIES})")
            
            # Passa a sessão mascarada do navegador para o yfinance
            t = yf.Ticker(ticker, session=session)
            info = t.info
            
            if not info or len(info) <= 1:
                raise ValueError("Resposta vazia da API.")

            payload = {
                "Ticker": ticker,
                "Preço": info.get("currentPrice", np.nan),
                "P/L": info.get("trailingPE", np.nan),
                "P/VP": info.get("priceToBook", np.nan),
                "DY (%)": (info.get("dividendYield", 0) or 0) * 100 if info.get("dividendYield") else np.nan,
                "ROE (%)": (info.get("returnOnEquity", 0) or 0) * 100 if info.get("returnOnEquity") else np.nan,
                "Margem Líq (%)": (info.get("profitMargins", 0) or 0) * 100 if info.get("profitMargins") else np.nan,
                "EV/EBITDA": info.get("enterpriseToEbitda", np.nan)
            }
            
            set_cache(ticker, payload)
            return payload

        except Exception as e:
            log(f"[{ticker}] Erro: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(5 * attempt)
            else:
                log(f"[{ticker}] Falha definitiva.")
                
    return {"Ticker": ticker, "Preço": np.nan, "P/L": np.nan, "P/VP": np.nan, "DY (%)": np.nan, "ROE (%)": np.nan, "Margem Líq (%)": np.nan, "EV/EBITDA": np.nan}

# ================== EXECUÇÃO PRINCIPAL ==================
def main():
    start_time = time.time()
    log("Iniciando atualização diária...")

    coluna_tickers = sheet_acoes.col_values(1)
    tickers = [t.strip().upper() for t in coluna_tickers[1:] if t.strip()]
    
    if not tickers:
        log("Nenhum ticker encontrado.")
        return

    log(f"Processando {len(tickers)} ativos sequencialmente...")

    # Loop estritamente sequencial (um ativo por vez)
    results = []
    for ticker in tickers:
        res = fetch_ticker_data(ticker)
        results.append(res)

    df_resultado = pd.DataFrame(results)
    df_resultado['Ticker'] = pd.Categorical(df_resultado['Ticker'], categories=tickers, ordered=True)
    df_resultado = df_resultado.sort_values('Ticker').reset_index(drop=True)
    df_resultado = df_resultado.replace([np.inf, -np.inf], np.nan).fillna("")

    header = df_resultado.columns.tolist()
    rows = df_resultado.values.tolist()
    data_to_write = [header] + rows

    log(f"Gravando dados na aba '{DATA_SHEET}'...")
    sheet_dados.clear()
    
    end_col = chr(64 + len(header)) if len(header) <= 26 else "Z"
    cell_range = f"A1:{end_col}{len(data_to_write)}"
    sheet_dados.update(range_name=cell_range, values=data_to_write)
    
    log(f"Finalizado em {time.time() - start_time:.2f} segundos!")

if __name__ == "__main__":
    main()
