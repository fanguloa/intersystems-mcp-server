---
name: uifw-notification
description: 'Display popup or banner notifications using @intersystems/notification. USE FOR: success/error/warning/info messages, toast notifications, persistent status banners, stacked popup messages. DO NOT USE FOR: modal confirmations (use uifw-confirmation-dialog), inline form validation, alert dialogs.'
argument-hint: 'Describe the notification behavior you need (type, mode, trigger)'
---

# Notification — Popup & Banner Messages

Display user feedback via popup notifications (stacked, multiple) or banner notifications (single, inline). All notifications are persistent by default and require manual dismissal unless an optional timeout is provided.

## Quick Start

```typescript
// 1. Import module in app.module.ts
import { NotificationModule } from '@intersystems/notification';

// 2. Add to template (popup mode at root level)
<fr-notification></fr-notification>

// 3. Inject service and show notifications
import { NotificationService } from '@intersystems/notification';

constructor(private notificationService: NotificationService) {}

this.notificationService.showSuccess('Operation completed!');
this.notificationService.showInfo('Report ready.');
this.notificationService.showWarning('Unsaved changes.');
this.notificationService.showAlert('Save failed.');

// Clear all (recommended on route changes)
this.notificationService.clearNotifications();
```

## Component API

**Selector:** `fr-notification`

**Input:** `bannerConfig?: Banner` — Enables banner mode (omit for popup mode)

```typescript
interface Banner {
  type: NOTIFICATION_TYPE;  // ALERT | WARNING | SUCCESS | INFO
  message: string;
  id?: string;
}
```

## NotificationService API

**Methods:**
- `showAlert(message: string, time?: number)` — Red, highest priority
- `showWarning(message: string, time?: number)` — Yellow
- `showSuccess(message: string, time?: number)` — Green
- `showInfo(message: string, time?: number)` — Blue
- `clearNotifications()` — Remove all
- `deleteNotification(type: string, index: number)` — Remove specific

**Auto-dismiss example:**
```typescript
this.notificationService.showSuccess('Saved!', 5000); // 5 seconds
```

## Notification Types

| Type | Use Case | Visual |
|------|----------|--------|
| `ALERT` | Critical errors, failed operations | Red |
| `WARNING` | Warnings, user attention needed | Yellow |
| `SUCCESS` | Successful operations, confirmations | Green |
| `INFO` | General information, status updates | Blue |

```typescript
import { NOTIFICATION_TYPE } from '@intersystems/notification';
// NOTIFICATION_TYPE.ALERT | WARNING | SUCCESS | INFO
```

## Modes

**Popup (default):** Multiple stacked notifications at root level. Ordered by type, numbered (1), (2), etc., manual dismissal via X.
```html
<fr-notification></fr-notification>
```

**Banner:** Single inline notification. No X button, controlled by app logic.
```typescript
bannerConfig: Banner = {
  type: NOTIFICATION_TYPE.ALERT,
  message: 'Error loading data.'
};
```
```html
<fr-notification [bannerConfig]="bannerConfig"></fr-notification>
<fr-notification *ngIf="hasError" [bannerConfig]="bannerConfig"></fr-notification>
```

## Configuration

**NotificationConfigService** handles i18n. Auto-configured when NotificationModule is imported. Supports 20 locales.

## Key Imports

```typescript
import { NotificationModule } from '@intersystems/notification';
import { NotificationService } from '@intersystems/notification';
import { Banner, NOTIFICATION_TYPE } from '@intersystems/notification';
```
