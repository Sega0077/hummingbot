import logging
import os
from pydantic import Field
from decimal import Decimal
from typing import List, Dict

from hummingbot.client.config.config_data_types import ClientFieldData, BaseClientModel
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import OrderType, PriceType, TradeType
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.core.event.events import OrderFilledEvent
from hummingbot.data_feed.candles_feed.candles_factory import CandlesConfig, CandlesFactory
from hummingbot.data_feed.market_data_provider import MarketDataProvider
from hummingbot.strategy.directional_strategy_base import DirectionalStrategyBase

import pandas_ta as ta


class RSIAMMConfig(BaseClientModel):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))
    exchange: str = Field("binance", client_data=ClientFieldData(prompt_on_new=True, prompt=lambda
        mi: "Enter the exchange where the bot will trade:"))
    trading_pair: str = Field("BTC-USDT", client_data=ClientFieldData(prompt_on_new=True, prompt=lambda
        mi: "Enter the trading pair in which the bot will place orders:"))
    order_amount: Decimal = Field(0.0003, client_data=ClientFieldData(prompt_on_new=True, prompt=lambda
        mi: "Enter the order amount (denominated in base asset):"))
    order_refresh_time: int = Field(15, client_data=ClientFieldData(prompt_on_new=True, prompt=lambda
        mi: "Enter the order refresh time (in seconds):"))
    rsi_high: int = Field(70, client_data=ClientFieldData(prompt_on_new=True, prompt=lambda
        mi: "Enter the RSI high (e.g. 70): "))
    rsi_low: int = Field(30, client_data=ClientFieldData(prompt_on_new=True, prompt=lambda
        mi: "Enter the RSI low (e.g. 30):"))
    interval: str = Field('3m', client_data=ClientFieldData(prompt_on_new=True, prompt=lambda
        mi: "Enter the candle interval (3m)"))
    natr_period: int = Field(100, client_data=ClientFieldData(prompt_on_new=True, prompt=lambda
        mi: "Enter the NATR (Natural average true range) period:"))
    natr_spread_multiplier: float = Field(0.5, client_data=ClientFieldData(prompt_on_new=True, prompt=lambda
        mi: "Enter the order spread NATR multiplier:"))
    max_records: int = Field(1000, client_data=ClientFieldData(prompt_on_new=True, prompt=lambda
        mi: "Enter the candle max records used for RSI calculation"))
    rsi_spread_change: float = Field(0.5, client_data=ClientFieldData(prompt_on_new=True, prompt=lambda
        mi: "Enter the mid price shift from spread from RSI signal (If set to 0.5 (50%), mid price will shift by 50% up or down of the NATR"))


class RSIAMM(DirectionalStrategyBase):
    """
    Design Template: https://hummingbot-foundation.notion.site/RSI-Adaptive-Market-Maker-RSI-AMM-b831361f06204dabb5d10bbfd43c2f8f
    Video: -
    Description:
    The bot will place two orders around the adjusted midprice based on RSI signal in a trading_pair on
    exchange, with a spread calculated by natural average true range multiplied by the natr_spread_multiplier.
    Every order_refresh_time in seconds, the bot will cancel and replace the orders.
    """

    create_timestamp = 0
    last_mid_price = None

    @classmethod
    def init_markets(cls, config: RSIAMMConfig):
        cls.markets = {config.exchange: {config.trading_pair}}
        cls.candles = [CandlesFactory.get_candle(
            CandlesConfig(connector=config.exchange, trading_pair=config.trading_pair,
                          interval=config.interval, max_records=config.max_records))]

    def __init__(self, connectors: Dict[str, ConnectorBase], config: RSIAMMConfig):
        super().__init__(connectors)
        self.config = config
        self.exchange = config.exchange
        self.market_data_provider = MarketDataProvider(connectors)
        self.max_records = self.config.natr_period + 10
        self.market_data_provider.initialize_candles_feed(
            config=CandlesConfig(connector=self.config.exchange,
                                 trading_pair=self.config.trading_pair,
                                 interval=self.config.interval,
                                 max_records=self.config.max_records)
        )

    def on_tick(self):
        if not self.market_data_provider.ready:
            return

        if self.create_timestamp <= self.current_timestamp:
            self.cancel_all_orders()
            mid_price = self.market_data_provider.get_price_by_type(self.config.exchange, self.config.trading_pair, PriceType.MidPrice)
            # cancel, propose, and place orders
            msg = (f"Mid price is {mid_price}")
            self.log_with_clock(logging.INFO, msg)
            proposal: List[OrderCandidate] = self.create_proposal(mid_price)
            self.place_orders(proposal)
            self.create_timestamp = self.config.order_refresh_time + self.current_timestamp

    def get_rsi_signal(self):
        """
        Generates the trading signal based on the RSI indicator.
        Returns:
            int: The trading signal (-1 for sell signal, 0 for netural signal, 1 for buy signal).
        """
        candles_df = self.get_processed_df()
        rsi_value = candles_df.iat[-1, -1]
        msg = (f"RSI signal is {rsi_value}")
        self.log_with_clock(logging.INFO, msg)
        if rsi_value > self.config.rsi_high:
            self.log_with_clock(logging.INFO, f"RSI signal is over {self.config.rsi_high} - adjusting mid price")
            return -1
        elif rsi_value < self.config.rsi_low:
            self.log_with_clock(logging.INFO, f"RSI signal is under {self.config.rsi_low} - adjusting mid price")
            return 1
        else:
            return 0

    def get_processed_df(self):
        """
        Retrieves the processed dataframe with RSI values.
        Returns:
            pd.DataFrame: The processed dataframe with RSI values.
        """
        candles_df = self.candles[0].candles_df
        candles_df.ta.rsi(length=7, append=True)
        return candles_df

    def create_proposal(self, mid_price) -> List[OrderCandidate]:
        natr = self.get_natr()

        # calculate RSI, if signal is 1 then BUY signal, -1 is SELL signal. Shift the MID price accordingly
        rsi_signal = self.get_rsi_signal()
        if rsi_signal == 1:
            adjusted_mid_price = mid_price * Decimal(1 + (self.config.natr_spread_multiplier * natr * self.config.rsi_spread_change))
        elif rsi_signal == -1:
            adjusted_mid_price = mid_price * Decimal(1 - (self.config.natr_spread_multiplier * natr * self.config.rsi_spread_change))
        else:
            adjusted_mid_price = mid_price
        self.log_with_clock(logging.INFO, f"Adjusted mid price is  {adjusted_mid_price}")
        buy_price = adjusted_mid_price * Decimal(1 - self.config.natr_spread_multiplier * natr)
        sell_price = adjusted_mid_price * Decimal(1 + self.config.natr_spread_multiplier * natr)

        buy_order = OrderCandidate(trading_pair=self.config.trading_pair, is_maker=True, order_type=OrderType.LIMIT,
                                   order_side=TradeType.BUY, amount=Decimal(self.config.order_amount), price=buy_price)

        sell_order = OrderCandidate(trading_pair=self.config.trading_pair, is_maker=True, order_type=OrderType.LIMIT,
                                    order_side=TradeType.SELL, amount=Decimal(self.config.order_amount), price=sell_price)

        return [buy_order, sell_order]

    def get_natr(self):
        candles_df = self.market_data_provider.get_candles_df(self.config.exchange, self.config.trading_pair, self.config.interval,
                                                              self.config.max_records)
        natr = ta.natr(high=candles_df["high"], low=candles_df["low"], close=candles_df["close"],
                       length=self.config.natr_period)
        return natr.iloc[-1] / 100

    def place_orders(self, proposal: List[OrderCandidate]) -> None:
        for order in proposal:
            self.place_order(connector_name=self.config.exchange, order=order)

    def place_order(self, connector_name: str, order: OrderCandidate):
        if order.order_side == TradeType.SELL:
            self.sell(connector_name=connector_name, trading_pair=order.trading_pair, amount=order.amount,
                      order_type=order.order_type, price=order.price)
        elif order.order_side == TradeType.BUY:
            self.buy(connector_name=connector_name, trading_pair=order.trading_pair, amount=order.amount,
                     order_type=order.order_type, price=order.price)

    def cancel_all_orders(self):
        for order in self.get_active_orders(connector_name=self.config.exchange):
            self.cancel(self.config.exchange, order.trading_pair, order.client_order_id)

    def did_fill_order(self, event: OrderFilledEvent):
        msg = (
            f"{event.trade_type.name} {round(event.amount, 2)} {event.trading_pair} {self.config.exchange} at {round(event.price, 2)}")
        self.log_with_clock(logging.INFO, msg)
        self.notify_hb_app_with_timestamp(msg)

    def format_status(self) -> str:
        """
        Returns status of the current strategy on user balances and current active orders. This function is called
        when status command is issued. Override this function to create custom status display output.
        """
        if not self.ready_to_trade:
            return "Market connectors are not ready."
        lines = []
        warning_lines = []
        warning_lines.extend(self.network_warning(self.get_market_trading_pair_tuples()))

        balance_df = self.get_balance_df()
        lines.extend(["", "  Balances:"] + ["    " + line for line in balance_df.to_string(index=False).split("\n")])
        market_status_df = self.get_market_status_df_with_depth()
        lines.extend(["", "  Market Status Data Frame:"] + ["    " + line for line in market_status_df.to_string(index=False).split("\n")])

        warning_lines.extend(self.balance_warning(self.get_market_trading_pair_tuples()))
        if len(warning_lines) > 0:
            lines.extend(["", "*** WARNINGS ***"] + warning_lines)
        return "\n".join(lines)

    def get_market_status_df_with_depth(self):
        market_status_df = self.market_status_data_frame(self.get_market_trading_pair_tuples())
        market_status_df["Exchange"] = market_status_df.apply(lambda x: self.get_exchange_status_name(x), axis=1)
        market_status_df["Volume (+1%)"] = market_status_df.apply(lambda x: self.get_volume_for_percentage_from_mid_price(x, 0.01), axis=1)
        market_status_df["Volume (-1%)"] = market_status_df.apply(lambda x: self.get_volume_for_percentage_from_mid_price(x, -0.01), axis=1)
        return market_status_df

    def get_exchange_status_name(self, row):
        if row["Exchange"] == "binance":
            exchange = row["Exchange"]
        else:
            exchange = row["Exchange"]
        return exchange
    def get_volume_for_percentage_from_mid_price(self, row, percentage):
        price = row["Mid Price"] * (1 + percentage)
        is_buy = percentage > 0
        result = self.connectors[row["Exchange"]].get_volume_for_price(row["Market"], is_buy, price)
        return result.result_volume