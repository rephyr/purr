"""The user's own try, after the first turn: a tuna toastie at half past four. The failure message is
what the user pastes back."""
import unittest

from cafe.menu import total


class UserRun(unittest.TestCase):
    def test_tuna_toastie_at_half_past_four(self):
        got = total(["tuna toastie"], hour=16)
        self.assertEqual(got, 5.52, f"\n>>> total(['tuna toastie'], hour=16)\n{got}\n")
