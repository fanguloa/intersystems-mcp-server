# Custom Cell Components

Inject any Angular component into a table cell.

---

## Setup

### 1. Create the Component

Extend `AbstractTableCellComponent`:

```typescript
import { Component } from '@angular/core';
import { AbstractTableCellComponent } from '@intersystems/table';

@Component({
  standalone: true,
  selector: 'app-status-badge',
  template: `
    <span [class]="'badge badge-' + row.status">
      {{ row.status | titlecase }}
    </span>
  `
})
export class StatusBadgeComponent extends AbstractTableCellComponent<MyRow> {
  constructor() { super(); }
}
```

### 2. Include in target component

The component must be imported in the target component or module where the table
is instantiated.

### 3. Reference in Column Config

```typescript
{
  key: 'status',
  title: 'Status',
  cellDisplay: {
    component: StatusBadgeComponent
  }
}
```

---

## Available Inputs

`AbstractTableCellComponent` provides these `@Input()` properties to your component:

| Input | Type | Description |
|-------|------|-------------|
| `tableId` | `string` | Table DOM ID |
| `column` | `Column<T>` | Column configuration |
| `row` | `T` | Current row data |
| `rowIndex` | `number` | Row index in current page |
| `rowChangeControl` | `boolean` | Toggles on row mutation (for pipe re-evaluation) |
| `paginator` | `MatPaginator` | Paginator instance |
| `disabled` | `boolean` | Table disabled state |
| `rowIdentifierProperty` | `string` | Row ID property name |
| `expandableConfigLibrary` | `any` | Data from expandable row config |
| `refreshRow` | `() => void` | Trigger row re-render |

---

## Passing Extra Data via `library`

Use `column.library` to pass services, configs, or utility functions:

```typescript
{
  key: 'phones',
  title: 'Phone Numbers',
  cellDisplay: { component: PhoneListComponent },
  library: { iteratedModelKey: 'contactInfo.phones' }
}
```

Access in component via `this.column.library.iteratedModelKey`.

---

## Updating Row Data

Call `this.refreshRow()` after mutating `this.row` to trigger table re-render:

```typescript
onSave() {
  this.row.status = 'saved';
  this.refreshRow();
}
```
