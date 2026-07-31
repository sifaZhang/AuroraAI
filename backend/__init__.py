"""AuroraAI backend package."""

from .env import load_env_file

# Load local development configuration before any backend module reads paths,
# tokens, or other settings. Explicit process environment values still win.
load_env_file()
