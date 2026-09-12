"""Склейка клиентов и сопоставление категорий — две операции, где данные Афиши грязные."""

from __future__ import annotations

import re
from dataclasses import dataclass


def normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def normalize_phone(value: str | None) -> str:
    """Только цифры; российские номера приводятся к виду 7XXXXXXXXXX."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    return digits


@dataclass(frozen=True)
class ContactKey:
    email: str
    phone: str

    def matches(self, other: "ContactKey") -> bool:
        """Совпадение по любому из полей — это один человек.

        Склейка автоматическая: пустое поле совпадением не считается.
        """
        return bool(
            (self.email and self.email == other.email)
            or (self.phone and self.phone == other.phone)
        )


def match_category(sector: str, aliases: dict[str, str]) -> str | None:
    """Сектор из CRM → наша категория по таблице алиасов.

    Алиасы заполняются руками. Незнакомый сектор не угадывается: вернём None,
    и он уйдёт в «Требует внимания» как новая категория.
    """
    needle = (sector or "").strip().lower()
    if not needle:
        return None
    for alias, category in aliases.items():
        if alias.strip().lower() in needle:
            return category
    return None
