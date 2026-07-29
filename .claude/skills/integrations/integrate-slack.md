# Slack Integration Setup

Start this setup through `/integrate-mcp` or `/connect`.

## The safe default

First check whether Slack is already available through the user's Claude
workspace connector. If it is, use that connection directly. Do not ask the
user to create a Slack app, find a browser cookie, open developer tools, paste
a token, or configure a localhost port.

Those routes are fragile, can expose a credential in a chat or terminal
transcript, and often require workspace-administrator approval.

## What to say

1. **Already connected through Claude**

   ```
   Good news — Slack is already available in this workspace. I can use the
   access your workspace has approved. Would you like me to look for anything
   specific?
   ```

2. **Not connected, and no Dex-managed Slack app is configured**

   ```
   Slack is not connected here yet. You do not need to create an app or deal
   with ports. Dex-managed Slack sign-in is not available on this installation
   yet, so the next safe step is for your Dex/workspace administrator to enable
   it. I can keep setting up the rest of Dex without Slack.
   ```

3. **Workspace blocks or requires approval**

   ```
   Your Slack workspace requires an administrator to approve this connection.
   Nothing has been connected and no messages have been read. Ask an admin to
   approve the Dex Slack connection, then come back here and we will continue.
   ```

## If a connection exists

Describe the requested scope before reading anything. For onboarding, default
to a bounded, read-only scan: the channels and date range the user selects.
Never imply that a personal Slack account gives access to a work workspace; the
account must be a member of the workspace being connected.

## Error handling

| Situation | User-facing response |
|---|---|
| No workspace/connection selected | "Slack is not connected here yet. We can continue without it." |
| Admin approval required | "Your workspace needs an admin to approve Slack access. Nothing has changed." |
| Dex-managed app not configured | "You do not need to create an app. This sign-in is not available on this installation yet." |
| OAuth callback problem | "The sign-in could not finish. The temporary browser callback is an internal detail; no action is needed from you. Try again after the connection is enabled." |

## Do not use

- Browser-cookie authentication or DevTools extraction
- User-created Slack apps as a normal onboarding route
- Pasting Slack client secrets, bot tokens, user tokens, or cookies into chat
- Instructions that expose callback ports as a user setup task
