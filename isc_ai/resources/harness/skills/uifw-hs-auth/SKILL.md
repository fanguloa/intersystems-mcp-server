---
name: uifw-hs-auth
description: 'Implement HealthShare authentication with OAuth 2.0 / OpenID Connect using @healthshare/auth. USE FOR: HealthShare app authentication, OAuth/OIDC login flows, IRIS instance auth, session state observables, user info retrieval, token management. DO NOT USE FOR: basic IRIS token auth without OAuth (use uifw-auth), login page UI (use uifw-hs-basic-login).'
argument-hint: 'Describe your HealthShare auth needs (OAuth setup, login flow, session management)'
---

# HS Auth — HealthShare OAuth/OIDC Authentication

HealthShare SDK for authentication and authorization. Supports both IRIS instance authentication AND OAuth 2.0 + OpenID Connect 1.0 flows. Auto-detects which mode to use based on server metadata.

**Package**: `@healthshare/auth`

## Quick Start

```typescript
// app.module.ts
import { HSAuthModule } from '@healthshare/auth';
HSAuthModule.forRoot("<unique-app-key>")  // Key must be unique per app

// app.component.html
<fr-notification></fr-notification>
<lib-spinner></lib-spinner>
<div *ngIf="authLoaded">...</div>

// app-routing.module.ts
RouterModule.forRoot(routes, { initialNavigation: "disabled" })

// app.component.ts
async ngOnInit() {
  await this.authService.configure({
    inProductionMode: environment.production,
    timeoutConfig: {
      onLogoutCallback: () => this.authService.logout(),
      onTimeoutCallback: () => this.authService.timeout()
    },
    shouldAttachToken: (url) => url.startsWith('/api'),
    onResourcePermissionCheck: () => true
  });
  this.router.initialNavigation();
  this.authLoaded = true;
}
```

## HSAuthService API

### Methods

| Method | Description |
|--------|-------------|
| `configure(config)` | **Required** — must be called in `app.component.ts` to initialize the service |
| `login(redirectUrl?)` | Redirect user to login page (OAuth server or local login page) |
| `logout()` | Revoke tokens and end session (Single Sign Out if SSO enabled) |
| `softLogout()` | Remove tokens from browser storage WITHOUT revoking on server |
| `timeout()` | Timeout current tab only (no SSO logout), redirect to timeout page |

### Observables

| Observable | Type | Description |
|------------|------|-------------|
| `isAuthenticated$` | `Observable<boolean>` | Whether user is currently authenticated |
| `userInfo$` | `Observable<HSUserInfo>` | User info from `/auth/status` (emits once authenticated) |
| `metadata$` | `Observable<HSMetadata>` | Server metadata (OAuth config, timeout, client ID) |

**Usage**: `<div *ngIf="authService.isAuthenticated$ | async">...</div>`

## Features

| Feature | Reference | Description |
|---------|-----------|-------------|
| OAuth/OIDC | `oauth-oidc.md` | OAuth 2.0 authorization code flow, OIDC discovery, token refresh |
| Auth State | `auth-state.md` | Authentication observables, timeout integration, session events |

## Key Imports

```typescript
import { HSAuthModule, HSAuthService, HSAuthConfig } from '@healthshare/auth';
import { EAuthRouteNames, AUTH_ROUTE_DESCRIPTIONS } from '@healthshare/auth';
```

## Built-in Routes

Auto-registered: `/login`, `/logout`, `/timeout`, `/unauthorized` (access via `EAuthRouteNames` enum)
