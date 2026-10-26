import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import flashcards as fc  # noqa: E402


class NormalizeTests(unittest.TestCase):
    def test_shapes(self):
        full = fc.normalize_deck({"name": "N", "cards": [{"front": "a", "back": "b"}]}, "x")
        self.assertEqual(full["name"], "N")
        bare = fc.normalize_deck([{"term": "a", "definition": "b"}], "x")
        self.assertEqual(bare["cards"][0], {"front": "a", "back": "b", "category": "", "tags": []})
        cat = fc.normalize_deck([{"front": "a", "back": "b", "category": " Kubernetes "}], "x")
        self.assertEqual(cat["cards"][0]["category"], "Kubernetes")
        flat = fc.normalize_deck({"a": "b", "c": ["d", "e"]}, "x")
        self.assertEqual(flat["cards"][1]["back"], "d\ne")
        pairs = fc.normalize_deck({"cards": [["q", "a"]]}, "x")
        self.assertEqual(pairs["cards"][0]["front"], "q")

    def test_dedupe_and_errors(self):
        d = fc.normalize_deck({"cards": [{"q": "a", "a": "1"}, {"q": "a", "a": "2"}]}, "x")
        self.assertEqual(len(d["cards"]), 1)
        with self.assertRaises(fc.DeckError):
            fc.normalize_deck({"cards": [{"front": "only"}]}, "x")
        self.assertEqual(fc.normalize_deck({"cards": []}, "x")["cards"], [])  # new decks start empty


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.deck = fc.normalize_deck({"cards": [{"front": str(i), "back": "x"} for i in range(5)]}, "d")
        self.deck["id"] = "d"

    def test_leitner_and_queue(self):
        p = fc.Progress(self.tmp / "p.json")
        k0 = fc.card_key("d", self.deck["cards"][0])
        k1 = fc.card_key("d", self.deck["cards"][1])
        p.record(k0, True, now=1000)
        p.record(k0, True, now=1000)
        self.assertEqual(p.get(k0)["box"], 3)
        p.record(k1, False, now=1000)
        self.assertEqual(p.get(k1)["box"], 1)

        rng = random.Random(0)
        self.assertEqual(len(fc.build_queue(self.deck, p, "all", rng=rng)), 5)
        due = fc.build_queue(self.deck, p, "due", now=1001, rng=rng)
        self.assertNotIn(self.deck["cards"][0], due)  # box 3 resting for an hour
        self.assertEqual(len(due), 4)
        self.assertEqual(len(fc.build_queue(self.deck, p, "unseen", rng=rng)), 3)
        weak = fc.build_queue(self.deck, p, "weak", rng=rng)
        self.assertEqual(len(weak), 4)

        p.save()
        self.assertEqual(fc.Progress(self.tmp / "p.json").get(k0)["box"], 3)

    def test_sets_and_combined_progress(self):
        decks_dir = self.tmp / "sets"
        decks_dir.mkdir()
        (decks_dir / "a.json").write_text(json.dumps({"set": "Interview", "cards": [["q1", "a"]]}))
        (decks_dir / "b.json").write_text(json.dumps({"set": "Interview", "cards": [["q2", "a"]]}))
        (decks_dir / "c.json").write_text(json.dumps({"cards": [["q3", "a"]]}))
        decks, _ = fc.load_all_decks(decks_dir)
        self.assertEqual(fc.sets_of(decks), ["General", "Interview"])

        combined = fc.combine_set(decks, "Interview")
        self.assertEqual(len(combined["cards"]), 2)
        p = fc.Progress(self.tmp / "p.json")
        # Answering inside the combined set lands on the card's own deck.
        p.record(fc.card_key(combined["id"], combined["cards"][0]), True, now=1)
        deck_a = next(d for d in decks if d["id"] == "a")
        self.assertEqual(fc.deck_stats(deck_a, p)["seen"], 1)

    def test_create_add_edit_delete(self):
        decks_dir = self.tmp / "edit"
        deck = fc.create_deck("System design", "Interview", decks_dir)
        self.assertEqual(deck["path"].name, "interview-system-design.json")
        self.assertEqual(fc.create_deck("System design", "Interview", decks_dir)["path"].name,
                         "interview-system-design-2.json")
        with self.assertRaises(fc.DeckError):
            fc.create_deck("  ", "Interview", decks_dir)

        card = fc.add_card(deck, " What is CAP? ", "C or A during a partition\n", "#dbs, theory")
        self.assertEqual(card["tags"], ["dbs", "theory"])
        with self.assertRaises(fc.DeckError):
            fc.add_card(deck, "What is CAP?", "dupe")
        with self.assertRaises(fc.DeckError):
            fc.add_card(deck, "no back", "  ")

        p = fc.Progress(self.tmp / "p.json")
        p.record(fc.card_key(deck["id"], card), True, now=1)
        fc.update_card(deck, card, "Explain CAP", "C or A", ["dbs"], p)
        self.assertEqual(p.get(fc.card_key(deck["id"], card))["box"], 2)  # progress followed the edit

        reloaded = fc.load_deck_file(deck["path"])
        self.assertEqual(reloaded["set"], "Interview")
        self.assertEqual(reloaded["cards"][0]["front"], "Explain CAP")
        self.assertNotIn("deck", json.loads(deck["path"].read_text(encoding="utf-8"))["cards"][0])

        k8s = fc.add_card(deck, "Pod Pending?", "can't schedule", category="Kubernetes")
        fc.update_card(deck, k8s, "Pod Pending?", "can't schedule: CPU/mem", "", p)  # category kept
        self.assertEqual(fc.load_deck_file(deck["path"])["cards"][1]["category"], "Kubernetes")
        self.assertEqual(fc.deck_categories(deck), ["Kubernetes"])
        only = fc.build_queue(deck, p, "all", category="Kubernetes")
        self.assertEqual([c["front"] for c in only], ["Pod Pending?"])
        fc.delete_card(deck, k8s, p)

        fc.delete_card(deck, card, p)
        self.assertEqual(fc.load_deck_file(deck["path"])["cards"], [])
        self.assertEqual(p.data, {})

    def test_import_and_load(self):
        src = self.tmp / "src"
        src.mkdir()
        (src / "good.json").write_text(json.dumps({"name": "G", "cards": [{"front": "a", "back": "b"}]}))
        (src / "bad.json").write_text("{not json")
        decks_dir = self.tmp / "decks"
        msgs = fc.import_files([src / "good.json", src / "bad.json"], decks_dir)
        self.assertTrue(msgs[0].startswith("imported"))
        self.assertTrue(msgs[1].startswith("SKIPPED"))
        decks, errors = fc.load_all_decks(decks_dir)
        self.assertEqual([d["name"] for d in decks], ["G"])
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
