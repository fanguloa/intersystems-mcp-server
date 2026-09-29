# Ngrx State Management

Centralized customization data storage and CRUD operations via ngrx store.

## Store Setup

### Feature Registration

**Feature Key:**
```typescript
import { CUSTOMIZATION_STORE_FEATURE_KEY } from "@intersystems/customizable-element";
// Value: "fr-customizable-element"
```

**Module Registration:**
```typescript
// Already handled by CustomizableElementModule internally
// No manual StoreModule.forFeature() required
```

**State Shape:**
```typescript
interface ICustomizationState {
  editableMode: boolean;  // Global edit/view toggle
  data: any;              // Hierarchical JSON of all customizations
}

// Initial state
{
  editableMode: false,
  data: {}
}
```

---

## Service Configuration

**Must be configured at root level before any components instantiate.**

```typescript
import { CustomizableElementService } from "@intersystems/customizable-element";

constructor(private customizationService: CustomizableElementService) {}

ngOnInit() {
  this.customizationService.configure({
    inProductionMode: environment.production,
    getData: (version: string) => Observable<any>,
    setData: (version: string, data: any) => Observable<any>,
    clearData: (version: string) => Observable<any>,
    parseError?: (error: HttpErrorResponse) => string  // Optional
  });
}
```

**Callbacks:**
- `getData(version)` — Fetch customization data from external storage (database, localStorage)
- `setData(version, data)` — Save data to external storage, return updated data
- `clearData(version)` — Delete data from external storage, return default/empty object
- `parseError(error)` — Extract error message for notification banner (defaults to `error.message`)

**Version Parameter:**
- Allows multiple customization versions (e.g., "v1", "v2", "theme-dark")
- Pass same version to load/save/clear for consistency

---

## CRUD Operations

### Load Data

**Trigger:**
```typescript
this.customizationService.loadCustomizedData("v1");
```

**Flow:**
1. Dispatches `CustomizationActions.loadCustomizationData({ version: "v1" })`
2. Effect calls `config.getData("v1")`
3. On success: dispatches `loadCustomizationDataSuccess({ data })` → updates `state.data`
4. On error: dispatches `customizationError({ error })` → shows alert notification

**Component Usage:**
```typescript
// All fr-customizable-element components automatically subscribe to state.data
// and update their displayed values when loadCustomizationDataSuccess fires
```

---

### Save Data

**Trigger:**
```typescript
this.customizationService.saveCustomizedData("v1");
```

**Flow:**
1. Dispatches `CustomizationActions.saveCustomizedData({ version: "v1" })`
2. Effect reads current `state.data` from store
3. Calls `config.setData("v1", state.data)`
4. On success: dispatches `saveCustomizedDataSuccess({ data })` → updates `state.data` with response
5. On error: dispatches `customizationError({ error })` → shows alert notification

**Use Cases:**
- Save button in edit mode UI
- Auto-save on edit mode exit
- Periodic auto-save

---

### Clear Data

**Trigger:**
```typescript
this.customizationService.clearCustomizedData("v1");
```

**Flow:**
1. Dispatches `CustomizationActions.clearCustomizedData({ version: "v1" })`
2. Effect calls `config.clearData("v1")`
3. On success: dispatches `clearCustomizedDataSuccess({ data })` → resets `state.data`
4. On error: dispatches `customizationError({ error })` → shows alert notification

**Expected Response:**
- Return empty object `{}` OR default customization values
- All elements will revert to their `initialValue`

---

### Update Single Key

**Internal Use (Automatic):**
```typescript
// Dispatched automatically by fr-customizable-element on blur/save
this.store.dispatch(
  CustomizationActions.updateKeyValue({
    key: "header.topbar.appName",
    value: "New App Name"
  })
);
```

**Reducer Behavior:**
```typescript
// Uses lodash set() to update nested path
const result = cloneDeep(state.data);
return {
  ...state,
  data: set(result, "header.topbar.appName", "New App Name")
};
```

**Resulting State:**
```json
{
  "editableMode": true,
  "data": {
    "header": {
      "topbar": {
        "appName": "New App Name"
      }
    }
  }
}
```

---

## Actions

**Edit Mode:**
```typescript
CustomizationActions.setEditableMode({ editableMode: boolean })
```

**Single Key Update:**
```typescript
CustomizationActions.updateKeyValue({ key: string, value: string })
```

**Load Data:**
```typescript
CustomizationActions.loadCustomizationData({ version: string })
CustomizationActions.loadCustomizationDataSuccess({ data: any })
```

**Save Data:**
```typescript
CustomizationActions.saveCustomizedData({ version: string })
CustomizationActions.saveCustomizedDataSuccess({ data: any })
```

**Clear Data:**
```typescript
CustomizationActions.clearCustomizedData({ version: string })
CustomizationActions.clearCustomizedDataSuccess({ data: any })
```

**Error Handling:**
```typescript
CustomizationActions.customizationError({ error: any })
```

---

## Selectors

**Edit Mode:**
```typescript
import { selectEditableMode } from "@intersystems/customizable-element";

this.inEditMode$ = this.store.select(selectEditableMode);
```

**All Data:**
```typescript
import { selectCustomDataAll } from "@intersystems/customizable-element";

this.allCustomizations$ = this.store.select(selectCustomDataAll);
```

**Specific Key (Factory Selector):**
```typescript
import { factorySelectCustomDataKey } from "@intersystems/customizable-element";

// Returns value at "header.topbar.appName" path
this.appName$ = this.store.select(
  factorySelectCustomDataKey("header.topbar.appName")
);
```

**Why Factory Selector:**
- Uses memoization for performance
- Alternative `createSelector` with `props` is deprecated and doesn't memoize
- Each component creates its own selector instance for its `storePath`

---

## Service Observables

**Configuration:**
```typescript
this.customizationService.config$.subscribe(config => {
  console.log("Service configured:", config);
});
```

**Edit Mode:**
```typescript
this.customizationService.inEditableMode$.subscribe(inEditMode => {
  this.showEditControls = inEditMode;
});
```

**All Data:**
```typescript
this.customizationService.data$.subscribe(data => {
  this.currentCustomizations = data;
});
```

---

## Data Persistence Patterns

### Pattern 1: Manual Save Button

```typescript
export class EditorComponent {
  inEditMode$ = this.customizationService.inEditableMode$;

  toggleEditMode() {
    this.customizationService.setEditableMode(true);
  }

  saveChanges() {
    this.customizationService.saveCustomizedData("v1");
    this.customizationService.setEditableMode(false);
  }

  discardChanges() {
    this.customizationService.loadCustomizedData("v1");
    this.customizationService.setEditableMode(false);
  }
}
```

---

### Pattern 2: Auto-Save on Exit

```typescript
export class EditorComponent implements OnDestroy {
  private wasInEditMode = false;

  ngOnInit() {
    this.customizationService.inEditableMode$.subscribe(inEditMode => {
      if (this.wasInEditMode && !inEditMode) {
        // Exiting edit mode → auto-save
        this.customizationService.saveCustomizedData("v1");
      }
      this.wasInEditMode = inEditMode;
    });
  }
}
```

---

### Pattern 3: Version-Based Themes

```typescript
loadTheme(theme: "light" | "dark") {
  const version = `theme-${theme}`;
  this.customizationService.loadCustomizedData(version);
}

saveTheme(theme: "light" | "dark") {
  const version = `theme-${theme}`;
  this.customizationService.saveCustomizedData(version);
}
```

---

## Debugging

**Redux DevTools:**
1. Install `@ngrx/store-devtools`
2. Install [Redux DevTools Chrome Extension](https://chrome.google.com/webstore/detail/redux-devtools/lmhkpmbekcpmknklioeibfkpmmfibljd)
3. Open DevTools → Redux tab
4. Inspect actions, state diffs, time-travel debugging

**Action Inspection:**
```
[fr-customizable-element] Set Editable Mode → { editableMode: true }
[fr-customizable-element] Update Key Value → { key: "header.logo.src", value: "data:image/png;base64..." }
[fr-customizable-element] Save Customized Data → { version: "v1" }
[fr-customizable-element] Save Customized Data Success → { data: {...} }
```

**State Shape in DevTools:**
```json
{
  "fr-customizable-element": {
    "editableMode": true,
    "data": {
      "header": {
        "topbar": {
          "appName": "Custom App Name",
          "companyLogoSrc": "https://example.com/custom-logo.png"
        }
      },
      "page": {
        "hero": {
          "title": "Welcome!",
          "banner": "data:image/png;base64,iVBORw0KGgo..."
        }
      }
    }
  }
}
```
