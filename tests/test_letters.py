from datetime import date

from envo import letters


def test_welcome_reads_like_the_old_one():
    letter = letters.welcome(name="Мария", tickets=2, event="UFC 333", event_date=date(2026, 10, 24),
                             order_id="4419077", instructions="Билеты придут за 5 дней.")
    assert letter.subject == "UFC 333. Заказ: 4419077"
    assert letter.text.startswith("Мария, салют!")
    assert "2 билета на UFC 333, 24 октября" in letter.text
    assert "Билеты придут за 5 дней." in letter.text
    assert letter.text.rstrip().endswith("Игорь, Envo Live")
    assert "<p" in letter.html and "@envo_help" in letter.html


def test_welcome_without_name_is_neutral():
    letter = letters.welcome(name="", tickets=1, event="UFC 333", event_date=None,
                             order_id="1", instructions="x")
    assert letter.text.startswith("Здравствуйте!")
    assert "билет на UFC 333." in letter.text


def test_links_become_anchors_in_html():
    letter = letters.welcome(name="Иван", tickets=1, event="X", event_date=None, order_id="1",
                             instructions="Ссылка: https://envo.live/help")
    assert '<a href="https://envo.live/help">' in letter.html


def test_cart_letter_offers_discount():
    letter = letters.cart(name="Олег", tickets=3, event="Tarkan", event_date=date(2026, 11, 27))
    assert letter.subject == "Tarkan, 27.11.2026"
    assert "3 билета на Tarkan, 27 ноября" in letter.text
    assert "скидку 10%" in letter.text
