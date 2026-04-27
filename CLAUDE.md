# Project: mimic-tts

## Commit policy (overrides global rule)

For this project, you and any subagents you dispatch ARE authorized to create
git commits without per-commit user approval. Use clean, conventional commit
messages and group related changes per task.

The user's global "don't auto-commit" rule does NOT apply here — this project
is being executed via plan-driven subagent workflow where each task ends in a
commit. The user reviews diffs after the fact.

Still applies:
- Never push to remote without explicit user request
- Never use `--no-verify` or skip hooks
- Never amend or force-push
- Always create new commits (no rewriting history)
