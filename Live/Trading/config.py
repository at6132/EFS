# =============================================================================
# MEXC ACCOUNT CONFIGURATION
# =============================================================================
# 
# Two MEXC accounts for distributed trading
# Each account will handle 6 different cryptocurrencies
#
# ACCOUNT 1: Handles DOGE, FLOKI, ORDI, PEPE, SEI, SHIB
# ACCOUNT 2: Handles KAVA, THETA, TURBO, UNI, WIF, ZEC
#

# MEXC API Tokens
ACCOUNT1 = "WEBb923793719a7d08e7c837ad5fdadaf9424ece9b536d5c7cb71a5c97398626db2"
ACCOUNT2 = "WEB3f6fa781e9663566d5ea5d670e9480dca70bc60141f9910fbd6fe2322fce267c"
ACCOUNT3 = "WEB6d91191204b5b40aa495568685d2e8d4a3f8c8abeacfa769a019387cfa8a14a1"

# Account assignments for each cryptocurrency
ACCOUNT_ASSIGNMENTS = {
    # Account 1 - 6 symbols
    "DOGE_USDT": ACCOUNT1,
    "FLOKI_USDT": ACCOUNT1, 
    "ORDI_USDT": ACCOUNT1,
    "PEPE_USDT": ACCOUNT1,
    "SEI_USDT": ACCOUNT1,
    "SHIB_USDT": ACCOUNT1,
    
    # Account 2 - 6 symbols  
    "KAVA_USDT": ACCOUNT2,
    "THETA_USDT": ACCOUNT2,
    "TURBO_USDT": ACCOUNT2,
    "UNI_USDT": ACCOUNT1,
    "WIF_USDT": ACCOUNT2,
    "ZEC_USDT": ACCOUNT2,

    # Account 3 - 6 symbols
    "ARKM_USDT": ACCOUNT3,
    "PYTH_USDT": ACCOUNT1,
    "RUNE_USDT": ACCOUNT3,
    "XAI_USDT": ACCOUNT3,
}

def get_api_token(symbol: str) -> str:
    """Get the appropriate API token for a given trading symbol"""
    return ACCOUNT_ASSIGNMENTS.get(symbol, ACCOUNT1)  # Default to ACCOUNT1 if symbol not found

def get_account_name(symbol: str) -> str:
    """Get the account name for a given trading symbol"""
    token = get_api_token(symbol)
    if token == ACCOUNT1:
        return "Account 1"
    elif token == ACCOUNT2:
        return "Account 2"
    else:
        return "Unknown Account"