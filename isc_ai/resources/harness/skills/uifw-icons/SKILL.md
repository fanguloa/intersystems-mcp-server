---
name: uifw-icons
description: 'Register and use ISC SVG icons using @intersystems/icons. USE FOR: registering ISC custom SVG icons with Angular Material icon registry, using ISC icons with mat-icon, accessing the full ISC icon set. DO NOT USE FOR: Material Design icons (already in styles), generic SVG rendering, icon fonts.'
argument-hint: 'Describe which icons you need or how to register custom icons'
---

# @intersystems/icons

Register and use ISC custom SVG icons with Angular Material.

## Quick Start

### 1. Register Icons (Required)

Call `registerAllIcons()` once in your app (typically in a root component or module):

```typescript
import { FrIconsService } from '@intersystems/icons';
import { inject } from '@angular/core';

export class AppComponent {
  iconService = inject(FrIconsService);
  
  ngOnInit() {
    this.iconService.registerAllIcons();
  }
}
```

### 2. Use Icons in Templates

Reference icons via the constant in your templates:

```typescript
import { ISC_ICONS } from '@intersystems/icons';

export class MyComponent {
  ISC_ICONS = ISC_ICONS;
}
```

```html
<mat-icon [svgIcon]="ISC_ICONS.ARROW.UP"></mat-icon>
<mat-icon [svgIcon]="ISC_ICONS.BUSINESS.CALENDAR"></mat-icon>
<mat-icon [svgIcon]="ISC_ICONS.NOTIFICATION.SUCCESS_COLORED"></mat-icon>
```

## Available Icon Categories

- **ARROW**: navigation arrows (up, down, left, right, double, expand/collapse)
- **BUSINESS**: flag, message, calendar, star, image, attach-clip, notification-bell, clock, change-management
- **CODE**: status-pending, variable-search
- **DATA**: table, propagate, graph-bar, graph-line, database, dataset, dataobject, star-folder
- **DEMOGRAPHICS**: status-deceased
- **EDITOR**: circle-plus, eye-view, trash-delete, edit-pencil, duplicate-copy, refresh, undo, download, upload, link, unlink, remove-circle
- **MENU**: home, settings-cog, hamburger, help, ellipsis, x
- **NOTIFICATION**: info, success, warning, alert, x-circle (all with optional `-colored` or `-solid` variants)
- **QUESTIONNAIRE**: question-list, clipboard-list, book, file-doc, vector, list, lab-beaker
- **RESERVED**: light-mode, dark-mode, isc-forms, isc-library, isc-new-form, isc-populated-form
- **VIEWING**: zoom-plus, search, filter, controls-filter

## Key Imports

```typescript
import { FrIconsService, ISC_ICONS } from '@intersystems/icons';
import { MatIconModule } from '@angular/material/icon';
```
