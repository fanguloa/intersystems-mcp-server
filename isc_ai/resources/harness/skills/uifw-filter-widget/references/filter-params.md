# Filter Parameters and Configuration

## FilterParamGroup Structure

Define predefined filter parameters as checkbox groups:

```typescript
import { FilterParamGroup } from "@intersystems/filter-widget";

filterParams: FilterParamGroup[] = [
  {
    key: "category",       // Model property
    id: "category",        // Testing ID
    groupName: "Category", // Display label
    params: [
      { text: "Electronics", value: "electronics" },
      { text: "Books", value: "books" }
    ]
  }
];
```

**Interfaces:**
```typescript
interface FilterParamGroup {
  key: string;           // Model property (required)
  id?: string;          // Testing ID
  groupName?: string;   // Display label
  params: FilterParam[];
}

interface FilterParam {
  text: string;  // Display value
  value: any;    // Coded value
}
```

## Custom FDN-Based Filter Forms

For advanced filtering beyond checkboxes, use FDN fields:

```typescript
import { Field, FIELD_TYPES } from "@intersystems/isc-form";

customFDNFilter: Field[] = [
  {
    key: "filterText",
    type: FIELD_TYPES.INPUT,
    templateOptions: { label: "Message Type" }
  },
  {
    key: "filterNumber",
    type: FIELD_TYPES.NUMBER,
    templateOptions: { label: "Min Count" }
  },
  {
    key: "facility",
    type: FIELD_TYPES.SELECT,
    templateOptions: {
      label: "Facility",
      options: [{ name: "MGH", value: "MGH" }]
    },
    data: {
      uniqueValueField: "value",
      displayField: "name"
    }
  }
];
```

**Supported:** `INPUT`, `TEXTAREA`, `NUMBER`, `SELECT`, `RADIO`, `MULTI_CHECK_BOX`, `DATE_PICKER`, `TIME`, `OR_DIVIDER`, `BUTTON`, `INSTRUCTIONS`

**NOT Supported:** `embeddedFormCollection`, `repeat`, `nested-form`, fields using `fieldGroup`

## Filter Control (BehaviorSubject)

The `filterControl` BehaviorSubject manages filter state bidirectionally.

**With filterParams:**
```typescript
initialModel = {
  planets: [{ text: "Earth", value: "Earth" }],
  moons: []
};
filterControlSubject = new BehaviorSubject<any>(initialModel);

this.filterControlSubject.asObservable().subscribe((model) => {
  console.log("Filter changed:", model);
});
```

**With FDNfields:**
```typescript
initialModel = {
  filterText: "ORU",
  filterNumber: 10,
  filterGroup1: { name: "2.5.1", value: "2.5.1" }
};
filterControlSubject = new BehaviorSubject<any>(initialModel);
```

**Programmatic Updates:**
```typescript
this.filterControlSubject.next({ planets: [], moons: [] });
```

**Check Filter Count:**
```typescript
import { FilterWidgetUtilityService } from "@intersystems/filter-widget";

getActiveFilterCount(filterModel: any): number {
  return this.filterUtilityService.getFilterCount(filterModel);
}
```
