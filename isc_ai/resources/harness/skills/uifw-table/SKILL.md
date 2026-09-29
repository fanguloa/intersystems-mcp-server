---
name: uifw-table
description: 'Create Angular table components using the fr-table library (@intersystems/table). USE FOR: adding data tables, configuring table columns, pagination, sorting, searching, row selection, expandable rows, action menus, editable cells, link cells, button cells, localized dates, custom cell components. DO NOT USE FOR: non-table UI components, standalone form fields, charts or graphs.'
argument-hint: 'Describe the table you want to create (columns, features, data shape)'
---

# fr-table — Angular Table Component

## Quick Start

### 1. Import the Module

```typescript
import { TableModule } from '@intersystems/table';

@NgModule({
  imports: [
    TableModule.forRoot({})
  ]
})
export class YourModule {}
```

> If using editable cells or filter widgets, provide runtime components:
> ```typescript
> TableModule.forRoot({
>   IscFormComponent: IscFormComponent,
>   FilterWidgetComponent: FilterWidgetComponent,
>   FilterBubblesComponent: FilterBubblesComponent
> })
> ```

### 2. Define Config and Data

```typescript
import { TableConfig } from '@intersystems/table';

interface Person {
  name: string;
  email: string;
  role: string;
}

tableConfig: TableConfig<Person> = {
  key: 'people-table',
  columns: [
    { key: 'name',  title: 'Name',  cellDisplay: { model: 'name' } },
    { key: 'email', title: 'Email', cellDisplay: { model: 'email' } },
    { key: 'role',  title: 'Role',  cellDisplay: { model: 'role' } }
  ]
};

data: Person[] = [
  { name: 'Alice', email: 'alice@example.com', role: 'Admin' },
  { name: 'Bob',   email: 'bob@example.com',   role: 'User' }
];
```

### 3. Add Template

```html
<fr-table
  [Id]="'people-table'"
  [config]="tableConfig"
  [data]="data"
></fr-table>
```

## Component API

| Input | Type | Description |
|-------|------|-------------|
| `Id` | `string` | DOM ID prefix for table and row elements |
| `config` | `TableConfig<T>` | Table configuration object |
| `data` | `T[]` | Synchronous data array |
| `dataAsync` | `Observable<T[]>` | Async data source |
| `configAsync` | `Observable<TableConfig<T>>` | Async config source |
| `paginatorConfig` | `PaginatorConfig<T>` | Pagination settings |
| `safeMode` | `boolean` | HTML sanitization (default: `true`) |
| `idOfSelectedRow` | `string \| null` | Two-way binding for selected row |
| `disabled` | `boolean` | Disables table interactions |

| Output | Description |
|--------|-------------|
| `idOfSelectedRowChange` | Selected row changed |
| `dataChange` | Table data mutated |
| `displayChange` | View changed (sort, search, filter, paginate, collapse) |
| `collapseChange` | Table collapsed/expanded |

### Content Projection

```html
<fr-table [Id]="'my-table'" [config]="config" [data]="data">
  <div table-header-space><!-- Custom header content --></div>
  <div above-table-body-space><!-- Content above table body --></div>
</fr-table>
```

## Cell Display Options

Every column requires a `cellDisplay` object. Choose **one** approach:

| Approach | Use When |
|----------|----------|
| `model: 'propertyName'` | Display a direct property value |
| `getDisplay: (row, col) => string` | Compute display value from row data |
| `template: '<b>static html</b>'` | Render static/hardcoded HTML (requires `safeMode: false`) |
| `component: MyComponent` | Inject a custom Angular component |
| `preset: EPresetOptions.*` | Use a built-in cell type (see features below) |

## Features

Each feature is documented in a reference file with configuration details and examples.

| Feature | Reference | Description |
|---------|-----------|-------------|
| Column Presets | [./references/column-presets.md](./references/column-presets.md) | Checkbox, action icons, dropdown menu, link, buttons, editable, localized date |
| Pagination | [./references/pagination.md](./references/pagination.md) | Client-side and server-side (async) pagination |
| Sorting | [./references/sorting.md](./references/sorting.md) | Column sorting with sync/async callbacks |
| Searching | [./references/searching.md](./references/searching.md) | Full-text search with multiple modes |
| Expandable Rows | [./references/expandable-rows.md](./references/expandable-rows.md) | Row detail expansion via click or icon |
| Custom Cell Components | [./references/custom-cells.md](./references/custom-cells.md) | Inject any Angular component into a cell |
| Table Header & Styling | [./references/styling.md](./references/styling.md) | Header, "New" button, sticky columns, CSS classes, super column headers |
| Filter Widget | [./references/filter-widget.md](./references/filter-widget.md) | Advanced filtering with filter bubbles |

## Key Imports

```typescript
// Main module and component
import { TableModule, TableComponent } from '@intersystems/table';

// Configuration interfaces
import {
  TableConfig,
  Column,
  PaginatorConfig,
  EPresetOptions,
  EActionIcons,
  SearchMode,
  ColumnSearchBehaviorType,
  AbstractTableCellComponent
} from '@intersystems/table';
```
