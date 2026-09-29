---
name: uifw-filter-widget
description: 'Add advanced data filtering with button+form and filter bubbles using @intersystems/filter-widget. USE FOR: data filtering UIs, search forms with active filter bubble display, filter integration with fr-table, predefined or custom FDN-based filter forms. DO NOT USE FOR: simple text search (use table built-in search), standalone form interfaces (use uifw-isc-form).'
argument-hint: 'Describe the filters you need (fields, types, integration with table)'
---

# Filter Widget — Advanced Data Filtering

The `@intersystems/filter-widget` library provides a dual-component system for advanced filtering: a filter widget (button that opens a form) and optional filter bubbles (displays active filters as removable chips). It supports both predefined checkbox-based filter parameters and custom FDN-based forms via isc-form integration.

## Quick Start

**1. Install and import:**
```bash
npm install @intersystems/filter-widget --save --registry <artifactory URL>
```

**2. Add to your module:**
```typescript
import { FilterWidgetModule } from "@intersystems/filter-widget";
import { TranslateModule } from "@ngx-translate/core";

@NgModule({
  imports: [
    FilterWidgetModule,
    TranslateModule.forRoot(), // Required for built-in translations
    ...
  ]
})
```

**3. Set up filter control in component:**
```typescript
import { BehaviorSubject } from "rxjs";
import { FilterParamGroup } from "@intersystems/filter-widget";

filterControlSubject = new BehaviorSubject<any>({ category: [], status: [] });

filterParams: FilterParamGroup[] = [
  {
    key: "category",
    groupName: "Category",
    params: [
      { text: "Electronics", value: "electronics" },
      { text: "Books", value: "books" }
    ]
  }
];

ngOnInit() {
  this.filterControlSubject.asObservable().subscribe((filterModel) => {
    this.applyFiltersToData(filterModel);
  });
}
```

**4. Add to template:**
```html
<fr-filter-widget
  [Id]="'my-filter'"
  [filterParams]="filterParams"
  [filterControl]="filterControlSubject"
  [showFloatingFilterCountLabel]="true"
></fr-filter-widget>

<fr-filter-bubbles
  [Id]="'my-filter'"
  [filterParams]="filterParams"
  [filterControl]="filterControlSubject"
></fr-filter-bubbles>
```

## Component API

### FilterWidgetComponent (`fr-filter-widget`)

**Inputs:**
- `Id: string` — Unique identifier (required)
- `filterParams: FilterParamGroup[]` — Checkbox-based filter groups (mutually exclusive with FDN inputs)
- `FDNfields: Field[]` — Custom form fields (mutually exclusive with filterParams)
- `FDNsections: Section[]` — Multi-section FDN
- `filterControl: BehaviorSubject<any>` — Bidirectional state manager (required)
- `showFloatingFilterCountLabel: boolean` — Show count badge (default: false)
- `hideWidgetButton: boolean` — Display form without button (default: false)
- `fieldDirection: "vertical" | "horizontal"` — Layout when hideWidgetButton is true (default: "vertical")

### FilterBubblesComponent (`fr-filter-bubbles`)

**Inputs:**
- `Id: string` — Must match associated widget (required)
- `filterParams: FilterParamGroup[]` or `FDNfields: Field[]` or `FDNsections: Section[]` — Must match widget
- `filterControl: BehaviorSubject<any>` — Same instance as widget (required)

**Both components:** No outputs. Use `filterControl` subscription to capture changes.

## Features

| Feature | Reference |
|---------|-----------|
| Predefined filter parameters (checkbox groups) | [filter-params.md](references/filter-params.md) |
| Custom FDN-based filter forms | [filter-params.md](references/filter-params.md) |
| Filter bubbles with remove buttons | [filter-bubbles.md](references/filter-bubbles.md) |
| Clear all filters functionality | [filter-bubbles.md](references/filter-bubbles.md) |
| Programmatic filter updates | [filter-params.md](references/filter-params.md) |

## Key Imports

```typescript
// Core module
import { FilterWidgetModule } from "@intersystems/filter-widget";

// Types and interfaces
import { FilterParamGroup, FilterParam } from "@intersystems/filter-widget";

// Services
import { 
  FilterWidgetService,
  FilterWidgetUtilityService 
} from "@intersystems/filter-widget";

// For custom FDN forms
import { Field, FIELD_TYPES, Section } from "@intersystems/isc-form";
```
