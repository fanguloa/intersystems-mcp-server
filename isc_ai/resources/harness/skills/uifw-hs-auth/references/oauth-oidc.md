# OAuth 2.0 / OpenID Connect Configuration

@healthshare/auth supports full OAuth 2.0 authorization code flow with OpenID Connect 1.0. The library auto-detects whether OAuth is enabled via the `/auth/status` endpoint and configures itself accordingly.

## Auto-Detection

App calls `/auth/status` — if `is_oauth_enabled` is true, uses OAuth; otherwise uses IRIS instance auth.

## OAuth Flow Steps

### 1. Discovery and Initialization

Library fetches OIDC discovery document from issuer to get authorization, token, and end session endpoints.

### 2. Login (Authorization Code Flow)

```typescript
await this.authService.login('/target-url');
```

**Process**:
1. App calls `/auth/login` endpoint (optional backend customization step)
2. Redirects to OAuth server `/authorize` endpoint with:
   - `client_id`: From metadata
   - `response_type`: `code`
   - `scope`: From metadata (e.g., `openid profile`)
   - `redirect_uri`: One of the allowed redirect URIs from metadata
   - `state`: Encoded redirect URL + optional app state
   - `aud`: REST API URL (production mode only)
3. User enters credentials at OAuth server
4. OAuth server redirects back with authorization code
5. Library exchanges code for tokens at `/token` endpoint
6. Stores access token, refresh token, and ID token in session storage
7. Redirects to original target URL

### 3. Token Management

**Access Token**: Bearer token auto-attached via interceptor  
**Refresh Token**: Auto-refreshed on expiry  
**ID Token**: JWT with user claims (required for logout)

### 4. Logout (RP-Initiated Logout)

Calls `/end_session` with `id_token_hint`, `post_logout_redirect_uri`, and `state`. OAuth server ends session (SSO) and redirects back.

### 5. Front Channel Logout

When another SSO app logs out, OAuth server calls `frontchannel_logout_uri` in hidden iframe. Library sets localStorage flag to trigger soft logout across all tabs.

### 6. Timeout

Revokes token for current tab only (no SSO). Calls `/revoke` endpoint, redirects to timeout page. Other tabs remain authenticated.

## Advanced OAuth Config

```typescript
oauthConfig: {
  appState: { returnToFeature: 'dashboard' },  // Persists through redirects
  stateSeparator: '|',  // Default: "~"
  onConfigureOAuthService: (statusResponse, oauthConfig) => {
    oauthConfig.customQueryParams = { prompt: 'login' };
    return oauthConfig;
  }
}

// Access app state after login
this.oauthService.appState$.subscribe(state => {
  // state = { returnToFeature: 'dashboard' }
});
```

Note: App state only emits once after login — store it immediately or it's lost on reload.

## Metadata from /auth/status

**HSMetadata** (when OAuth enabled):

```typescript
{
  is_oauth_enabled: true,
  timeout: 120,  // seconds
  client_id: "my-angular-app",
  issuer: "https://auth.example.com/oauth2",
  scopes: "openid profile email",
  redirect_uris: ["http://localhost:4200/login", "https://app.example.com/login"],
  frontchannel_logout_uri: "http://localhost:4200/logout",
  post_logout_redirect_uris: ["http://localhost:4200/logout"]
}
```

## Security

**Token Attachment**: Always implement `shouldAttachToken` to prevent CSRF: `url.startsWith('/api')`

**Production**: Sends `aud` parameter to OAuth server  
**Development**: Omits `aud` (proxy mismatch), requires server DevMode settings

## Error Handling

Auto-handles: token refresh errors (triggers logout), invalid state/nonce (retries login), missing ID token (uses login).

## SAML Integration

Auto-detects SAML parameters (`SAMLResponse`, `SAMLart`) and switches to POST for large responses.

## Backend Requirements

- `GET /auth/status` — metadata and user info
- `POST /auth/login` — (optional) customize OAuth params
- OAuth 2.0 client with correct redirect URIs
- OIDC discovery at `<issuer>/.well-known/openid-configuration`
