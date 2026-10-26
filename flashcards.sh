#!/bin/sh
# Launch Flashcards on macOS / Linux from anywhere: run it directly, from another
# folder, or through a symlink (e.g. ln -s /path/to/flashcards.sh ~/bin/flashcards).
# If you copy this file elsewhere instead, set FLASHCARDS_HOME to the app folder.

# Follow symlinks to find the real folder this script lives in.
src="$0"
while [ -L "$src" ]; do
    dir=$(cd -P "$(dirname "$src")" && pwd)
    src=$(readlink "$src")
    case "$src" in /*) ;; *) src="$dir/$src" ;; esac
done
app_dir=$(cd -P "$(dirname "$src")" && pwd)

if [ ! -f "$app_dir/flashcards.py" ]; then
    app_dir="${FLASHCARDS_HOME:-}"
fi
if [ -z "$app_dir" ] || [ ! -f "$app_dir/flashcards.py" ]; then
    echo "error: can't find flashcards.py. Set FLASHCARDS_HOME to the flashcards folder." >&2
    exit 1
fi

if command -v python3 >/dev/null 2>&1; then
    py=python3
elif command -v python >/dev/null 2>&1; then
    py=python
else
    echo "error: Python 3 not found. Install it from https://www.python.org/downloads/" >&2
    exit 1
fi

cd "$app_dir" && exec "$py" flashcards.py "$@"
