import math
import logging
import datetime
import threading
from typing import List, Dict

from xtquant import xtdata, xtconstant
from xtquant.xttype import XtPosition, XtTrade, XtOrder, XtOrderError, XtOrderResponse

from tools.utils_basic import logging_init, get_code_exchange
from tools.utils_cache import load_json, daily_once, all_held_inc, new_held, del_held
from tools.utils_ding import sample_send_msg
from tools.utils_xtdata import check_today_is_open_day
from tools.xt_subscriber import sub_whole_quote
from tools.xt_delegate import XtDelegate, XtBaseCallback

strategy_name = '低开翻红样板策略'

my_client_path = r'C:\国金QMT交易端模拟\userdata_mini'
my_account_id = '55009728'

my_lock = threading.Lock()  # 创建互斥锁

path_held = './_cache/prod/held_days.json'  # 记录持仓日期
path_date = './_cache/prod/curr_date.json'  # 用来标记每天执行一次任务的缓存
path_logs = './_cache/prod/log.txt'         # 用来存储选股和委托操作

history_cache: Dict[str, List] = {}  # 记录选股历史，目的是为了去重

time_cache = {
    'prev_datetime': '',  # 限制每秒执行一次的缓存
    'prev_minutes': '',  # 限制每分钟屏幕打印心跳的缓存
}

target_stock_prefix = [
    '000', '001', '002', '003',
    '300', '301',
    '600', '601', '603', '605',
]


class p:
    hold_days = 0           # 持仓天数
    max_count = 10          # 持股数量上限
    amount_each = 10000     # 每个仓的资金上限
    stop_income = 1.05      # 换仓阈值
    upper_income = 1.168    # 止盈率
    upper_income_c = 1.368  # 止盈率:30开头创业板
    lower_income = 0.95     # 止损率
    low_open = 0.98         # 低开阈值
    turn_red_upper = 1.03   # 翻红阈值上限，防止买太高
    turn_red_lower = 1.02   # 翻红阈值下限


class MyCallback(XtBaseCallback):
    def on_stock_trade(self, trade: XtTrade):

        if trade.order_type == xtconstant.STOCK_BUY:
            log = f'买入成交 {trade.stock_code} {trade.traded_volume}股 均价:{trade.traded_price}'
            logging.warning(log)
            sample_send_msg(strategy_name + log, 0)
            new_held(my_lock, path_held, [trade.stock_code])

        if trade.order_type == xtconstant.STOCK_SELL:
            log = f'卖出成交 {trade.stock_code} {trade.traded_volume}股 均价:{trade.traded_price}'
            logging.warning(log)
            sample_send_msg(strategy_name + log, 0)
            del_held(my_lock, path_held, [trade.stock_code])

    def on_stock_order(self, order: XtOrder):
        log = f'委托回调 id:{order.order_id} code:{order.stock_code} remark:{order.order_remark}',
        logging.warning(log)

    def on_order_stock_async_response(self, res: XtOrderResponse):
        log = f'异步委托回调 id:{res.order_id} sysid:{res.error_msg} remark:{res.order_remark}',
        logging.warning(log)

    def on_order_error(self, order_error: XtOrderError):
        log = f'委托报错 id:{order_error.order_id} error_id:{order_error.error_id} error_msg:{order_error.error_msg}'
        logging.warning(log)


def held_increase():
    print(f'All held stock day +1!')
    all_held_inc(my_lock, path_held)


def order_submit(order_type: int, code: str, order_price: float, order_volume: int, order_remark: str):
    price_type = xtconstant.LATEST_PRICE
    if get_code_exchange(code) == 'SZ':
        price_type = xtconstant.MARKET_SZ_CONVERT_5_CANCEL
    if get_code_exchange(code) == 'SH':
        price_type = xtconstant.MARKET_PEER_PRICE_FIRST

    xt_delegate.order_submit(
        stock_code=code,
        order_type=order_type,
        order_volume=order_volume,
        price_type=price_type,
        price=order_price,  # 最优五档依然会按照市价下单
        strategy_name=strategy_name,
        order_remark=order_remark,
    )


def scan_sell(quotes: dict, positions: List[XtPosition]) -> None:
    # 卖出逻辑
    held_days = load_json(path_held)

    for position in positions:
        code = position.stock_code
        if (code in quotes) and (code in held_days):
            # 如果有数据且有持仓时间记录
            quote = quotes[code]
            curr_price = quote['lastPrice']
            cost_price = position.open_price
            sell_volume = position.volume

            if held_days[code] > p.hold_days:
                # 判断持仓超过限制时间
                if cost_price * p.lower_income < curr_price < cost_price * p.stop_income:
                    # 不满足盈利的持仓平仓
                    order_submit(xtconstant.STOCK_SELL, code, curr_price + 0.01, sell_volume, '换仓卖单')
                    logging.warning(f'换仓委托 {code} {sell_volume}股 现价:{curr_price}')

            if held_days[code] > 0:
                # 判断持仓超过一天
                if curr_price <= cost_price * p.lower_income:
                    # 止损卖出
                    order_submit(xtconstant.STOCK_SELL, code, curr_price - 0.01, sell_volume, '止损卖单')
                    logging.warning(f'止损委托 {code} {sell_volume}股 现价:{curr_price}')
                elif curr_price >= cost_price * p.upper_income_c and code[:2] == '30':
                    # 止盈卖出：创业板
                    order_submit(xtconstant.STOCK_SELL, code, curr_price - 0.01, sell_volume, '止盈卖单')
                    logging.warning(f'止盈委托 {code} {sell_volume}股 现价:{curr_price}')
                elif curr_price >= cost_price * p.upper_income:
                    # 止盈卖出：主板
                    order_submit(xtconstant.STOCK_SELL, code, curr_price - 0.01, sell_volume, '止盈卖单')
                    logging.warning(f'止盈委托 {code} {sell_volume}股 现价:{curr_price}')


def scan_buy(quotes: dict, positions: List[XtPosition], curr_date: str) -> None:
    position_codes = [position.stock_code for position in positions]

    # 扫描全市场选股
    selections = []
    for code in quotes:
        if code[:3] not in target_stock_prefix:
            continue

        last_close = quotes[code]['lastClose']
        curr_open = quotes[code]['open']
        curr_price = quotes[code]['lastPrice']

        # 筛选符合买入条件的股票
        if ((curr_open < last_close * p.low_open)
                and (last_close * p.turn_red_upper > curr_price)
                and (last_close * p.turn_red_lower < curr_price)):
            if code not in position_codes:  # 如果目前没有持仓则记录
                selections.append({'code': code, 'price': curr_price})

    if len(selections) > 0:  # 选出一个以上的股票
        selections = sorted(selections, key=lambda x: x['price'])  # 选出的股票按照现价从小到大排序

        asset = xt_delegate.check_asset()

        buy_count = max(0, p.max_count - len(position_codes))       # 确认剩余的仓位
        buy_count = min(buy_count, asset.cash / p.amount_each)      # 确认现金够用
        buy_count = min(buy_count, len(selections))                 # 确认选出的股票够用
        buy_count = min(buy_count, 1)                               # 限每次最多买入数量

        # 依次买入
        for i in range(buy_count):
            code = selections[i]['code']
            price = selections[i]['price']
            buy_volume = math.floor(p.amount_each / price / 100) * 100

            # 如果有可用的买点则买入
            if buy_volume > 0:
                order_submit(xtconstant.STOCK_BUY, code, price + 0.01, buy_volume, '选股买单')
                logging.warning(f'买入委托 {code} {buy_volume}股 现价:{price}')

        # 记录选股历史
        if curr_date not in history_cache.keys():
            history_cache[curr_date] = []

        for selection in selections:
            if selection['code'] not in history_cache[curr_date]:
                history_cache[curr_date].append(selection['code'])
                logging.warning(f'记录选股历史 code: {selection["code"]} 现价: {selection["price"]}')


def callback_sub_whole(quotes: dict) -> None:
    now = datetime.datetime.now()

    # 限制执行频率，每秒至多一次
    curr_datetime = now.strftime("%Y%m%d %H:%M:%S")
    if time_cache['prev_datetime'] != curr_datetime:
        time_cache['prev_datetime'] = curr_datetime
    else:
        return

    # 屏幕输出 HeartBeat 每分钟一个点
    curr_time = now.strftime('%H:%M')
    if time_cache['prev_minutes'] != curr_time:
        time_cache['prev_minutes'] = curr_time
        if curr_time[-1:] == '0':
            print('\n' + curr_time, end='')
        print('.', end='')

    # 只有在交易日才执行
    if not check_today_is_open_day(now):
        return

    # 盘前
    if '09:15' <= curr_time <= '09:29':
        curr_date = now.strftime('%Y%m%d')
        daily_once(my_lock, time_cache, path_date, '_daily_once_held_inc', curr_date, held_increase)

    # 早盘
    elif '09:30' <= curr_time <= '11:30':
        positions = xt_delegate.check_positions()
        scan_sell(quotes, positions)

        curr_date = now.strftime('%Y%m%d')
        scan_buy(quotes, positions, curr_date)

    # 午盘
    elif '13:00' <= curr_time <= '14:56':
        positions = xt_delegate.check_positions()
        scan_sell(quotes, positions)


if __name__ == '__main__':
    logging_init(path=path_logs, level=logging.INFO)

    xt_delegate = XtDelegate(
        account_id=my_account_id,
        client_path=my_client_path,
        xt_callback=MyCallback(),
    )

    sub_whole_quote(callback_sub_whole)
    xtdata.run()  # 死循环 阻塞主线程退出
