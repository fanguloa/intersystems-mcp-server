---
name: uifw-spinner
description: 'Show loading overlays using @intersystems/spinner. USE FOR: loading spinners during HTTP calls (automatic), manual loading states for custom async operations, full-page overlay blocking UI during operations. DO NOT USE FOR: inline progress indicators, determinate progress bars, skeleton loaders.'
argument-hint: 'Describe how you want the spinner to work (automatic HTTP, manual control, or both)'
---

# Spinner — Loading Overlay

Full-page loading spinner overlay built on Angular Material spinner with Frost styling. Supports automatic triggering via HTTP interceptor and manual control via service. Handles multiple parallel operations correctly.

## Quick Start

**Install:**
```bash
npm install @intersystems/spinner --registry <artifactory url>
```

**Import module:**
```typescript
import { SpinnerModule } from "@intersystems/spinner";

@NgModule({
  imports: [SpinnerModule]
})
```

**Add component to root template (app.component.html):**
```html
<fr-spinner></fr-spinner>
```

## Automatic Mode (HTTP Interceptor)

Shows spinner automatically during HTTP calls. Tracks multiple parallel requests — only hides when all complete.

**Setup interceptor in app.module.ts:**
```typescript
import { SpinnerInterceptor } from '@intersystems/spinner';
import { HTTP_INTERCEPTORS } from '@angular/common/http';

export const httpInterceptorProviders = [
  { provide: HTTP_INTERCEPTORS, useClass: SpinnerInterceptor, multi: true }
];

@NgModule({
  providers: [httpInterceptorProviders]
})
```

**Trigger spinner via header:**
```typescript
const options = {
  headers: new HttpHeaders().set("showloaders", "true")
};
this.http.get("api/endpoint", options).subscribe();
```

The interceptor reads and removes the `showloaders` header before forwarding the request (header never sent to backend).

**Recommended:** wrap HttpClient in a service that adds `showloaders: true` by default, except for exempt calls. See `@intersystems/shared-utilities/api` for helper service.

## Manual Mode (SpinnerService)

Use `SpinnerService` for custom async operations. Internal counter tracks push/pop calls — spinner visible when counter > 0.

**Inject service:**
```typescript
import { SpinnerService } from "@intersystems/spinner";

constructor(private spinnerService: SpinnerService) {}
```

**Show spinner (increment counter):**
```typescript
this.spinnerService.pushStack();           // no message
this.spinnerService.pushStack("Processing..."); // with message
```

**Hide spinner (decrement counter):**
```typescript
this.spinnerService.popStack();
```

**Clear all pending (reset counter to 0):**
```typescript
this.spinnerService.clearStack();
```

**Check visibility:**
```typescript
this.spinnerService.hasPendingRequest(); // returns boolean
```

## Component API

**SpinnerComponent** (`<fr-spinner>`)

No inputs or outputs. Component automatically subscribes to `SpinnerService` state. Optional message displayed under spinner (translatable).

## Configuration

- **Placement:** always instantiate at root level (overlays entire viewport)
- **Styling:** uses Angular Material spinner with Frost theme
- **Translation:** supports 20 locales via `@intersystems/shared-utilities/uifw-translation`
- **Z-index:** CSS controls overlay stacking (see `spinner.component.scss`)

## Key Imports

```typescript
import { SpinnerModule } from "@intersystems/spinner";
import { SpinnerService } from "@intersystems/spinner";
import { SpinnerInterceptor } from "@intersystems/spinner";
```
