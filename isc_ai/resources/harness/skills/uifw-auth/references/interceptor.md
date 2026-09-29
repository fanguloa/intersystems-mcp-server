# AuthInterceptorService - Automatic Token Injection

The `AuthInterceptorService` automatically attaches Bearer tokens to outgoing HTTP requests and handles token refresh on 401 responses.

## How It Works

**Automatic Setup** - The interceptor is automatically registered when you import `AuthModule` and call `authService.configure()`.

**Token Injection** - Adds `Authorization: Bearer <access-token>` header to all requests except:
- Login endpoint
- Refresh endpoint
- URLs excluded by `shouldAttachToken()` callback

**401 Handling** - On 401 responses (except from logout/revoke endpoints):
1. Automatically calls refresh endpoint
2. Retries original request with new token
3. If refresh fails, propagates error

## Control Token Attachment

By default, tokens are attached to all requests. To selectively attach tokens:

```typescript
const authConfig: AuthConfig = {
  // ... other config
  shouldAttachToken: (url: string) => {
    // Only attach token to requests to your API
    return url.startsWith('/api/');
  }
};
```

**Use Case**: Making HTTP requests to external servers that require different authentication.

## Token Refresh Flow

```
Request → 401 Response
  ↓
Call /refresh endpoint
  ↓
Success → Update stored tokens → Retry original request
  ↓
Failure → Propagate error (user likely needs to re-login)
```

The interceptor waits for any in-progress token refresh before proceeding, preventing duplicate refresh requests from concurrent API calls.

## Error Handling

```typescript
this.http.get('/api/protected').subscribe({
  next: (data) => console.log(data),
  error: (err: HttpErrorResponse) => {
    // err could be from original request or from failed refresh
    if (err.status === 401) {
      console.log('Token refresh failed, redirect to login');
    }
  }
});
```

## Token Storage

Tokens are retrieved from the configured `AbstractStorageService` implementation:
- `SessionStorageService` - persists across page refreshes
- `NoStorageService` - in-memory only (lost on refresh)

See [storage.md](storage.md) for details.

## No Manual Token Management Needed

You do NOT need to:
- Manually add Authorization headers
- Check if token is expired before requests
- Handle 401s and call refresh yourself

The interceptor handles all of this automatically.
