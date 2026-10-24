# CLAUDE.md

Diablo IV screen-vision bot (Python, Windows). It reads only the screen and sends mouse/keyboard input; no memory reading or injection - keep it that way.

**At the start of every session, read `HANDOFF.md` first.** Section 0 ("NEXT SESSION AT THE HOME PC - START HERE") is a step-by-step checklist the user asked to be guided through: the user does not know what to do next, so take them through it one step at a time (exact command, what they should see, wait for their result), skipping steps already marked done.

- The user runs everything through `.\run.cmd ...` / `.\setup.cmd` in PowerShell from `C:\AI STUFF\Claude\Diablo4\d4-visual-bot`.
- Tests: `pytest` (no game needed; tests needing local screenshots skip).
- After every change or live result: update `HANDOFF.md` in the same commit and push.
- Botting can get the account banned; the user watches every run and F8 stops it.
