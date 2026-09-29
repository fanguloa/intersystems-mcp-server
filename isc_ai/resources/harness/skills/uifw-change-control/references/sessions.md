# Change Sessions and Uncommitted Queue

## Session Lifecycle

A change session is active when a session key is set. The session key is stored in localStorage (`CCR_SESSION_KEY`) and attached to HTTP requests via the interceptor.

**Flow:**
1. User starts a session (enters session key)
2. Session key stored in ngrx state + localStorage
3. All HTTP requests include `X-CHANGESESSION` header
4. User makes changes (they appear in uncommitted queue)
5. User commits changes to CCR or reverts them
6. User ends session (clears session key)

## SessionManagementComponent

Minimal component that prompts users to start a session when needed.

**Usage:** Place at top of change-controlled pages
```html
<fr-change-control-session-management></fr-change-control-session-management>
```

**Behavior:**
- Only renders when change control is enabled AND no session key is active
- Displays button to start a session
- Does NOT show "leave session" option

## ChangeSessionManagementComponent

Full-featured component for managing sessions and uncommitted changes.

**Usage:** Add as routed page
```typescript
{ path: "change-session-management", component: ChangeSessionManagementComponent }
```

**Features:** Start/end session, view uncommitted queue, filter by session/user, commit/revert changes, view diffs

**Linking:** Set `changeSessionManagementRoute` in config to enable linking from InstanceInfoComponent

## InstanceInfoComponent

Displays instance metadata and active session key.

**Usage:** `<fr-change-control-instance-info></fr-change-control-instance-info>`

**Displays:** Server, instance, namespace, environment (optional), session key (links to manager if route configured)

**Placement:** Directly below header, always visible

## SessionKeyInterceptor

HTTP interceptor that attaches session key to requests.

**Setup:** Add to app.module.ts providers:
```typescript
{ provide: HTTP_INTERCEPTORS, useClass: SessionKeyInterceptor, multi: true }
```

**Behavior:** Reads session key from ngrx store, adds `X-CHANGESESSION` header to all HTTP requests. If request already has session key header, leaves it (allows overrides for committing to different sessions).

## ChangeControlService API

### Configuration
```typescript
configure(config: ChangeControlConfig): void
```

**ChangeControlConfig:**
```typescript
interface ChangeControlConfig {
  onlyTier2: boolean;  // Filter to tier 2 changes only
  changeSessionManagementRoute?: string;  // Link from instance info
  restApiBaseUrl?: string;  // Default: "api"
  onError: (error: HttpErrorResponse) => void;  // Error handler
  currentUser$: Observable<string>;  // Current logged-in user
}
```

### Observables
```typescript
isLocked$: Observable<boolean>
```
Emits true when edits should be disabled:
- Change control enabled AND (system locked OR no active session)

Use to hide/disable add/edit/delete buttons in your app.

```typescript
instanceInfo$: Observable<InstanceInfo>
```
Instance metadata displayed in InstanceInfoComponent. Useful for general app purposes.

```typescript
interface InstanceInfo {
  server: string;
  instance: string;
  namespace: string;
  environment?: string;
}
```

## FindGuidComponent

Debug tool for finding changes by GUID in higher environments.

**Usage:** `{ path: "find-guid", component: FindGuidComponent }`

**Purpose:** Troubleshoot item set load failures by searching for a GUID and viewing change information.
