"""Склейка клиентов и алиасы категорий."""

import unittest

from envo.normalize import ContactKey, match_category, normalize_email, normalize_phone


class Normalizing(unittest.TestCase):
    def test_email_is_lowercased_and_trimmed(self):
        self.assertEqual(normalize_email("  A.Petrov@Mail.RU "), "a.petrov@mail.ru")

    def test_phone_keeps_digits_and_fixes_leading_eight(self):
        self.assertEqual(normalize_phone("+7 (916) 123-45-67"), "79161234567")
        self.assertEqual(normalize_phone("8 916 123 45 67"), "79161234567")
        self.assertEqual(normalize_phone(None), "")


class Merging(unittest.TestCase):
    def test_same_email_is_one_person(self):
        a = ContactKey("m@x.ru", "79161234567")
        b = ContactKey("m@x.ru", "")
        self.assertTrue(a.matches(b))

    def test_same_phone_is_one_person(self):
        a = ContactKey("old@x.ru", "79161234567")
        b = ContactKey("new@x.ru", "79161234567")
        self.assertTrue(a.matches(b))

    def test_empty_fields_never_match(self):
        self.assertFalse(ContactKey("", "").matches(ContactKey("", "")))
        self.assertFalse(ContactKey("a@x.ru", "").matches(ContactKey("", "79161234567")))


class Categories(unittest.TestCase):
    ALIASES = {"категория 6": "Кат. 6", "верхний ярус": "Кат. 6", "категория 5": "Кат. 5"}

    def test_alias_matches_both_names(self):
        self.assertEqual(match_category("Категория 6", self.ALIASES), "Кат. 6")
        self.assertEqual(
            match_category("Верхний ярус секторов со 101 до 115", self.ALIASES), "Кат. 6"
        )

    def test_unknown_sector_is_not_guessed(self):
        self.assertIsNone(match_category("Партер", self.ALIASES))
        self.assertIsNone(match_category("", self.ALIASES))


if __name__ == "__main__":
    unittest.main()
