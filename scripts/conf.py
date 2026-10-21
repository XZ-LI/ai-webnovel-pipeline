import os
from pathlib import Path

# cookies/ and state/ live next to the user's novel project, not inside the skill
# (a skill directory may be read-only or synced between machines).
# Override with WNU_HOME if you keep sessions somewhere else.
BASE_DIR = Path(os.environ.get("WNU_HOME") or Path.cwd()).resolve()
