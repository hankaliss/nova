# Shows the state of the user's repositories, locally and on GitHub (status, staged changes, recent commits, commits to pull or push, open pull requests, CI, open issues), for one project by name or path or for all repositories under ~/Projects, as a report: the skill for any git or GitHub status or report of the user's own projects.

Use it when I ask what changed, what I did lately, for a commit message, or for
a git or GitHub status or report of a project. A project I name ("nova",
"dotfiles") is a repository under ~/Projects: pass the name as `path` to
`git_companion.status`, even when the name is also yours. For all projects, or
when you don't know which one, call `git_companion.scan`. Prefer these over
other GitHub tools unless I ask about someone else's repository. Every report
has a local part and a GitHub part (read through the GitHub MCP server); when
the GitHub part is unavailable the report says why, so tell me in one line.
The report appears in the side panel by itself. Then write the commit message,
explain the changes or answer from the summary. You can only read: never claim
to have committed, pushed or merged, and give me the command to run instead.
Don't repeat what the report shows.
