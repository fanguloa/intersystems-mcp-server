# AuthGuard - Route Protection

The `AuthGuard` automatically protects all routes from unauthenticated access and redirects authenticated users away from login-only pages.

## How It Works

**Automatic Setup** - When you call `authService.configure()`, the `AuthGuard` is automatically added to ALL routes with:
- `canLoad` - for lazy-loaded modules
- `canActivate` - for components and lazy modules on subsequent navigation
- `canActivateChild` - for child routes

**No Manual Route Configuration Needed** - You do NOT need to add `canActivate: [AuthGuard]` to your route definitions.

## Auth Check Flow

When a user navigates to any route, `AuthGuard` performs these checks:

### 1. Public Pages
If `isPublicPage(url)` returns `true`:
- Allow access regardless of authentication status
- Example: documentation, marketing pages

### 2. Login-Only Pages  
If `allowsUnauthenticatedAccessOnly(url)` returns `true`:
- **Unauthenticated users**: Allow access
- **Authenticated users**: Call `onInvalidAccess(url)` callback, block access
- Use for: login page, timeout page, password reset

### 3. Protected Pages (default)
If neither callback returns `true`:
- **Authenticated users**: Allow access
- **Unauthenticated users**: Call `onUnauthenticatedAccess(url)` callback, block access

## Configuration

```typescript
const authConfig: AuthConfig = {
  isPublicPage: (url) => url.startsWith('/public') || url === '/',
  allowsUnauthenticatedAccessOnly: (url) => url === '/auth/login' || url === '/auth/timeout',
  onUnauthenticatedAccess: (url) => this.router.navigateByUrl('/auth/login?from=' + encodeURIComponent(url)),
  onInvalidAccess: (url) => this.router.navigateByUrl('/dashboard')
};
```

## Login Flow Example

```typescript
// User navigates to /dashboard while not logged in
// → AuthGuard blocks access
// → onUnauthenticatedAccess('/dashboard') called
// → Redirects to /auth/login?from=%2Fdashboard

// After successful login:
login(username, password) {
  this.authService.login(username, password).subscribe({
    next: (success) => {
      if (success) {
        // Get the 'from' query param and redirect back
        const from = this.route.snapshot.queryParams['from'] || '/dashboard';
        this.router.navigateByUrl(decodeURIComponent(from));
      }
    }
  });
}
```

## Logout Integration

After calling `authService.logout()`:
- The `onLogout()` callback is invoked
- If `onLogout()` returns `true`, auth checks are run on the current URL
- If the current page requires authentication, `onUnauthenticatedAccess()` is called
- Typically redirects user back to login page

```typescript
const authConfig: AuthConfig = {
  onLogout: (serverResponse, url) => {
    console.log('User logged out from:', url);
    return true;  // Run auth checks, will redirect if on protected page
  }
};
```

## Multiple Route Guards

If you have other route guards, `AuthGuard` is added to the **beginning** of the guard array to ensure authentication is checked first before any other route logic.
