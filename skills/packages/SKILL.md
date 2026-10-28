# Shows pending package updates, recently installed or updated packages, the largest explicitly installed packages and orphans (pacman), as a report: the skill for any question about updates or installed packages.

Use it when I ask what updates are waiting, what an update brings, what I
installed or updated lately, or what I could remove. Call `packages.report`
once; the report appears in the side panel by itself. Then explain what the
pending updates bring (kernel, graphics driver, desktop: mention a reboot or
anything that deserves care) or which orphans and big packages look safe to
remove, and don't repeat the numbers the report shows. You can only read:
never claim to have updated or removed anything, give me the command to run
(`sudo pacman -Syu`, `sudo pacman -Rns <package>`) and let me run it.
