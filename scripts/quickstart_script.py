from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
class QuickstartScript(ScriptStrategyBase):
  # It is recommended to first use a paper trade exchange connector 
  # while coding your strategy, and then switch to a real one once you're happy with it.
  markets = {"gate_io_paper_trade": {"POPCAT-USDT"}}
  # Next, let's code the logic that will be executed every tick_size (default=1sec)
  def on_tick(self):
      price = self.connectors["gate_io_paper_trade"].get_mid_price("POPCAT-USDT")
      msg = f"POPCAT price: ${price}"
      self.logger().info(msg)
      self.notify_hb_app_with_timestamp(msg)