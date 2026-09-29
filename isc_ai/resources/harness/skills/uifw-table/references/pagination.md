# Pagination

Configure via the `[paginatorConfig]` input on `<fr-table>`.

```typescript
import { PaginatorConfig } from '@intersystems/table';
```

> **Prerequisite:** The parent module must import `BrowserAnimationsModule` or `NoopAnimationsModule`.

---

## Client-Side Pagination

All data is loaded upfront; the table handles page slicing.

```typescript
paginatorConfig: PaginatorConfig = {
  pageSize: 10,
  pageSizeOptions: [5, 10, 20, 50]
};
```

```html
<fr-table
  [Id]="'my-table'"
  [config]="tableConfig"
  [data]="data"
  [paginatorConfig]="paginatorConfig"
></fr-table>
```

---

## Server-Side (Async) Pagination

Provide `asyncGetPage` to fetch pages on demand. Set `length` to the total row count.

```typescript
paginatorConfig: PaginatorConfig<MyRow> = {
  pageSize: 10,
  pageSizeOptions: [10, 25, 50],
  length: 500,
  asyncGetPage: (pageEvent, sort, filterControls) => {
    return this.myService.fetchPage(pageEvent.pageIndex, pageEvent.pageSize, sort);
  }
};
```

The callback receives:
- `pageEvent: PageEvent` — `pageIndex`, `pageSize`, `previousPageSize`, `length`
- `sort: Sort` — current sort state (`active`, `direction`)
- `filterControls` — current filter state (if filter widget is used)

---

## PaginatorConfig Properties

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `pageSize` | `number` | `5` | Initial page size |
| `minimumTablePageSize` | `number` | `5` | Minimum selectable page size |
| `pageSizeOptions` | `number[]` | — | Dropdown options for page size |
| `length` | `number` | — | Total row count (async only) |
| `asyncGetPage` | `(pageEvent, sort, filters) => Observable<T[]>` | — | Server-side page fetch |
| `cssPaginatorClass` | `string` | — | CSS class on paginator container |

The paginator includes a built-in jump-to-page input field. Page index resets to 0 on sort or filter changes.
