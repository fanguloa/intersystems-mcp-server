---
name: uifw-change-control
description: 'Manage IRIS source control change sessions using @intersystems/change-control. USE FOR: creating/managing change sessions, viewing uncommitted changes, commit/revert operations, session key HTTP interceptor, instance info display, GUID debugging. DO NOT USE FOR: version history browsing (use uifw-change-history), git operations, file-level source control.'
argument-hint: 'Describe the change control workflow you need (sessions, commit, revert, instance info)'
---

# Change Control - Source Control Sessions

Angular library for managing IRIS change control sessions. Integrates with Tier 2 CCR workflows, provides ngrx state management for session keys, and offers components for starting/managing sessions, viewing uncommitted changes, committing/reverting, and debugging GUIDs.

## Quick Start

```typescript
// 1. Import ChangeControlModule + setup ngrx + interceptor in app.module.ts
imports: [StoreModule.forRoot({}), EffectsModule.forRoot([]), ChangeControlModule]
providers: [{ provide: HTTP_INTERCEPTORS, useClass: SessionKeyInterceptor, multi: true }]

// 2. Configure in app.component.ts
this.changeControlService.configure({
  onlyTier2: true,
  currentUser$: this.authService.currentUser$,
  onError: (error) => this.handleApiError(error)
});

// 3. Place instance info below header
<fr-topbar>
  <fr-change-control-instance-info></fr-change-control-instance-info>
</fr-topbar>
```

## Components

| Component | Purpose | Usage |
|-----------|---------|-------|
| `InstanceInfoComponent` | Display server/instance/namespace/session info | Place below header, always visible |
| `SessionManagementComponent` | Prompt user to start session when needed | Place at top of change-controlled pages |
| `ChangeSessionManagementComponent` | Full session + uncommitted queue manager | Add as routed page component |
| `FindGuidComponent` | Debug tool - search for changes by GUID | Add as routed page component |

## Features

| Feature | Reference |
|---------|-----------|
| Session lifecycle (create/end) | [sessions.md](./references/sessions.md) |
| Uncommitted queue (view/commit/revert) | [sessions.md](./references/sessions.md) |
| Session key interceptor | [sessions.md](./references/sessions.md) |
| Ngrx state (actions/selectors/effects) | [ngrx-state.md](./references/ngrx-state.md) |
| Service observables (isLocked$, instanceInfo$) | [ngrx-state.md](./references/ngrx-state.md) |

## Key Imports

```typescript
import {
  ChangeControlModule,
  ChangeControlService,
  SessionKeyInterceptor,
  InstanceInfoComponent,
  SessionManagementComponent,
  ChangeSessionManagementComponent,
  FindGuidComponent,
  ChangeControlConfig,
  InstanceInfo
} from "@intersystems/change-control";
```

## Co-requisites

- `@ngrx/store` and `@ngrx/effects` (redux state management)
- `@intersystems/notification` (toast notifications)
- `@intersystems/isc-form` (FindGuidComponent uses form)
