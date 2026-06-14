import yfinance as yf
import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from datetime import datetime
import time

# ================== CONFIGURAÇÕES ==================
SHEET_ID = "1saHSvkcUV7FUbYaJWJUtC6LBH2svMBOs-5kd8TMGpFU"

# Nome das abas
TICKERS_SHEET = "açoes"      # Onde estão os tickers
DATA_SHEET = "dados"         # Onde vamos injetar os dados

# ===================================================

def get_fundamentals(ticker):
    try:
        t = yf.Ticker(f"{ticker}.SA")
        info = t.info
        
        margem_liquida = info.get('profitMargins')
        roe = info.get('returnOnEquity')
        pvp = info.get('priceToBook')
        div_yield = info.get('trailingAnnualDividendYield')
        
        # Dívida / EBITDA
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

# Autenticação
creds = Credentials.from_service_account_file('credentials.json')
client = gspread.authorize(creds)
spreadsheet = client.open_by_key(SHEET_ID)

# Ler tickers da aba "açoes" - Coluna A
sheet_acoes = spreadsheet.worksheet(TICKERS_SHEET)
tickers_data = sheet_acoes.get_all_values()

# Extrair tickers da coluna A (índice 0), ignorando cabeçalho
tickers = []
for row in tickers_data[1:]:  # pula a primeira linha (cabeçalho)
    if row and len(row) > 0:
        ticker = row[0].strip().upper()  # Coluna A = índice 0
        if ticker and ticker not in tickers and len(ticker) > 1:
            tickers.append(ticker)

print(f"✅ Encontrados {len(tickers)} tickers na coluna A da aba 'açoes'")

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

print("✅ Atualização concluída com sucesso na aba 'dados'!")
