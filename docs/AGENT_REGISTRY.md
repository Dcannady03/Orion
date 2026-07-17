# Orion Agent Registry — Phase 1

An AI Team role defines a job in a workflow. An agent defines the configurable worker
that performs that job. This separation lets Orion change providers, models, specialist
instructions, and future permissions without rewriting the workflow.

## Commands

```text
agent list
agent show <name>
agent create
agent enable <name>
agent disable <name>
agent test <name>
```

`agent create` is a guided, least-privilege setup. Advanced definitions can be edited
as YAML and then checked with `agent show` or `agent test`.

## External persistence

Each agent is stored independently under Orion's external user-data root:

```text
~/.orion/agents/security-reviewer.yaml
```

Orion seeds `architect.yaml`, `engineer.yaml`, and `reviewer.yaml` if they are missing.
It never replaces an existing definition during startup or application updates.

Assign an agent to a workflow role in Orion's user configuration:

```yaml
team:
  roles:
    architect:
      agent: security-reviewer
```

The workflow remains Architect; `security-reviewer` is the worker that receives that
role's bounded planning assignment.

## Strict YAML schema

```yaml
name: Security Reviewer
enabled: true
provider: openai
model: configured-default
instructions: >
  Review plans for secrets exposure, unsafe file access, command injection,
  permission problems, and insecure defaults.
tools:
  - read_files
  - inspect_diff
limits:
  max_turns: 3
  can_modify_files: false
permissions:
  filesystem:
    read: true
    write: false
  shell:
    run_tests: false
    arbitrary_commands: false
  git:
    create_branch: false
    commit: false
    push: false
```

The filename is the agent ID. Commands normalize underscores and spaces to hyphens,
so `security_reviewer` resolves to `security-reviewer.yaml`. Missing fields, unknown
fields, unsupported providers, malformed capability names, inconsistent write limits,
and invalid permission types are rejected.

The only recognized Phase 1 tool declarations are `read_files`, `inspect_diff`, and
`run_tests`. They remain inactive, and an unknown tool name is rejected instead of
silently becoming eligible for future execution.

Phase 1 supports Orion's existing runtime providers: `ollama`, `openai`, `gemini`, and
`configured-default`. A future Codex provider requires a dedicated provider adapter;
using “Codex Engineer” as a display name does not grant Codex execution.

## Safety boundary

Tools and permissions are declarations for future phases. They grant no runtime
capability in Phase 1. Both `agent test` and `team plan` make bounded provider calls
without a tool dispatcher and cannot read or write files, run tests or arbitrary shell
commands, create commits, push branches, or open pull requests.

`agent test` makes exactly one provider call, requires the same strict structured JSON
contract used by AI Team roles, rejects unknown output fields, and stops immediately on
invalid output. An agent assigned to a role must be enabled.
