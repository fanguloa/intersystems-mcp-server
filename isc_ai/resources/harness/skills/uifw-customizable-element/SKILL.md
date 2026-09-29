---
name: uifw-customizable-element
description: 'Enable in-place element customization with edit/view modes using @intersystems/customizable-element. USE FOR: making HTML elements user-editable (headings, paragraphs, images), white-labeling UIs, edit/view mode toggling, collecting customization data via ngrx. DO NOT USE FOR: form building (use uifw-isc-form), CMS/content editing, rich text editors.'
argument-hint: 'Describe what elements need customization and the edit/view workflow'
---

# Customizable Element — In-Place Content Editing

Enable in-place customization of HTML elements with edit/view mode toggling. Users can double-click images to upload new ones, edit text content inline, and all changes are collected in a centralized ngrx store. Primarily used for white-labeling in the Universal Login Application.

## Quick Start

```typescript
// 1. Configure ngrx store in app.module.ts
import { StoreModule } from "@ngrx/store";
import { EffectsModule } from "@ngrx/effects";
import { CustomizableElementModule } from "@intersystems/customizable-element";

@NgModule({
  imports: [
    StoreModule.forRoot({}),
    EffectsModule.forRoot([]),
    CustomizableElementModule
  ]
})

// 2. Configure service (root level only)
import { CustomizableElementService } from "@intersystems/customizable-element";

constructor(private customizationService: CustomizableElementService) {}

this.customizationService.configure({
  inProductionMode: environment.production,
  getData: (version) => // Return Observable<any> from storage,
  setData: (version, data) => // Save and return Observable<any>,
  clearData: (version) => // Clear and return Observable<any>
});

// 3. Wrap elements in template
<fr-customizable-element [config]="myConfigObj">
</fr-customizable-element>

// 4. Toggle edit mode globally
this.customizationService.setEditableMode(true);
```

## Component API

**Input:**
- `configObj: CustomElementConfig | CustomImageConfig | CustomTopbarConfig | CustomNotificationConfig` — Configuration defining element type, store path, and initial value

**Config Interface:**
```typescript
interface CustomElementConfig {
  dataType: CustomElementDataType;           // h1-5, p, img, fr-topbar, fr-notification
  storePath: string;                         // Unique hierarchical key (e.g., "header.logo.src")
  initialValue: string | Banner;             // Default value before customization
  id?: string;                               // DOM id (defaults to storePath with - replacing .)
  class?: string;                            // Additional CSS classes
  interpolationParams?: { [key: string]: {  // Dynamic parameter substitution
    tooltip: string;                         // Description shown in edit tooltip
    value: Observable<string>;               // Value to replace {{param}}
  }};
}
```

## Supported Elements

**HTML Elements:**
- `h1`, `h2`, `h3`, `h4`, `h5` — Editable headings (contentEditable in edit mode)
- `p` — Editable paragraph text
- `img` — Double-click to upload new image via file picker

**Complex Elements:**
- `fr-topbar` — Customize app name and company logo (requires `@intersystems/header`)
- `fr-notification` — Customize banner message and icon type (requires `@intersystems/notification`)

See `references/elements.md` for per-element configuration and edit behaviors.

## Features

| Feature | Description | Reference |
|---------|-------------|-----------|
| Edit/View Modes | Global toggle between editable and view modes | `references/ngrx-state.md` |
| Ngrx Store | Centralized customization data management | `references/ngrx-state.md` |
| Image Upload | Double-click images to upload via file picker | `references/elements.md` |
| Text Editing | Inline contentEditable with paste sanitization | `references/elements.md` |
| Interpolation | Dynamic parameter substitution with {{param}} | `references/elements.md` |
| Data Persistence | Load/save/clear via configured callbacks | `references/ngrx-state.md` |

## Key Imports

```typescript
import { CustomizableElementModule, CustomizableElementService } from "@intersystems/customizable-element";
import { CustomElementConfig, CustomElementDataType, CUSTOMIZATION_STORE_FEATURE_KEY } from "@intersystems/customizable-element";
```

## Common Patterns

**Nested Content (e.g., router-outlet in topbar):**
```html
<fr-customizable-element [config]="topbarConfig">
  <router-outlet fr-customizable-element-header-content></router-outlet>
</fr-customizable-element>
```

**Accessing Current Data:**
```typescript
this.customizationService.data$.subscribe(data => {
  console.log("All customizations:", data);
});
```

**Loading Stored Data:**
```typescript
this.customizationService.loadCustomizedData("v1");
```

**Saving Current State:**
```typescript
this.customizationService.saveCustomizedData("v1");
```
