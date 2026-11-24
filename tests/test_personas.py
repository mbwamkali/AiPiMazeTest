"""Persona names: Mooore (formerly NoMoe) everywhere, the model's prompt included."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ant import personas as P  # noqa: E402
from ant import bots as B  # noqa: E402
from ant import compact as C  # noqa: E402


class TestPersonas(unittest.TestCase):
    def test_names(self):
        self.assertEqual(P.NAMES, ("Mooore", "Harry", "Curlee"))
        self.assertEqual(P.canonical("NoMoe"), "Mooore")
        self.assertEqual(P.canonical("Harry"), "Harry")
        self.assertEqual(set(B.SAY), set(P.NAMES))

    def test_model_prompt(self):
        self.assertEqual(P.SYSTEM["Mooore"], "You are Mooore, a bossy, decisive ant exploring a maze one room at a time. "
                                             "You give the orders and never second-guess yourself.")
        self.assertEqual(P.system_prompt("NoMoe"), P.SYSTEM["Mooore"])
        self.assertTrue(P.SYSTEM["Harry"].startswith("You are Harry, "))
        self.assertTrue(P.system_prompt("Mooore", "NoMoe").startswith("You are NoMoe, a bossy"))

    def test_messages_prompt_name(self):
        screen = "\nYou are in the Coral Hall.\nPaths: Iron Den\nNotes: untried from here: Iron Den (new). Other visited rooms with untried paths: 0 (type map).\n> "
        msgs, _ = C.messages("Mooore", screen, None, False)
        self.assertIn("You are Mooore,", msgs[0]["content"])
        msgs, _ = C.messages("Mooore", screen, None, False, prompt_name="NoMoe")
        self.assertIn("You are NoMoe,", msgs[0]["content"])   # the name the model was trained with, for A/B runs
        self.assertIn("You are Mooore,", P.messages("Mooore", screen, None, True, "Mooore")[0]["content"])


if __name__ == "__main__":
    unittest.main()
