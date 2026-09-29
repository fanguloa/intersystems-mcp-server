---
name: uifw-confirmation-dialog
description: 'Show confirmation prompts using @intersystems/confirmation-dialog. USE FOR: yes/no confirmations, destructive action warnings, dialogs with checkbox acknowledgment, multi-button confirmation prompts. DO NOT USE FOR: version info (use uifw-about-dialog), complex form modals (use isc-form modal), application selection (use uifw-user-default).'
argument-hint: 'Describe the confirmation dialog you need (buttons, message, checkbox)'
---

# Confirmation Dialog

Show confirmation prompts using the Angular Material Dialog with UX-approved layout.

## Quick Start

```typescript
import { MatDialog, MatDialogConfig } from '@angular/material/dialog';
import { ConfirmationDialogComponent, ConfirmationDialogConfig } from '@intersystems/confirmation-dialog';

constructor(private dialog: MatDialog) {}

showConfirmation() {
  const confirmationDialogConfig: ConfirmationDialogConfig = {
    title: "Delete Item",
    primary: "Are you sure you want to delete this item?",
    secondary: "This action cannot be undone.",
    checkbox: {
      value: false,
      label: "I understand this action is permanent"
    },
    buttons: {
      primary: { text: "Delete", color: "warning" },
      secondary: { text: "Cancel" }
    }
  };

  const dialogConfig = new MatDialogConfig();
  dialogConfig.data = confirmationDialogConfig;
  dialogConfig.panelClass = "fr-layout-wrapper-mat-dialog-panel";

  const dialogRef = this.dialog.open(ConfirmationDialogComponent, dialogConfig);

  dialogRef.afterClosed().subscribe(response => {
    if (response?.button === 'primary' && response?.checked) {
      // User confirmed and checked the acknowledgment
      this.performDelete();
    }
  });
}
```

## Component API

### ConfirmationDialogConfig
- `title` (string): Dialog title
- `primary` (string): Main message (rendered as h4)
- `secondary` (string): Secondary message (rendered as h5)
- `tertiary` (string): Additional text (rendered as p)
- `checkbox` (object): Optional checkbox with `value` (boolean) and `label` (string)
- `hideCloseButton` (boolean): Hide the X button (does not disable backdrop close)
- `buttons` (object): Configure up to 4 buttons (primary, secondary, tertiary, warning)

### ConfirmationDialogButton
- `text` (string): Button label
- `color` ('primary' | 'accent' | 'warning'): Button color theme
- `cssClass` (string): Additional CSS class
- `tooltip` (string): Button tooltip

### Response Object
- `button` (string): Which button was clicked ('primary', 'secondary', 'tertiary', 'warning', 'close')
- `checked` (boolean): Checkbox state at time of close

## Button Layout

Buttons render left-to-right: `[Secondary] [Tertiary] [Primary] [Warning]`

## Module Setup

```typescript
// app.module.ts
import { ConfirmationDialogModule } from "@intersystems/confirmation-dialog";

@NgModule({
  imports: [ConfirmationDialogModule, ...]
})
```

```scss
// styles.scss
@use "@intersystems/styles/css/layout-wrapper-mat-dialog-panel";
```

## Key Imports
```typescript
import { MatDialog, MatDialogConfig } from '@angular/material/dialog';
import { ConfirmationDialogComponent, ConfirmationDialogConfig } from '@intersystems/confirmation-dialog';
```
