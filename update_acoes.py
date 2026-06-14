import yfinance as yf
import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from datetime import datetime
import time

# ================== CONFIGURAÇÕES ==================
SHEET_ID = "1saHSvkcUV7FUbYaJWJUtC6LBH2svMBOs-5kd8TMGpFU"

TICKERS_SHEET = "açoes"
DATA_SHEET = "dados"

# ===================================================

def get_fundamentals(ticker):
    try:
        t = yf.Ticker(f"{ticker}.SA")
        info = t.info
        
        margem_liquida = info.get('profitMargins')
        roe = info.get('returnOnEquity')
        pvp = info.get('priceToBook')
        div_yield = info.get('trailingAnnualDividendYield')
        
        try:
            balance = t.balance_sheet
            financials = t.financials
            if not balance.empty and not financials.empty:
                total_debt = balance.loc['Total Debt'].iloc[0] if 'Total Debt' in balance.index else 0
                ebitda = financials.loc['EBITDA'].iloc[0] if 'EBITDA' in financials.index else None
                div_ebitda = round(total_debt / ebitda, 2) if ebitda and ebitda != 0 else None
            else:
                div_ebitda = None
        except:
            div_ebitda = None
        
        return {
            "Ticker": ticker,
            "Margem Líquida (%)": round(margem_liquida * 100, 2) if margem_liquida else None,
            "ROE (%)": round(roe * 100, 2) if roe else None,
            "P/VP": round(pvp, 2) if pvp else None,
            "Div Yield 12M (%)": round(div_yield * 100, 2) if div_yield else None,
            "Dívida/EBITDA": div_ebitda,
            "Atualizado em": datetime.now().strftime("%d/%m/%Y %H:%M")
        }
    except Exception as e:
        print(f"Erro ao buscar {ticker}: {e}")
        return {"Ticker": ticker, "Erro": "Falha na busca"}

# ================== EXECUÇÃO ==================
print("🚀 Iniciando atualização...")

# === AUTENTICAÇÃO CORRIGIDA ===
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
client = gspread.authorize(creds)

spreadsheet = client.open_by_key(SHEET_ID)

# Ler tickers da coluna A da aba "açoes"
sheet_acoes = spreadsheet.worksheet(TICKERS_SHEET)
tickers_data = sheet_acoes.get_all_values()

tickers = []
for row in tickers_data[1:]:
    if row and len(row) > 0:
        ticker = str(row[0]).strip().upper()
        if ticker and ticker not in tickers and len(ticker) > 1:
            tickers.append(ticker)

print(f"✅ Encontrados {len(tickers)} tickers")

# Buscar dados
dados = []
for i, ticker in enumerate(tickers):
    print(f"Buscando {ticker}... ({i+1}/{len(tickers)})")
    dados.append(get_fundamentals(ticker))
    time.sleep(1.8)

# Atualizar aba "dados"
df = pd.DataFrame(dados)
sheet_dados = spreadsheet.worksheet(DATA_SHEET)

sheet_dados.clear()
sheet_dados.update([df.columns.values.tolist()] + df.values.tolist())

print("✅ Atualização concluída com sucesso!")
