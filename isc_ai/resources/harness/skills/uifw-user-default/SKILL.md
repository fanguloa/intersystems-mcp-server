---
name: uifw-user-default
description: 'Show application selection modals using @intersystems/user-default. USE FOR: default app picker dialogs, user application selection with radio buttons, hierarchical app tree display, configurable application icons. DO NOT USE FOR: general list selection, settings pages, confirmation prompts (use uifw-confirmation-dialog).'
argument-hint: 'Describe the application selector you need (options, hierarchy, icons)'
---

# @intersystems/user-default

Material Dialog modal for selecting a default application from a hierarchical list with radio buttons.

## Quick Start

```typescript
import { UserDefaultModule } from '@intersystems/user-default';

@NgModule({
  imports: [UserDefaultModule]
})
```

In `styles.scss`:
```scss
@use "@intersystems/styles/css/layout-wrapper-mat-dialog-panel";
```

## Opening the Dialog

```typescript
import { MatDialog } from '@angular/material/dialog';
import { UserDefaultData, UserDefaultComponent } from '@intersystems/user-default';

constructor(private dialog: MatDialog) {}

openDialog() {
  const dialogConfig: UserDefaultData = {
    currentActiveAppId: "first-item",
    userDefaultData: [
      {
        name: "First Item",
        id: "first-item",
        iconUrlPath: "./assets/images/FirstItem.svg"
      },
      {
        name: "Second Item",
        id: "second-item",
        iconUrlPath: "./assets/images/SecondItem.svg",
        children: [
          {
            name: "Nested Item",
            id: "nested-item",
            iconUrlPath: "./assets/images/NestedItem.svg"
          }
        ]
      }
    ]
  };

  const dialogRef = this.dialog.open(UserDefaultComponent, {
    data: dialogConfig,
    panelClass: "fr-layout-wrapper-mat-dialog-panel"
  });

  dialogRef.afterClosed().subscribe((selectedAppId: string) => {
    if (selectedAppId) {
      console.log("User selected:", selectedAppId);
    } else {
      console.log("User cancelled");
    }
  });
}
```

## Data Model

### UserDefaultData
```typescript
interface UserDefaultData {
  userDefaultData: ApplicationData[];
  currentActiveAppId?: string;  // Initially selected radio button
}
```

### ApplicationData
```typescript
interface ApplicationData {
  name: string;           // Display name
  id: string;             // Unique identifier
  iconUrlPath?: string;   // Path to icon image
  children?: ApplicationData[];  // Nested applications
  _hidden?: boolean;      // Internal: visibility control
  _expanded?: boolean;    // Internal: expansion state
  [key: string]: any;     // Additional custom properties
}
```

## Features

- **Hierarchical Tree**: Supports nested application structures
- **Radio Selection**: Single selection with radio buttons
- **Icons**: Optional icons per application
- **Expand/Collapse**: Toggle visibility of nested children
- **Pre-selection**: Set initial active app via `currentActiveAppId`

## Key Imports

```typescript
import { UserDefaultModule } from '@intersystems/user-default';
import { UserDefaultComponent, UserDefaultData, ApplicationData } from '@intersystems/user-default';
import { UserDefaultConfigService } from '@intersystems/user-default';
```
