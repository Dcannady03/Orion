# Docker Integration

## Purpose and boundary

The Docker commands answer one question: what is running in Docker on
this machine, and is it healthy? Orion talks to the Docker daemon
through the `docker` CLI (subprocess, no new dependencies) and reports
containers, their state and health, and their recent logs in one place.

Discovery, health, logs, and diagnosis are read-only. Control (start, stop,
restart) is a mutating action: it shows the container's current state,
asks for explicit interactive approval, re-resolves the container
before acting, and re-inspects it afterwards to verify the new state.
Every applied control action is recorded in the recovery history
(`recovery --kind docker`).

Docker is deliberately kept separate from the `health` command's
services section: that section reports Orion's own internal service
registry, while `docker health` reports container health. Use `docker
health` for containers and `health` for Orion itself.

## Commands

```text
docker list
docker ps
docker inspect <name>
docker start <name>
docker stop <name>
docker restart <name>
docker health
docker logs <name>
docker logs <name> --tail N
docker diagnose <name>
docker list --json
docker inspect <name> --json
docker health --json
docker logs <name> --json
docker diagnose <name> --json
```

`docker ps` is an alias for `docker list`. Control commands take no
`--json` flag: approval is an interactive text flow.

## Discovery

`docker list` shows every container, running or stopped (`docker ps
-a` underneath), with name, image, status, health, and ports.
`docker inspect <name>` shows one container in detail: state, health,
image, creation time, start and finish times, ports, and mounts.

Containers are matched by:

- exact name (a leading `/` is ignored, as Docker reports it);
- exact container id;
- a unique id prefix of at least four characters.

An unknown name is refused with "No container matches". An ambiguous
prefix — one matching more than one container — is refused with the
matching names listed, so Orion never acts on the wrong container.

Malformed lines in `docker ps` output are skipped and counted, never
reinterpreted.

## Control and approval

`docker start`, `docker stop`, and `docker restart` follow the same
approval pattern as `remediate apply` and `backup import`:

1. Orion inspects the container and shows its current state.
2. Orion asks `Approve? [y/N]`. Anything but `y` or `yes` cancels
   with nothing changed.
3. On approval, Orion re-resolves the container (so a stale name can
   never act on the wrong container) and runs the action.
4. Orion re-inspects the container to verify the new state.

There is no `--yes` bypass. The handler refuses to act without
explicit confirmation, so a control action can only run through the
interactive approval flow.

Applied actions are recorded in the recovery history with kind
`docker` and the container name as the target, verified or not:

```text
recovery --kind docker
```

A failed docker command (nothing applied) and a refused action (no
approval, unknown container) are not recorded — nothing changed.

## Health

`docker health` summarizes container health across every container:

| Count | Meaning |
| --- | --- |
| `total` | Every container, running or stopped. |
| `running` / `exited` | Containers by state. |
| `healthy` | Running containers whose healthcheck reports healthy. |
| `unhealthy` | Containers whose healthcheck reports unhealthy. |
| `starting` | Containers whose healthcheck is still starting. |
| `no_healthcheck` | Containers with no healthcheck configured. |

The overall result is `error` when any container is unhealthy,
`warning` when any is starting, and `ok` otherwise. Unhealthy
containers are listed by name. The command itself fails (non-zero
result) when the overall result is `error`, so scripts and
`docker health --json` consumers can key on it.

## Logs

`docker logs <name>` shows recent log lines for one container,
newest last. The default window is 50 lines; `--tail N` sets it
(clamped to 1–500). Output is capped at 20,000 characters: when the
window exceeds the cap, the oldest lines are dropped and the output
says so. Logs are read-only and never modify the container.

## Diagnose

`docker diagnose <name>` answers "why can't I reach this container?"
in one read-only pass. It never starts, stops, or restarts anything.
The chain, in order:

1. **State.** Is the container running? If not, that is the primary
   finding, with the last exit code and finish time from inspect.
2. **Ports.** For each published host port (a real host binding, not
   merely an exposed port), TCP-connect to localhost with a 3-second
   timeout, then try a plain HTTP GET and report the status code — or
   "not HTTP" when the port answers TCP but is not a web service.
3. **Logs.** Scan the last 100 log lines for error signals
   (`error`, `exception`, `fail`, `traceback`, `panic`,
   case-insensitive) and report the count plus the five most recent
   matching lines, truncated.

It reports one verdict:

| Verdict | Meaning | Suggested next step |
| --- | --- | --- |
| `not-running` | The container is stopped. | `docker start <name>` (asks for approval). |
| `no-published-ports` | Running, but nothing on the host can reach it. | Recreate it with a published port. |
| `port-unreachable` | Running, but a published port does not answer. | Check the logs; `docker restart <name>`. |
| `http-error` | The port answers, but returns an HTTP error status. | Check the logs for the failing request. |
| `ok` | Running and answering. | The problem is elsewhere (network, firewall, client). |

The `container-unreachable` runbook (`runbooks for can't access
<name>`) covers the same ground as a step-by-step procedure. And
`remediate` proposes starting a stopped container that publishes a
host port (`start-container-<name>`): the fix side of this diagnosis,
applied only with your approval. A stopped container with no
published port never gets a proposal — it was probably stopped on
purpose.

## Unavailable Docker

When the `docker` command is missing, the daemon is unreachable, or a
command times out, every docker command fails closed with a clear
"Docker is not available" message and nothing is attempted. The test
suite needs no live daemon: all Docker tests run against an in-memory
stand-in for the CLI.

Timeouts: 15 seconds for the availability probe, 30 seconds for
reads, 60 seconds for control actions.

## Safety guarantees

The Docker commands:

- never act on an unknown or ambiguous container;
- never start, stop, or restart without explicit interactive approval;
- never print secrets (container output is passed through as-is, and
  Orion never injects credentials into docker commands);
- never create, delete, or rebuild containers, images, volumes, or
  networks — discovery, control, health, and logs only.

## See also

- [Health Check and Health Graphs](HEALTH_CHECK.md)
- [Approval-Based Remediation](REMEDIATION.md)
- [Backup and Restore](BACKUP_RESTORE.md)
- [Diagnostics and Diagnostic Runbooks](DIAGNOSTICS.md)
