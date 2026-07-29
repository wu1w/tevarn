"""Default deny globs for secrets / credentials (Grok-inspired hard floor).

Applied as *deny* overlay regardless of always-approve / free profile.
Users can add more via agent_permission_deny; they cannot weaken these
defaults unless agent_permission_relax_secrets=True (explicit opt-out).
"""

from __future__ import annotations

from backend.agent.permissions_rules import PERM_EDIT, PERM_READ, PermissionRule

# Path/basename globs — matched by PermissionGate._match_path
_SECRET_GLOBS: tuple[str, ...] = (
    "*.env",
    "*.env.*",
    ".env",
    ".env.local",
    ".env.production",
    "**/.env",
    "**/.env.*",
    "**/secrets/**",
    "**/*secret*",
    "**/*credentials*",
    "**/id_rsa",
    "**/id_rsa.*",
    "**/id_ed25519",
    "**/id_ed25519.*",
    "**/*.pem",
    "**/*.p12",
    "**/*.pfx",
    "**/*_rsa",
    "**/credentials.json",
    "**/service-account*.json",
    "**/.aws/credentials",
    "**/.ssh/**",
    "**/Cookies",
    "**/Login Data",
    "**/auth.json",
)

# .env.example is safe to read
_SECRET_ALLOW_READ: tuple[str, ...] = (
    "*.env.example",
    "**/.env.example",
    "*.env.sample",
    "**/.env.sample",
)


def secret_deny_rules() -> list[PermissionRule]:
    rules: list[PermissionRule] = []
    for g in _SECRET_GLOBS:
        rules.append(PermissionRule(PERM_READ, "deny", pattern=g))
        rules.append(PermissionRule(PERM_EDIT, "deny", pattern=g))
    for g in _SECRET_ALLOW_READ:
        rules.append(PermissionRule(PERM_READ, "allow", pattern=g))
    return rules
