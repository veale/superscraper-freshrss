---
name: Sidecar venv location
description: The sidecar .venv was broken (shebang pointed to a deleted repo); fixed to use the current location
type: project
---

`sidecar/.venv` was originally created from `superscraper-freshrss/sidecar/` (an old repo that no longer exists). The `pip` shebang pointed to that deleted path, making `.venv/bin/pip` unusable.

**Fix applied:** Recreated venv with `/opt/homebrew/opt/python@3.10/bin/python3.10 -m venv --clear sidecar/.venv`, then reinstalled from `requirements.txt`. The venv now resolves correctly.

**How to apply:** If pip is broken again, recreate with the same command. Use `.venv/bin/pip` or `.venv/bin/python3` for any package operations in this repo.
