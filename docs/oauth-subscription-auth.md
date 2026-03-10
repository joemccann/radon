# Using LLM Subscriptions via OAuth in Native Mac Apps

## Overview

Native Mac apps can authenticate against a user's existing LLM subscription (Claude Pro/Max, ChatGPT Plus/Pro, Gemini, GitHub Copilot) instead of requiring separate API keys. This is done via **OAuth 2.0 with PKCE** — the same protocol used for "Sign in with Google/Apple/GitHub" flows, adapted here to obtain short-lived API access tokens tied to the user's consumer subscription.

**Result:** Users pay nothing extra. All inference runs against their existing subscription quota.

---

## Architecture

```
┌─────────────┐     1. Open browser      ┌──────────────────┐
│  Native Mac  │ ──────────────────────── │  Provider OAuth   │
│     App      │                          │  (claude.ai,      │
│              │     2. User logs in &    │   auth.openai.com) │
│              │        approves scopes   │                    │
│              │ ◄─────────────────────── │                    │
│              │     3. Auth code          └──────────────────┘
│              │
│              │     4. Exchange code      ┌──────────────────┐
│              │        for tokens         │  Token Endpoint   │
│              │ ──────────────────────── │  (provider API)   │
│              │                          │                    │
│              │     5. Access + Refresh   │                    │
│              │ ◄─────────────────────── │                    │
│              │                          └──────────────────┘
│              │
│              │     6. Call inference     ┌──────────────────┐
│              │        with access token  │  Inference API    │
│              │ ──────────────────────── │  (api.anthropic,  │
│              │                          │   api.openai.com)  │
│              │     7. Response           │                    │
│              │ ◄─────────────────────── │                    │
└─────────────┘                          └──────────────────┘
```

---

## Supported Providers

| Provider | Subscription Tier | OAuth Authorize URL | Token URL | Scopes |
|----------|-------------------|---------------------|-----------|--------|
| **Anthropic** | Claude Pro / Max | `https://claude.ai/oauth/authorize` | `https://console.anthropic.com/v1/oauth/token` | `org:create_api_key user:profile user:inference` |
| **OpenAI** | ChatGPT Plus / Pro | `https://auth.openai.com/oauth/authorize` | `https://auth.openai.com/oauth/token` | `openid profile email offline_access` |
| **Google Gemini** | Gemini subscription | Google's standard OAuth | Google's token endpoint | Google-standard scopes |
| **GitHub Copilot** | Copilot Individual / Business | GitHub OAuth | GitHub token endpoint | Copilot-specific scopes |

---

## OAuth 2.0 + PKCE Flow (Step by Step)

### Why PKCE?

Native apps cannot securely store a client secret (the binary can be decompiled). **PKCE (Proof Key for Code Exchange)** replaces the client secret with a cryptographic challenge that's generated fresh for each login attempt. This is the OAuth-recommended flow for public/native clients.

### Step 1: Generate PKCE Pair

Before each login, generate a random `code_verifier` and derive a `code_challenge` from it.

```swift
import CryptoKit
import Foundation

func generatePKCE() -> (verifier: String, challenge: String) {
    // 32 random bytes → base64url-encoded verifier
    var bytes = [UInt8](repeating: 0, count: 32)
    _ = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
    let verifier = Data(bytes)
        .base64EncodedString()
        .replacingOccurrences(of: "+", with: "-")
        .replacingOccurrences(of: "/", with: "_")
        .replacingOccurrences(of: "=", with: "")

    // SHA-256 hash of verifier → base64url-encoded challenge
    let hash = SHA256.hash(data: Data(verifier.utf8))
    let challenge = Data(hash)
        .base64EncodedString()
        .replacingOccurrences(of: "+", with: "-")
        .replacingOccurrences(of: "/", with: "_")
        .replacingOccurrences(of: "=", with: "")

    return (verifier, challenge)
}
```

### Step 2: Build Authorization URL & Open Browser

Construct the authorization URL with all required parameters and open it in the user's default browser.

```swift
func buildAuthURL(provider: Provider, challenge: String, state: String) -> URL {
    var components = URLComponents(string: provider.authorizeURL)!
    components.queryItems = [
        URLQueryItem(name: "response_type", value: "code"),
        URLQueryItem(name: "client_id", value: provider.clientID),
        URLQueryItem(name: "redirect_uri", value: provider.redirectURI),
        URLQueryItem(name: "scope", value: provider.scopes),
        URLQueryItem(name: "code_challenge", value: challenge),
        URLQueryItem(name: "code_challenge_method", value: "S256"),
        URLQueryItem(name: "state", value: state),
    ]
    return components.url!
}

// Open in default browser
NSWorkspace.shared.open(authURL)
```

**Provider-specific parameters:**

| Provider | Client ID | Redirect URI | Extra Params |
|----------|-----------|--------------|--------------|
| Anthropic | Public client ID (see below) | `https://console.anthropic.com/oauth/code/callback` | `code=true` |
| OpenAI | `app_EMoamEEZ73f0CkXaXp7hrann` | `http://localhost:1455/auth/callback` | `id_token_add_organizations=true`, `codex_cli_simplified_flow=true` |

> **Note on Client IDs:** Anthropic and OpenAI have published OAuth client IDs for CLI/native app use. These are public clients (no client secret). The security comes from PKCE + redirect URI validation.

### Step 3: Capture the Authorization Code

Two strategies, depending on the provider's redirect URI:

#### Strategy A: Local HTTP Server (OpenAI)

OpenAI redirects to `http://localhost:1455/auth/callback`. Start a local HTTP server before opening the browser.

```swift
import Foundation

class OAuthCallbackServer {
    var server: HTTPServer?
    var continuation: CheckedContinuation<String, Error>?

    func start() async throws -> String {
        return try await withCheckedThrowingContinuation { continuation in
            self.continuation = continuation
            // Start HTTP server on localhost:1455
            // When GET /auth/callback?code=XXX&state=YYY arrives:
            //   1. Validate state matches
            //   2. Extract code
            //   3. Return success HTML to browser
            //   4. Resume continuation with the code
        }
    }
}
```

#### Strategy B: Manual Paste (Anthropic)

Anthropic redirects to their own domain and shows the user a code. The user copies and pastes it back into your app.

```swift
// After opening browser, show a text field:
// "Paste the authorization code from your browser:"
let code = await promptUserForCode()
```

#### Strategy C: Custom URL Scheme (Universal)

Register a custom URL scheme (e.g., `myapp://oauth/callback`) and handle it via `NSAppleEventManager` or SwiftUI's `onOpenURL`.

```swift
// Info.plist: Register URL scheme
// <key>CFBundleURLTypes</key> → myapp://

// SwiftUI
.onOpenURL { url in
    guard url.scheme == "myapp",
          url.host == "oauth",
          let code = URLComponents(url: url, resolvingAgainstBaseURL: false)?
              .queryItems?.first(where: { $0.name == "code" })?.value
    else { return }
    handleAuthCode(code)
}
```

### Step 4: Exchange Code for Tokens

POST the authorization code + PKCE verifier to the token endpoint. You receive an access token (short-lived) and a refresh token (long-lived).

```swift
struct TokenResponse: Codable {
    let access_token: String
    let refresh_token: String
    let expires_in: Int      // seconds until access token expires
    let token_type: String   // "Bearer"
}

func exchangeCodeForTokens(
    provider: Provider,
    code: String,
    verifier: String
) async throws -> TokenResponse {
    var request = URLRequest(url: URL(string: provider.tokenURL)!)
    request.httpMethod = "POST"
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")

    let body: [String: String] = [
        "grant_type": "authorization_code",
        "client_id": provider.clientID,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": provider.redirectURI,
    ]
    request.httpBody = try JSONEncoder().encode(body)

    // OpenAI uses application/x-www-form-urlencoded instead:
    // request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
    // request.httpBody = body.map { "\($0)=\($1)" }.joined(separator: "&").data(using: .utf8)

    let (data, response) = try await URLSession.shared.data(for: request)
    guard (response as? HTTPURLResponse)?.statusCode == 200 else {
        throw OAuthError.tokenExchangeFailed(String(data: data, encoding: .utf8) ?? "")
    }
    return try JSONDecoder().decode(TokenResponse.self, from: data)
}
```

### Step 5: Store Tokens Securely

Use the macOS **Keychain** for token storage. Never store tokens in plain text files, UserDefaults, or plist.

```swift
import Security

struct TokenStore {
    private let service = "com.yourapp.oauth"

    func save(provider: String, credentials: OAuthCredentials) throws {
        let data = try JSONEncoder().encode(credentials)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: provider,
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
        ]
        // Delete existing, then add
        SecItemDelete(query as CFDictionary)
        let status = SecItemAdd(query as CFDictionary, nil)
        guard status == errSecSuccess else {
            throw OAuthError.keychainWriteFailed(status)
        }
    }

    func load(provider: String) throws -> OAuthCredentials? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: provider,
            kSecReturnData as String: true,
        ]
        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess, let data = result as? Data else { return nil }
        return try JSONDecoder().decode(OAuthCredentials.self, from: data)
    }
}

struct OAuthCredentials: Codable {
    let accessToken: String
    let refreshToken: String
    let expiresAt: Date       // Date.now + expires_in - 5min buffer
    let accountId: String?    // OpenAI includes this in the JWT
}
```

### Step 6: Use Access Token for Inference

Include the access token as a Bearer token in API requests. The provider's inference API treats it identically to an API key — but billing goes to the user's subscription.

```swift
func callInference(
    provider: Provider,
    accessToken: String,
    messages: [Message]
) async throws -> InferenceResponse {
    var request = URLRequest(url: URL(string: provider.inferenceURL)!)
    request.httpMethod = "POST"
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")

    // Anthropic
    // POST https://api.anthropic.com/v1/messages
    // Header: x-api-key: <access_token>  (Anthropic uses x-api-key, not Authorization)
    // Header: anthropic-version: 2023-06-01

    // OpenAI
    // POST https://api.openai.com/v1/chat/completions
    // Header: Authorization: Bearer <access_token>

    let body = InferenceRequest(model: provider.defaultModel, messages: messages)
    request.httpBody = try JSONEncoder().encode(body)

    let (data, _) = try await URLSession.shared.data(for: request)
    return try JSONDecoder().decode(InferenceResponse.self, from: data)
}
```

### Step 7: Auto-Refresh Expired Tokens

Access tokens expire (typically in 1 hour). Before every API call, check expiry and refresh if needed.

```swift
class AuthManager {
    private let tokenStore = TokenStore()
    private let refreshLock = NSLock()  // Prevent concurrent refresh races

    func getValidAccessToken(provider: String) async throws -> String {
        guard let creds = try tokenStore.load(provider: provider) else {
            throw OAuthError.notLoggedIn
        }

        // 5-minute buffer before actual expiry
        if creds.expiresAt > Date.now.addingTimeInterval(-300) {
            return creds.accessToken
        }

        // Token expired — refresh it
        return try await refreshToken(provider: provider, creds: creds)
    }

    private func refreshToken(
        provider: String,
        creds: OAuthCredentials
    ) async throws -> String {
        refreshLock.lock()
        defer { refreshLock.unlock() }

        // Re-check after acquiring lock (another thread may have refreshed)
        if let fresh = try tokenStore.load(provider: provider),
           fresh.expiresAt > Date.now.addingTimeInterval(-300) {
            return fresh.accessToken
        }

        var request = URLRequest(url: URL(string: providerConfig(provider).tokenURL)!)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let body: [String: String] = [
            "grant_type": "refresh_token",
            "client_id": providerConfig(provider).clientID,
            "refresh_token": creds.refreshToken,
        ]
        request.httpBody = try JSONEncoder().encode(body)

        let (data, response) = try await URLSession.shared.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else {
            // Refresh failed — user needs to re-login
            throw OAuthError.refreshFailed
        }

        let tokenResponse = try JSONDecoder().decode(TokenResponse.self, from: data)
        let newCreds = OAuthCredentials(
            accessToken: tokenResponse.access_token,
            refreshToken: tokenResponse.refresh_token,
            expiresAt: Date.now.addingTimeInterval(
                TimeInterval(tokenResponse.expires_in) - 300
            ),
            accountId: creds.accountId
        )
        try tokenStore.save(provider: provider, credentials: newCreds)
        return newCreds.accessToken
    }
}
```

---

## Provider Configuration Reference

### Anthropic (Claude Pro/Max)

```swift
let anthropicConfig = ProviderConfig(
    id: "anthropic",
    name: "Claude Pro/Max",
    clientID: "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
    authorizeURL: "https://claude.ai/oauth/authorize",
    tokenURL: "https://console.anthropic.com/v1/oauth/token",
    redirectURI: "https://console.anthropic.com/oauth/code/callback",
    scopes: "org:create_api_key user:profile user:inference",
    inferenceURL: "https://api.anthropic.com/v1/messages",
    contentType: .json,                    // Token endpoint uses JSON
    authCodeDelivery: .manualPaste,        // User pastes code back
    extraAuthParams: ["code": "true"],     // Required extra param
)
```

### OpenAI (ChatGPT Plus/Pro)

```swift
let openaiConfig = ProviderConfig(
    id: "openai",
    name: "ChatGPT Plus/Pro",
    clientID: "app_EMoamEEZ73f0CkXaXp7hrann",
    authorizeURL: "https://auth.openai.com/oauth/authorize",
    tokenURL: "https://auth.openai.com/oauth/token",
    redirectURI: "http://localhost:1455/auth/callback",
    scopes: "openid profile email offline_access",
    inferenceURL: "https://api.openai.com/v1/chat/completions",
    contentType: .formURLEncoded,          // Token endpoint uses form encoding
    authCodeDelivery: .localServer(1455),  // Localhost callback server
    extraAuthParams: [
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    ],
)
```

---

## API Key Resolution Priority

When your app needs to authenticate with a provider, check sources in this order:

| Priority | Source | Type | Use Case |
|----------|--------|------|----------|
| 1 | User override | Static key | Advanced users who prefer API keys |
| 2 | Keychain (OAuth) | OAuth token | **Primary — subscription-based auth** |
| 3 | Environment variable | Static key | Developer/CI environments |

```swift
func resolveAPIKey(provider: String) async throws -> String {
    // 1. Check user override (Settings → API Keys)
    if let override = UserDefaults.standard.string(forKey: "apiKey.\(provider)") {
        return override
    }

    // 2. Check OAuth credentials in Keychain
    if let creds = try tokenStore.load(provider: provider) {
        return try await authManager.getValidAccessToken(provider: provider)
    }

    // 3. Check environment variable
    let envKey = "\(provider.uppercased())_API_KEY"  // e.g., ANTHROPIC_API_KEY
    if let envValue = ProcessInfo.processInfo.environment[envKey] {
        return envValue
    }

    throw OAuthError.noCredentials(provider)
}
```

---

## Comparison: Subscription OAuth vs API Keys

| | API Keys | OAuth Subscription |
|---|---|---|
| **Billing** | Pay-per-token, metered API billing | Included in existing subscription ($20-200/mo) |
| **User cost** | Additional expense on top of subscription | Zero additional cost |
| **Setup** | User creates key at provider console | One-click browser login |
| **Security** | Static secret, must be rotated manually | Short-lived tokens, auto-refreshed |
| **Token lifetime** | Permanent until revoked | ~1 hour access, long-lived refresh |
| **Client secret** | Required for confidential clients | Not needed (PKCE replaces it) |
| **Rate limits** | API tier limits | Subscription tier limits (may differ) |
| **Best for** | Server-side apps, CI/CD | Native desktop/mobile apps |

---

## Security Considerations

1. **Never embed client secrets** in native app binaries — use PKCE instead
2. **Store tokens in Keychain**, not UserDefaults, files, or plist
3. **Use `kSecAttrAccessibleWhenUnlockedThisDeviceOnly`** to prevent token extraction from backups
4. **Validate the `state` parameter** on callback to prevent CSRF attacks
5. **Handle refresh failures gracefully** — prompt re-login, don't crash
6. **Use a lock/mutex around token refresh** to prevent concurrent refresh races
7. **Buffer expiry by ~5 minutes** to avoid using tokens that expire mid-request
8. **Clear tokens on logout** — delete from Keychain completely

---

## Error Handling

| Error | Cause | Recovery |
|-------|-------|----------|
| Token exchange failed (400/401) | Invalid code, expired code, PKCE mismatch | Re-initiate login flow |
| Refresh failed (401) | Refresh token revoked or expired | Prompt full re-login |
| Inference 401 | Access token expired between check and use | Refresh and retry once |
| Inference 429 | Subscription rate limit hit | Back off, show user their plan limits |
| Inference 403 | Subscription inactive or insufficient tier | Show upgrade prompt |
| Localhost port in use | Another instance running | Try alternative port or use manual paste fallback |

---

## Minimal Implementation Checklist

- [ ] PKCE generator (verifier + SHA-256 challenge)
- [ ] Browser-open for authorization URL
- [ ] Auth code capture (localhost server and/or manual paste)
- [ ] Token exchange (code → access + refresh tokens)
- [ ] Keychain storage (save/load/delete)
- [ ] Token refresh with lock (prevent races)
- [ ] Expiry check before every API call
- [ ] Inference call with Bearer token
- [ ] Logout (clear Keychain)
- [ ] Error handling (refresh failures → re-login prompt)
