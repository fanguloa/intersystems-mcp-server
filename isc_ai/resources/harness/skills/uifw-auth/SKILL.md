---
name: uifw-auth
description: 'Implement IRIS REST API token-based authentication using @intersystems/auth. USE FOR: login/logout flows, HTTP auth token interceptors, route guards, token storage configuration. DO NOT USE FOR: OAuth/OIDC authentication (use uifw-hs-auth), login page UI (use uifw-hs-basic-login).'
argument-hint: 'Describe your auth needs (login flow, interceptor setup, guard configuration)'
---

# Auth - IRIS Token Authentication

Token-based authentication for IRIS REST APIs with automatic token injection, refresh, and route protection.

## Quick Start

```typescript
// 1. app.module.ts - Import and configure storage
import { AuthModule, AbstractStorageService, SessionStorageService } from '@intersystems/auth';

@NgModule({
  imports: [AuthModule],
  providers: [{ provide: AbstractStorageService, useClass: SessionStorageService }]
})

// 2. Configure endpoints and callbacks
import { AuthService, AuthConfig } from '@intersystems/auth';

constructor(private authService: AuthService, private router: Router) {
  this.authService.configure({
    apiUrl: "/api",
    endpoints: { login: "login", logout: "logout", refresh: "refresh", revoke: "revoke" },
    onUnauthenticatedAccess: (url) => this.router.navigateByUrl("/auth/login?from=" + encodeURIComponent(url)),
    onLogout: () => true,
    onTimeout: () => true,
    allowsUnauthenticatedAccessOnly: (url) => url === "/auth/login",
    isPublicPage: (url) => url.startsWith("/public"),
    refreshCushion: 1000
  });
}

// 3. Login/logout
login(user: string, password: string) {
  this.authService.login(user, password).subscribe({
    next: (success) => { if (success) this.router.navigateByUrl('/dashboard'); }
  });
}

logout() { this.authService.logout(); }
```

## AuthService API

| Method | Description |
|--------|-------------|
| `configure(config: AuthConfig)` | **Required** - Configure endpoints and callbacks before login |
| `login(user?, password?)` | Authenticate user, returns Observable<boolean \| HttpErrorResponse> |
| `logout()` | Log out current user, calls `onLogout()` callback |
| `timeout()` | Time out current user, calls `onTimeout()` callback |
| `isAuthenticated$` | Observable<boolean> - listen to auth status changes |
| `user$` | Observable<string> - listen to current user changes |

## Features

| Feature | Reference File | Purpose |
|---------|----------------|---------|
| HTTP Interceptor | [interceptor.md](references/interceptor.md) | Auto-inject Bearer tokens, handle 401 refresh |
| Route Guards | [guards.md](references/guards.md) | Protect routes, redirect unauthenticated users |
| Token Storage | [storage.md](references/storage.md) | Persist tokens (session/none/custom) |

## Server Requirements

IRIS REST API must have JWT enabled (`JWTEnabled` on web application set to 1). Optionally override token endpoint parameters in `%CSP.REST` subclass: `TokenLoginEndpoint`, `TokenRefreshEndpoint`, `TokenLogoutEndpoint`, `TokenRevokeEndpoint`.
