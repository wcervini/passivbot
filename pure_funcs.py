import sys
import numpy as np


if '--nojit' in sys.argv:
    print('not using numba')
    def njit(pyfunc=None, **kwargs):
        def wrap(func):
            return func

        if pyfunc is not None:
            return wrap(pyfunc)
        else:
            return wrap
else:
    print('using numba')
    from numba import njit


def flatten_dict(d, parent_key='', sep='_'):
    items = []
    for k, v in d.items():
        new_key = parent_key + sep + k if parent_key else k
        if type(v) == dict:
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


@njit
def round_dynamic(n: float, d: int):
    if n == 0.0:
        return n
    return round(n, d - int(np.floor(np.log10(abs(n)))) - 1)


def format_float(num):
    return np.format_float_positional(num, trim='-')


def compress_float(n: float, d: int) -> str:
    if n / 10**d >= 1:
        n = round(n)
    else:
        n = round_dynamic(n, d)
    nstr = format_float(n)
    if nstr.startswith('0.'):
        nstr = nstr[1:]
    elif nstr.startswith('-0.'):
        nstr = '-' + nstr[2:]
    elif nstr.endswith('.0'):
        nstr = nstr[:-2]
    return nstr


@njit
def round_up(n, step, safety_rounding=10) -> float:
    return np.round(np.ceil(np.round(n / step, safety_rounding)) * step, safety_rounding)


@njit
def round_dn(n, step, safety_rounding=10) -> float:
    return np.round(np.floor(np.round(n / step, safety_rounding)) * step, safety_rounding)


@njit
def round_(n, step, safety_rounding=10) -> float:
    return np.round(np.round(n / step) * step, safety_rounding)


@njit
def calc_diff(x, y):
    return abs(x - y) / abs(y)


@njit
def nan_to_0(x) -> float:
    return x if x == x else 0.0


@njit
def calc_min_entry_qty(price, inverse, qty_step, min_qty, min_cost) -> float:
    return min_qty if inverse else max(min_qty, round_up(min_cost / price if price > 0.0 else 0.0, qty_step))


@njit
def calc_max_entry_qty(entry_price, available_margin, inverse, qty_step, c_mult):
    return round_dn(cost_to_qty(available_margin, entry_price, inverse, c_mult), qty_step)


@njit
def cost_to_qty(cost, price, inverse, c_mult):
    return cost * price / c_mult if inverse else (cost / price if price > 0.0 else 0.0)


@njit
def qty_to_cost(qty, price, inverse, c_mult) -> float:
    return (abs(qty / price) if price > 0.0 else 0.0) * c_mult if inverse else abs(qty * price)


@njit
def calc_ema(alpha, alpha_, prev_ema, new_val) -> float:
    return prev_ema * alpha_ + new_val * alpha


@njit
def calc_emas(xs: [float], span: int) -> np.ndarray:
    alpha = 2 / (span + 1)
    alpha_ = 1 - alpha
    emas = np.empty_like(xs)
    emas[0] = xs[0]
    for i in range(1, len(xs)):
        emas[i] = emas[i - 1] * alpha_ + xs[i] * alpha
    return emas


@njit
def iter_MA_ratios_chunks(xs: [float], spans: [int], chunk_size: int = 65536):

    def to_ratios(emass_):
        ratios = np.empty((emass_.shape[0], emass_.shape[1] - 1))
        for i in range(1, emass_.shape[1]):
            ratios[:,i - 1] = emass_[:,i - 1] / emass_[:,i]
        return ratios

    max_spans = max(spans)
    if len(xs) < max_spans:
        return

    chunk_size = max(chunk_size, max_spans)

    n_chunks = int(round_up(len(xs) / chunk_size, 1.0))

    alphas = 2 / (spans + 1)
    alphas_ = 1 - alphas

    emass = np.empty((chunk_size, len(spans)), dtype=np.float64)
    emass[0] = xs[0]
    for i in range(1, chunk_size):
        emass[i] = emass[i - 1] * alphas_ + xs[i] * alphas
    yield to_ratios(emass), 0

    for k in range(1, n_chunks):
        kc = chunk_size * k
        new_emass = np.empty((chunk_size, len(spans)), dtype=np.float64)
        new_emass[0] = emass[-1] * alphas_ + xs[kc] * alphas
        for i in range(1, chunk_size):
            new_emass[i] = new_emass[i - 1] * alphas_ + xs[kc + i] * alphas
        yield to_ratios(new_emass), k
        emass = new_emass
    return emass


@njit
def calc_long_pnl(entry_price, close_price, qty, inverse, c_mult) -> float:
    if inverse:
        if entry_price == 0.0 or close_price == 0.0:
            return 0.0
        return abs(qty) * c_mult * (1 / entry_price - 1 / close_price)
    else:
        return abs(qty) * (close_price - entry_price)


@njit
def calc_shrt_pnl(entry_price, close_price, qty, inverse, c_mult) -> float:
    if inverse:
        if entry_price == 0.0 or close_price == 0.0:
            return 0.0
        return abs(qty) * c_mult * (1 / close_price - 1 / entry_price)
    else:
        return abs(qty) * (entry_price - close_price)


@njit
def calc_available_margin(balance,
                          long_psize,
                          long_pprice,
                          shrt_psize,
                          shrt_pprice,
                          last_price,
                          inverse, c_mult, leverage) -> float:
    used_margin = 0.0
    equity = balance
    if long_pprice and long_psize:
        long_psize_real = long_psize * c_mult
        equity += calc_long_pnl(long_pprice, last_price, long_psize_real, inverse, c_mult)
        used_margin += qty_to_cost(long_psize_real, long_pprice, inverse, c_mult) / leverage
    if shrt_pprice and shrt_psize:
        shrt_psize_real = shrt_psize * c_mult
        equity += calc_shrt_pnl(shrt_pprice, last_price, shrt_psize_real, inverse, c_mult)
        used_margin += qty_to_cost(shrt_psize_real, shrt_pprice, inverse, c_mult) / leverage
    return max(0.0, equity - used_margin)


@njit
def calc_new_psize_pprice(psize, pprice, qty, price, qty_step) -> (float, float):
    if qty == 0.0:
        return psize, pprice
    new_psize = round_(psize + qty, qty_step)
    if new_psize == 0.0:
        return 0.0, 0.0
    return new_psize, nan_to_0(pprice) * (psize / new_psize) + price * (qty / new_psize)


@njit
def eqf(vals: np.ndarray, coeffs: np.ndarray) -> float:
    return np.sum(vals ** 2 * coeffs[:, 0] + vals * coeffs[:, 1])


@njit
def calc_ientry_qty(balance, entry_price, MA_ratios, iqty_const, iqty_MAr_coeffs, qty_step, min_entry_qty,
                    max_entry_qty, inverse, c_mult):
    qty = round_dn(min(max_entry_qty, max(min_entry_qty, (cost_to_qty(balance, entry_price, inverse, c_mult) *
                                                          (iqty_const + eqf(MA_ratios, iqty_MAr_coeffs))))), qty_step)
    return qty if qty >= min_entry_qty else 0.0


def calc_rentry_qty(psize, entry_price, MA_ratios, rqty_const, rqty_MAr_coeffs, qty_step, min_entry_qty, max_entry_qty):
    qty = round_dn(min(max_entry_qty, max(min_entry_qty, psize * (rqty_const + eqf(MA_ratios, rqty_MAr_coeffs)))),
                   qty_step)
    return qty if qty >= min_entry_qty else 0.0


@njit
def calc_ientry_price(MA, MA_ratios, iprc_const, iprc_MAr_coeffs):
    # returns unrounded price
    return MA * (iprc_const + eqf(MA_ratios, iprc_MAr_coeffs))


@njit
def calc_rentry_price(balance, psize, pprice, MA_ratios, rprc_const, rprc_PBr_coeffs, rprc_MAr_coeffs, inverse, c_mult):
    # returns unrounded price
    pcost_bal_ratio = qty_to_cost(psize, pprice, inverse, c_mult) / balance
    return pprice * (rprc_const + eqf(MA_ratios, rprc_MAr_coeffs) + eqf([pcost_bal_ratio**2, pcost_bal_ratio],
                                                                        rprc_PBr_coeffs))


@njit
def iter_orders(balance,
                long_psize,
                long_pprice,
                shrt_psize,
                shrt_pprice,
                highest_bid,
                lowest_ask,
                MA,
                last_price,
                MA_ratios,
        
                inverse,
                do_long,
                do_shrt,
                qty_step,
                price_step,
                min_qty,
                min_cost,
                c_mult,
                leverage,
                hedge_liq_diff_thr,
                hedge_psize_pct,
                stop_liq_diff_thr,
                stop_psize_pct,
                entry_liq_diff_thr,
                iqty_const,
                iprc_const,
                rqty_const,
                rprc_const,
                markup_const,
                iqty_MAr_coeffs,
                rprc_PBr_coeffs,
                iprc_MAr_coeffs,
                rqty_MAr_coeffs,
                rprc_MAr_coeffs,
                markup_MAr_coeffs):
    '''

    :param balance:
    :param long_psize:
    :param long_pprice:
    :param shrt_psize:
    :param shrt_pprice:
    :param highest_bid:
    :param lowest_ask:
    :param MA:
    :param last_price:
    :param MA_ratios:
    :param inverse:
    :param do_long:
    :param do_shrt:
    :param qty_step:
    :param price_step:
    :param min_qty:
    :param min_cost:
    :param c_mult:
    :param leverage:
    :param hedge_liq_diff_thr:
    :param hedge_psize_pct:
    :param stop_liq_diff_thr:
    :param stop_psize_pct:
    :param entry_liq_diff_thr:
    :param iqty_const:
    :param iprc_const:
    :param rqty_const:
    :param rprc_const:
    :param markup_const:
    :param iqty_MAr_coeffs:
    :param rprc_PBr_coeffs:
    :param iprc_MAr_coeffs:
    :param rqty_MAr_coeffs:
    :param rprc_MAr_coeffs:
    :param markup_MAr_coeffs:
    :return: generator object which yields tuple (float, float, float, float, str)

    '''
    bankruptcy_price = calc_bankruptcy_price(balance, long_psize, long_pprice, shrt_psize, shrt_pprice, inverse, c_mult)

    ### stop order ###
    if calc_diff(bankruptcy_price, last_price) < stop_liq_diff_thr:
        abs_shrt_psize = abs(shrt_psize)
        if long_psize > abs_shrt_psize:
            stop_qty = min(long_psize, max(min_qty, round_dn(long_psize * stop_psize_pct, qty_step)))
            if stop_qty > min_qty:
                long_psize = max(0.0, round_(long_psize - stop_qty, qty_step))
                yield -stop_qty, lowest_ask, long_psize, long_pprice, 'long_sclose'
        else:
            stop_qty = min(abs_shrt_psize, max(min_qty, round_dn(abs_shrt_psize * stop_psize_pct, qty_step)))
            if stop_qty > min_qty:
                shrt_psize = min(0.0, round_(shrt_psize + stop_qty, qty_step))
                yield stop_qty, highest_bid, shrt_psize, long_pprice, 'shrt_sclose'

    if long_psize > 0.0:
        ### long normal close ###
        yield (-long_psize,
               max(lowest_ask, round_up(long_pprice * (markup_const[0] + eqf(MA_ratios, markup_MAr_coeffs[0])))),
               0.0, 0.0, 'long_nclose')

    if shrt_psize < 0.0:
        ### shrt normal close ###
        yield (-shrt_psize,
               min(highest_bid, round_dn(shrt_pprice * (markup_const[1] + eqf(MA_ratios, markup_MAr_coeffs[1])))),
               0.0, 0.0, 'shrt_nclose')

    while True:
        available_margin = calc_available_margin(balance, long_psize, long_pprice, shrt_psize, shrt_pprice, last_price,
                                                 inverse, c_mult, leverage)
        orders = []
        if do_long:
            if long_psize == 0.0:
                ### initial long entry ###
                long_entry_price = min(highest_bid, round_dn(calc_ientry_price(MA[0], MA_ratios, iprc_const[0],
                                                                               iprc_MAr_coeffs[0])))
                if long_entry_price > 0.0:
                    min_entry_qty = calc_min_entry_qty(long_entry_price, inverse, qty_step, min_qty, min_cost)
                    max_entry_qty = calc_max_entry_qty(long_entry_price, available_margin, inverse, qty_step, c_mult)
                    long_entry_qty = calc_ientry_qty(balance, long_entry_price, MA_ratios, iqty_const[0],
                                                     iqty_MAr_coeffs[0], qty_step, min_entry_qty, max_entry_qty,
                                                     inverse, c_mult)
                    if long_entry_qty > 0.0:
                        new_bankruptcy_price = calc_bankruptcy_price(balance, long_entry_qty, long_entry_price,
                                                                     shrt_psize, shrt_pprice, inverse, c_mult)
                        if calc_diff(new_bankruptcy_price, last_price) > entry_liq_diff_thr:
                            orders.append((long_entry_qty, long_entry_price, long_psize, long_pprice, 'long_ientry'))
            else:
                ### long reentry ###
                long_entry_price = min(highest_bid,
                                       round_dn(calc_rentry_price(balance, long_psize, long_pprice, MA_ratios,
                                                                  rprc_const[0], rprc_PBr_coeffs[0], rprc_MAr_coeffs[0],
                                                                  inverse, c_mult), price_step))
                if long_entry_price > 0.0:
                    min_entry_qty = calc_min_entry_qty(long_entry_price, inverse, qty_step, min_qty, min_cost)
                    max_entry_qty = calc_max_entry_qty(long_entry_price, available_margin, inverse, qty_step, c_mult)
                    long_entry_qty = calc_rentry_qty(long_psize, long_entry_price, MA_ratios, rqty_const[0],
                                                     rqty_MAr_coeffs[0], qty_step, min_entry_qty, max_entry_qty)
                    if long_entry_qty > 0.0:
                        new_long_psize, new_long_pprice = calc_new_psize_pprice(long_psize, long_pprice, long_entry_qty,
                                                                                long_entry_price, qty_step)
                        new_bankruptcy_price = calc_bankruptcy_price(balance, new_long_psize, new_long_pprice,
                                                                     shrt_psize, shrt_pprice, inverse, c_mult)
                        if calc_diff(new_bankruptcy_price, last_price) > entry_liq_diff_thr:
                            orders.append((long_entry_qty, long_entry_price,
                                           new_long_psize, new_long_pprice, 'long_rentry'))
        if do_shrt:
            if shrt_psize == 0.0:
                ### initial shrt entry ###
                shrt_entry_price = max(lowest_ask, round_up(calc_ientry_price(MA[1], MA_ratios, iprc_const[1],
                                                                              iprc_MAr_coeffs[1])))
                if shrt_entry_price > 0.0:
                    min_entry_qty = calc_min_entry_qty(shrt_entry_price, inverse, qty_step, min_qty, min_cost)
                    max_entry_qty = calc_max_entry_qty(shrt_entry_price, available_margin, inverse, qty_step, c_mult)
                    shrt_entry_qty = -calc_ientry_qty(balance, shrt_entry_price, MA_ratios, iqty_const[1],
                                                      iqty_MAr_coeffs[1], qty_step, min_entry_qty, max_entry_qty,
                                                      inverse, c_mult)
                    if shrt_entry_qty < 0.0:
                        new_bankruptcy_price = calc_bankruptcy_price(balance, shrt_entry_qty, shrt_entry_price,
                                                                     shrt_psize, shrt_pprice, inverse, c_mult)
                        if calc_diff(new_bankruptcy_price, last_price) > entry_liq_diff_thr:
                            orders.append((shrt_entry_qty, shrt_entry_price, shrt_psize, shrt_pprice, 'shrt_ientry'))
            else:
                ### shrt reentry ###
                shrt_entry_price = max(lowest_ask,
                                       round_up(calc_rentry_price(balance, shrt_psize, shrt_pprice, MA_ratios,
                                                                  rprc_const[1], rprc_PBr_coeffs[1], rprc_MAr_coeffs[1],
                                                                  inverse, c_mult), price_step))
                if shrt_entry_price > 0.0:
                    min_entry_qty = calc_min_entry_qty(shrt_entry_price, inverse, qty_step, min_qty, min_cost)
                    max_entry_qty = calc_max_entry_qty(shrt_entry_price, available_margin, inverse, qty_step, c_mult)
                    shrt_entry_qty = calc_rentry_qty(shrt_psize, shrt_entry_price, MA_ratios, rqty_const[1],
                                                     rqty_MAr_coeffs[1], qty_step, min_entry_qty, max_entry_qty)
                    if shrt_entry_qty > 0.0:
                        new_shrt_psize, new_shrt_pprice = calc_new_psize_pprice(shrt_psize, shrt_pprice, shrt_entry_qty,
                                                                                shrt_entry_price, qty_step)
                        new_bankruptcy_price = calc_bankruptcy_price(balance, new_shrt_psize, new_shrt_pprice,
                                                                     shrt_psize, shrt_pprice, inverse, c_mult)
                        if calc_diff(new_bankruptcy_price, last_price) > entry_liq_diff_thr:
                            orders.append((shrt_entry_qty, shrt_entry_price, new_shrt_psize,
                                           new_shrt_pprice, 'shrt_rentry'))

        ### hedge order ###
        if calc_diff(bankruptcy_price, last_price) < hedge_liq_diff_thr:
            if long_psize > abs(shrt_psize):
                if do_shrt:
                    min_entry_qty = calc_min_entry_qty(lowest_ask, inverse, qty_step, min_qty, min_cost)
                    max_entry_qty = calc_max_entry_qty(lowest_ask, available_margin, inverse, qty_step, c_mult)
                    hedge_qty = max(min_entry_qty, min(max_entry_qty, round_dn(long_psize * hedge_psize_pct, qty_step)))
                    if hedge_qty >= min_entry_qty:
                        hedge_qty = -hedge_qty
                        new_shrt_psize, new_shrt_pprice = calc_new_psize_pprice(shrt_psize, shrt_pprice, hedge_qty,
                                                                                lowest_ask, qty_step)
                        orders.append((hedge_qty, lowest_ask, new_shrt_psize, new_shrt_pprice, 'shrt_hentry'))
            else:
                if do_long:
                    min_entry_qty = calc_min_entry_qty(highest_bid, inverse, qty_step, min_qty, min_cost)
                    max_entry_qty = calc_max_entry_qty(highest_bid, available_margin, inverse, qty_step, c_mult)
                    hedge_qty = max(min_entry_qty, min(max_entry_qty, round_dn(long_psize * hedge_psize_pct, qty_step)))
                    if hedge_qty >= min_entry_qty:
                        new_long_psize, new_long_pprice = calc_new_psize_pprice(long_psize, long_pprice,
                                                                                hedge_qty, highest_bid, qty_step)
                        orders.append((hedge_qty, highest_bid, new_long_psize, new_long_pprice, 'long_hentry'))

        orders = sorted(orders, key=lambda x: calc_diff(x[1], last_price))
        if orders[0][0] == 0.0:
            break
        yield orders[0]
        if 'entry' in orders[0][4]:
            if 'long' in orders[0][4]:
                long_psize, long_pprice = orders[0][2:4]
            else:
                shrt_psize, shrt_pprice = orders[0][2:4]
        bankruptcy_price = calc_bankruptcy_price(balance, long_psize, long_pprice,
                                                 shrt_psize, shrt_pprice, inverse, c_mult)


@njit
def calc_bankruptcy_price(balance,
                          long_psize,
                          long_pprice,
                          shrt_psize,
                          shrt_pprice,
                          inverse, c_mult):
    long_pprice = nan_to_0(long_pprice)
    shrt_pprice = nan_to_0(shrt_pprice)
    long_psize *= c_mult
    abs_shrt_psize = abs(shrt_psize) * c_mult
    if inverse:
        shrt_cost = abs_shrt_psize / shrt_pprice if shrt_pprice > 0.0 else 0.0
        long_cost = long_psize / long_pprice if long_pprice > 0.0 else 0.0
        denominator = (shrt_cost - long_cost - balance)
        if denominator == 0.0:
            return 0.0
        bankruptcy_price = (abs_shrt_psize - long_psize) / denominator
    else:
        denominator = long_psize - abs_shrt_psize
        if denominator == 0.0:
            return 0.0
        bankruptcy_price = (-balance + long_psize * long_pprice - abs_shrt_psize * shrt_pprice) / denominator
    return max(0.0, bankruptcy_price)


def calc_spans(min_span: int, max_span: int, n_spans) -> [int]:
    return [1] + [int(round(min_span * ((max_span / min_span)**(1 / (n_spans - 1))) ** i)) for i in range(0, n_spans)]


def get_starting_coeffs(n_spans: int):
    return np.array([[(x := 1 / (n_spans * 2)), x]] * n_spans)


def fill_template_config(c, r=False):
    for side in ['long', 'shrt']:
        for k in c[side]:
            if 'MAr' in k:
                c[side][k] = get_starting_coeffs(c['n_spans'])
                if r:
                    c[side][k] += np.random.random(c[side][k].shape) * 0.1 - 0.05
            elif 'PBr' in k:
                c[side][k] = get_starting_coeffs(1)
                if r:
                    c[side][k] += np.random.random(c[side][k].shape) * 0.1 - 0.05
    return c


def unpack_config(d):
    new = {}
    for k, v in flatten_dict(d, sep='§').items():
        try:
            assert type(v) != str
            for _ in v:
                break
            for i in range(len(v)):
                new[f'{k}${str(i).zfill(3)}'] = v[i]
        except:
            new[k] = v
    if new == d:
        return new
    return unpack_config(new)


def pack_config(d):
    result = {}
    while any('$' in k for k in d):
        new = {}
        for k, v in d.items():
            if '$' in k:
                ks = k.split('$')
                k0 = '$'.join(ks[:-1])
                if k0 in new:
                    new[k0].append(v)
                else:
                    new[k0] = [v]
            else:
                new[k] = v
        d = new
    new = {}
    for k, v in d.items():
        if type(v) == list:
            new[k] = np.array(v)
        else:
            new[k] = v
    d = new
                
    new = {}
    for k, v in d.items():
        if '§' in k:
            k0, k1 = k.split('§')
            if k0 in new:
                new[k0][k1] = v
            else:
                new[k0] = {k1: v}
        else:
            new[k] = v
    return new


def get_template_live_config():
    return {
        "config_name": "name",
        "logging_level": 0,
        "min_span": 6000,
        "max_span": 300000,
        "n_spans": 3,
        "long": {
            "enabled":            True,
            "leverage":           10,     # borrow cat
            "hedge_liq_diff_thr": 0.5,    # make counter order if diff(liq, last) < thr
            "hedge_psize_pct":    0.05,   # % of psize for hedge order
            "stop_liq_diff_thr":  0.21,   # partially close pos at a loss if diff(liq, last) < thr
            "stop_psize_pct":     0.05,   # % of psize for stop loss order
            "entry_liq_diff_thr": 0.21,   # prevent entries whose filling would result in diff(new_liq, last) < thr
            "iqty_const":         0.05,   # initial entry qty pct
            "iprc_const":         0.991,  # initial entry price ema_spread
            "rqty_const":         0.5,    # reentry qty ddown faxtor
            "rprc_const":         0.99,   # reentry price grid spacing
            "markup_const":       1.004,  # markup
                                          # coeffs: [[quadratic_coeff, linear_coeff]] * n_spans
                                          # e.g. n_spans = 3,
                                          # coeffs = [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]]
                                          # all coeff ranges [min, max] = [-10.0, 10.0]
            "iqty_MAr_coeffs":    [],     # initial qty pct Moving Average ratio coeffs    formerly qty_pct
            "iprc_MAr_coeffs":    [],     # initial price pct Moving Average ratio coeffs  formerly ema_spread
            "rqty_MAr_coeffs":    [],     # reentry qty pct Moving Average ratio coeffs    formerly ddown_factor
            "rprc_MAr_coeffs":    [],     # reentry price pct Moving Average ratio coeffs  formerly grid_spacing
            "rprc_PBr_coeffs":    [],     # reentry Position cost to Balance ratio coeffs (PBr**2, PBr)
                                          # formerly pos_margin_grid_coeff
            "markup_MAr_coeffs":  [],     # markup price pct Moving Average ratio coeffs
            "MA_idx":             2       # index of ema span from which to calc initial entry prices
        },
        "shrt": {
            "enabled":            True,
            "leverage":           10,     # borrow cat
            "hedge_liq_diff_thr": 0.5,    # make counter order if diff(liq, last) < thr
            "hedge_psize_pct":    0.05,   # % of psize for hedge order
            "stop_liq_diff_thr":  0.21,   # partially close pos at a loss if diff(liq, last) < thr
            "stop_psize_pct":     0.05,   # % of psize for stop loss order
            "entry_liq_diff_thr": 0.21,   # prevent entries whose filling would result in diff(new_liq, last) < thr
            "iqty_const":         0.05,   # initial entry qty pct
            "iprc_const":         1.009,  # initial entry price ema_spread
            "rqty_const":         0.5,    # reentry qty ddown faxtor
            "rprc_const":         1.01,   # reentry price grid spacing
            "markup_const":       0.996,  # markup
                                          # coeffs: [[quadratic_coeff, linear_coeff]] * n_spans
                                          # e.g. n_spans = 3,
                                          # coeffs = [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]]
                                          # all coeff ranges [min, max] = [-10.0, 10.0]
            "iqty_MAr_coeffs":    [],     # initial qty pct Moving Average ratio coeffs    formerly qty_pct
            "iprc_MAr_coeffs":    [],     # initial price pct Moving Average ratio coeffs  formerly ema_spread
            "rqty_MAr_coeffs":    [],     # reentry qty pct Moving Average ratio coeffs    formerly ddown_factor
            "rprc_MAr_coeffs":    [],     # reentry price pct Moving Average ratio coeffs  formerly grid_spacing
            "rprc_PBr_coeffs":    [],     # reentry Position cost to Balance ratio coeffs (PBr**2, PBr)
                                          # formerly pos_margin_grid_coeff
            "markup_MAr_coeffs":  [],     # markup price pct Moving Average ratio coeffs
            "MA_idx":             2       # index of ema span from which to calc initial entry prices
        }
    }














