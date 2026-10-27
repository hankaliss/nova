# Shows the state of a git repository (status, staged changes, recent commits) and scans all repositories under ~/Projects (uncommitted work, ahead/behind, last commit), as a report.

Use it when I ask what changed, what I did lately, or for a commit message.
Call `git_companion.status` for one repository (default: the current
directory) or `git_companion.scan` for all of them; the report appears in the
side panel by itself. Then write the commit message, explain the changes or
answer from the summary. You can only read: never claim to have committed, and
give me the commit command to run instead. Don't repeat what the report shows.
