import yfinance as yf
import gspread
import pandas as pd
import requests
import random
import time
from datetime import datetime
import numpy as np
from google.oauth2.service_account import Credentials

# ================== CONFIGURAÇÕES ==================
SHEET_ID = "1saHSvkcUV7FUbYaJWJUtC6LBH2svMBOs-5kd8TMGpFU"
TICKERS_SHEET = "AÇÕES"
DATA_SHEET = "Dados"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
]

# ===================================================

print("🚀 Iniciando versão melhorada...")

SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
client = gspread.authorize(creds)
spreadsheet = client.open_by_key(SHEET_ID)

sheet_acoes = spreadsheet.worksheet(TICKERS_SHEET)
tickers = [str(row[0]).strip().upper() for row in sheet_acoes.get_all_values()[1:] if row and str(row[0]).strip()]
tickers = list(dict.fromkeys([t for t in tickers if len(t) > 1]))

def get_fundamentals(ticker):
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    
    for attempt in range(5):
        try:
            t = yf.Ticker(f"{ticker}.SA")
            info = t.info
            
            # Tentativa mais agressiva de pegar dividend yield
            div_yield = info.get('trailingAnnualDividendYield') or info.get('dividendYield') or None
            
            margem = info.get('profitMargins')
            roe = info.get('returnOnEquity')
            pvp = info.get('priceToBook')
            
            # Dívida/EBITDA mais robusto
            debt_ebitda = None
            try:
                bal = t.balance_sheet
                fin = t.financials
                debt = bal.loc['Total Debt'].iloc[0] if not bal.empty and 'Total Debt' in bal.index else None
                ebitda = fin.loc['EBITDA'].iloc[0] if not fin.empty and 'EBITDA' in fin.index else None
                if debt is not None and ebitda and ebitda != 0:
                    debt_ebitda = round(debt / ebitda, 2)
            except:
                pass

            return {
                "Ticker": ticker,
                "Margem Líquida (%)": round(margem * 100, 2) if margem is not None else None,
                "ROE (%)": round(roe * 100, 2) if roe is not None else None,
                "P/VP": round(pvp, 2) if pvp is not None else None,
                "Div Yield 12M (%)": round(div_yield * 100, 2) if div_yield is not None else None,
                "Dívida/EBITDA": debt_ebitda,
                "Fonte": "Yahoo",
                "Atualizado em": datetime.now().strftime("%d/%m/%Y %H:%M")
            }
        except:
            time.sleep(7 + random.uniform(4, 12))
    
    return {"Ticker": ticker, "Erro": "Sem dados"}

# Execução
dados = []
for i, ticker in enumerate(tickers):
    print(f"[{i+1}/{len(tickers)}] {ticker}")
    dados.append(get_fundamentals(ticker))
    time.sleep(6.5 + random.uniform(0, 3))

# Limpar e enviar
df = pd.DataFrame(dados)
df = df.replace([np.inf, -np.inf, float('nan')], None)

sheet_dados = spreadsheet.worksheet(DATA_SHEET)
sheet_dados.clear()
sheet_dados.update([df.columns.values.tolist()] + df.values.tolist())

print("✅ Atualização concluída!")
