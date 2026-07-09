# Orion Architecture

## Mission

Orion is a personal AI operating layer designed to help its user think, code, remember, automate, and interact with local and cloud intelligence systems while keeping the user in control.

Orion is not intended to replace Windows, Linux, or Bazzite. Orion runs above the operating system as an intelligent command layer.

## Core Principles

1. **Local First** — Use local tools and models whenever practical.
2. **Privacy by Default** — User data belongs to the user.
3. **Approval Before Action** — Destructive or sensitive actions require user approval.
4. **Modular by Design** — Providers, skills, services, and agents should be replaceable.
5. **Memory With Control** — Orion should remember useful context, but the user decides what matters.
6. **Provider Independent** — Orion should not depend on one AI company or one model.
7. **Assist, Do Not Control** — Orion helps the user act; it does not take over.

## Current Components

### Core

The `core` package boots Orion, loads configuration, and runs the command router.

### Router

The command router handles CLI commands and sends intelligence requests to the Brain.

The router should stay thin. It should not contain business logic, AI logic, memory logic, or tool logic.

### Brain

The Brain is Orion's central intelligence coordinator.

The Brain is responsible for deciding how a request should be handled. Today, it forwards requests to the configured AI provider. In the future, it will coordinate memory, skills, tools, agents, approval checks, and model selection.

### Intelligence Providers

AI providers connect Orion to local or cloud models.

Current providers:

- Ollama
- OpenAI placeholder

Future providers may include Claude, Gemini, LM Studio, vLLM, OpenRouter, and specialized coding models.

### Skills

Skills are user-facing abilities such as coding help, Git, Docker, Home Assistant, weather, calendar, email, and file operations.

### Services

Services are reusable internal systems such as memory, voice, shell execution, file access, database access, and provider management.

### Agents

Agents are higher-level workflows that may coordinate multiple skills and services to complete larger tasks.

## Current Request Flow

```text
User input
    ↓
Command Router
    ↓
Brain
    ↓
AI Provider
    ↓
Response
```

## Near-Term Roadmap

- Add the Brain as the central intelligence layer.
- Add basic intent detection.
- Add coding-oriented request handling.
- Add memory service.
- Add approval system before file edits or shell commands.
- Add Git and project tools.

## Long-Term Goal

Orion should become a personal AI operating system that can:

- Think
- Code
- Remember
- Automate
- Use tools safely
- Control local services
- Integrate with Home Assistant
- Use local and cloud models interchangeably
- Act as a long-term development partner
