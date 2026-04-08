"""Probe Pionex docs gitbook for futures page URLs."""
import httpx

base = "https://pionex-doc.gitbook.io/docs/restful"

# Based on the navigation structure found in search results:
# Account: Get Balance, Get Futures Account Balance, Get Active Positions, Get Historical Positions, Get Account Detail
# Leverage: Get Leverage, Modify Leverage
# Position: Get Position Mode, Change Position Mode
# Future Orders: New Future Order, Get One Order, Cancel One Order, etc.
# Others: Get Margin Type, Change Margin Type, Modify Isolated Position Margin, Get Funding Fee

slugs = [
    # Account section
    "account/get-balance",
    "account/get-futures-account-balance",
    "account/get-active-positions",
    "account/get-historical-positions",
    "account/get-account-detail",
    # Leverage
    "leverage/get-leverage",
    "leverage/modify-leverage",
    # Position
    "position/get-position-mode",
    "position/change-position-mode",
    # Future Orders
    "future-order/new-future-order",
    "future-orders/new-future-order",
    "futures/new-future-order",
    "future/new-future-order",
    "future-order/get-one-order",
    "future-order/cancel-one-order",
    "future-order/get-open-orders",
    "future-order/get-all-orders",
    "future-order/cancel-all-orders",
    "future-order/get-fills",
    # Margin
    "margin/get-margin-type",
    "margin/change-margin-type",
    "margin/modify-isolated-position-margin",
    # Funding
    "funding/get-funding-fee",
    "account/get-funding-fee",
    # General
    "general/basic",
    "general/authentication",
    "general/rate-limit",
    # Orders (existing)
    "orders/new-order",
]

client = httpx.Client(timeout=10, follow_redirects=True)

print("Probing docs pages...\n")
found = []
for slug in slugs:
    url = f"{base}/{slug}"
    try:
        resp = client.get(url)
        status = resp.status_code
        marker = "OK" if status == 200 else f"{status}"
        if status == 200:
            found.append(slug)
            print(f"  [OK]  {slug}")
        # Skip 404s silently
    except Exception as e:
        pass

client.close()
print(f"\nFound {len(found)} valid pages.")
