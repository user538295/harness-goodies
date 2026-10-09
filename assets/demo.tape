# Harness Goodies hero GIF. Regenerate with:  vhs assets/demo.tape
# (run from the repo root; sources assets/demo-setup.zsh for the scripted output).
# Update assets/demo-setup.zsh when a command's output format changes.

Output assets/demo.gif
Set Shell "zsh"
Set Theme "Dracula"
Set Width 900
Set Height 600
Set FontSize 14
Set TypingSpeed 40ms

# Bootstrap: load scripted command handlers (hidden)
Hide
Type "source assets/demo-setup.zsh && clear"
Enter
Sleep 300ms
Show

# ---------------------------------------------------------------------------
# Act 1 — title stays visible, command types directly below it
# ---------------------------------------------------------------------------
Hide
Type "clear && echo '' && echo '' && echo '  1 / Refine the idea'"
Enter
Sleep 200ms
Show
Sleep 2s
Type "/feature-refinement Add dark mode toggle to the settings page"
Enter
Sleep 11s

# ---------------------------------------------------------------------------
# Act 2 — same pattern: clear between acts, then title + command together
# ---------------------------------------------------------------------------
Hide
Type "clear && echo '' && echo '' && echo '  2 / Build the plan'"
Enter
Sleep 200ms
Show
Sleep 2s
Type "/plan-maker Documentation/Backlog/dark-mode-toggle-brief.md"
Enter
Sleep 10s

# ---------------------------------------------------------------------------
# Act 3 — ship the task
# ---------------------------------------------------------------------------
Hide
Type "clear && echo '' && echo '' && echo '  3 / Ship the task'"
Enter
Sleep 200ms
Show
Sleep 2s
Type "/implement next plans/FEAT-042-dark-mode.md"
Enter
Sleep 10s
