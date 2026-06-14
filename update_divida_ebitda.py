import yfinance as yf
import gspread
import random
import time
from datetime import datetime
from google.oauth2.service_account import Credentials

SHEET_ID = "1saHSvkcUV7FUbYaJWJUtC6LBH2svMBOs-5kd8TMGpFU"
DATA_SHEET = "Dados"

print("🚀 Tentando Dívida/EBITDA com múltiplas estratégias...")

SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
client = gspread.authorize(creds)
spreadsheet = client.open_by_key(SHEET_ID)
sheet = spreadsheet.worksheet(DATA_SHEET)

data = sheet.get_all_values()
tickers = [row[0].strip().upper() for row in data[1:] if len(row) > 0 and row[0].strip()]

print(f"✅ {len(tickers)} tickers")

def get_debt_ebitda(ticker):
    try:
        t = yf.Ticker(f"{ticker}.SA")
        time.sleep(3)
        
        # Estratégia 1: balance_sheet (padrão)
        try:
            bal = t.balance_sheet
            fin = t.financials
            debt = bal.loc['Total Debt'].iloc[0] if not bal.empty and 'Total Debt' in bal.index else None
            ebitda = fin.loc['EBITDA'].iloc[0] if not fin.empty and 'EBITDA' in fin.index else None
            if debt and ebitda:
                return round(float(debt) / float(ebitda), 2)
        except:
            pass

        # Estratégia 2: quarterly
        try:
            bal = t.quarterly_balance_sheet
            fin = t.quarterly_financials
            debt = bal.loc['Total Debt'].iloc[0] if not bal.empty and 'Total Debt' in bal.index else None
            ebitda = fin.loc['EBITDA'].iloc[0] if not fin.empty and 'EBITDA' in fin.index else None
            if debt and ebitda:
                return round(float(debt) / float(ebitda), 2)
        except:
            pass

        # Estratégia 3: info keys (mais estável)
        info = t.info
        debt = info.get('totalDebt') or info.get('longTermDebt')
        ebitda = info.get('ebitda')
        if debt and ebitda:
            return round(float(debt) / float(ebitda), 2)

        return None
    except:
        return None

# Busca e update
valores = []
for i, ticker in enumerate(tickers):
    print(f"[{i+1}/{len(tickers)}] {ticker}")
    value = get_debt_ebitda(ticker)
    valores.append([value])
    time.sleep(4)

# Update em batch
if valores:
    sheet.update(range_name=f"F2:F{len(valores)+1}", values=valores)
    print(f"✅ Atualizados {len([v for v in valores if v[0] is not None])} valores!")
else:
    print("Nenhum valor gerado")

print("Finalizado!")
