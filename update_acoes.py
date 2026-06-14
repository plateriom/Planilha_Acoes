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

print("🚀 Iniciando versão rápida (máscara leve)...")

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
    for attempt in range(3):  # reduzido de 5 para 3
        try:
            t = yf.Ticker(f"{ticker}.SA")
            info = t.info
            
            div_yield = info.get('trailingAnnualDividendYield') or info.get('dividendYield')
            
            margem = info.get('profitMargins')
            roe = info.get('returnOnEquity')
            pvp = info.get('priceToBook')
            
            # Dívida/EBITDA
            debt_ebitda = None
            try:
                bal = t.balance_sheet
                fin = t.financials
                debt = bal.loc['Total Debt'].iloc[0] if not bal.empty and 'Total Debt' in bal.index else None
                ebitda = fin.loc['EBITDA'].iloc[0] if not fin.empty and 'EBITDA' in fin.index else None
                if debt is not None and ebitda and ebitda != 0:
                    debt_ebitda = round(float(debt) / float(ebitda), 2)
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
            time.sleep(4 + random.uniform(0, 5))
    
    return {"Ticker": ticker, "Erro": "Sem dados"}

# ================== EXECUÇÃO COM TEMPO CONTROLADO ==================
dados = []
for i, ticker in enumerate(tickers):
    print(f"[{i+1}/{len(tickers)}] {ticker}")
    dados.append(get_fundamentals(ticker))
    time.sleep(3.5 + random.uniform(0, 2))   # pausa bem menor

# Enviar para planilha
df = pd.DataFrame(dados)
df = df.replace([np.inf, -np.inf, float('nan')], None)

sheet_dados = spreadsheet.worksheet(DATA_SHEET)
sheet_dados.clear()
sheet_dados.update([df.columns.values.tolist()] + df.values.tolist())

print("✅ Atualização concluída!")
