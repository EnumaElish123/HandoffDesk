# Security policy

## Scope

HandoffDesk is a local desktop and CLI tool. It is not a multi-user service and has no network API, authentication layer, or encrypted record store. Use it under a trusted OS account with appropriately restricted state-directory permissions.

Registered source projects are read-only to the application. Tool state is kept separately. File and script opening, path containment, SQLite writes, and agent payload handling are important security boundaries.

## Supported version

Security fixes are currently targeted at the latest published version. Older personal or development builds should not be assumed to receive backports.

## Reporting a vulnerability

Use the repository's **Security → Advisories → Report a vulnerability** option if private vulnerability reporting is enabled. If it is unavailable, open a minimal issue asking the maintainer to arrange a private reporting channel; omit exploit details, sensitive paths, tokens, original databases, and personal project files until a channel is agreed.

Provide the affected version, OS and Python version, a small reproduction using synthetic files, the expected boundary, and the observed effect. Do not upload credentials or test against someone else's data. Response times are not guaranteed.

For accidental secret exposure, revoke or rotate the affected secret before relying on history cleanup. Ordinary interface and workflow bugs can be reported publicly with sanitized examples.
