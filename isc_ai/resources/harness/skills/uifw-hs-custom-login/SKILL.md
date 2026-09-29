---
name: uifw-hs-custom-login
description: 'Build customizable HealthShare login pages using @healthshare/custom-login. USE FOR: white-labeled login pages (Universal Login Application), login with editable branding elements, customizable headers and forms. DO NOT USE FOR: standard login pages (use uifw-hs-basic-login), authentication logic (use uifw-hs-auth), non-HealthShare login.'
argument-hint: 'Describe the customizable login you need (branding, editable elements, layout)'
---

# @healthshare/custom-login

Customizable login page for HealthShare applications with editable branding elements. Primary use is Universal Login Application.

## Quick Start

```typescript
// app.module.ts
import { HSCustomLoginModule } from '@healthshare/custom-login';
import { CustomizableElementModule } from '@intersystems/customizable-element';

@NgModule({
  imports: [HSCustomLoginModule, CustomizableElementModule]
})
```

```html
<hs-custom-login
  [Id]="'my-custom-login'"
  [config]="loginConfig"
  [loginForm]="loginForm">
</hs-custom-login>
```

```typescript
import { HSBasicLoginConfig } from '@healthshare/basic-login';
loginConfig: HSBasicLoginConfig = { /* auth config */ };
loginForm: FormGroup = this.fb.group({ username: '', password: '' });
```

## HSCustomLoginComponent

**Selector:** `hs-custom-login`

### Inputs
- `Id` (required): Unique identifier for the login instance
- `config`: `HSBasicLoginConfig` object for authentication
- `loginForm`: Angular `FormGroup` for username/password binding (optional)

### Editable Elements
All text/images are customizable via `@intersystems/customizable-element`:

- `infoBannerText` — Top notification banner (CustomNotificationConfig)
- `underFormText` — Text below login form (Paragraph)
- `rightTitleText` — Right panel title (Heading2)
- `rightFirstTitleText`, `rightFirstTitleContent` — Section 1 (Heading3 + Paragraph)
- `rightSecondTitleText`, `rightSecondTitleContent` — Section 2
- `rightThirdTitleText`, `rightThirdTitleContent` — Section 3
- `bottomTitleText`, `bottomTitleContent` — Legal notice at bottom

**Edit Mode:** When `CustomizableElementService.inEditableMode$` is true, the login form is automatically disabled to prevent submission during editing.

## HSCustomLoginHeaderComponent

**Selector:** `hs-custom-login-header`

Standalone header with customizable logo and title.

### Editable Elements
- `titleImg` — Logo image (CustomImageConfig), defaults to ISC logo SVG
- `titleText` — Page title (Heading1), defaults to "Welcome to HealthShare"

Both use `fr-customizable-element` and can be edited in customization mode.

## Key Imports

```typescript
import { HSCustomLoginModule } from '@healthshare/custom-login';
import { HSBasicLoginConfig } from '@healthshare/basic-login';
import { CustomizableElementService, CustomizableElementModule } from '@intersystems/customizable-element';
```

## Integration with Customizable Element

This component depends on `@intersystems/customizable-element`. Each editable piece has a `storePath` for persistence:
- `header.titleImg`, `header.titleText`
- `login.top.infoBannerText`
- `login.middle.left.underFormText`
- `login.middle.right.*` (sections 1-3)
- `login.bottom.titleText`, `login.bottom.contentText`

Configure CustomizableElement per its documentation to enable edit mode and persistence.
