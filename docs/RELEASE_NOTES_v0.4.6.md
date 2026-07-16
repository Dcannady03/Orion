# Orion v0.4.6 — Polaris

Polaris completes Orion's user-facing OpenAI API connection workflow.

## Connect

```text
ai connect openai
```

Orion securely prompts for an API key, stores it in Orion Vault, verifies the account, discovers available models, and offers to make OpenAI active.

## Verify and manage

```text
ai test openai
ai provider models openai
ai provider use openai
ai disconnect openai
```

`ai test openai` uses the Models endpoint, so it does not generate a model response. API keys remain outside normal configuration and source control.
