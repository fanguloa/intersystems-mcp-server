# Authentication State Management

@healthshare/auth provides reactive observables for tracking authentication state, user information, and server metadata throughout the application lifecycle.

## Core Observables

### isAuthenticated$

`Observable<boolean>` — tracks authentication state. Emits after configuration. Auto-switches between OAuth and token auth.

```typescript
<div *ngIf="authService.isAuthenticated$ | async">...</div>
```

### userInfo$

`Observable<HSUserInfo>` — emits when authenticated. Contains `username` and `accessMap` (recursive permission structure: `READ` | `WRITE`).

```typescript
<span *ngIf="authService.userInfo$ | async as user">
  Welcome, {{ user.username }}
</span>
```

### metadata$

`Observable<HSMetadata>` — emits after `/auth/status`. Contains `is_oauth_enabled`, `timeout`, `client_id`, `issuer`, `scopes`, redirect URIs.

## Session Lifecycle

**Login**: Redirects to login page → `isAuthenticated$` emits `true` → `userInfo$` emits → redirects to target URL

**Logout**: Revokes tokens → `isAuthenticated$` emits `false` → `userInfo$` cleared → redirects through OAuth server

**Soft Logout**: Removes tokens from storage (no server revoke) → `isAuthenticated$` emits `false`

**Timeout**: Revokes current tab only → `isAuthenticated$` emits `false` → redirects to timeout page

## Timeout Integration

Uses `@intersystems/timeout` with auto-config from server metadata.

**Warning Time**: 60s before timeout (or halfway if timeout ≤ 2min)

**Modal**: "Stay Logged In" (continues) or "Log Out" (calls `onLogoutCallback` — full SSO logout)

**Expiry**: Calls `onTimeoutCallback` (current tab only, no SSO)

**Disable Modal**: Set `disableTimeoutWarningModal: true` (useful for child apps)

## Error Handling

**Token Refresh Error**: Auto-triggers `logout()` (SSO)

**Network Error**: Clears tokens, retries `/auth/status` without token

**401/403**: OAuth interceptor calls `softLogout()` → guard redirects to login

## Multi-Tab Synchronization

**OAuth Front Channel**: Logout in one app triggers soft logout across all tabs via localStorage event

**Instance Auth**: Shared session cookie — logout affects all tabs, timeout does NOT

## Best Practices

**Block Render**: Use `authLoaded` flag + `*ngIf` to prevent flashing before auth checks complete.

**Combine Observables**: Use `combineLatest([isAuthenticated$, userInfo$])` for view models.

**Route Guards**: Auto-applied during `configure()` — `HSOAuthGuard` (auth flow) + `HSAuthorizationGuard` (permission check).
