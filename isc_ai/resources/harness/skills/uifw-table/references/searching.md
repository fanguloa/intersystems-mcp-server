# Searching

Built-in search bar configured via `config.useSearch` and `config.searchConfig`.

```typescript
import { SearchMode, ColumnSearchBehaviorType } from '@intersystems/table';
```

---

## Basic Setup

```typescript
tableConfig: TableConfig<MyRow> = {
  key: 'my-table',
  useSearch: true,
  searchConfig: {
    placeholderLabel: 'Search...',
    noEntriesFoundLabel: 'No matching entries',
    mode: SearchMode.DISPLAYED_COLUMNS
  },
  columns: [ /* ... */ ]
};
```

---

## Search Modes

| Mode | Enum | Behavior |
|------|------|----------|
| Simple | `SearchMode.SIMPLE` | Searches all row properties (displayed and hidden) |
| Displayed Columns | `SearchMode.DISPLAYED_COLUMNS` | Searches only visible columns |
| Marked Columns | `SearchMode.MARKED_COLUMNS` | Searches only columns with `searchable: true` |

---

## Custom Search per Column

For columns where the display value differs from the raw data (e.g. formatted dates):

```typescript
{
  key: 'hireDate',
  title: 'Hire Date',
  searchable: true,
  searchBehaviorType: ColumnSearchBehaviorType.CUSTOM,
  customSearchFunction: (row, col) => dayjs(row.hireDate).format('D MMMM YYYY'),
  cellDisplay: {
    getDisplay: (row) => dayjs(row.hireDate).format('D MMMM YYYY')
  }
}
```

---

## SearchConfig Properties

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `mode` | `SearchMode` | `SIMPLE` | Which columns to search |
| `placeholderLabel` | `string` | — | Placeholder text in search input |
| `noEntriesFoundLabel` | `string` | — | Message when search yields no results |
| `selectedRowHiddenLabel` | `(count) => string` | — | Message when selected rows are hidden by search |
| `debounceTime` | `number` | `500` | Debounce in ms before applying search |

Search resets pagination to page 0.
