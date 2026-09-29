---
name: uifw-hs-basic-login
description: 'Build HealthShare login pages using @healthshare/basic-login. USE FOR: basic username/password login pages, login title branding, form-only login components, HealthShare app authentication UI. DO NOT USE FOR: customizable/themeable login (use uifw-hs-custom-login), authentication logic (use uifw-hs-auth), non-HealthShare login.'
argument-hint: 'Describe the login page you need (composite vs form-only, branding, layout)'
---

# @healthshare/basic-login

Build standardized HealthShare login pages with domain selection, forgot password links, and ISC branding.

## Quick Start

**1. Import the module**
```typescript
import { HSBasicLoginModule } from '@healthshare/basic-login';

@NgModule({
  imports: [HSBasicLoginModule]
})
```

**2. Choose your variant**

**Composite (full page)** - title + form with centered layout:
```html
<hs-basic-login
  [Id]="'login-page'"
  [loginConfig]="loginConfig"
  [loginForm]="loginForm">
</hs-basic-login>
```

**Form only** - for custom layouts:
```html
<hs-basic-login-form
  [Id]="'login-form'"
  [loginConfig]="loginConfig"
  [loginForm]="loginForm">
</hs-basic-login-form>
```

**Title only** - for separate branding:
```html
<hs-basic-login-title [welcomeTitle]="'Welcome to MyApp'"></hs-basic-login-title>
```

## Component API

### HSBasicLoginComponent
Composite component with title and form.

**Inputs:**
- `Id: string` (required) - Element IDs prefix
- `loginConfig: HSBasicLoginConfig` (required) - Configuration object
- `loginForm?: UntypedFormGroup` - Optional form instance for advanced control
- `welcomeTitle?: string` - Title text (defaults to "Welcome to HealthShare")

### HSBasicLoginFormComponent
Login form with username, password, optional domain selector, and forgot password link.

**Inputs:**
- `Id: string` (required) - Element IDs prefix
- `loginConfig: HSBasicLoginConfig` (required) - Configuration object
- `loginForm?: UntypedFormGroup` - Optional form instance for advanced control

### HSBasicLoginTitleComponent
Logo and welcome text header.

**Inputs:**
- `welcomeTitle?: string` - Custom title text (defaults to "Welcome to HealthShare")

## HSBasicLoginConfig Interface

```typescript
interface HSBasicLoginConfig {
  onLogin: (username: string, password: string, domain?: string) => void; // Required
  showDomainSelector?: Observable<boolean>;     // Show domain dropdown
  initialDomain?: Observable<string>;           // Pre-select domain
  domains?: Observable<string[]>;               // Domain options
  showForgotPassword?: Observable<boolean>;     // Show "Forgot Password?" link
  loginErrorMessage?: Observable<string>;       // Error banner message
  onForgotPassword?: () => void;                // Forgot password callback
}
```

## FDN Integration Notes

The form uses `@intersystems/isc-form` internally with these field keys:
- `domain` - SELECT field (conditionally hidden)
- `username` - INPUT (text)
- `password` - INPUT (password)
- `banner` - BANNER_NOTIFICATION (for errors)
- `forgotPassword` - Custom TextAndLink field (conditionally hidden)

Login button is disabled until username, password (and domain if shown) are provided.

## Key Imports

```typescript
import { HSBasicLoginModule, HSBasicLoginConfig } from '@healthshare/basic-login';
import { UntypedFormGroup } from '@angular/forms';
```
