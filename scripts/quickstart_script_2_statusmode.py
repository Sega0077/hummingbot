import logging
from decimal import Decimal
from typing import List

import pandas as pd
import pandas_ta as ta  

from hummingbot.client.config.config_helpers import ClientConfigAdapter
from hummingbot.core.data_type.common import OrderType, PriceType, TradeType
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.core.event.events import OrderFilledEvent
from hummingbot.data_feed.candles_feed.candles_factory import CandlesFactory, CandlesConfig
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase


class QuickstartScript2(ScriptStrategyBase):
    bid_spread = 0.02
    ask_spread = 0.02
    order_refresh_time = 15
    order_amount = 40
    create_timestamp = 0
    trading_pair = "POPCAT-USDT"
    exchange = "gate_io_paper_trade"
    price_source = PriceType.MidPrice

    markets = {exchange: {trading_pair}}
    
    candles_config = CandlesConfig(connector="gate_io",
                                   trading_pair="POPCAT-USDT",
                                   interval="1m",
                                   max_records=500)

    eth_1m_candles = CandlesFactory.get_candle(candles_config)

    def __init__(self, config: ClientConfigAdapter):
        super().__init__(config)
        self.markets = {self.exchange: {self.trading_pair}}
        self.connectors = {self.exchange: self.market}
        self.eth_1m_candles.start()
        self.logger().info("Candles feed started.")
        self.price_ceiling = Decimal("0")
        self.price_floor = Decimal("0")

    def on_stop(self):
        self.eth_1m_candles.stop()
        self.logger().info("Candles feed stopped.")

    def on_tick(self):
        try:
            if self.create_timestamp <= self.current_timestamp:
                if hasattr(self.eth_1m_candles, 'is_ready') and self.eth_1m_candles.is_ready:
                    self.cancel_all_orders()
                    self.calculate_price_ceiling_floor()
                    proposal: List[OrderCandidate] = self.create_proposal()
                    proposal_filtered: List[OrderCandidate] = self.apply_price_ceiling_floor_filter(proposal)
                    proposal_adjusted: List[OrderCandidate] = self.adjust_proposal_to_budget(proposal_filtered)
                    self.place_orders(proposal_adjusted)
                    self.create_timestamp = self.order_refresh_time + self.current_timestamp
                    self.logger().info("Orders placed.")
                else:
                    self.logger().warning("SpotCandles is not ready. Skipping tick.")
        except Exception as e:
            self.logger().error(f"Unexpected error in on_tick: {str(e)}")

    def create_proposal(self) -> List[OrderCandidate]:
        try:
            ref_price = self.connectors[self.exchange].get_price_by_type(self.trading_pair, self.price_source)
            buy_price = ref_price * Decimal(1 - self.bid_spread)
            sell_price = ref_price * Decimal(1 + self.ask_spread)

            buy_order = OrderCandidate(trading_pair=self.trading_pair, is_maker=True, order_type=OrderType.LIMIT,
                                       order_side=TradeType.BUY, amount=Decimal(self.order_amount), price=buy_price)

            sell_order = OrderCandidate(trading_pair=self.trading_pair, is_maker=True, order_type=OrderType.LIMIT,
                                        order_side=TradeType.SELL, amount=Decimal(self.order_amount), price=sell_price)

            return [buy_order, sell_order]
        except Exception as e:
            self.logger().error(f"Error in create_proposal: {str(e)}")
            return []

    def adjust_proposal_to_budget(self, proposal: List[OrderCandidate]) -> List[OrderCandidate]:
        try:
            proposal_adjusted = self.connectors[self.exchange].budget_checker.adjust_candidates(proposal, all_or_none=True)
            return proposal_adjusted
        except Exception as e:
            self.logger().error(f"Error in adjust_proposal_to_budget: {str(e)}")
            return []

    def place_orders(self, proposal: List[OrderCandidate]) -> None:
        try:
            for order in proposal:
                self.place_order(connector_name=self.exchange, order=order)
        except Exception as e:
            self.logger().error(f"Error in place_orders: {str(e)}")

    def place_order(self, connector_name: str, order: OrderCandidate):
        try:
            if order.order_side == TradeType.SELL:
                self.sell(connector_name=connector_name, trading_pair=order.trading_pair, amount=order.amount,
                          order_type=order.order_type, price=order.price)
            elif order.order_side == TradeType.BUY:
                self.buy(connector_name=connector_name, trading_pair=order.trading_pair, amount=order.amount,
                         order_type=order.order_type, price=order.price)
        except Exception as e:
            self.logger().error(f"Error in place_order: {str(e)}")

    def cancel_all_orders(self):
        try:
            for order in self.get_active_orders(connector_name=self.exchange):
                self.cancel(self.exchange, order.trading_pair, order.client_order_id)
        except Exception as e:
            self.logger().error(f"Error in cancel_all_orders: {str(e)}")

    def did_fill_order(self, event: OrderFilledEvent):
            try:
                msg = (f"{event.trade_type.name} {round(event.amount, 2)} {event.trading_pair} {self.exchange} at {round(event.price, 2)}")
                self.log_with_clock(logging.INFO, msg)
                self.notify_hb_app_with_timestamp(msg)
            except Exception as e:
                self.logger().error(f"Error in did_fill_order: {str(e)}")

    def format_status(self) -> str:
        try:
            if not self.ready_to_trade:
                return "Market connectors are not ready."
            lines = []
            mid_price = self.connectors[self.exchange].get_price_by_type(self.trading_pair, PriceType.MidPrice)
            best_ask = self.connectors[self.exchange].get_price_by_type(self.trading_pair, PriceType.BestAsk)
            best_bid = self.connectors[self.exchange].get_price_by_type(self.trading_pair, PriceType.BestBid)
            last_trade_price = self.connectors[self.exchange].get_price_by_type(self.trading_pair, PriceType.LastTrade)
            custom_format_status = f"""
    | Mid price: {mid_price:.2f}| Last trade price: {last_trade_price:.2f}
    | Best ask: {best_ask:.2f} | Best bid: {best_bid:.2f} 
    | Price ceiling {self.price_ceiling:.2f} | Price floor {self.price_floor:.2f}
    """
            lines.extend([custom_format_status])
            if self.eth_1m_candles.is_ready:
                lines.extend([
                    "\n############################################ Market Data ############################################\n"])
                candles_df = self.eth_1m_candles.candles_df
                # Let's add some technical indicators
                candles_df.ta.bbands(length=100, std=2, append=True)
                candles_df["timestamp"] = pd.to_datetime(candles_df["timestamp"], unit="ms")
                lines.extend([f"Candles: {self.eth_1m_candles.name} | Interval: {self.eth_1m_candles.interval}\n"])
                lines.extend(["    " + line for line in candles_df.tail().to_string(index=False).split("\n")])
                lines.extend(["\n-----------------------------------------------------------------------------------------------------------\n"])
            else:
                lines.extend(["", "  No data collected."])
            return "\n".join(lines)
        except Exception as e:
            self.logger().error(f"Error in format_status: {str(e)}")
            return "Error in formatting status."

    def apply_price_ceiling_floor_filter(self, proposal):
        try:
            proposal_filtered = []
            for order in proposal:
                if order.order_side == TradeType.SELL and order.price > self.price_floor:
                    proposal_filtered.append(order)
                elif order.order_side == TradeType.BUY and order.price < self.price_ceiling:
                    proposal_filtered.append(order)
            return proposal_filtered
        except Exception as e:
            self.logger().error(f"Error in apply_price_ceiling_floor_filter: {str(e)}")
            return []

    def calculate_price_ceiling_floor(self):
        try:
            candles_df = self.eth_1m_candles.candles_df
            candles_df.ta.bbands(length=100, std=2, append=True)
            last_row = candles_df.iloc[-1]
            self.price_ceiling = last_row['BBU_100_2.0'].item()
            self.price_floor = last_row['BBL_100_2.0'].item()
        except Exception as e:
            self.logger().error(f"Error in calculate_price_ceiling_floor: {str(e)}")