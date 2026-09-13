"""Экономика сделки: каналы продаж, себестоимость, цены по волнам маржи.

Все доли считаются от цены, которую фактически платит покупатель (P), а не от закупки.
Порядок удержаний: площадка забирает свой процент с витрины, затем налоги с поступившего,
затем отправка за рубеж; остаток за вычетом закупки — прибыль.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from decimal import Decimal

# Доли по умолчанию. Меняются в настройках, не в коде.
PLATFORM_FEE = 0.06  # Яндекс Афиша удерживает с продажи
TAX = 0.12  # УСН 7% + НДС 5% с суммы после площадки
XBORDER_FEE = 0.009  # отправка денег за рубеж
VIRTUAL_DISCOUNT = 0.10  # витрина показывается со скидкой
REFUND_PREMIUM = (0.15, 0.20)  # надбавка за возвратный билет: дороже — меньше
WAVES = (0.30, 0.40, 0.50)  # волны маржи


@dataclass(frozen=True)
class Channel:
    """Канал продажи. Отличаются только сбором площадки."""

    code: str  # так канал хранится в базе
    label: str  # так показывается человеку
    platform_fee: float
    tax: float = TAX
    xborder_fee: float = XBORDER_FEE

    @property
    def net_share(self) -> float:
        """Какая доля цены остаётся до вычета закупки."""
        return (1 - self.platform_fee) * (1 - self.tax) - self.xborder_fee

    @property
    def tax_share(self) -> float:
        """Какая доля цены уходит в налоги."""
        return (1 - self.platform_fee) * self.tax


AFISHA = Channel("afisha", "Афиша", platform_fee=PLATFORM_FEE)
DIRECT = Channel("direct", "Прямой", platform_fee=0.0)
CHANNELS = {c.code: c for c in (AFISHA, DIRECT)}


def nice_up(value: float) -> int:
    """Округление вверх до «красивой» витринной цены: x990, ниже 10 000 — x90."""
    step = 1_000 if value >= 10_000 else 100
    return int(math.ceil((value + 10) / step) * step - 10)


def unit_cost_rub(
    price: float,
    *,
    seller_fee: float = 0.10,
    rate: float,
    rate_buffer: float = 1.0,
    actual_charged: float | None = None,
    qty: int = 1,
) -> float:
    """Себестоимость одного билета в рублях.

    `actual_charged` — сколько реально списал банк за весь платёж: если известно,
    считаем по нему, иначе по курсу ЦБ с буфером. Конвертация карты иначе врёт в прибыли.
    """
    if actual_charged is not None:
        if qty <= 0:
            raise ValueError("qty должно быть положительным")
        return actual_charged / qty
    return price * (1 + seller_fee) * (rate + rate_buffer)


def price_for_margin(cost: float, margin: float, channel: Channel = AFISHA) -> float:
    """Цена к оплате, при которой маржа составит заданную долю цены."""
    denominator = channel.net_share - margin
    if denominator <= 0:
        raise ValueError(
            f"маржа {margin:.0%} недостижима: до закупки остаётся {channel.net_share:.1%}"
        )
    return cost / denominator


def margin_at_price(price: float, cost: float, channel: Channel = AFISHA) -> float:
    """Обратная задача: какая маржа получается при уже выставленной цене."""
    if price <= 0:
        raise ValueError("цена должна быть положительной")
    return (price * channel.net_share - cost) / price


@dataclass(frozen=True)
class WavePrice:
    """Цены одной волны для одной категории."""

    margin: float
    cost: float
    payable: float  # столько платит покупатель за невозвратный
    listed: int  # столько показываем на витрине (со скидкой -10%)
    refundable: int  # возвратный билет
    channel: str

    @property
    def platform(self) -> float:
        return self.payable * CHANNELS[self.channel].platform_fee

    @property
    def taxes(self) -> float:
        return self.payable * CHANNELS[self.channel].tax_share

    @property
    def xborder(self) -> float:
        return self.payable * CHANNELS[self.channel].xborder_fee

    @property
    def profit(self) -> float:
        return self.payable * CHANNELS[self.channel].net_share - self.cost


def wave_price(
    cost: float,
    margin: float,
    *,
    channel: Channel = AFISHA,
    discount: float = VIRTUAL_DISCOUNT,
    refund_premium: float = REFUND_PREMIUM[0],
) -> WavePrice:
    """Одна волна: от себестоимости к витрине."""
    raw = price_for_margin(cost, margin, channel)
    listed = nice_up(raw / (1 - discount))
    payable = listed * (1 - discount)
    refundable = nice_up(payable * (1 + refund_premium))
    return WavePrice(
        margin=margin,
        cost=cost,
        payable=payable,
        listed=listed,
        refundable=refundable,
        channel=channel.code,
    )


def refund_premium_for(rank: int, total: int) -> float:
    """Надбавка за возвратный: 20% у самой дешёвой категории, 15% у самой дорогой."""
    lo, hi = REFUND_PREMIUM
    if total <= 1:
        return hi
    return hi - (hi - lo) * (rank / (total - 1))


def ladder(
    cost: float, *, channel: Channel = AFISHA, waves: tuple[float, ...] = WAVES, **kw
) -> list[WavePrice]:
    """Лестница цен по волнам маржи."""
    return [wave_price(cost, m, channel=channel, **kw) for m in waves]


@dataclass(frozen=True)
class EventPnL:
    """Деньги по событию. Все суммы в рублях."""

    invested: float  # закупка всего, включая нераспроданное
    revenue: float  # сколько заплатили покупатели
    cost_of_sold: float  # себестоимость проданных
    taxes: float
    platform: float
    written_off: float = 0.0  # остаток, списанный в убыток после события

    @property
    def profit(self) -> float:
        return self.revenue - self.taxes - self.platform - self.cost_of_sold - self.written_off

    @property
    def frozen(self) -> float:
        """Деньги, лежащие в нераспроданном остатке по себестоимости."""
        return self.invested - self.cost_of_sold - self.written_off


def event_pnl(
    sales: list[tuple[float, str]],
    *,
    invested: float,
    cost_of_sold: float,
    written_off: float = 0.0,
) -> EventPnL:
    """Считает P&L события из списка продаж `(цена, канал)`.

    Канал влияет только на сбор площадки, поэтому прямые продажи считаются наравне.
    """
    revenue = taxes = platform = 0.0
    for price, channel_code in sales:
        channel = CHANNELS[channel_code]
        revenue += price
        taxes += price * channel.tax_share
        platform += price * channel.platform_fee
    return EventPnL(
        invested=invested,
        revenue=revenue,
        cost_of_sold=cost_of_sold,
        taxes=taxes,
        platform=platform,
        written_off=written_off,
    )


def weighted_cost(lines: list[tuple[int, float]]) -> float:
    """Средневзвешенная себестоимость по строкам закупки `(количество, цена за штуку)`."""
    qty = sum(q for q, _ in lines)
    if qty == 0:
        return 0.0
    return sum(q * c for q, c in lines) / qty


def allocate_charge(total_charged: float, line_values: list[float]) -> list[float]:
    """Распределяет фактически списанную сумму по строкам покупки пропорционально стоимости.

    Копейки отдаются последней строке, чтобы сумма частей совпадала с платежом.
    """
    base = sum(line_values)
    if base <= 0:
        raise ValueError("покупка без стоимости строк")
    cents = [
        int(Decimal(str(total_charged * value / base)).quantize(Decimal("0.01")) * 100)
        for value in line_values
    ]
    target = int(Decimal(str(total_charged)).quantize(Decimal("0.01")) * 100)
    cents[-1] += target - sum(cents)
    return [c / 100 for c in cents]


def with_rate(price: WavePrice, cost: float) -> WavePrice:
    """Пересчёт волны под изменившуюся себестоимость, маржа сохраняется."""
    return replace(wave_price(cost, price.margin, channel=CHANNELS[price.channel]), cost=cost)
