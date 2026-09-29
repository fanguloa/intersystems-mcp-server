# Sorting

Configure via `config.sort` and per-column `sortable: true`.

---

## Basic Setup

Mark columns as sortable and provide a sort function:

```typescript
tableConfig: TableConfig<MyRow> = {
  key: 'my-table',
  sort: {
    sortFunction: (sortEvent, data, paginator, filterControls) => {
      return [...data].sort((a, b) => {
        const val = a[sortEvent.active] > b[sortEvent.active] ? 1 : -1;
        return sortEvent.direction === 'asc' ? val : -val;
      });
    },
    initialSortArrowDirection: {
      columnKey: 'name',
      direction: 'asc',
      doInitialSort: false
    }
  },
  columns: [
    { key: 'name', title: 'Name', sortable: true, cellDisplay: { model: 'name' } },
    { key: 'age',  title: 'Age',  sortable: true, cellDisplay: { model: 'age' } },
    { key: 'role', title: 'Role', sortable: false, cellDisplay: { model: 'role' } }
  ]
};
```

---

## Async Sorting (Server-Side)

Use `asyncSortFunction` for server-side sorting. Returns an `Observable<T[]>`.

```typescript
sort: {
  asyncSortFunction: (sortEvent, data, paginator, filterControls) => {
    return this.myService.fetchSorted(sortEvent.active, sortEvent.direction, paginator);
  }
}
```

> If both `sortFunction` and `asyncSortFunction` are defined, `sortFunction` takes precedence.

---

## TableSortConfig Properties

| Property | Type | Description |
|----------|------|-------------|
| `sortFunction` | `(sortEvent, data, paginator, filterControls) => T[]` | Synchronous sort callback |
| `asyncSortFunction` | `(sortEvent, data, paginator, filterControls) => Observable<T[]>` | Async sort callback |
| `initialSortArrowDirection.columnKey` | `string` | Column key to show initial arrow on |
| `initialSortArrowDirection.direction` | `'asc' \| 'desc'` | Initial arrow direction |
| `initialSortArrowDirection.doInitialSort` | `boolean` | Actually sort data on load (use carefully with server-side) |

Sort indicators are hidden when the table has fewer than 2 rows. Page index resets to 0 on sort.
