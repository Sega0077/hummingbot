# custom_profit_calculation_script.py

def calculate_net_profit(trade_history, fee_percent):
    """
    Расчет чистой прибыли с учетом комиссии.
    :param trade_history: Список сделок.
    :param fee_percent: Комиссия биржи.
    :return: Чистая прибыль.
    """
    net_profit = 0

    for trade in trade_history:
        if trade['type'] == 'buy':
            # Учитываем комиссию при покупке
            cost_with_fee = trade['cost'] * (1 + fee_percent)
            net_profit -= cost_with_fee
        elif trade['type'] == 'sell':
            # Учитываем комиссию при продаже
            revenue_with_fee = trade['revenue'] * (1 - fee_percent)
            net_profit += revenue_with_fee

    return net_profit

# Пример использования
trade_history = [
    {'type': 'buy', 'cost': 1000},
    {'type': 'sell', 'revenue': 1015},
]

fee_percent = 0.002  # 0.2%

net_profit = calculate_net_profit(trade_history, fee_percent)
print(f"Net Profit: {net_profit}")
