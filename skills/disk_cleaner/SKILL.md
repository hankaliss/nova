# Finds where disk space went (largest folders in home, caches, Ollama models, node_modules, old package versions) and clears a cache folder when I confirm.

Use it when I ask why the disk is full, what takes space or what I can delete.
Call `disk_cleaner.scan` once; the report appears in the side panel by itself.
Then propose a short cleanup plan, safest first, and don't repeat the numbers
the report shows. To clear a cache folder under `~/.cache` call
`disk_cleaner.clear_cache` (it asks me every time). Old package versions need
root: tell me to run `sudo paccache -r` myself, never run it.
