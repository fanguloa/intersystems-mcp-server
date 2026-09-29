---
name: uifw-timeout
description: 'Handle session timeout and inactivity detection using @intersystems/timeout. USE FOR: session timeout warnings, inactivity detection (clicks/keypresses), timeout warning modals, ping/logout/timeout callbacks, auto-stop on navigation. DO NOT USE FOR: HTTP request timeouts, debounce/throttle, countdown timers.'
argument-hint: 'Describe your timeout needs (duration, warning behavior, callbacks)'
---

# @intersystems/timeout

Session timeout and inactivity detection for Angular applications. Monitors clicks and keypresses, displays warning modals before timeout, and provides configurable callbacks for ping, logout, and timeout actions.

## Quick Start

### 1. Install and Import

```bash
npm install @intersystems/timeout --registry <artifactory URL>
```

```typescript
// app.module.ts
import { TimeoutModule } from '@intersystems/timeout';

@NgModule({
  imports: [TimeoutModule]
})
```

Requires: `@intersystems/confirmation-dialog` (import `ConfirmationDialogModule` in root `app.module.ts`)

### 2. Configure and Initialize

```typescript
// app.component.ts
import { TimeoutService, TimeoutConfig, TimeoutPolicy } from '@intersystems/timeout';

ngOnInit() {
  // Configure callbacks and behavior
  const config: TimeoutConfig = {
    onLogoutCallback: () => this.handleLogout(),
    onTimeoutCallback: () => this.handleTimeout(),
    warnTime: 300,              // Show warning 5 minutes before timeout
    pingInterval: 300           // Ping server every 5 minutes
  };
  this.timeoutService.configureTimeoutService(config);

  // Start the timeout watch
  const policy: TimeoutPolicy = { timeoutLength: 900 }; // 15 minutes
  this.timeoutService.initTimeoutWatch(policy);
}
```

## TimeoutService API

### Methods

- `configureTimeoutService(config: TimeoutConfig)` - Set timeout behavior (callbacks, intervals, modal config)
- `initTimeoutWatch(policy?: TimeoutPolicy)` - Start monitoring inactivity (call on login/refresh)
- `stopTimeoutWatch()` - Stop monitoring (e.g., on informational pages without auth)
- `resetTimeoutTimer()` - Manually reset the timeout counter (automatically called on click/keypress)

### Activity Detection

Per UX direction, **only clicks and keypresses** count as activity. Mouse movement does not reset the timer.

## TimeoutConfig Options

```typescript
interface TimeoutConfig {
  pingObservable?(): Observable<any>;             // Server ping function
  onLogoutCallback?(): any;                       // Called when user clicks logout in modal
  onTimeoutCallback?(): any;                      // Called when timer reaches zero
  resetTimeoutTimerCallback?(): any;              // Called on activity (for iframe scenarios)
  resetTimeoutTimerCallbackInterval?: number;     // Throttle interval for reset callback (ms, default: 60000)
  pingInterval?: number;                          // Ping frequency (seconds, default: 300)
  warnTime?: number;                              // Show warning N seconds before timeout (default: 300)
  warningModalConfig?: ConfirmationDialogConfig;  // Customize modal (title, buttons, text)
}

interface TimeoutPolicy {
  timeoutLength: number;  // Total timeout duration (seconds). Must be > warnTime.
}
```

## TimeoutPageComponent

Pre-built timeout page component for displaying "Your session has expired" pages.

```typescript
// Routes: navigate with 'from' query param to enable return navigation
this.router.navigate(['/timeout'], { queryParams: { from: currentUrl } });
```

Component:
- Automatically calls `stopTimeoutWatch()` on init
- Restarts watch on destroy
- Validates `from` query param for security (must start with `window.location.origin` or `/`)
- Provides login button to navigate back to original page

## Callbacks

### Ping Observable

Invoked every `pingInterval` seconds when user is active. Logout callback fires if ping fails.

```typescript
pingObservable: () => this.http.post('/api/ping', {})
```

### Logout Callback

Triggered when:
- User clicks "Logout" button in warning modal
- `pingObservable()` returns error

```typescript
onLogoutCallback: () => {
  this.authService.logout();
  this.router.navigate(['/login']);
}
```

### Timeout Callback

Triggered when inactivity timer reaches zero. Modal closes automatically.

```typescript
onTimeoutCallback: () => {
  this.router.navigate(['/timeout'], { queryParams: { from: this.router.url } });
}
```

### Reset Timer Callback

Triggered on activity (click/keypress). Throttled by `resetTimeoutTimerCallbackInterval` (default: 1 minute). Useful for iframe parent/child communication.

## Key Imports

```typescript
import { TimeoutModule } from '@intersystems/timeout';
import { TimeoutService, TimeoutConfig, TimeoutPolicy } from '@intersystems/timeout';
import { TimeoutPageComponent } from '@intersystems/timeout';
import { TimeoutConfigService } from '@intersystems/timeout';  // Auto-configures i18n
```
