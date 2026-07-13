# Search Plugin

Provides safe, read-only workspace search through the `search` and `find`
commands.

Examples:

```text
search SessionMemory
search --files plugin
search --type py "register"
search --regex "class\s+\w+Plugin"
search --path docs architecture
```

The plugin ignores common generated directories, skips binary/oversized files,
and never searches outside the active workspace.
