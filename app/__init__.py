"""Package marker.

`from . import agent` makes `app/` a valid ADK app directory, so `adk web app` and
`adk eval app ...` find `root_agent` the way the ADK CLI expects.
"""

from . import agent  # noqa: F401
