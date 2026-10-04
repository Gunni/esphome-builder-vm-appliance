# Appliance design rules

## Working agreement

- Ask before changing features or defaults. Bug fixes and documentation consistency edits within the requested scope are authorized.
- Do not commit, push, tag, or publish unless explicitly requested. Tell the user when a change is ready to commit.
- Keep documentation short and describe current behavior; do not list removed features.

## Implementation

- Prefer the smallest solution and native systemd facilities for lifecycle, ordering, timers, conditions, credentials and privilege elevation.
- Use systemd-tmpfiles for appliance directory creation, permissions, ownership, empty runtime files and cleanup. Ignition delivers static configuration and scripts. Do not add shell mkdir/chown setup or Ignition directory entries where tmpfiles suffices.
- Ensure consumers start after tmpfiles processing. Use systemd service dependencies and ordering whenever possible; use completion markers only when service ordering cannot express the required coordination.
- Run ESPHome rootless as the dedicated unprivileged `esphome` account. Keep administrative access separate. Use run0 and the existing Polkit policy for appliance administration.
- Order the esphome user manager after setup through the user@2000.service drop-in. Setup needs only user-runtime-dir@2000.service.
- Failed first-boot OS update checks may continue; required package installation and initial container pull must succeed. Zincati handles subsequent OS updates.

## Builds and verification

- Build will run on every branch push. Publishing releases only for eligible tags on main or new Fedora stable releases built from the latest eligible tag.
- Pin GitHub Actions to full commit SHAs. Respect immutable releases: assemble and verify draft assets before publication.
- Give every external release asset its Fedora version and source revision suffix.
- Use GitHub MCP for repository access; use another API transport only when MCP does not expose the required operation.
- Put reasonable automated checks in Actions. Run focused local tests for fixes, but never report them as proof of a successful VM installation.
