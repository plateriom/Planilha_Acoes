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
MAX_RETRIES = 3
RATE_LIMIT = 2.2

CACHE_FILE = "cache.json"
CACHE_TTL = 21600

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

# ================== SETORES ==================
SETOR_MAP = {
    "BBAS3": "Bancos","ITUB4": "Bancos",
    "BBSE3": "Seguros","PSSA3": "Seguros",
    "TAEE11": "Energia","TRPL11": "Energia",
    "SBSP3": "Saneamento","CSMG3": "Saneamento",
    "VALE3": "Commodities","PETR4": "Commodities",
    "VIVT3": "Telecom"
}

PESO_SETOR = {
    "Bancos": {"roe_min": 15, "pvp_max": 1.5},
    "Seguros": {"roe_min": 15, "pvp_max": 2.5},
    "Energia": {"roe_min": 8, "pvp_max": 1.8},
    "Saneamento": {"roe_min": 8, "pvp_max": 2.0},
    "Commodities": {"roe_min": 10, "pvp_max": 2.0},
    "Telecom": {"roe_min": 8, "pvp_max": 2.2}
}

# ================== AUTH ==================
creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
client = gspread.authorize(creds)
spreadsheet = client.open_by_key(SHEET_ID)
sheet_acoes = spreadsheet.worksheet(TICKERS_SHEET)
sheet_dados = spreadsheet.worksheet(DATA_SHEET)

# ================== RATE LIMIT ==================
lock = threading.Lock()
last_call = [0]

def rate_limiter():
    with lock:
        elapsed = time.time() - last_call[0]
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
    if time.time() - data["timestamp"] > CACHE_TTL:
        return None
    return data["payload"]

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

# ================== LOAD ==================
def load_tickers():
    values = sheet_acoes.get_all_values()
    return [
        str(r[0]).strip().upper()
        for r in values[1:]
        if r and str(r[0]).strip()
    ]

# ================== FETCH ==================
def fetch_ticker(ticker):

    cached = get_cached(ticker)
    if cached:
        cached["Status"] = "CACHE"
        return cached

    for attempt in range(MAX_RETRIES):
        try:
            rate_limiter()

            t = yf.Ticker(f"{ticker}.SA")
            info = t.info

            div = info.get('trailingAnnualDividendYield') or info.get('dividendYield')

            data = {
                "Ticker": ticker,
                "Margem Líquida (%)": safe_percent(info.get('profitMargins')),
                "ROE (%)": safe_percent(info.get('returnOnEquity')),
                "P/VP": safe_round(info.get('priceToBook')),
                "Div Yield 12M (%)": safe_percent(div),
                "Status": "OK",
                "Erro": None,
                "Atualizado em": datetime.now().strftime("%d/%m/%Y %H:%M")
            }

            set_cache(ticker, data)
            return data

        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                sleep_time = (3 ** attempt) + random.uniform(1, 2)
                time.sleep(sleep_time)
            else:
                return {
                    "Ticker": ticker,
                    "Margem Líquida (%)": None,
                    "ROE (%)": None,
                    "P/VP": None,
                    "Div Yield 12M (%)": None,
                    "Status": "ERRO",
                    "Erro": str(e),
                    "Atualizado em": None
                }

# ================== ENGINE ==================
def filtro(row):
    return (
        row.get("ROE (%)") is not None and
        row.get("Margem Líquida (%)") is not None and
        row.get("P/VP") is not None and
        row.get("Div Yield 12M (%)") is not None and
        row.get("Margem Líquida (%)") >= 0
    )

def score_base(row):
    score = 0

    roe = row["ROE (%)"]
    margem = row["Margem Líquida (%)"]
    pvp = row["P/VP"]
    dy = row["Div Yield 12M (%)"]

    if roe > 20: score += 18
    elif roe > 15: score += 14
    elif roe > 10: score += 10
    else: score += 5

    if margem > 20: score += 17
    elif margem > 10: score += 13
    elif margem > 5: score += 9

    if pvp < 1: score += 15
    elif pvp < 1.5: score += 12
    elif pvp < 2: score += 8
    elif pvp > 3: score -= 10

    # ✅ peso maior em dividendos (ajuste correto)
    if dy > 10: score += 30
    elif dy > 8: score += 25
    elif dy > 6: score += 20
    elif dy > 4: score += 12
    elif dy > 2: score += 6
    else: score += 2

    return max(0, min(100, score))

def ajuste_setor(row, score):
    if score == 0:
        return 0, "Sem dados"

    setor = SETOR_MAP.get(row["Ticker"], "Outro")
    regras = PESO_SETOR.get(setor, {})

    roe = row.get("ROE (%)")
    pvp = row.get("P/VP")

    ajuste = 0
    if roe is not None:
        ajuste += 5 if roe >= regras.get("roe_min", 0) else -5
    if pvp is not None:
        ajuste += 5 if pvp <= regras.get("pvp_max", 10) else -5

    return max(0, min(100, score + ajuste)), setor

def momentum(ticker, score):
    if score == 0:
        return 0
    return score  # mantido leve (anti-ban)

def decisao(score):
    if score >= 80: return "COMPRAR FORTE"
    if score >= 65: return "COMPRAR"
    if score >= 45: return "MANTER"
    if score >= 30: return "REDUZIR"
    return "VENDER"

# ================== MAIN ==================
def main():
    tickers = load_tickers()

    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(fetch_ticker, t) for t in tickers]

        for i, f in enumerate(as_completed(futures)):
            r = f.result()
            results.append(r)
            print(f"[{i+1}/{len(tickers)}] {r['Ticker']}")

    df = pd.DataFrame(results)

    df["Valido"] = df.apply(filtro, axis=1)
    df["Score Base"] = df.apply(lambda r: score_base(r) if r["Valido"] else 0, axis=1)

    ajuste = df.apply(lambda r: ajuste_setor(r, r["Score Base"]), axis=1)
    df["Score Ajustado"] = [x[0] for x in ajuste]
    df["Setor"] = [x[1] for x in ajuste]

    df["Score Final"] = df["Score Ajustado"]
    df["Decisão"] = df["Score Final"].apply(decisao)

    df = sanitize(df)
    df = df.sort_values(by="Score Final", ascending=False)

    data = [df.columns.tolist()] + df.values.tolist()
    sheet_dados.batch_clear(["A1:Z1000"])
    sheet_dados.update(range_name="A1", values=data)

    save_cache(cache)

    print("✅ FINALIZADO (ANTI-BAN ESTÁVEL + FAIL-SAFE)")

if __name__ == "__main__":
    main()
