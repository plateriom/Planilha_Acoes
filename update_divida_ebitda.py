import yfinance as yf
import gspread
import random
import time
from datetime import datetime
from google.oauth2.service_account import Credentials

SHEET_ID = "1saHSvkcUV7FUbYaJWJUtC6LBH2svMBOs-5kd8TMGpFU"
DATA_SHEET = "Dados"

print("🔍 INICIANDO DEBUG COMPLETO...")

SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
client = gspread.authorize(creds)
spreadsheet = client.open_by_key(SHEET_ID)
sheet = spreadsheet.worksheet(DATA_SHEET)

data = sheet.get_all_values()
print(f"Total de linhas na aba: {len(data)}")
print(f"Cabeçalho: {data[0] if data else 'Sem cabeçalho'}")

tickers = [row[0].strip().upper() for row in data[1:] if len(row) > 0 and row[0].strip()]
print(f"Tickers encontrados: {len(tickers)}")
print(f"Primeiros 5 tickers: {tickers[:5]}")

# Teste com apenas 3 tickers primeiro
test_tickers = tickers[:3]
valores = []

for ticker in test_tickers:
    try:
        t = yf.Ticker(f"{ticker}.SA")
        bal = t.balance_sheet
        fin = t.financials
        debt = bal.loc['Total Debt'].iloc[0] if not bal.empty and 'Total Debt' in bal.index else None
        ebitda = fin.loc['EBITDA'].iloc[0] if not fin.empty and 'EBITDA' in fin.index else None
        value = round(float(debt) / float(ebitda), 2) if debt and ebitda and float(ebitda) != 0 else None
        valores.append([value])
        print(f"✓ {ticker} → {value}")
    except Exception as e:
        valores.append([None])
        print(f"✗ {ticker} → Erro: {type(e).__name__}")
    time.sleep(4)

print(f"\nValores gerados: {valores}")

# Tentativa de update
try:
    sheet.update(range_name=f"F2:F{len(valores)+1}", values=valores)
    print("✅ Update executado sem erro técnico!")
    print("Verifique a planilha em 10-20 segundos")
except Exception as e:
    print(f"❌ Erro no update: {e}")

print("Debug finalizado.")
