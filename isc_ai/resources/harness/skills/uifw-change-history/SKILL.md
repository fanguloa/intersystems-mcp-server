---
name: uifw-change-history
description: 'Display and search configuration change history logs using @intersystems/change-history. USE FOR: change log viewing, searching change history with filters, side panel history display, retention settings configuration. DO NOT USE FOR: source control sessions (use uifw-change-control), git log display, audit trails.'
argument-hint: 'Describe how you want to display change history (full page, side panel, search filters)'
---

# @intersystems/change-history

Display and search configuration change history logs with full-page search, side panel, and retention settings.

## Quick Start

### Service Configuration

Configure the service with API endpoints and permissions:

```typescript
import { ChangeHistoryService } from "@intersystems/change-history";

constructor(private changeHistory: ChangeHistoryService) {}

ngOnInit() {
  this.changeHistory.configure({
    apiBaseUrl: "/api",
    changeHistoryRoute: "/change-history",
    hasAccess: (feature, permission) => true,
    onError: (error) => console.error(error)
  });
}
```

### Full Page Component (Route)

Add as a standalone route for search with filters and results table:

```typescript
{
  path: "change-history",
  loadComponent: () =>
    import("@intersystems/change-history").then(m => m.ChangeHistoryPageComponent)
}
```

Or use predefined routes:

```typescript
import { CHANGE_HISTORY_ROUTES } from "@intersystems/change-history";

{
  path: "change-history",
  children: CHANGE_HISTORY_ROUTES
}
```

### Side Panel Component (Embedded)

Show history for a specific record in a side panel:

```typescript
import { ChangeHistoryPanelComponent } from "@intersystems/change-history";

@Component({
  selector: "my-component",
  standalone: true,
  imports: [ChangeHistoryPanelComponent],
  template: `
    <fr-change-history-panel
      *ngIf="showPanel"
      [Id]="'history-panel'"
      [configuration]="'Service Registry'"
      [recordValue]="selectedRecordId"
      [refreshTrigger]="refreshCounter"
      (close)="showPanel = false"
    ></fr-change-history-panel>
  `
})
export class MyComponent {
  showPanel = false;
  selectedRecordId = "";
  refreshCounter = 0;

  openHistory(recordId: string) {
    this.selectedRecordId = recordId;
    this.showPanel = true;
  }

  afterUpdate() {
    this.refreshCounter++; // Triggers panel refresh
  }
}
```

## Component API

### ChangeHistoryPageComponent

Full-page search and results table. Standalone component with built-in filtering, sorting, expandable diff rows.

**Selector:** `fr-change-history-page`

**Inputs:** None (self-contained)

### ChangeHistoryPanelComponent

Side panel for viewing change history of a specific record.

**Selector:** `fr-change-history-panel`

**Inputs:**
- `Id: string` (required) - Unique identifier for accessibility
- `configuration: string` (required) - Feature/table name to filter by
- `recordValue: string` (required) - Record identifier to display
- `refreshTrigger: number | string` - Change to reload panel data

**Outputs:**
- `close: EventEmitter<null>` - Emitted when user closes panel

### ChangeHistorySettingsComponent

Settings page for retention configuration.

**Selector:** `fr-change-history-settings`

**Inputs:** None

## Key Imports

```typescript
import {
  ChangeHistoryPageComponent,
  ChangeHistoryPanelComponent,
  ChangeHistoryService,
  CHANGE_HISTORY_ROUTES
} from "@intersystems/change-history";
```
