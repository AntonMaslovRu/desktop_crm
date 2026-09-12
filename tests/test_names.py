"""Обращение по имени: лучше промолчать, чем назвать человека фамилией."""

import unittest

from envo.names import first_name, plural, tickets_phrase


class FirstName(unittest.TestCase):
    def test_known_given_name_wins_regardless_of_order(self):
        self.assertEqual(first_name("Петров Александр"), "Александр")
        self.assertEqual(first_name("Александр Петров"), "Александр")
        self.assertEqual(first_name("Иванова Мария Сергеевна"), "Мария")

    def test_unknown_name_detected_by_surname_endings(self):
        self.assertEqual(first_name("Гатин Рустэм"), "Рустэм")

    def test_latin_is_skipped(self):
        self.assertEqual(first_name("John Smith"), "")
        self.assertEqual(first_name("ivanov"), "")

    def test_empty_and_junk(self):
        self.assertEqual(first_name(None), "")
        self.assertEqual(first_name("   "), "")
        self.assertEqual(first_name("+7 916 000"), "")

    def test_single_word_is_taken_as_a_name(self):
        self.assertEqual(first_name("мария"), "Мария")

    def test_hyphenated_name_keeps_both_parts(self):
        self.assertEqual(first_name("анна-мария"), "Анна-Мария")

    def test_ambiguous_pair_stays_silent(self):
        self.assertEqual(first_name("Иванов Петров"), "")


class Plural(unittest.TestCase):
    def test_russian_numerals(self):
        self.assertEqual(tickets_phrase(1), "билет")
        self.assertEqual(tickets_phrase(2), "2 билета")
        self.assertEqual(tickets_phrase(5), "5 билетов")
        self.assertEqual(tickets_phrase(11), "11 билетов")
        self.assertEqual(tickets_phrase(21), "21 билет")
        self.assertEqual(plural(114, "день", "дня", "дней"), "дней")


if __name__ == "__main__":
    unittest.main()
