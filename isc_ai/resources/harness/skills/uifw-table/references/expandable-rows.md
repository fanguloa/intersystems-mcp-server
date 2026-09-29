# Expandable Rows

Configure via `config.expandableRowConfig` to show detail content below a row.

---

## Setup

```typescript
tableConfig: TableConfig<MyRow> = {
  key: 'my-table',
  expandableRowConfig: {
    trigger: 'icon-column',
    colspan: 3,                   // should match total number of visible columns
    multipleRowsExpansion: true,
    addExpandAllButton: true,
    component: MyExpandedRowComponent
  },
  columns: [ /* ... */ ]
};
```

---

## Trigger Options

| Trigger | Behavior |
|---------|----------|
| `'row-click'` | Click anywhere on the row to expand |
| `'icon-column'` | Click the expand arrow icon (prepended column) |
| `'row-click-with-icon-column'` | Both row click and icon trigger expansion |
| `'always-expanded'` | Expanded content always visible |

---

## Expansion Modes

- **`multipleRowsExpansion: true`** — Multiple rows can be expanded simultaneously
- **`multipleRowsExpansion: false`** (default) — Only one row expanded at a time
- **`addExpandAllButton: true`** — Shows expand/collapse all button in the header (requires `icon-column` or `row-click-with-icon-column` trigger and `multipleRowsExpansion: true`)

---

## Expanded Row Component

Create a component extending `AbstractTableCellComponent`:

```typescript
import { AbstractTableCellComponent } from '@intersystems/table';

@Component({
  selector: 'app-expanded-detail',
  template: `
    <div class="detail-panel">
      <h4>{{ row.name }}</h4>
      <p>{{ row.description }}</p>
      <pre>{{ expandableConfigLibrary | json }}</pre>
    </div>
  `
})
export class ExpandedDetailComponent extends AbstractTableCellComponent<MyRow> {}
```

The component receives all `AbstractTableCellComponent` inputs: `row`, `column`, `rowIndex`, `tableId`, `paginator`, `disabled`, `expandableConfigLibrary`.

---

## Passing Data to Expanded Row

Use `library` to pass shared data/methods:

```typescript
expandableRowConfig: {
  trigger: 'icon-column',
  colspan: 3,
  component: ExpandedDetailComponent,
  library: { apiService: this.myService, mode: 'detail' }
}
```

Access via `this.expandableConfigLibrary` in the component.

---

## Conditional Hide

Hide the expand icon for specific rows:

```typescript
expandableRowConfig: {
  trigger: 'icon-column',
  colspan: 3,
  hideIconColumn: (row) => row.noDetails
}
```

---

## ExpandableRowConfig Properties

| Property | Type | Description |
|----------|------|-------------|
| `trigger` | `'row-click' \| 'icon-column' \| 'row-click-with-icon-column' \| 'always-expanded'` | How expansion is triggered |
| `colspan` | `number` | Column span of detail row |
| `multipleRowsExpansion` | `boolean` | Allow multiple expanded rows |
| `addExpandAllButton` | `boolean` | Show expand/collapse all button |
| `hideIconColumn` | `(row) => boolean` | Conditionally hide expand icon |
| `library` | `any` | Data passed to expanded row component |
| `component` | `Type<AbstractTableCellComponent>` | Angular component for expanded content |
