# Table Header & Styling

Configure the table header, CSS classes, sticky columns, and super column headers.

---

## Table Header

```typescript
tableConfig: TableConfig = {
  key: 'my-table',
  header: {
    title: 'Employees',
    titleTooltip: { text: 'List of all employees' },
    cssTableHeaderClass: 'my-header-class',
    newButton: {
      text: 'Add Employee',
      tooltip: 'Create a new employee record',
      onClick: (event) => { /* open dialog */ },
      disabled: false,
      hidden: false
    }
  },
  columns: [ /* ... */ ]
};
```

The `newButton` supports `Observable<boolean>` for async `hidden`/`disabled` states.

---

## CSS Classes

| Property | Applies To |
|----------|-----------|
| `config.cssTableClass` | `<mat-table>` element |
| `config.cssTableContainerClass` | Container wrapping the table |
| `config.cssTRClass` | Every `<tr>` element |
| `config.cssTRClassFromRow: (row) => string` | Per-row dynamic class |
| `config.cssNoDataMessageClass` | No-data message |
| `column.cssTDClass` | All `<td>` in a column |
| `column.cssTDClassFromRow: (row, col) => string` | Per-cell dynamic class |
| `column.cssTHClass` | Column `<th>` header |
| `column.cellDisplay.justify` | `'left' \| 'right' \| 'center'` |
| `column.cellDisplay.bolded` | Bold cell text |

---

## Sticky Columns & Headers

```typescript
columns: [
  { key: 'name', title: 'Name', sticky: true, cellDisplay: { model: 'name' } },
  // ... middle columns ...
  { key: 'actions', title: '', stickyEnd: true, cellDisplay: { preset: EPresetOptions.ACTIONS_ICONS, /* ... */ } }
]
```

Sticky header row (stays visible when scrolling):
```typescript
tableConfig: TableConfig = {
  key: 'my-table',
  stickyHeaderRow: true,
  // ...
};
```

---

## Super Column Headers

Group columns under spanning headers:

```typescript
tableConfig: TableConfig = {
  key: 'my-table',
  superColumnHeaders: [
    { title: 'Personal Info', colspan: 2 },
    { title: 'Work Info', colspan: 3, cssClass: 'work-header' }
  ],
  columns: [
    // 2 columns under "Personal Info"
    { key: 'name', title: 'Name', cellDisplay: { model: 'name' } },
    { key: 'email', title: 'Email', cellDisplay: { model: 'email' } },
    // 3 columns under "Work Info"
    { key: 'role', title: 'Role', cellDisplay: { model: 'role' } },
    { key: 'dept', title: 'Department', cellDisplay: { model: 'dept' } },
    { key: 'salary', title: 'Salary', cellDisplay: { model: 'salary' } }
  ]
};
```

Super headers support `sticky` and `stickyEnd` properties. The sum of all `colspan` values must equal the total number of columns.

---

## Row Click & Highlight

```typescript
tableConfig: TableConfig = {
  key: 'my-table',
  rowIdentifierProperty: 'id',
  onRowClick: (row) => { /* handle row click */ },
  highlightRowOnClick: true,
  // ...
};
```

Use `[(idOfSelectedRow)]` for two-way binding of the selected row:
```html
<fr-table
  [Id]="'my-table'"
  [config]="config"
  [data]="data"
  [(idOfSelectedRow)]="selectedId"
></fr-table>
```

---

## No Data Message

```typescript
tableConfig: TableConfig = {
  key: 'my-table',
  noDataMessage: 'No records found.',
  cssNoDataMessageClass: 'empty-state',
  // ...
};
```

Only displays when data is explicitly an empty array (`[]`), not when data is `null` (loading state).

---

## Collapsible Table

```typescript
tableConfig: TableConfig = {
  key: 'my-table',
  collapse: true,   // starts collapsed; also accepts Observable<boolean>
  header: { title: 'Collapsible Table' },
  // ...
};
```

Listen for collapse changes:
```html
<fr-table (collapseChange)="onCollapse($event)" ...></fr-table>
```

---

## Tooltips

**Column header tooltip:**
```typescript
{ key: 'name', title: 'Name', headerTooltip: { text: 'Full name' }, cellDisplay: { model: 'name' } }
```

**Cell tooltip (static, model, or dynamic):**
```typescript
{ key: 'email', title: 'Email', tooltip: { model: 'email' }, cellDisplay: { model: 'email' } }
{ key: 'status', title: 'Status', tooltip: { getTooltip: (row) => `Status: ${row.status}` }, cellDisplay: { model: 'status' } }
```
