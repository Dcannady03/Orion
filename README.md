#<img width="1254" height="1254" alt="orion_icon" src="https://github.com/user-attachments/assets/789cf0e9-1713-4782-826b-6eff0b2bf56c" />

 Orion

> **A Personal AI Operating System**

Orion is an AI Operating System designed to unify local AI models, cloud AI providers, automation, communication, and intelligent services behind a single persistent identity.

Unlike traditional chatbots, Orion is built around a central operating core. Every interface—CLI, Discord, future GUI, voice, and mobile—communicates with the same Orion brain.

---

# Current Version

**v0.4.3 – Signal**

Status:
- Active Development
- CLI Interface
- Discord Interface
- Multi-Provider AI
- Service-Based Architecture

---

# Vision

Orion is being built to become a true personal AI operating system capable of:

- Personal assistant
- Software engineering partner
- Home automation controller
- Communications hub
- Knowledge manager
- Voice assistant
- Mobile companion

The long-term goal is a persistent AI that remembers context, manages connected services, and assists across every device while keeping the user in complete control.

---

# Features

## Core

- User Profiles
- First Contact onboarding
- Configuration system
- Workspace management
- Session memory
- Project context
- Service registry
- Plugin architecture

---

## AI Center

Supported providers:

- Ollama
- Google Gemini
- OpenAI (framework ready)

Features:

- Live provider switching
- Model switching
- AI profiles
- Secure API Vault
- Shared request routing

Example:

```text
Orion> ai status

Orion> ai providers

Orion> ai use qwen3.5:9b

Orion> ai provider use gemini
```

---

## Communications Center

### Discord ✅

Two-way Discord integration.

Features:

- Dedicated Orion channel
- Natural conversation
- Shared Orion brain
- Owner approval model
- Channel-wide conversations
- Runtime diagnostics

Example:

```text
#orion

Good morning Orion.

What's the weather today?

How is the project looking?
```

No @ mention is required inside Orion's dedicated channel.

---

### Email (In Progress)

Planned support:

- Gmail
- Microsoft Outlook
- AI summaries
- Draft replies
- Approval before sending

---

## Services

Current built-in services:

- Weather
- Calendar
- Workspace
- Memory
- Project Context
- AI Providers
- Discord Gateway

Future services:

- Home Assistant
- Docker
- Git
- Notifications
- Voice
- Camera
- Smart Home

---

# Security

Orion follows an approval-first philosophy.

Read-only requests are available to everyone inside approved communication channels.

Sensitive actions always require an approved Orion owner.

Protected operations include:

- Shell
- File modification
- Git
- Docker
- Email sending
- AI configuration
- Vault changes
- Software installation

The user always remains in control.

---

# Architecture

```
                 Orion Core
                      │
        ┌─────────────┼─────────────┐
        │             │             │
      CLI         Discord        Future GUI
        │             │             │
        └─────────────┴─────────────┘
                  Request Router
                        │
        ┌───────────────┼────────────────┐
        │               │                │
    Weather         Calendar         AI Center
        │               │                │
        └───────────────┴────────────────┘
                  Orion Services
```

One Orion.

Many interfaces.

---

# Roadmap

## Phase 1 — Foundation ✅

- Core
- Configuration
- Profiles
- Brain
- AI Providers
- Identity

---

## Phase 2 — Intelligence ✅

- Workspace Manager
- Code Skills
- Session Memory
- Project Context
- Search
- Plugin Framework

---

## Phase 3 — Communications 🚧

Completed

- Discord
- AI Provider Center
- Secure Vault

Coming Next

- Gmail
- Outlook
- Unified Inbox

---

## Phase 4 — Automation

- Home Assistant
- Docker
- Git
- Shell Approval
- Notifications

---

## Phase 5 — Voice

- Wake Word
- Speech Recognition
- Natural Voice
- Mobile Companion

---

## Phase 6 — Orion Command Center

Desktop GUI

Features planned:

- Animated Orion avatar
- Service dashboard
- Voice visualization
- AI Command Center
- Live status panels
- Memory browser
- Home dashboard

---

# Design Philosophy

Orion is **not** another chatbot.

It is a persistent operating system where AI is only one component.

The interface may change.

The brain remains the same.

---

# Built With

Python

Ollama

Google Gemini

OpenAI

Discord

Docker

Home Assistant

Future:

Qt GUI

Mobile Companion

---

# License

Private development project.

© Daniel Cannady
