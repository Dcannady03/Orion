# Orion Image Center

Image Center is Orion's provider-neutral image-generation service. It is separate from
the text AI provider, so changing an image provider never changes the model used for
chat, planning, routing, or AI Team work.

## Quick start

OpenAI is the first production image adapter. Connect OpenAI through the existing AI
Center flow before generating an image:

```text
ai provider configure openai
image
image status
image providers
image provider use openai
image generate "a small observatory beneath a violet night sky"
image history
image show <image-id>
```

The OpenAI API key is read from Orion Vault. Image Center never places the key in
normal configuration, prompts, result metadata, history, logs, or workspace files.
OpenAI text and image use are independently selectable even though they may share the
same Vault credential.

## Storage and history

Generated images and their immutable metadata are stored outside application and
project directories:

```text
~/.orion/images/
```

Each attempt receives an opaque image ID. Successful artifacts record a relative
external filename, media type, byte count, dimensions when available, and SHA-256.
History is bounded by `image.history_limit`. Failed attempts retain only safe status
and error categories; raw provider responses, temporary provider URLs, base64 image
data, prompts beyond the configured safe preview, and credentials are not persisted.

Orion validates decoded content, supported media type, maximum byte size, dimensions,
and the stored hash before showing or copying an artifact. A corrupt or missing index
is recovered without trusting unvalidated paths.

## Saving an image into the active workspace

Generation does not write to the active workspace. To create a project file, request
an explicit copy:

```text
image save <image-id> assets/generated/observatory.png
```

Orion shows the exact image ID, external source, workspace destination, byte count,
and SHA-256 before asking for approval. Only explicit `y` or `yes` approves the normal
`image_save` action. The destination must be a relative path inside the active
workspace. Absolute paths, traversal, protected Orion/Git metadata, symlink escapes,
existing files, and hash mismatches fail closed. Image Center never overwrites a file
and never grants access to a parent or unrelated directory.

This approval is deliberately independent from AI Team plan approval. Approving an
image copy does not approve implementation, and an AI Team approval does not approve
an image copy.

## Discord image generation

Discord uses the existing restricted Orion bot gateway; it does not create a second
bot, token store, or provider path. Image requests are disabled by default:

```yaml
connect:
  discord_bot:
    image_generation:
      enabled: false
      allowed_user_ids: []
      cooldown_seconds: 30
      max_prompt_chars: 1000
      max_upload_bytes: 8388608
      max_concurrent_channels: 2
```

Enable the feature in external user configuration only after the bot's existing user,
channel, role, direct-message, and mention restrictions are correct. The image-specific
user list is an additional restriction; an empty list authorizes nobody.

Supported requests are explicit, for example:

```text
!image a friendly robot tending a rooftop garden
@Orion generate an image of a glass lighthouse in a storm
@Orion draw a watercolor map of an imaginary island
```

Ordinary chat and technical phrases such as "Docker image" remain on Orion's normal
text route. Accepted work runs off the Discord event loop and is bounded by per-user
and per-channel cooldowns plus a global concurrent-channel limit. Orion uploads only
the validated generated artifact and a provider-neutral caption. If Discord's upload
limit is exceeded, the external artifact remains available by image ID; Orion does not
expose its local path or automatically copy it into a workspace.

## Configuration

Application defaults are under `image` in `config/default.yaml`; user changes belong
in `~/.orion/config.yaml`:

```yaml
image:
  enabled: true
  provider: openai
  history_limit: 100
  history_display_limit: 10
  max_prompt_chars: 4000
  prompt_preview_chars: 300
  max_images_per_request: 1
  max_image_bytes: 20971520
  request_timeout_seconds: 120
  max_concurrent_jobs: 2
  output_format: png
  openai:
    model: gpt-image-2
    size: 1024x1024
    quality: medium
```

The current OpenAI adapter supports one image per request, PNG/JPEG/WebP output,
bounded response decoding, and the documented GPT Image sizes and quality values.
Provider availability checks are local and do not make a paid generation call.

## Status and troubleshooting

```text
image
image status
image providers
vault health
connect health
discord bot status
```

- `credential_missing`: configure OpenAI through AI Center; never paste a key into
  image configuration.
- `client_missing`: install the repository requirements in Orion's active Python
  environment.
- `disabled`: enable Image Center or the provider in external user configuration.
- `timeout`, `rate_limited`, `content_rejected`, or `provider_error`: Orion records a
  sanitized category. Retry only when appropriate; raw provider diagnostics are not
  persisted.
- Discord refuses an image: confirm the base bot policy first, then the separate image
  enablement, allowed-user list, cooldown, prompt limit, and upload limit.

Image Center does not edit images, accept arbitrary provider URLs from users, browse
for assets, or silently place generated content into a project in this milestone.
