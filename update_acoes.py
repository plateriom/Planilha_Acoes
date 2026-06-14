import yfinance as yf
import gspread
import pandas as pd
import random
import time
from datetime import datetime
import numpy as np
from google.oauth2.service_account import Credentials

# ================== CONFIGURAÇÕES ==================
SHEET_ID = "1saHSvkcUV7FUbYaJWJUtC6LBH2svMBOs-5kd8TMGpFU"
TICKERS_SHEET = "AÇÕES"
DATA_SHEET = "Dados"

# ===================================================

print("🚀 Iniciando versão ULTRA RÁPIDA...")

SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
client = gspread.authorize(creds)
spreadsheet = client.open_by_key(SHEET_ID)

# Ler tickers
sheet_acoes = spreadsheet.worksheet(TICKERS_SHEET)
tickers = [str(row[0]).strip().upper() for row in sheet_acoes.get_all_values()[1:] 
           if row and str(row[0]).strip()]
tickers = list(dict.fromkeys([t for t in tickers if len(t) > 1]))
print(f"✅ {len(tickers)} tickers")

def get_fundamentals(ticker):
    try:
        t = yf.Ticker(f"{ticker}.SA")
        info = t.info
        
        div_yield = info.get('trailingAnnualDividendYield') or info.get('dividendYield')
        
        return {
            "Ticker": ticker,
            "Margem Líquida (%)": round(info.get('profitMargins', 0) * 100, 2),
            "ROE (%)": round(info.get('returnOnEquity', 0) * 100, 2),
            "P/VP": round(info.get('priceToBook', 0), 2),
            "Div Yield 12M (%)": round(div_yield * 100, 2) if div_yield else None,
            "Dívida/EBITDA": None,   # Temporariamente desativado (muito lento)
            "Fonte": "Yahoo Rápido",
            "Atualizado em": datetime.now().strftime("%d/%m/%Y %H:%M")
        }
    except:
        return {"Ticker": ticker, "Erro": "Falha"}

# Execução RÁPIDA
dados = []
for i, ticker in enumerate(tickers):
    print(f"[{i+1}/{len(tickers)}] {ticker}")
    dados.append(get_fundamentals(ticker))
    time.sleep(2.2 + random.uniform(0, 1.5))   # pausa mínima

# Enviar
df = pd.DataFrame(dados)
df = df.replace([np.inf, -np.inf, float('nan')], None)

sheet_dados = spreadsheet.worksheet(DATA_SHEET)
sheet_dados.clear()
sheet_dados.update([df.columns.values.tolist()] + df.values.tolist())

print("✅ Concluído em modo rápido!")
