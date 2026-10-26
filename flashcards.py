"""Flashcards - a small, self-hosted Quizlet replacement.

Decks are plain JSON files in ./decks. Drop a file there (or use File > Import)
and it shows up in the deck list. Study progress is kept in ./data/progress.json.

Run:      python flashcards.py
Import:   python flashcards.py import path/to/cards.json [more.json ...]
Validate: python flashcards.py check path/to/cards.json
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DECKS_DIR = APP_DIR / "decks"
DATA_DIR = APP_DIR / "data"
PROGRESS_FILE = DATA_DIR / "progress.json"

DEFAULT_SET = "General"  # set for decks whose JSON has no "set" field
MAX_BOX = 5  # Leitner boxes 1..5; box 1 = needs work, box 5 = mastered
# How long (seconds) a card rests after being answered correctly, by box.
BOX_INTERVALS = {1: 0, 2: 10 * 60, 3: 60 * 60, 4: 6 * 3600, 5: 24 * 3600}

FRONT_KEYS = ("front", "term", "question", "q", "prompt")
BACK_KEYS = ("back", "definition", "answer", "a", "response")


# --------------------------------------------------------------------------- #
# Deck loading
# --------------------------------------------------------------------------- #

class DeckError(ValueError):
    pass


def _pick(card: dict, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        if k in card and card[k] is not None:
            v = card[k]
            return "\n".join(map(str, v)) if isinstance(v, list) else str(v)
    return None


def normalize_deck(raw, default_name: str) -> dict:
    """Accept a few JSON shapes and return {"name", "description", "cards"}.

    Supported:
      {"name": ..., "cards": [{"front": ..., "back": ...}, ...]}
      [{"front": ..., "back": ...}, ...]
      {"Some term": "its definition", ...}            (flat mapping)
    An optional "set" groups decks by subject (defaults to "General").
    Card keys may also be term/definition, question/answer, q/a.
    A back given as a list of strings becomes one line per item.
    """
    if isinstance(raw, list):
        raw = {"cards": raw}
    if not isinstance(raw, dict):
        raise DeckError("top level must be an object or a list of cards")

    if "cards" not in raw:
        # Flat {"term": "definition"} mapping.
        if raw and all(isinstance(v, (str, list)) for v in raw.values()):
            raw = {"cards": [{"front": k, "back": v} for k, v in raw.items()]}
        else:
            raise DeckError('missing "cards" list')

    cards_in = raw["cards"]
    if not isinstance(cards_in, list):
        raise DeckError('"cards" must be a list')

    cards, seen = [], set()
    for i, c in enumerate(cards_in, 1):
        if isinstance(c, (list, tuple)) and len(c) >= 2:
            c = {"front": c[0], "back": c[1]}
        if not isinstance(c, dict):
            raise DeckError(f"card #{i} is not an object")
        front, back = _pick(c, FRONT_KEYS), _pick(c, BACK_KEYS)
        if not front or not back:
            raise DeckError(f"card #{i} needs both a front and a back")
        front, back = front.strip(), back.strip()
        if front in seen:
            continue  # silently drop exact duplicate fronts
        seen.add(front)
        tags = c.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        category = str(c.get("category") or c.get("subcategory") or "").strip()
        cards.append({"front": front, "back": back, "category": category, "tags": [str(t) for t in tags]})

    # An empty deck is allowed: it's what "New deck" creates before cards are added.
    return {
        "name": str(raw.get("name") or default_name),
        "set": str(raw.get("set") or DEFAULT_SET).strip(),
        "description": str(raw.get("description") or ""),
        "cards": cards,
    }


def load_deck_file(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise DeckError(f"invalid JSON: line {e.lineno} col {e.colno}: {e.msg}") from e
    deck = normalize_deck(raw, default_name=path.stem.replace("_", " ").replace("-", " "))
    deck["path"] = path
    deck["id"] = path.stem
    for c in deck["cards"]:
        c["deck"] = path.stem  # lets cards keep their own progress inside a combined set
    return deck


def sets_of(decks: list[dict]) -> list[str]:
    return sorted({d["set"] for d in decks}, key=str.lower)


def combine_set(decks: list[dict], set_name: str) -> dict:
    """One pseudo-deck holding every card of every deck in a set."""
    members = [d for d in decks if d["set"] == set_name]
    return {
        "id": f"set:{set_name}",
        "name": f"All of {set_name}",
        "set": set_name,
        "description": "",
        "cards": [c for d in members for c in d["cards"]],
        "combined": True,
    }


def load_all_decks(decks_dir: Path = DECKS_DIR) -> tuple[list[dict], list[str]]:
    """Return (decks, errors). Bad files are reported, not fatal."""
    decks, errors = [], []
    decks_dir.mkdir(parents=True, exist_ok=True)
    for p in sorted(decks_dir.glob("*.json")):
        try:
            decks.append(load_deck_file(p))
        except (DeckError, OSError) as e:
            errors.append(f"{p.name}: {e}")
    return decks, errors


def import_files(paths: list[Path], decks_dir: Path = DECKS_DIR) -> list[str]:
    """Validate and copy deck files into decks_dir. Returns messages."""
    decks_dir.mkdir(parents=True, exist_ok=True)
    msgs = []
    for src in paths:
        try:
            deck = load_deck_file(src)
        except (DeckError, OSError) as e:
            msgs.append(f"SKIPPED {src.name}: {e}")
            continue
        dest = decks_dir / src.name
        if src.resolve() != dest.resolve():
            shutil.copyfile(src, dest)
        msgs.append(f"imported {src.name}: [{deck['set']}] {deck['name']} ({len(deck['cards'])} cards)")
    return msgs


# --------------------------------------------------------------------------- #
# Editing decks (used by the in-app card editor)
# --------------------------------------------------------------------------- #

def parse_tags(text: str) -> list[str]:
    """'dns, networking  tls' -> ['dns', 'networking', 'tls']"""
    return [t for t in re.split(r"[,\s]+", text.strip().lstrip("#")) if t]


def save_deck(deck: dict) -> None:
    """Write a deck back to its JSON file (atomically)."""
    def dump(value) -> str:
        return json.dumps(value, ensure_ascii=False)

    header = {"set": deck["set"], "name": deck["name"]}
    if deck.get("description"):
        header["description"] = deck["description"]
    cards = []
    for c in deck["cards"]:
        card = {"front": c["front"], "back": c["back"]}
        if c.get("category"):
            card["category"] = c["category"]
        if c["tags"]:
            card["tags"] = c["tags"]
        cards.append(card)

    # One card per line, like the hand-written decks: easy to read, small git diffs.
    text = "{\n" + "".join(f"  {dump(k)}: {dump(v)},\n" for k, v in header.items())
    if cards:
        text += '  "cards": [\n' + ",\n".join(f"    {dump(c)}" for c in cards) + "\n  ]\n}\n"
    else:
        text += '  "cards": []\n}\n'
    path: Path = deck["path"]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")  # LF on every OS
    tmp.replace(path)


def create_deck(name: str, set_name: str, decks_dir: Path = DECKS_DIR) -> dict:
    name, set_name = name.strip(), set_name.strip() or DEFAULT_SET
    if not name:
        raise DeckError("the deck needs a name")
    decks_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", f"{set_name} {name}".lower()).strip("-") or "deck"
    path, n = decks_dir / f"{slug}.json", 2
    while path.exists():
        path, n = decks_dir / f"{slug}-{n}.json", n + 1
    deck = {"set": set_name, "name": name, "description": "", "cards": [], "path": path, "id": path.stem}
    save_deck(deck)
    return deck


def deck_categories(deck: dict) -> list[str]:
    return sorted({c["category"] for c in deck["cards"] if c.get("category")}, key=str.lower)


def _clean_card(deck: dict, front: str, back: str, tags, category: str = "",
                skip: dict | None = None) -> dict:
    front, back = front.strip(), back.strip()
    if not front or not back:
        raise DeckError("a card needs both a front and a back")
    if any(c["front"] == front for c in deck["cards"] if c is not skip):
        raise DeckError("this deck already has a card with that front")
    tags = parse_tags(tags) if isinstance(tags, str) else list(tags)
    return {"front": front, "back": back, "category": (category or "").strip(), "tags": tags}


def add_card(deck: dict, front: str, back: str, tags="", category: str = "") -> dict:
    card = _clean_card(deck, front, back, tags, category)
    card["deck"] = deck["id"]
    deck["cards"].append(card)
    save_deck(deck)
    return card


def update_card(deck: dict, card: dict, front: str, back: str, tags, progress: "Progress",
                category: str | None = None) -> None:
    """Edit a card in place (so an open study queue sees it) and keep its progress.
    category=None leaves the card's category as it is."""
    if category is None:
        category = card.get("category", "")
    new = _clean_card(deck, front, back, tags, category, skip=card)
    old_key = card_key(deck["id"], card)
    card.update(new)
    new_key = card_key(deck["id"], card)
    if new_key != old_key and old_key in progress.data:
        progress.data[new_key] = progress.data.pop(old_key)
        progress.save()
    save_deck(deck)


def delete_card(deck: dict, card: dict, progress: "Progress") -> None:
    deck["cards"] = [c for c in deck["cards"] if c is not card]
    progress.reset([card_key(deck["id"], card)])
    progress.save()
    save_deck(deck)


def card_key(deck_id: str, card: dict) -> str:
    # A card loaded from a file remembers its own deck, so progress is shared
    # between studying that deck alone and studying its whole set.
    h = hashlib.sha1(card["front"].encode("utf-8")).hexdigest()[:12]
    return f"{card.get('deck', deck_id)}:{h}"


# --------------------------------------------------------------------------- #
# Progress (Leitner boxes)
# --------------------------------------------------------------------------- #

class Progress:
    def __init__(self, path: Path = PROGRESS_FILE):
        self.path = path
        self.data: dict[str, dict] = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                backup = path.with_suffix(".corrupt.json")
                shutil.copyfile(path, backup)
                self.data = {}

    def get(self, key: str) -> dict:
        return self.data.get(key, {"box": 1, "right": 0, "wrong": 0, "last": 0})

    def record(self, key: str, correct: bool, now: float | None = None) -> dict:
        s = dict(self.get(key))
        if correct:
            s["box"] = min(MAX_BOX, s["box"] + 1)
            s["right"] += 1
        else:
            s["box"] = 1
            s["wrong"] += 1
        s["last"] = now if now is not None else time.time()
        self.data[key] = s
        return s

    def is_due(self, key: str, now: float | None = None) -> bool:
        s = self.get(key)
        now = now if now is not None else time.time()
        return now - s["last"] >= BOX_INTERVALS[s["box"]]

    def reset(self, keys) -> None:
        for k in keys:
            self.data.pop(k, None)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8", newline="\n")
        tmp.replace(self.path)


MODES = {
    "all": "All cards (shuffled)",
    "due": "Due (spaced repetition)",
    "weak": "Weak only (box 1-2)",
    "unseen": "Never studied",
}


def build_queue(deck: dict, progress: Progress, mode: str, tag: str | None = None,
                now: float | None = None, rng: random.Random | None = None,
                category: str | None = None) -> list[dict]:
    rng = rng or random
    cards = [c for c in deck["cards"]
             if (not category or c.get("category") == category) and (not tag or tag in c["tags"])]
    keyed = [(card_key(deck["id"], c), c) for c in cards]
    if mode == "due":
        keyed = [kc for kc in keyed if progress.is_due(kc[0], now)]
    elif mode == "weak":
        keyed = [kc for kc in keyed if progress.get(kc[0])["box"] <= 2]
    elif mode == "unseen":
        keyed = [kc for kc in keyed if kc[0] not in progress.data]
    rng.shuffle(keyed)
    if mode == "due":
        # Lowest box first, but shuffled within a box.
        keyed.sort(key=lambda kc: progress.get(kc[0])["box"])
    return [c for _, c in keyed]


def deck_stats(deck: dict, progress: Progress) -> dict:
    boxes = [progress.get(card_key(deck["id"], c))["box"] for c in deck["cards"]]
    seen = sum(1 for c in deck["cards"] if card_key(deck["id"], c) in progress.data)
    return {
        "total": len(boxes),
        "seen": seen,
        "mastered": sum(1 for b in boxes if b >= 4),
        "weak": sum(1 for b in boxes if b <= 2),
    }


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #

def run_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    BG, CARD, FG, MUTED = "#1e1f26", "#2b2d38", "#eceef4", "#9aa0b4"
    GOOD, BAD, ACCENT = "#3fb27f", "#e05d5d", "#6c8cff"

    # Per-OS bits: fonts differ, and macOS uses Cmd where others use Ctrl.
    IS_MAC = sys.platform == "darwin"
    if IS_MAC:
        UI_FONT = "Helvetica Neue"
    elif sys.platform.startswith("win"):
        UI_FONT = "Segoe UI"
    else:
        UI_FONT = "DejaVu Sans"
    ALL_SETS = "(all sets)"
    ALL_CATEGORIES = "(all categories)"
    MOD_KEY, MOD_LABEL =("Command", "Cmd") if IS_MAC else ("Control", "Ctrl")

    def flat_button(parent, text, command, bg, fg="white", bold=True, pady=10):
        # A Label acting as a button: macOS ignores tk.Button background colours.
        b = tk.Label(parent, text=text, bg=bg, fg=fg, cursor="hand2", pady=pady,
                     font=(UI_FONT, 11, "bold" if bold else "normal"))
        b.bind("<Button-1>", lambda e: command())
        return b

    def deck_label(d: dict) -> str:
        return d["name"]

    class Dialog(tk.Toplevel):
        """Dark, modal window shared by the card editor and the new-deck form."""

        def __init__(self, app, title):
            super().__init__(app)
            self.app = app
            self.title(title)
            self.configure(bg=BG, padx=16, pady=14)
            self.transient(app)
            self.protocol("WM_DELETE_WINDOW", self.close)
            self.bind("<Escape>", lambda e: self.close())

        def label(self, text):
            tk.Label(self, text=text, bg=BG, fg=MUTED, font=(UI_FONT, 9, "bold")).pack(anchor="w", pady=(8, 2))

        def text_box(self, height, value=""):
            t = tk.Text(self, height=height, width=60, wrap="word", bg=CARD, fg=FG, insertbackground=FG,
                        relief="flat", padx=8, pady=6, font=(UI_FONT, 11), undo=True)
            t.insert("1.0", value)
            t.pack(fill="both", expand=True)
            # Tab moves to the next field instead of typing a tab; Ctrl/Cmd+Enter submits.
            t.bind("<Tab>", lambda e: (e.widget.tk_focusNext().focus_set(), "break")[1])
            t.bind(f"<{MOD_KEY}-Return>", lambda e: (self.submit(), "break")[1])
            return t

        def entry(self, value=""):
            e = tk.Entry(self, bg=CARD, fg=FG, insertbackground=FG, relief="flat", font=(UI_FONT, 11))
            e.insert(0, value)
            e.pack(fill="x", ipady=4)
            e.bind("<Return>", lambda ev: self.submit())
            return e

        def message_line(self):
            self.msg = tk.Label(self, text="", bg=BG, fg=MUTED, font=(UI_FONT, 9), anchor="w")
            self.msg.pack(fill="x", pady=(8, 0))

        def say(self, text, ok=True):
            self.msg.configure(text=text, fg=GOOD if ok else BAD)

        def show(self):
            self.update_idletasks()
            self.grab_set()  # modal: study keys in the main window stay quiet while typing

        def submit(self):
            raise NotImplementedError

        def close(self):
            self.grab_release()
            self.destroy()

    class CardDialog(Dialog):
        """Add cards (stays open for quick entry) or edit one card."""

        def __init__(self, app, target: dict, card: dict | None = None, default_category: str = ""):
            super().__init__(app, "Edit card" if card else "Add cards")
            self.card = card
            self.added = 0
            self.real_decks = [d for d in app.decks]

            self.label("DECK")
            self.deck_box = ttk.Combobox(self, state="disabled" if card else "readonly",
                                         values=[deck_label(d) for d in self.real_decks])
            self.deck_box.current(self.real_decks.index(target))
            self.deck_box.pack(fill="x")
            self.deck_box.bind("<<ComboboxSelected>>", lambda e: self.refresh_categories())

            self.label("CATEGORY  (optional: pick one or type a new one, e.g. Kubernetes)")
            self.category = ttk.Combobox(self)
            self.category.set(card.get("category", "") if card else default_category)
            self.category.pack(fill="x")
            self.refresh_categories()

            self.label("FRONT")
            self.front = self.text_box(4, card["front"] if card else "")
            self.label("BACK")
            self.back = self.text_box(8, card["back"] if card else "")
            self.label("TAGS  (optional, separated by commas or spaces)")
            self.tags = self.entry(" ".join(card["tags"]) if card else "")

            row = tk.Frame(self, bg=BG)
            row.pack(fill="x", pady=(12, 0))
            if card:
                flat_button(row, f"Save  ({MOD_LABEL}+Enter)", self.submit, bg=GOOD).pack(
                    side="left", expand=True, fill="x", padx=(0, 4))
                flat_button(row, "Delete card", self.delete, bg=BAD).pack(side="left", padx=4)
                flat_button(row, "Cancel  (Esc)", self.close, bg=CARD, fg=FG, bold=False).pack(
                    side="left", padx=(4, 0))
            else:
                flat_button(row, f"Add card  ({MOD_LABEL}+Enter)", self.submit, bg=GOOD).pack(
                    side="left", expand=True, fill="x", padx=(0, 4))
                flat_button(row, "Done  (Esc)", self.close, bg=CARD, fg=FG, bold=False).pack(
                    side="left", padx=(4, 0))
            self.message_line()
            if not card:
                self.say("Cards are saved as soon as you add them. Category and tags carry over.")
                self.msg.configure(fg=MUTED)
            self.show()
            self.front.focus_set()

        def target(self) -> dict:
            return self.real_decks[self.deck_box.current()]

        def refresh_categories(self):
            self.category["values"] = deck_categories(self.target())

        def submit(self):
            front, back = self.front.get("1.0", "end"), self.back.get("1.0", "end")
            category = self.category.get()
            try:
                if self.card:
                    update_card(self.target(), self.card, front, back, self.tags.get(), self.app.progress,
                                category=category)
                else:
                    add_card(self.target(), front, back, self.tags.get(), category)
            except DeckError as e:
                self.say(str(e), ok=False)
                return
            if self.card:
                self.close()
                self.app.after_card_edit()
                return
            self.added += 1
            d = self.target()
            self.say(f"Added #{self.added} to {d['name']} (now {len(d['cards'])} cards)")
            self.refresh_categories()  # a newly typed category becomes a suggestion
            self.front.delete("1.0", "end")
            self.back.delete("1.0", "end")
            self.front.focus_set()

        def delete(self):
            if messagebox.askyesno("Delete card", "Delete this card for good?", parent=self):
                self.close()
                self.app.delete_current_card(confirm=False)

        def close(self):
            deck = self.target()  # read before the window (and its widgets) is destroyed
            super().close()
            if not self.card and self.added:
                self.app.after_cards_added(deck)

    class NewDeckDialog(Dialog):
        def __init__(self, app, set_name: str):
            super().__init__(app, "New deck")
            self.label("DECK NAME")
            self.name = self.entry()
            self.label("SET  (pick one or type a new subject)")
            self.set_box = ttk.Combobox(self, values=sets_of(app.decks))
            self.set_box.set(set_name)
            self.set_box.pack(fill="x")
            self.set_box.bind("<Return>", lambda e: self.submit())
            row = tk.Frame(self, bg=BG)
            row.pack(fill="x", pady=(12, 0))
            flat_button(row, "Create and add cards", self.submit, bg=GOOD).pack(
                side="left", expand=True, fill="x", padx=(0, 4))
            flat_button(row, "Cancel  (Esc)", self.close, bg=CARD, fg=FG, bold=False).pack(side="left")
            self.message_line()
            self.show()
            self.name.focus_set()

        def submit(self):
            try:
                deck = create_deck(self.name.get(), self.set_box.get())
            except DeckError as e:
                self.say(str(e), ok=False)
                return
            self.close()
            self.app.after_deck_created(deck)

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title("Flashcards")
            self.geometry("980x640")
            self.minsize(640, 420)
            self.configure(bg=BG)

            self.progress = Progress()
            self.decks: list[dict] = []
            self.visible: list[dict] = []  # what the deck list shows, row for row
            self.deck: dict | None = None
            self.set_var = tk.StringVar(value=ALL_SETS)
            self.queue: list[dict] = []
            self.missed: list[dict] = []
            self.idx = 0
            self.flipped = False
            self.session_right = self.session_wrong = 0

            self.mode_var = tk.StringVar(value="all")
            self.tag_var = tk.StringVar(value="(all tags)")
            self.category_var = tk.StringVar(value=ALL_CATEGORIES)
            self.reverse_var = tk.BooleanVar(value=False)

            self._build_menu(tk)
            self._build_layout(tk, ttk)
            self._bind_keys()
            self.reload_decks()
            self.protocol("WM_DELETE_WINDOW", self.on_close)

        # ---- layout ------------------------------------------------------ #
        def _build_menu(self, tk):
            m = tk.Menu(self)
            f = tk.Menu(m, tearoff=0)
            f.add_command(label="Import JSON deck(s)...", command=self.import_dialog, accelerator=f"{MOD_LABEL}+I")
            f.add_command(label="Reload decks folder", command=self.reload_decks, accelerator="F5")
            f.add_command(label="Open decks folder", command=self.open_decks_folder)
            f.add_separator()
            f.add_command(label="Reset progress for this deck", command=self.reset_deck_progress)
            f.add_separator()
            f.add_command(label="Quit", command=self.on_close)
            m.add_cascade(label="File", menu=f)
            c = tk.Menu(m, tearoff=0)
            c.add_command(label="Add cards...", command=self.open_add_cards, accelerator=f"{MOD_LABEL}+N")
            c.add_command(label="Edit current card...", command=self.open_edit_card, accelerator="E")
            c.add_command(label="Delete current card...", command=self.delete_current_card)
            c.add_separator()
            c.add_command(label="New deck...", command=self.open_new_deck, accelerator=f"{MOD_LABEL}+Shift+N")
            m.add_cascade(label="Cards", menu=c)
            h = tk.Menu(m, tearoff=0)
            h.add_command(label="Keyboard shortcuts", command=self.show_help)
            h.add_command(label="JSON format", command=self.show_format)
            m.add_cascade(label="Help", menu=h)
            self.config(menu=m)

        def _build_layout(self, tk, ttk):
            style = ttk.Style(self)
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass
            style.configure("TCombobox", fieldbackground=CARD, background=CARD, foreground=FG, arrowcolor=FG)
            # Read-only dropdowns otherwise turn light grey with grey text.
            style.map("TCombobox",
                      fieldbackground=[("readonly", CARD), ("disabled", BG)],
                      foreground=[("readonly", FG), ("disabled", MUTED)],
                      selectbackground=[("readonly", CARD)], selectforeground=[("readonly", FG)],
                      background=[("readonly", CARD)])
            self.option_add("*TCombobox*Listbox.background", CARD)
            self.option_add("*TCombobox*Listbox.foreground", FG)
            self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
            style.configure("TCheckbutton", background=BG, foreground=FG, font=(UI_FONT, 10))
            style.map("TCheckbutton", background=[("active", BG)])

            side = tk.Frame(self, bg=BG, width=260)
            side.pack(side="left", fill="y", padx=(12, 6), pady=12)
            side.pack_propagate(False)

            tk.Label(side, text="SET", bg=BG, fg=MUTED, font=(UI_FONT, 9, "bold")).pack(anchor="w")
            self.set_box = ttk.Combobox(side, state="readonly", textvariable=self.set_var)
            self.set_box.pack(fill="x", pady=(2, 8))
            self.set_box.bind("<<ComboboxSelected>>", lambda e: self.fill_deck_list())

            tk.Label(side, text="DECKS", bg=BG, fg=MUTED, font=(UI_FONT, 9, "bold")).pack(anchor="w")
            self.deck_list = tk.Listbox(side, bg=CARD, fg=FG, selectbackground=ACCENT,
                                        highlightthickness=0, borderwidth=0, activestyle="none",
                                        font=(UI_FONT, 11), exportselection=False)
            self.deck_list.pack(fill="both", expand=True, pady=(4, 8))
            self.deck_list.bind("<<ListboxSelect>>", lambda e: self.select_deck())

            self.stats_lbl = tk.Label(side, text="", bg=BG, fg=MUTED, justify="left",
                                      font=(UI_FONT, 9), anchor="w")
            self.stats_lbl.pack(fill="x", pady=(0, 8))

            tk.Label(side, text="MODE", bg=BG, fg=MUTED, font=(UI_FONT, 9, "bold")).pack(anchor="w")
            self.mode_box = ttk.Combobox(side, state="readonly", values=list(MODES.values()))
            self.mode_box.current(0)
            self.mode_box.pack(fill="x", pady=(2, 8))
            self.mode_box.bind("<<ComboboxSelected>>", lambda e: self.start_session())

            tk.Label(side, text="CATEGORY", bg=BG, fg=MUTED, font=(UI_FONT, 9, "bold")).pack(anchor="w")
            self.category_box = ttk.Combobox(side, state="readonly", textvariable=self.category_var)
            self.category_box.pack(fill="x", pady=(2, 8))
            self.category_box.bind("<<ComboboxSelected>>", lambda e: self.on_category_change())

            tk.Label(side, text="TAG", bg=BG, fg=MUTED, font=(UI_FONT, 9, "bold")).pack(anchor="w")
            self.tag_box = ttk.Combobox(side, state="readonly", textvariable=self.tag_var)
            self.tag_box.pack(fill="x", pady=(2, 8))
            self.tag_box.bind("<<ComboboxSelected>>", lambda e: self.start_session())

            self.reverse_chk = ttk.Checkbutton(side, text="Show back first (reverse)",
                                               variable=self.reverse_var, command=self.start_session)
            self.reverse_chk.pack(anchor="w")
            flat_button(side, "Restart session  (R)", self.start_session, bg=CARD, fg=FG,
                        bold=False, pady=6).pack(fill="x", pady=(8, 0))
            edit_row = tk.Frame(side, bg=BG)
            edit_row.pack(fill="x", pady=(6, 0))
            flat_button(edit_row, "+ Add cards", self.open_add_cards, bg=CARD, fg=FG,
                        bold=False, pady=6).pack(side="left", expand=True, fill="x", padx=(0, 3))
            flat_button(edit_row, "+ New deck", self.open_new_deck, bg=CARD, fg=FG,
                        bold=False, pady=6).pack(side="left", expand=True, fill="x", padx=(3, 0))

            main = tk.Frame(self, bg=BG)
            main.pack(side="left", fill="both", expand=True, padx=(6, 12), pady=12)

            top = tk.Frame(main, bg=BG)
            top.pack(fill="x")
            self.counter_lbl = tk.Label(top, text="", bg=BG, fg=MUTED, font=(UI_FONT, 10))
            self.counter_lbl.pack(side="left")
            self.score_lbl = tk.Label(top, text="", bg=BG, fg=MUTED, font=(UI_FONT, 10))
            self.score_lbl.pack(side="right")

            self.bar = tk.Canvas(main, height=4, bg=CARD, highlightthickness=0)
            self.bar.pack(fill="x", pady=(4, 10))

            self.card = tk.Frame(main, bg=CARD, cursor="hand2")
            self.card.pack(fill="both", expand=True)
            self.side_lbl = tk.Label(self.card, text="", bg=CARD, fg=MUTED, font=(UI_FONT, 9, "bold"))
            self.side_lbl.pack(anchor="nw", padx=16, pady=(12, 0))
            self.card_text = tk.Label(self.card, text="", bg=CARD, fg=FG, font=(UI_FONT, 18),
                                      justify="center", wraplength=600)
            self.card_text.pack(fill="both", expand=True, padx=24, pady=24)
            self.tags_lbl = tk.Label(self.card, text="", bg=CARD, fg=MUTED, font=(UI_FONT, 9))
            self.tags_lbl.pack(anchor="se", padx=16, pady=(0, 10))
            for w in (self.card, self.card_text, self.side_lbl):
                w.bind("<Button-1>", lambda e: self.flip())
            self.card.bind("<Configure>", self._rewrap)

            btns = tk.Frame(main, bg=BG)
            btns.pack(fill="x", pady=(10, 0))
            flat_button(btns, "✗  Missed it  (←/J/1)", lambda: self.answer(False),
                        bg=BAD).pack(side="left", expand=True, fill="x", padx=(0, 4))
            flat_button(btns, "Flip  (Space)", self.flip,
                        bg=ACCENT).pack(side="left", expand=True, fill="x", padx=4)
            flat_button(btns, "✓  Got it  (→/K/2)", lambda: self.answer(True),
                        bg=GOOD).pack(side="left", expand=True, fill="x", padx=(4, 0))

            self.status = tk.Label(self, text="", bg=BG, fg=MUTED, font=(UI_FONT, 9), anchor="w")
            self.status.place(relx=0, rely=1, anchor="sw", x=12, y=-2)

        def _rewrap(self, event):
            self.card_text.configure(wraplength=max(200, event.width - 60))
            size = 18
            text = self.card_text.cget("text")
            if len(text) > 400:
                size = 13
            elif len(text) > 180:
                size = 15
            self.card_text.configure(font=(UI_FONT, size))

        def _bind_keys(self):
            # "break" stops the focused widget's own handling of the key.
            self.bind("<space>", lambda e: (self.flip(), "break")[1])
            self.bind("<Up>", lambda e: (self.flip(), "break")[1])
            self.bind("<Down>", lambda e: (self.flip(), "break")[1])
            for k in ("<Right>", "k", "2"):
                self.bind(k, lambda e: (self.answer(True), "break")[1])
            for k in ("<Left>", "j", "1"):
                self.bind(k, lambda e: (self.answer(False), "break")[1])
            # Sidebar widgets keep focus after a click, and their own class bindings
            # (Space re-selects a deck, arrows move the selection) would run first.
            # Put the window's bindings ahead of them so the card keys always win.
            win = str(self)
            for w in (self.set_box, self.deck_list, self.mode_box, self.category_box,
                      self.tag_box, self.reverse_chk):
                w.bindtags((win,) + tuple(t for t in w.bindtags() if t != win))
            self.bind("s", lambda e: self.skip())
            self.bind("r", lambda e: self.start_session())
            self.bind("m", lambda e: self.review_missed())
            self.bind("<F5>", lambda e: self.reload_decks())
            self.bind(f"<{MOD_KEY}-i>", lambda e: self.import_dialog())
            self.bind(f"<{MOD_KEY}-n>", lambda e: self.open_add_cards())
            self.bind(f"<{MOD_KEY}-N>", lambda e: self.open_new_deck())  # with Shift
            self.bind("e", lambda e: self.open_edit_card())

        # ---- deck management --------------------------------------------- #
        def reload_decks(self):
            self.decks, errors = load_all_decks()
            if errors:
                messagebox.showwarning("Some decks failed to load", "\n".join(errors))
            sets = sets_of(self.decks)
            self.set_box["values"] = [ALL_SETS] + sets
            if self.set_var.get() not in sets:
                self.set_var.set(ALL_SETS)
            self.fill_deck_list()
            if self.decks:
                self.set_status(f"Loaded {len(self.decks)} deck(s) in {len(sets)} set(s) from {DECKS_DIR}")

        def fill_deck_list(self):
            """Show the decks of the chosen set; keep the current deck selected if it's still there."""
            current = self.deck["id"] if self.deck else None
            chosen = self.set_var.get()
            if chosen == ALL_SETS:
                self.visible = list(self.decks)
                labels = [f"{d['name']}  ({len(d['cards'])})" for d in self.visible]
            else:
                members = [d for d in self.decks if d["set"] == chosen]
                self.visible = members[:]
                labels = [f"{d['name']}  ({len(d['cards'])})" for d in members]
                if len(members) > 1:
                    combined = combine_set(self.decks, chosen)
                    self.visible.insert(0, combined)
                    labels.insert(0, f"★ All {len(members)} decks  ({len(combined['cards'])})")
            self.deck_list.delete(0, "end")
            for label in labels:
                self.deck_list.insert("end", label)
            if not self.visible:
                self.deck = None
                self.queue = []
                self.show_text("No decks yet.\n\nFile > Import JSON deck(s)…  or drop .json files into\n"
                               f"{DECKS_DIR}\nthen press F5.", side="")
                return
            ids = [d["id"] for d in self.visible]
            i = ids.index(current) if current in ids else 0
            self.deck_list.selection_clear(0, "end")
            self.deck_list.selection_set(i)
            self.deck_list.see(i)
            self.select_deck()

        def select_deck(self):
            sel = self.deck_list.curselection()
            if not sel:
                return
            self.deck = self.visible[sel[0]]
            self.category_var.set(ALL_CATEGORIES)
            self.tag_var.set("(all tags)")
            self.update_tag_choices()
            self.start_session()

        def chosen_category(self) -> str | None:
            c = self.category_var.get()
            return None if c == ALL_CATEGORIES else c

        def on_category_change(self):
            self.update_tag_choices()  # the tag list narrows to the chosen category
            self.start_session()

        def update_tag_choices(self):
            """Refresh the Category and Tag dropdowns; keep the choices that still exist."""
            cats = deck_categories(self.deck)
            self.category_box["values"] = [ALL_CATEGORIES] + cats
            if self.category_var.get() not in cats:
                self.category_var.set(ALL_CATEGORIES)
            cat = self.chosen_category()
            tags = sorted({t for c in self.deck["cards"]
                           if not cat or c.get("category") == cat for t in c["tags"]})
            self.tag_box["values"] = ["(all tags)"] + tags
            if self.tag_var.get() not in tags:
                self.tag_var.set("(all tags)")

        def import_dialog(self):
            paths = filedialog.askopenfilenames(title="Import flashcard JSON",
                                                filetypes=[("JSON", "*.json"), ("All files", "*.*")])
            if not paths:
                return
            msgs = import_files([Path(p) for p in paths])
            messagebox.showinfo("Import", "\n".join(msgs))
            self.reload_decks()

        def open_decks_folder(self):
            import os
            import subprocess
            DECKS_DIR.mkdir(parents=True, exist_ok=True)
            if sys.platform.startswith("win"):
                os.startfile(DECKS_DIR)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", DECKS_DIR])
            else:
                subprocess.Popen(["xdg-open", DECKS_DIR])

        # ---- making and editing cards ------------------------------------ #
        def real_deck(self, deck_id: str) -> dict | None:
            return next((d for d in self.decks if d["id"] == deck_id), None)

        def current_card(self) -> dict | None:
            if self.deck and self.idx < len(self.queue):
                return self.queue[self.idx]
            return None

        def open_add_cards(self):
            if not self.decks:
                self.open_new_deck()
                return
            target = self.deck
            if target is None or target.get("combined"):
                # Combined set (or nothing chosen): default to the first deck shown.
                target = next((d for d in self.visible if not d.get("combined")), self.decks[0])
            CardDialog(self, target, default_category=self.chosen_category() or "")

        def open_edit_card(self):
            c = self.current_card()
            deck = self.real_deck(c["deck"]) if c else None
            if deck:
                CardDialog(self, deck, card=c)

        def open_new_deck(self):
            chosen = self.set_var.get()
            NewDeckDialog(self, DEFAULT_SET if chosen == ALL_SETS else chosen)

        def after_card_edit(self):
            # The card was edited in place, so the queue already holds the new text.
            self.update_tag_choices()
            self.show_card()

        def after_cards_added(self, deck: dict):
            self.fill_deck_list()  # rebuilds counts and combined sets, restarts the session
            self.set_status(f"Saved new cards to {deck['path'].name}")

        def after_deck_created(self, deck: dict):
            self.deck = deck
            self.set_var.set(deck["set"])
            self.reload_decks()
            new = self.real_deck(deck["id"])
            if new:
                CardDialog(self, new)

        def delete_current_card(self, confirm: bool = True):
            c = self.current_card()
            deck = self.real_deck(c["deck"]) if c else None
            if not deck:
                return
            if confirm and not messagebox.askyesno("Delete card", f"Delete this card for good?\n\n{c['front'][:200]}"):
                return
            delete_card(deck, c, self.progress)
            if self.deck.get("combined"):
                self.deck["cards"] = [x for x in self.deck["cards"] if x is not c]
            self.queue.pop(self.idx)
            self.missed = [x for x in self.missed if x is not c]
            self.idx -= 1
            self.advance()  # shows the next card, or the end-of-session screen
            self.set_status(f"Deleted a card from {deck['name']}")

        def reset_deck_progress(self):
            if not self.deck:
                return
            if messagebox.askyesno("Reset progress", f"Forget all progress for '{self.deck['name']}'?"):
                self.progress.reset(card_key(self.deck["id"], c) for c in self.deck["cards"])
                self.progress.save()
                self.start_session()

        # ---- study session ----------------------------------------------- #
        def current_mode(self) -> str:
            return list(MODES)[self.mode_box.current()]

        def start_session(self, cards: list[dict] | None = None):
            if not self.deck:
                return
            tag = self.tag_var.get()
            tag = None if tag == "(all tags)" else tag
            self.queue = cards if cards is not None else build_queue(
                self.deck, self.progress, self.current_mode(), tag, category=self.chosen_category())
            self.missed = []
            self.idx = 0
            self.session_right = self.session_wrong = 0
            self.update_stats()
            if not self.deck["cards"]:
                self.show_text(f"This deck has no cards yet.\n\nPress {MOD_LABEL}+N or click “+ Add cards”.", side="")
                self.update_counter()
                return
            if not self.queue:
                self.show_text("Nothing to study in this mode.\n\nTry “All cards”, or come back later "
                               "for spaced-repetition cards.", side="")
                self.update_counter()
                return
            self.show_card()

        def review_missed(self):
            if self.missed:
                cards = self.missed[:]
                random.shuffle(cards)
                self.start_session(cards)
                self.set_status(f"Reviewing {len(cards)} missed card(s)")

        def show_card(self):
            self.flipped = False
            c = self.queue[self.idx]
            text, label = (c["back"], "BACK") if self.reverse_var.get() else (c["front"], "FRONT")
            if c.get("category"):
                label = f"{label}  ·  {c['category']}"
            self.show_text(text, side=label)
            self.tags_lbl.configure(text="  ".join(f"#{t}" for t in c["tags"]))
            box = self.progress.get(card_key(self.deck["id"], c))["box"]
            self.set_status(f"Box {box}/{MAX_BOX}   ·   Space flip · → got it · ← missed · S skip · M review missed")
            self.update_counter()

        def show_text(self, text: str, side: str):
            self.card_text.configure(text=text)
            self.side_lbl.configure(text=side)
            self.tags_lbl.configure(text="")
            self._rewrap(type("E", (), {"width": self.card.winfo_width() or 700})())

        def flip(self):
            if not self.deck or self.idx >= len(self.queue):
                return
            c = self.queue[self.idx]
            self.flipped = not self.flipped
            showing_back = self.flipped != self.reverse_var.get()
            self.card_text.configure(text=c["back"] if showing_back else c["front"])
            side = "BACK" if showing_back else "FRONT"
            self.side_lbl.configure(text=f"{side}  ·  {c['category']}" if c.get("category") else side)
            self._rewrap(type("E", (), {"width": self.card.winfo_width() or 700})())

        def answer(self, correct: bool):
            if not self.deck or self.idx >= len(self.queue):
                return
            c = self.queue[self.idx]
            self.progress.record(card_key(self.deck["id"], c), correct)
            self.progress.save()
            if correct:
                self.session_right += 1
            else:
                self.session_wrong += 1
                self.missed.append(c)
            self.advance()

        def skip(self):
            if self.deck and self.idx < len(self.queue):
                self.advance()

        def advance(self):
            self.idx += 1
            self.update_stats()
            if self.idx < len(self.queue):
                self.show_card()
                return
            total = self.session_right + self.session_wrong
            pct = f"{100 * self.session_right / total:.0f}%" if total else "—"
            msg = f"Session done!\n\n✓ {self.session_right}    ✗ {self.session_wrong}    ({pct})\n\n"
            msg += ("Press M to drill the cards you missed, or R to restart."
                    if self.missed else "Press R to go again.")
            self.show_text(msg, side="")
            self.update_counter()

        # ---- labels ------------------------------------------------------ #
        def update_counter(self):
            n = len(self.queue)
            shown = min(self.idx + 1, n) if self.idx < n else n
            self.counter_lbl.configure(text=f"{self.deck['name'] if self.deck else ''}   ·   {shown} / {n}")
            self.score_lbl.configure(text=f"✓ {self.session_right}   ✗ {self.session_wrong}")
            self.bar.delete("all")
            w = self.bar.winfo_width() or 600
            frac = (self.idx / n) if n else 0
            self.bar.create_rectangle(0, 0, w * frac, 4, fill=ACCENT, width=0)

        def update_stats(self):
            if not self.deck:
                return
            s = deck_stats(self.deck, self.progress)
            self.stats_lbl.configure(
                text=f"{s['total']} cards · {s['seen']} studied\n"
                     f"{s['mastered']} mastered · {s['weak']} weak")

        def set_status(self, text: str):
            self.status.configure(text=text)

        def show_help(self):
            messagebox.showinfo("Shortcuts",
                                "Space / ↑ / ↓ / click   flip card\n"
                                "→  K  2                 got it\n"
                                "←  J  1                 missed it\n"
                                "S                       skip\n"
                                "M                       drill missed cards\n"
                                "R                       restart session\n"
                                "F5                      reload decks folder\n"
                                f"{MOD_LABEL}+I                  import JSON\n"
                                f"{MOD_LABEL}+N                  add cards\n"
                                f"{MOD_LABEL}+Shift+N            new deck\n"
                                "E                       edit the current card\n"
                                f"{MOD_LABEL}+Enter (in editor)  save card")

        def show_format(self):
            messagebox.showinfo("JSON format", (APP_DIR / "decks" / "example.json").as_posix() +
                                "\n\n{\n  \"name\": \"My deck\",\n  \"set\": \"Subject (optional)\",\n  \"cards\": [\n"
                                "    {\"front\": \"Q\", \"back\": \"A\",\n"
                                "     \"category\": \"Kubernetes (optional)\", \"tags\": [\"topic\"]}\n  ]\n}\n\n"
                                "Also accepted: a bare list of cards, term/definition or\n"
                                "question/answer keys, or a flat {\"term\": \"definition\"} map.")

        def on_close(self):
            self.progress.save()
            self.destroy()

    App().mainloop()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "import":
        for line in import_files([Path(p) for p in argv[1:]]):
            print(line)
        return 0
    if len(argv) >= 2 and argv[0] == "check":
        ok = True
        for p in argv[1:]:
            try:
                d = load_deck_file(Path(p))
                print(f"OK   {p}: [{d['set']}] {d['name']} ({len(d['cards'])} cards)")
            except (DeckError, OSError) as e:
                ok = False
                print(f"FAIL {p}: {e}")
        return 0 if ok else 1
    if argv:
        print(__doc__)
        return 2
    run_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
