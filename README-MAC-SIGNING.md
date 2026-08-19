# Developer ID signing + notarization for ParserOnSocial

The repository contains a separate workflow, `.github/workflows/release-macos-signed.yml`, that builds both Apple Silicon (`arm64`) and Intel (`x86_64`) macOS apps, signs them with **Developer ID Application**, sends the distribution ZIP to Apple's notarization service with `notarytool`, staples the approved ticket to the `.app`, runs Gatekeeper checks, and finally publishes the notarized ZIP artifact.

Apple's supported distribution model for software downloaded outside the Mac App Store is Developer ID signing plus notarization. See Apple's Developer ID documentation and `notarytool` technical note.

## 1. Apple Developer account

You need an Apple Developer Program membership and the Account Holder role (or the appropriate certificate permission) to create a **Developer ID Application** certificate.

In Apple Developer:

1. Certificates, Identifiers & Profiles → Certificates → `+`.
2. Select **Developer ID**.
3. Select **Developer ID Application**.
4. Create the certificate signing request (CSR), upload it, and download the resulting certificate.
5. Export the certificate together with its private key as a `.p12` file. The GitHub runner needs the private key; the `.cer` by itself is not enough.

Keep the private key secret.

## 2. App Store Connect API key for notarization

Create an App Store Connect API key with permission to use the Apple notary service. Record:

- Key ID
- Issuer ID
- the downloaded `.p8` private key

The workflow uses this API key with `xcrun notarytool` so no Apple ID password is stored in GitHub.

## 3. GitHub Actions secrets

Repository Settings → Secrets and variables → Actions → New repository secret.

Create these secrets:

| Secret | Value |
|---|---|
| `APPLE_DEVELOPER_ID_CERTIFICATE_BASE64` | Base64 of the Developer ID Application `.p12` |
| `APPLE_DEVELOPER_ID_CERTIFICATE_PASSWORD` | Password used when exporting the `.p12` |
| `APPLE_SIGNING_KEYCHAIN_PASSWORD` | Any strong temporary password for the CI keychain |
| `APPLE_TEAM_ID` | Your Apple Team ID |
| `APPLE_NOTARY_KEY_ID` | App Store Connect API Key ID |
| `APPLE_NOTARY_ISSUER_ID` | App Store Connect Issuer ID |
| `APPLE_NOTARY_API_KEY_BASE64` | Base64 of the `.p8` private key |

GitHub recommends storing signing material in repository/organization secrets rather than committing certificates or keys to the repository.

### Base64 encoding

On macOS/Linux:

```bash
base64 -i DeveloperIDApplication.p12 | pbcopy
base64 -i AuthKey_XXXXXXXXXX.p8 | pbcopy
```

On Windows PowerShell:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes('.\DeveloperIDApplication.p12'))
[Convert]::ToBase64String([IO.File]::ReadAllBytes('.\AuthKey_XXXXXXXXXX.p8'))
```

Copy each resulting string into the corresponding GitHub secret.

## 4. Run the signed release

Either push a version tag such as `v1.1.1`, or use:

**GitHub → Actions → Release macOS (Developer ID + notarization) → Run workflow**

The workflow creates two release artifacts:

- `ParserOnSocial-mac-arm64-signed`
- `ParserOnSocial-mac-x86_64-signed`

Each contains a ZIP and SHA-256 checksum.

## 5. What the workflow verifies

Before publishing an artifact, CI checks:

1. the main executable architecture;
2. nested Mach-O payloads including the Playwright driver;
3. Developer ID signature and strict code-sign verification;
4. the packaged self-test;
5. real `.app` launch on the correct macOS architecture;
6. Apple notarization result;
7. stapled notarization ticket;
8. `stapler validate`;
9. `spctl --assess --type execute`;
10. final strict code-sign verification.

Only after all of these pass does the workflow upload the final ZIP.

## Important

A notarized Developer ID build cannot be created without your Apple credentials/certificate. The repository is deliberately set up so the certificate private key and notarization API key never enter Git history. The first signed workflow run will fail with a clear message until the seven GitHub secrets above are configured.
