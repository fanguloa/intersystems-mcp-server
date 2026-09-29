# Filter Bubbles Component

The `fr-filter-bubbles` component displays active filters as removable chips (bubbles), providing visual feedback and quick removal functionality.

## Basic Setup

```html
<fr-filter-bubbles
  [Id]="'my-filter'"
  [filterControl]="filterControlSubject"
  [filterParams]="filterParams"
></fr-filter-bubbles>
```

**Required Inputs:**
- `Id` — Must match the associated filter widget's Id
- `filterControl` — Same BehaviorSubject instance used by the widget
- Either `filterParams`, `FDNfields`, or `FDNsections` — Must match what the widget uses

## Display Behavior

**With filterParams (checkbox groups):**
Each selected checkbox appears as a bubble. The bubble shows the group label followed by the selected value:
```
Category: Electronics  [x]
Status: Active  [x]
```

**With FDNfields (custom forms):**
Bubbles display based on field type:
- Primitive values (text, number): Shows label + value
- Object values (select/radio): Uses `displayField` from field.data
- Array values: One bubble per array element

## Removing Filters

**Individual Removal:**
Each bubble has a close button (x) that removes that specific filter selection. Clicking it:
1. Removes the value from the filter model
2. Emits the updated model via `filterControl`
3. Both widget and bubbles update automatically

**Programmatic Clearing:**
Clear all filters by resetting the filter control:
```typescript
// Clear all filters
this.filterControlSubject.next({});

// Clear specific filter group
const currentFilters = this.filterControlSubject.value;
delete currentFilters.category;
this.filterControlSubject.next(currentFilters);
```

## Integration with Filter Widget

The filter widget and bubbles share state via the same `filterControl` BehaviorSubject:

```typescript
export class MyComponent {
  filterControlSubject = new BehaviorSubject<any>({});
  
  ngOnInit() {
    // Both components emit changes to the same subject
    this.filterControlSubject.asObservable().subscribe((model) => {
      console.log("Filter changed by widget or bubbles:", model);
      this.applyFilters(model);
    });
  }
}
```

**Synchronization:**
- User clicks "Apply" in widget → bubbles update
- User clicks "x" on bubble → widget form updates
- Parent component calls `.next()` → both update

## Advanced Configuration

**Custom display for object values:**
```typescript
{
  key: "facility",
  type: FIELD_TYPES.SELECT,
  templateOptions: {
    options: [{ name: "Mass General Hospital", value: "MGH" }]
  },
  data: {
    uniqueValueField: "value",  // Stored in model
    displayField: "name"        // Displayed in bubble
  }
}
// Bubble shows: "Mass General Hospital"
// Model contains: { facility: { name: "Mass General Hospital", value: "MGH" } }
```

**Translation Support:**
Labels and values support i18n via `@ngx-translate`:
```typescript
filterParams: FilterParamGroup[] = [
  {
    key: "status",
    groupName: "filters.status.label",  // Translation key
    params: [
      { text: "filters.status.active", value: "active" }
    ]
  }
];
```

**CSS Classes:**
- `.filter-bubble` — Container
- `.active-filter-text` — Text content
- `.active-filter-cancel-button` — Remove button
