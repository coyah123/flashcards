# Flashcards

A small self-hosted Quizlet replacement. Pure Python standard library (Tkinter) — no installs, no accounts, no service.

## Run

Works on Windows, macOS and Linux with Python 3.10+.

| OS | Launcher | Shortcut anywhere |
| --- | --- | --- |
| Windows | double-click `Flashcards.bat` | right-click it → **Send to → Desktop (create shortcut)** |
| macOS | double-click `Flashcards.command` (first time: right-click → Open) | drag it to the Dock / make a Finder alias, or `ln -s "$PWD/flashcards.sh" /usr/local/bin/flashcards` |
| Linux | `./flashcards.sh` | `ln -s "$PWD/flashcards.sh" ~/.local/bin/flashcards` |

The launchers find the app folder themselves, so they work from any directory and through shortcuts, aliases and symlinks. If you **copy** a launcher somewhere else instead, set `FLASHCARDS_HOME` to the flashcards folder. You can always run `python3 flashcards.py` (`python` on Windows) from the app folder.

Tkinter must be present. The python.org installers include it. With Homebrew Python on a Mac, run `brew install python-tk`. On Debian/Ubuntu, run `sudo apt install python3-tk`. Check it with `python3 -m tkinter`, which opens a small test window.

On a Mac, shortcuts that use Ctrl on Windows use Cmd instead (Cmd+I to import).

## Making cards in the app

| Do | How |
| --- | --- |
| Add cards | **+ Add cards** or Ctrl+N (Cmd+N). The window stays open: type the front, Tab, type the back, then Ctrl/Cmd+Enter saves and clears for the next card. Tags carry over. Esc closes it. |
| New deck | **+ New deck** or Ctrl+Shift+N. Give it a name and a set (choose an existing set or type a new one), then add cards straight away. |
| Edit the card on screen | **E**, or Cards → Edit current card. Progress is kept even if you change the front. |
| Delete the card on screen | Cards → Delete current card, or **Delete card** in the edit window. |

Each change is saved to the deck's JSON file straight away, written one card per line, so app edits and hand edits mix fine.

## Adding cards from JSON

Every `*.json` file in `decks/` is a deck. Three ways to get one there:

- Write/copy the file into `decks/` and press **F5** in the app.
- **File → Import JSON deck(s)…** (Ctrl+I) — validates and copies the file in.
- CLI: `python flashcards.py import path/to/deck.json`

Validate a file without importing it: `python flashcards.py check path/to/deck.json`

### Format

```json
{
  "set": "Interview prep",
  "name": "System design",
  "description": "optional",
  "cards": [
    { "front": "What is CAP?", "back": "Consistency, Availability, Partition tolerance — pick 2 under a partition.", "category": "Databases", "tags": ["distributed"] },
    { "front": "Multi-line back", "back": ["line one", "line two"] }
  ]
}
```

### Categories (subtopics inside a deck)

`"category"` on a card is optional and gives it one subtopic, such as `"Kubernetes"` inside the Troubleshooting deck. The sidebar's **Category** box studies only that subtopic and narrows the Tag list to match. The card shows its category next to FRONT / BACK. In the editor the Category box suggests the deck's existing categories. You can type a new one or leave it blank. Tags still work as extra, looser labels, and a card can have several.

Grouping from widest to narrowest: **set** (subject) → **deck** (one file) → **category** (one per card) → **tags** (any number).

### Sets (grouping by subject)

`"set"` is optional and groups decks by subject. Decks without it go in **General**. Pick a set in the sidebar's **Set** box and the deck list shows only that subject. When a set has more than one deck, a **★ All N decks** row at the top studies all of them mixed together. Progress is shared: a card you master in the mixed session is also mastered in its own deck. Use one file per topic within a subject, and give them all the same `"set"`. Use `tags` to split one deck into smaller parts.

Also accepted: a bare list of cards; `term`/`definition`, `question`/`answer` or `q`/`a` keys; `["front", "back"]` pairs; or a flat `{"term": "definition"}` map. Cards with a duplicate front are dropped.

## Studying

| Key | Action |
| --- | --- |
| Ctrl+I (Cmd+I on Mac) | import JSON |
| Ctrl+N / Ctrl+Shift+N (Cmd on Mac) | add cards / new deck |
| E | edit the current card |
| Space / ↑ / ↓ / click | flip |
| → / K / 2 | got it |
| ← / J / 1 | missed it |
| S | skip |
| M | drill only the cards you missed this session |
| R | restart session |
| F5 | reload `decks/` |

**Modes:** All (shuffled) · Due (spaced repetition) · Weak only · Never studied. Filter by tag, or tick *Show back first* to study in reverse.

Progress uses Leitner boxes (1–5): right moves a card up a box, wrong sends it back to box 1. In *Due* mode a card rests 0 min / 10 min / 1 h / 6 h / 24 h depending on its box. Progress is stored in `data/progress.json` (gitignored), keyed by deck file name + card front, so editing a card's back keeps its progress.

## Tests

```bash
python -m unittest discover tests
```
