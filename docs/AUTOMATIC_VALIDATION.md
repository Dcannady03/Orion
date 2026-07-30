# Automatic Validation Portability and Safety

## Finding

The reported Linux failure was in the real bounded Tester regression, not in the
workspace, Vault, network, Git, or nested-process guard itself. Targeted tests were
invoked as dotted modules such as `tests.test_guard`. A repository `tests/` directory
without `__init__.py` is a namespace-package candidate. On environments that also
install a regular top-level package named `tests`, Python can resolve the unrelated
package and fail before Orion's intended test runs.

## Fix

Automatic Validation now invokes every test through path-based `unittest discover`
rooted under the approved workspace's `tests` directory. The bounded runner accepts
only:

```text
python -m unittest discover -s <tests-path> -p <test-pattern>
```

The start path must remain under `tests`, and the pattern must be a local
`test_*.py`-style filename without path separators. Dotted modules and other unittest
forms are rejected before process creation. Full and targeted selection use the same
portable mechanism.

Validation paths are parsed as both Windows and POSIX forms before native resolution.
Windows drive paths, UNC paths, POSIX absolute paths, parent traversal, credential
paths, and symlink escapes are rejected consistently.

## Preserved boundaries

The child guard still:

- permits writes only in its isolated validation directory;
- prevents implementation-file and protected metadata writes;
- blocks Vault and credential reads;
- blocks nested subprocesses, including Git;
- blocks socket connections and datagram sends;
- uses an isolated home, temp directory, bytecode cache, and sanitized environment;
- compares implementation and protected workspace state after validation.

No operating-system skip was added for the boundary. Symlink regression coverage is
skipped only when the host genuinely cannot create a symlink.

## Platforms and limitations

The complete suite and real child-guard regression were executed on Windows during
this milestone. Windows, POSIX, and UNC parsing behavior is covered by
platform-independent unit tests. The fix specifically removes the Linux-dependent
namespace import, but a native Linux/macOS suite was not available in this checkout.

The guard is defense in depth around Orion-selected Python validation, not a general
host sandbox. It deliberately supports only isolated Python compile checks and
allowlisted unittest discovery. Additional languages require separate bounded
runners rather than widening this allowlist.
