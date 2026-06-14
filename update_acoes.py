import yfinance as yf
import gspread
import pandas as pd
import requests
from google.oauth2.service_account import Credentials
from datetime import datetime
import time
import random

# ================== CONFIGURAÇÕES ==================
SHEET_ID = "1saHSvkcUV7FUbYaJWJUtC6LBH2svMBOs-5kd8TMGpFU"

TICKERS_SHEET = "AÇÕES"
DATA_SHEET = "Dados"          # Nome exato corrigido

# ===================================================

print("🚀 Iniciando atualização...")

# Autenticação
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
client = gspread.authorize(creds)
spreadsheet = client.open_by_key(SHEET_ID)

# Ler tickers
sheet_acoes = spreadsheet.worksheet(TICKERS_SHEET)
tickers = [str(row[0]).strip().upper() for row in sheet_acoes.get_all_values()[1:] 
           if row and str(row[0]).strip()]

tickers = list(dict.fromkeys([t for t in tickers if len(t) > 1]))
print(f"✅ Encontrados {len(tickers)} tickers")

# ================== BUSCA COM FALLBACK ==================
def get_fundamentals(ticker):
    # Tenta primeiro no Yahoo
    for tentativa in range(3):
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
                div_ebitda = round(debt / ebitda, 2) if ebitda else None
            except:
                div_ebitda = None

            return {
                "Ticker": ticker,
                "Margem Líquida (%)": round(margem * 100, 2) if margem else None,
                "ROE (%)": round(roe * 100, 2) if roe else None,
                "P/VP": round(pvp, 2) if pvp else None,
                "Div Yield 12M (%)": round(div_yield * 100, 2) if div_yield else None,
                "Dívida/EBITDA": div_ebitda,
                "Fonte": "Yahoo",
                "Atualizado em": datetime.now().strftime("%d/%m/%Y %H:%M")
            }
        except Exception as e:
            if "Too Many Requests" in str(e):
                time.sleep(8 + random.uniform(0, 6))
            else:
                time.sleep(4)
    
    # FALLBACK: brapi.dev (muito bom para ações brasileiras)
    try:
        print(f"  → Tentando fallback brapi para {ticker}")
        r = requests.get(f"https://brapi.dev/api/quote/{ticker}", timeout=10)
        if r.status_code == 200:
            data = r.json().get('results', [{}])[0]
            return {
                "Ticker": ticker,
                "Margem Líquida (%)": round(data.get('profitMargin', 0) * 100, 2),
                "ROE (%)": round(data.get('returnOnEquity', 0) * 100, 2),
                "P/VP": round(data.get('priceToBook', 0), 2),
                "Div Yield 12M (%)": round(data.get('dividendYield', 0) * 100, 2),
                "Dívida/EBITDA": round(data.get('debtToEbitda', 0), 2),
                "Fonte": "brapi",
                "Atualizado em": datetime.now().strftime("%d/%m/%Y %H:%M")
            }
    except:
        pass
    
    return {"Ticker": ticker, "Erro": "Sem dados (rate limit)"}

# ================== EXECUÇÃO ==================
dados = []
for i, ticker in enumerate(tickers):
    dados.append(get_fundamentals(ticker))
    time.sleep(6)  # pausa segura

# Atualizar planilha
df = pd.DataFrame(dados)
sheet_dados = spreadsheet.worksheet(DATA_SHEET)

sheet_dados.clear()
sheet_dados.update([df.columns.values.tolist()] + df.values.tolist())

print("✅ Atualização concluída com sucesso na aba 'Dados'!")
