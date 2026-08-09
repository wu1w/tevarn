# Tevarn Mobile — iOS / iPad (stub)

Same stack as Android:

- Shared Rust crate `tevarn-mobile-core` + host
- Pixel Console UI in `../ui` (tokens from PC `frontend/app/pixel-console.css`)
- Shell: `WKWebView` loading local host or bundled assets

## Next steps

1. Create Xcode project with staticlib link to `tevarn_mobile_host`
2. iPad: adaptive width > 768 uses two-column (sessions + chat)
3. Keychain for JWT storage instead of file store
