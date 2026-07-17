# Orion Plugin System

Orion discovers plugins from `plugins/*/plugin.py` during startup. Each plugin exports `create_plugin()` and returns an `OrionPlugin` instance.

## Design rules

- Plugins register capabilities through the Service Registry.
- Plugins own their CLI commands and help entries.
- Plugin failures are isolated and do not prevent Orion from booting.
- Significant or dangerous actions remain subject to Orion's approval model.
- User data remains owned and controlled by the user.

## Minimal plugin

```python
from orion.plugins.base import OrionPlugin

class HelloPlugin(OrionPlugin):
    name = "hello"
    version = "1.0.0"
    description = "Adds a hello command."

    def handle(self, command: str) -> bool:
        if command.lower() != "hello":
            return False
        print("Hello from a plugin.")
        return True

    def help_lines(self):
        return ["  hello  Say hello [plugin]"]

def create_plugin():
    return HelloPlugin()
```


## Built-in Search Plugin

The Search Plugin registers the `search` service and provides `search` / `find` commands. It is read-only, workspace-bound, ignores generated directories, and skips binary or oversized files.

## Network Watch Plugin

The Network Watch Plugin registers the `network` service and provides one-time and
background connectivity checks. It distinguishes local gateway failures from likely
Internet or ISP outages and records JSON Lines monitoring logs under the external
user-data directory at `~/.orion/logs/network/`.
