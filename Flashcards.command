#!/bin/sh
# Double-click launcher for macOS (Finder runs .command files in Terminal).
# Works through a Finder alias or symlink too; the real work is in flashcards.sh.
src="$0"
while [ -L "$src" ]; do
    dir=$(cd -P "$(dirname "$src")" && pwd)
    src=$(readlink "$src")
    case "$src" in /*) ;; *) src="$dir/$src" ;; esac
done
exec sh "$(cd -P "$(dirname "$src")" && pwd)/flashcards.sh" "$@"
