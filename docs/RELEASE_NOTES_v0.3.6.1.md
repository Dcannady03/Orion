# Orion v0.3.6.1 — Constellation

Constellation makes Calendar provider-neutral and adds Microsoft Outlook / Microsoft 365 support through Microsoft Graph.

## Highlights

- Google Calendar remains supported as a provider.
- Microsoft Outlook, Outlook.com, Hotmail, Live, and Microsoft 365 calendars can be connected through Microsoft Graph.
- Multiple enabled providers are queried independently.
- Events are merged, sorted, and labeled with their source account.
- `calendar providers` lists configured providers.
- `calendar connect google` and `calendar connect microsoft` authorize a specific provider.
- OAuth never begins during startup; only explicit connect commands may open a browser.
- One provider failure does not discard successful events from another provider.

## Microsoft setup

1. Open Microsoft Entra admin center and create an App registration.
2. Choose supported account types appropriate for your users. For personal Outlook accounts, include personal Microsoft accounts.
3. Under Authentication, enable **Allow public client flows**.
4. Add delegated Microsoft Graph permissions:
   - `Calendars.Read`
   - `User.Read`
   - `offline_access`
5. Copy the Application (client) ID into `calendar.microsoft.client_id` in `config/default.yaml`.
6. Set `calendar.microsoft.enabled: true`.
7. Install dependencies and run:

   `calendar connect microsoft`

The token cache is stored locally at `.orion/microsoft-calendar-token.json` and must not be committed.

## Migration

The old flat Google Calendar settings moved under `calendar.google`. Existing Google users should keep their credentials and token files at their current paths; no reauthorization is required when those paths remain unchanged.
