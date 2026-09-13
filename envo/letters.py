"""Тексты писем клиентам. Тон и подпись — как в нынешних письмах, от Игоря из Envo."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from envo.names import MONTHS, tickets_phrase

SIGNATURE = "Игорь, Envo Live"
STYLE = ('font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.55;'
         'color:#1a1a1a;max-width:560px;')


@dataclass(frozen=True)
class Letter:
    subject: str
    text: str
    html: str


def _greeting(name: str | None) -> str:
    return f"{name}, салют!" if name else "Здравствуйте!"


def _html(text: str) -> str:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    inner = "\n".join(
        '<p style="margin:0 0 16px 0;">{}</p>'.format(
            re.sub(r"(https?://[^\s<]+)", r'<a href="\1">\1</a>', p).replace("\n", "<br>")
        )
        for p in paragraphs
    )
    return f'<div style="{STYLE}">\n{inner}\n</div>'


def _when(day: date | datetime | None) -> str:
    return f", {day.day} {MONTHS[day.month]}" if day else ""


def welcome(
    *,
    name: str | None,
    tickets: int,
    event: str,
    event_date: date | datetime | None,
    order_id: str,
    instructions: str,
    fresh: bool = True,
) -> Letter:
    """Вэлком после покупки: как получить билеты. Инструкция берётся из карточки события."""
    text = (
        f"{_greeting(name)}\n\n"
        "Меня зовут Игорь, я работаю в Службе поддержки клиентов Envo. "
        "Мы партнёр Яндекс Афиши по зарубежным событиям.\n\n"
        + ("Вы только что приобрели " if fresh else "Вы приобрели ")
        + f"{tickets_phrase(tickets)} на {event}{_when(event_date)}.\n\n"
        "Поздравляем, вы отлично проведёте время! А теперь о том, как получите билеты.\n\n"
        f"{instructions.strip()}\n\n"
        "Если появится что-то срочное, пишите в Telegram: @envo_help. "
        "Всё, что может подождать до вечера, лучше сюда, на почту, так ничего не потеряется.\n\n"
        f"Хорошего дня!\n{SIGNATURE}"
    )
    title = event[0].upper() + event[1:] if event else event
    return Letter(subject=f"{title}. Заказ: {order_id}", text=text, html=_html(text))


def cart(*, name: str | None, tickets: int, event: str, event_date: date | datetime | None) -> Letter:
    """Догон брошенной корзины: скидка 10% на любой тип билета."""
    text = (
        f"{_greeting(name)}\n\n"
        "Меня зовут Игорь, я работаю в Envo, партнёре Яндекс Афиши по зарубежным событиям.\n\n"
        f"Вижу, что вы начали оформлять {tickets_phrase(tickets)} на {event}{_when(event_date)}, "
        "но до конца дело не дошло.\n"
        "Бывает по-разному: неудобный момент, но иногда и просто дорого.\n\n"
        "Будем рады вас видеть среди гостей и дадим скидку 10% на покупку билета любого типа.\n"
        "Просто дайте знать, и я всё пришлю.\n\n"
        f"Хорошего дня!\n\n{SIGNATURE}"
    )
    title = event[0].upper() + event[1:] if event else event
    when = f", {event_date.strftime('%d.%m.%Y')}" if event_date else ""
    return Letter(subject=f"{title}{when}", text=text, html=_html(text))
