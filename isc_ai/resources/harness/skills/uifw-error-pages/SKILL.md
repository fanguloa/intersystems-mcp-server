---
name: uifw-error-pages
description: 'Display error pages (404, 403) using @intersystems/error-pages. USE FOR: page not found errors, unauthorized access pages, custom error displays, route-based error handling with static or dynamic configuration. DO NOT USE FOR: inline error messages, form validation, notification banners (use uifw-notification).'
argument-hint: 'Describe the error page you need (type, custom message, navigation)'
---

# @intersystems/error-pages

Display standardized error pages (404, 403) with configurable messages and navigation.

## Quick Start

**Install:**
```bash
npm install @intersystems/error-pages --registry https://arti.iscinternal.com/api/npm/npm/
```

**Import module** (app.module.ts):
```typescript
import { ErrorPagesModule } from "@intersystems/error-pages";

@NgModule({
  imports: [ErrorPagesModule]
})
```

**Add routes** (app-routing.module.ts):
```typescript
import { BaseErrorPageComponent, PAGE_NOT_FOUND_CONFIG, UNAUTHORIZED_CONFIG } from "@intersystems/error-pages";

const routes: Routes = [
  { path: "not-found", component: BaseErrorPageComponent, data: PAGE_NOT_FOUND_CONFIG },
  { path: "unauthorized", component: BaseErrorPageComponent, data: UNAUTHORIZED_CONFIG }
];
```

## Built-in Templates

- **PAGE_NOT_FOUND_CONFIG**: 404 page not found error
- **UNAUTHORIZED_CONFIG**: 403 forbidden/unauthorized access error

## Static Configuration (Route Data)

Define custom error config per route:

```typescript
import { ErrorConfig } from "@intersystems/error-pages";

const customErrorConfig: ErrorConfig = {
  title: "Oops something went wrong",
  text: "The page you are looking for does not exist",
  button: {
    text: "Go to Homepage",
    onClick: (event, router: Router) => router.navigate(["home"]),
    tooltip: "Return to home",
    cssClass: "fr-primary"
  }
};

{ path: "not-found", component: BaseErrorPageComponent, data: customErrorConfig }
```

## Dynamic Configuration (Navigation State)

Pass error config during navigation for context-specific messages:

```typescript
const dynamicErrorConfig: ErrorConfig = {
  title: "Forbidden!!",
  text: "You do not possess clinician level access to view this page"
};

this.router.navigate(["unauthorized"], { state: dynamicErrorConfig });
```

**Note:** Dynamic config (state) takes precedence over static config (data). Page refresh falls back to static config.

## Key Imports

```typescript
import { ErrorPagesModule, BaseErrorPageComponent, ErrorConfig, PAGE_NOT_FOUND_CONFIG, UNAUTHORIZED_CONFIG } from "@intersystems/error-pages";
```
