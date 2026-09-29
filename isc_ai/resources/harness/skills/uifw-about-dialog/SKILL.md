---
name: uifw-about-dialog
description: 'Show version/system info dialogs using @intersystems/about-dialog. USE FOR: "About" modals, version info display, IRIS instance details, system information dialogs. DO NOT USE FOR: confirmation prompts (use uifw-confirmation-dialog), custom modals, error messages.'
argument-hint: 'Describe the about dialog you need (version info, product details)'
---

# @intersystems/about-dialog

Display version information about a system (e.g. IRIS instance) in a UX-approved modal dialog built on Angular Material.

## Quick Start

### 1. Install & Import
```bash
npm install @intersystems/about-dialog --registry <artifactory URL>
```

```typescript
// app.module.ts
import { AboutDialogModule } from "@intersystems/about-dialog";

@NgModule({
  imports: [AboutDialogModule, ...]
})
```

```scss
// styles.scss
@use "@intersystems/styles/css/layout-wrapper-mat-dialog-panel";
```

### 2. Open Dialog
```typescript
import { MatDialog } from "@angular/material/dialog";
import { AboutDialogData, AboutDialogComponent } from "@intersystems/about-dialog";

constructor(private dialog: MatDialog) {}

openAboutDialog() {
  const dialogData: AboutDialogData = {
    primaryAboutAppName: "HealthShare",
    primaryAboutVersion: "HealthShare UCR 2021.2.0 Build: 3105 - IRIS 2021.1",
    secondaryAboutAppName: "Another App",     // Optional
    secondaryAboutVersion: "App 1.0.0"        // Optional
  };
  
  this.dialog.open(AboutDialogComponent, {
    panelClass: "fr-layout-wrapper-mat-dialog-panel",  // Required
    data: dialogData,
    disableClose: true
  });
}
```

## Configuration

### AboutDialogData Interface
```typescript
interface AboutDialogData {
  primaryAboutAppName: string;           // Required: Main app name (e.g. "IRIS")
  primaryAboutVersion: string;           // Required: Version string (e.g. $ZV)
  secondaryAboutAppName?: string;        // Optional: Nested app name
  secondaryAboutVersion?: string;        // Optional: Nested app version
  hidePrimaryVersionLabel?: boolean;     // Optional: Hide primary label
  hideSecondaryVersionLabel?: boolean;   // Optional: Hide secondary label
}
```

**Note:** Secondary fields must be provided together or omitted together.

## Key Imports
- `AboutDialogModule` — Feature module
- `AboutDialogComponent` — Dialog component
- `AboutDialogData` — Configuration interface
- `AboutDialogConfigService` — Translation configuration (auto-configured)

## Panel Class
Always use `panelClass: "fr-layout-wrapper-mat-dialog-panel"` for proper styling. UIFW2 only officially supports this shipped panel class.
