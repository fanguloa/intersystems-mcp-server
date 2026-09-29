# Token Storage - Persistence Strategies

The `AbstractStorageService` defines how tokens are persisted across browser sessions and page refreshes.

## Built-in Implementations

### SessionStorageService (Recommended)

Uses browser `sessionStorage` - persists across page refreshes, cleared on tab close, isolated per tab.

```typescript
providers: [{ provide: AbstractStorageService, useClass: SessionStorageService }]
```

Storage keys (if appId = "myApp"): `myApp-user`, `myApp-accessToken`, `myApp-refreshToken`

### NoStorageService

Memory only - lost on page refresh. Use for high-security scenarios or testing.

```typescript
providers: [{ provide: AbstractStorageService, useClass: NoStorageService }]
```

## Custom Storage Implementation

Implement `AbstractStorageService` for custom strategies (localStorage, IndexedDB, encryption).

**Interface:** `setTokenInfo(tokenInfo)`, `getTokenInfo()`, `clearTokenInfo()`, `setAppId(id)`

**TokenInfo:** `{ accessToken: string, refreshToken: string, user: string }`

### Example

```typescript
export class LocalStorageService extends AbstractStorageService {
  private getKey = (prop: string) => this._appId ? `${this._appId}-${prop}` : prop;

  setTokenInfo(tokenInfo: TokenInfo) {
    localStorage.setItem(this.getKey('user'), tokenInfo.user);
    localStorage.setItem(this.getKey('accessToken'), tokenInfo.accessToken);
    localStorage.setItem(this.getKey('refreshToken'), tokenInfo.refreshToken);
  }

  getTokenInfo(): TokenInfo | null {
    const user = localStorage.getItem(this.getKey('user'));
    const accessToken = localStorage.getItem(this.getKey('accessToken'));
    const refreshToken = localStorage.getItem(this.getKey('refreshToken'));
    return (user && accessToken && refreshToken) ? { user, accessToken, refreshToken } : null;
  }

  clearTokenInfo() {
    ['user', 'accessToken', 'refreshToken'].forEach(k => localStorage.removeItem(this.getKey(k)));
  }
}

// Register: providers: [{ provide: AbstractStorageService, useClass: LocalStorageService }]
```

## When to Use Which Strategy

| Strategy | Use Case |
|----------|----------|
| **SessionStorageService** | Default choice - persists during session, cleared on tab close |
| **NoStorageService** | High-security apps, kiosks, or when testing auth flows |
| **LocalStorageService** (custom) | Persist tokens across browser restarts (less secure) |
| **EncryptedStorageService** (custom) | Sensitive apps requiring client-side token encryption |

## Multi-App Scenarios

When multiple Angular apps run in the same browser tab (e.g., micro-frontends), use `setAppId()` to prevent token collisions:

```typescript
constructor(
  private authService: AuthService,
  private storageService: AbstractStorageService
) {
  this.storageService.setAppId('app1');
  this.authService.configure(authConfig);
}
```

Without `setAppId()`, storage keys are not prefixed and may conflict between apps.
