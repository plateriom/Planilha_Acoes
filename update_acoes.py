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

# User-Agents para mascarar (anti-block)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
]

# ===================================================

print("🚀 Iniciando atualização com máscara anti-block...")

# Autenticação Google
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
client = gspread.authorize(creds)
spreadsheet = client.open_by_key(SHEET_ID)

# Ler tickers
sheet_acoes = spreadsheet.worksheet(TICKERS_SHEET)
tickers = [str(row[0]).strip().upper() for row in sheet_acoes.get_all_values()[1:] 
           if row and str(row[0]).strip()]
tickers = list(dict.fromkeys([t for t in tickers if len(t) > 1]))
print(f"✅ {len(tickers)} tickers encontrados")

def get_fundamentals(ticker):
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    
    # 1. yfinance com máscara
    for attempt in range(4):
        try:
            t = yf.Ticker(f"{ticker}.SA")
            info = t.info
            
            margem = info.get('profitMargins')
            roe = info.get('returnOnEquity')
            pvp = info.get('priceToBook')
            div_yield = info.get('trailingAnnualDividendYield')
            
            # Dívida/EBITDA
            try:
                bal = t.balance_sheet
                fin = t.financials
                debt = bal.loc['Total Debt'].iloc[0] if not bal.empty and 'Total Debt' in bal.index else 0
                ebitda = fin.loc['EBITDA'].iloc[0] if not fin.empty and 'EBITDA' in fin.index else None
                debt_ebitda = round(debt / ebitda, 2) if ebitda and ebitda != 0 else None
            except:
                debt_ebitda = None

            return {
                "Ticker": ticker,
                "Margem Líquida (%)": round(margem * 100, 2) if margem else None,
                "ROE (%)": round(roe * 100, 2) if roe else None,
                "P/VP": round(pvp, 2) if pvp else None,
                "Div Yield 12M (%)": round(div_yield * 100, 2) if div_yield else None,
                "Dívida/EBITDA": debt_ebitda,
                "Fonte": "Yahoo (máscara)",
                "Atualizado em": datetime.now().strftime("%d/%m/%Y %H:%M")
            }
        except Exception as e:
            if "Too Many Requests" in str(e):
                wait = 10 + random.uniform(5, 12)
                print(f"  Rate limit em {ticker} → esperando {wait:.1f}s")
                time.sleep(wait)
            else:
                time.sleep(3)
    
    # 2. Fallback bolsai
    try:
        r = requests.get(f"https://api.usebolsai.com/fundamentals/{ticker}", headers=headers, timeout=12)
        if r.status_code == 200:
            data = r.json()
            return {**{
                "Ticker": ticker,
                "Margem Líquida (%)": round(data.get('profitMargin', 0)*100, 2),
                "ROE (%)": round(data.get('roe', 0), 2),
                "P/VP": round(data.get('priceToBook', 0), 2),
                "Div Yield 12M (%)": round(data.get('dividendYield', 0)*100, 2),
                "Dívida/EBITDA": round(data.get('debtToEbitda', 0), 2),
                "Fonte": "bolsai",
                "Atualizado em": datetime.now().strftime("%d/%m/%Y %H:%M")
            }}
    except:
        pass

    # 3. Fallback HG Brasil (se você tiver chave gratuita)
    # try:
    #     r = requests.get(f"https://api.hgbrasil.com/finance/stock_price?key=SUA_CHAVE&symbol={ticker}", timeout=10)
    #     ...
    
    return {"Ticker": ticker, "Erro": "Sem dados após tentativas"}

# ================== EXECUÇÃO ==================
dados = []
for i, ticker in enumerate(tickers):
    print(f"[{i+1}/{len(tickers)}] Processando {ticker}...")
    dados.append(get_fundamentals(ticker))
    time.sleep(5.5 + random.uniform(0, 3))  # delay humano

# Limpar NaN e enviar
df = pd.DataFrame(dados)
df = df.replace([np.inf, -np.inf, float('nan')], None)

sheet_dados = spreadsheet.worksheet(DATA_SHEET)
sheet_dados.clear()
sheet_dados.update([df.columns.values.tolist()] + df.values.tolist())

print("✅ Atualização finalizada com máscara anti-block!")
