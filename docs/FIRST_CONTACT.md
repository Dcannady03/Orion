# First Contact

First Contact is Orion's single supported initial-setup and reconfiguration workflow.
It uses the same profile, configuration, provider, Vault, routing, and execution-engine
services as the normal Orion runtime.

Run it explicitly with:

```powershell
python -m orion.main --first-contact
```

The legacy `python setup_orion.py` entry point delegates to this command and no longer
creates a second project scaffold or configuration workflow.

## AI choices

First Contact offers:

1. Ollama / local AI
2. OpenAI
3. Google Gemini
4. Multiple providers
5. Skip for now

Ollama setup checks the configured address, discovers installed local models, and lets
the user choose a default. An unavailable Ollama service does not block profile or
service setup.

OpenAI and Gemini keys are collected through hidden input. Orion verifies each
candidate against the provider's model-list endpoint before it writes anything. A
verified key is committed only through external Orion Vault at
`~/.orion/vault/vault.yaml`; it never enters normal configuration, output, logs, task
artifacts, or execution artifacts. Blank input, invalid credentials, unavailable APIs,
and failed persistence leave the previous credential and active provider unchanged.

Multiple-provider setup can connect any combination of Ollama, OpenAI, and Gemini,
choose the initial active provider, and select the existing Fast, Balanced, Coding, or
Research routing profile. The same settings remain available later through:

```text
ai providers
ai provider configure <openai|gemini>
ai provider use <ollama|openai|gemini>
ai profile <fast|balanced|coding|research>
vault health
```

## Non-destructive reruns

Forced First Contact reads the existing external profile and layered configuration.
Current identity, workspace, provider, routing, and service values become defaults.
Pressing Enter preserves them. Blank or failed cloud credentials never overwrite a
working Vault entry. The profile document is merged so unknown or future fields remain
intact, and pre-change YAML backups retain the `.before-first-contact` suffix.

The review confirmation occurs before profile or configuration writes. Cancelling it
leaves those files and Vault unchanged.

## Final summary

The completion summary reports:

- active provider and model;
- other configured providers;
- active routing profile;
- active workspace;
- configured services;
- detected CLI engines and independent Codex/ChatGPT desktop-app status.

ChatGPT Desktop is always labeled as a desktop application, not a CLI execution
engine. `OpenAI.Codex` is labeled Codex Desktop, not ChatGPT Desktop. Execution
detection is informational and does not change Codex Bridge approval, workspace, or
single-use execution safety.

## Email choices

First Contact offers Gmail, Outlook / Microsoft 365, both providers, or skipping Email.
It constructs the same `EmailService` and adapters used after startup. Selecting a
provider requests only read access and never enables send or mailbox mutations.

Google Calendar configuration can supply the same Desktop OAuth client file, and
Microsoft Calendar configuration can supply the same Entra client ID and tenant. Mail
still requires separate explicit read-only consent and uses a separate token cache, so
rerunning setup or disconnecting Mail cannot replace or remove Calendar authorization.

Working Mail connections are preserved on rerun. Failed or cancelled consent does not
enable the new provider, replace the default account, delete another provider, or modify
Calendar. OAuth tokens remain beneath external `~/.orion/tokens/`; First Contact never
prints or writes them to profile or normal configuration.
