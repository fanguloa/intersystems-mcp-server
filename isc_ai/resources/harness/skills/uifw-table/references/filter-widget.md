# Filter Widget

Advanced filtering with filter bubbles, configured via `config.filterWidgetConfig`.

> **Prerequisite:** Import `TableModule.forRoot()` with `FilterWidgetComponent` and `FilterBubblesComponent` provided.

---

## Setup

```typescript
import { BehaviorSubject } from 'rxjs';

filterControl = new BehaviorSubject<any>(null);

tableConfig: TableConfig<MyRow> = {
  key: 'my-table',
  filterWidgetConfig: {
    filterControl: this.filterControl,
    filterParams: [ /* FilterParamGroup[] from @intersystems/filter-widget */ ],
    showFloatingFilterCountLabel: true,
    showFilterBubbles: true,
    doInitialFilter: false,
    filterFunction: (filterControls, data, paginator, sort) => {
      return data.filter(row => {
        // Apply filter logic based on filterControls
        return matchesFilter(row, filterControls);
      });
    }
  },
  columns: [ /* ... */ ]
};
```

---

## Async Filtering

Return an `Observable<T[]>` from `filterFunction` for server-side filtering:

```typescript
filterFunction: (filterControls, data, paginator, sort) => {
  return this.myService.fetchFiltered(filterControls, paginator, sort);
}
```

---

## FilterWidgetConfig Properties

| Property | Type | Description |
|----------|------|-------------|
| `filterControl` | `BehaviorSubject<any>` | Emits current filter selections |
| `filterParams` | `FilterParamGroup[]` | Filter parameter definitions |
| `filterFDNFields` | `Field[]` | ISC form field definitions for filters |
| `showFloatingFilterCountLabel` | `boolean` | Show active filter count label (default: `false`) |
| `showFilterBubbles` | `boolean` | Show filter bubbles below header (default: `true`) |
| `filterFunction` | `(filters, data, paginator, sort) => T[] \| Observable<T[]>` | Filter callback (required) |
| `doInitialFilter` | `boolean` | Run filter on table load (default: `false`) |

Filter changes reset pagination to page 0.
