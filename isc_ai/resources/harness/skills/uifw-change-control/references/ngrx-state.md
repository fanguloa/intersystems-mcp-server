# Ngrx State Management

The library uses ngrx for state management. All redux aspects are abstracted away - you don't need to understand redux to use the library, but this reference documents the internals for advanced use cases.

## Store Setup

**Feature key:** `fr-change-control` (exported as `STORE_FEATURE_KEY`)

**Registration:** Import `ChangeControlModule` after `StoreModule.forRoot()` and `EffectsModule.forRoot()` - the module registers its feature slice and effects internally.

## State Shape

```typescript
interface ChangeControlState {
  config: InternalChangeControlConfig;    // Client config (minus currentUser$)
  serverConfig: ChangeControlConfigOutput; // Server-side config
  sessionKey: string;                      // Active session key
  instanceInfo: InstanceInfo;              // Server/instance/namespace info
}
```

**Initial state:**
- `sessionKey` loaded from localStorage (`CCR_SESSION_KEY`)
- Other fields null until configured

## Actions

**storeConfig** - Triggered by `configure()`. Kicks off effects to load server config + instance info.

**loadServerConfigSuccess** - Dispatched by effect with server-side config (enabled, locked, environment, etc).

**loadInstanceInfoSuccess** - Dispatched by effect with instance metadata (server, instance, namespace, environment).

**setSessionKey** - Set or clear active session key. Updates localStorage (`CCR_SESSION_KEY`).

## Selectors

All selectors imported from `@intersystems/change-control/store/reducer`.

**Core:**
- `selectSessionKey` - active session key string (empty if no session)
- `selectInstanceInfo` - server/instance/namespace/environment metadata
- `selectEnabled` - whether change control enabled on server
- `selectLocked` - whether system locked (prevents edits even with session)

**Config:**
- `selectOnlyTier2` - filter to tier 2 changes only
- `selectChangeSessionManagementRoute` - route for linking
- `selectRestApiBaseUrl` - base URL for REST API (default: "api")
- `selectOnError` - error handler function

**Example:**
```typescript
this.store.select(selectSessionKey).subscribe(key => console.log("Session:", key));
```

## Effects

**loadServerConfig$** - Triggered by `storeConfig`, fetches backend config, dispatches `loadServerConfigSuccess`

**loadInstanceInfo$** - Triggered by `storeConfig`, fetches instance metadata, dispatches `loadInstanceInfoSuccess`

## Service Observables

`ChangeControlService` exposes derived observables.

**isLocked$** - Emits `true` when change control enabled AND (system locked OR no active session). Use to hide/disable edit buttons.

**instanceInfo$** - Pass-through of `selectInstanceInfo` selector. Instance metadata useful for general app purposes.
