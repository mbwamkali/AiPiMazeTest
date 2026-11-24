"""Persona names: Mooore (formerly NoMoe) everywhere a person sees it; the model keeps its trained prompt."""
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

    def test_model_keeps_trained_prompt(self):
        self.assertEqual(P.SYSTEM["Mooore"], "You are NoMoe, a bossy, decisive ant exploring a maze one room at a time. "
                                             "You give the orders and never second-guess yourself.")
        self.assertEqual(P.system_prompt("NoMoe"), P.SYSTEM["Mooore"])
        self.assertTrue(P.SYSTEM["Harry"].startswith("You are Harry, "))
        self.assertTrue(P.system_prompt("Mooore", "Mooore").startswith("You are Mooore, a bossy"))

    def test_messages_use_trained_prompt_unless_named(self):
        screen = "\nYou are in the Coral Hall.\nPaths: Iron Den\nNotes: untried from here: Iron Den (new). Other visited rooms with untried paths: 0 (type map).\n> "
        msgs, _ = C.messages("Mooore", screen, None, False)
        self.assertIn("You are NoMoe,", msgs[0]["content"])
        msgs, _ = C.messages("Mooore", screen, None, False, prompt_name="Mooore")
        self.assertIn("You are Mooore,", msgs[0]["content"])
        self.assertIn("You are Mooore,", P.messages("Mooore", screen, None, True, "Mooore")[0]["content"])


if __name__ == "__main__":
    unittest.main()
