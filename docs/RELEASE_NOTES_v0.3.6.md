# Orion v0.3.6 — Calendar

Calendar teaches Orion to understand your schedule using Google Calendar as the source of truth.

## Highlights

- Read today and tomorrow's agenda
- Find the next upcoming event
- Answer morning and afternoon availability questions
- Route conversational calendar questions without asking the LLM
- Add today's schedule to Morning Star
- Keep Calendar optional and fault-isolated during startup
- Use read-only Google OAuth permissions

## Commands

```text
calendar
calendar today
calendar tomorrow
calendar next
calendar connect
ask what's on my calendar today?
ask am I free tomorrow morning?
```

## Google Calendar setup

1. Create an OAuth **Desktop app** credential in Google Cloud and enable the Google Calendar API.
2. Download the credential JSON to:

   `config/google-calendar-credentials.json`

3. In `config/default.yaml`, change:

```yaml
calendar:
  enabled: true
```

4. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

5. Start Orion and run:

```text
calendar connect
```

Google opens a local authorization page. Orion stores the resulting token locally at `.orion/google-calendar-token.json`. Both credential files are ignored by Git.

## Safety and privacy

This release requests read-only calendar access. It cannot create, edit, accept, decline, or delete events. Those actions will be added later through Orion's Action and Approval systems.

## Verification

```text
Ran 95 tests
OK
```
