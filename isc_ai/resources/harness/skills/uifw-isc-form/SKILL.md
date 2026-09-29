---
name: uifw-isc-form
description: Use when building Angular forms with @intersystems/isc-form — creating FDN definitions, adding custom field types or wrappers, configuring validation, working with embedded tables, recursive/nested forms, or integrating isc-form with other @intersystems packages like table or layout. Also use when questions arise about Formly concepts in the ISC context.
---

# @intersystems/isc-form

## Overview

isc-form is an Angular form library built on [@ngx-formly/core](https://formly.dev) that renders forms from a declarative JSON structure called **FDN** (Form Document Notation). It provides 22+ built-in field types, edit/view mode toggling, autosave, recursive forms, and full extensibility for custom fields, wrappers, and validators.

**Source:** `uifw2-gitlab` monorepo at `libs/isc-form/`  
**Package:** `@intersystems/isc-form` (v18.x, Angular 18)  
**Formly version:** `@ngx-formly/core` 6.3.12

## Quick Reference

### Installation & Module Setup

```typescript
import { IscFormModule } from "@intersystems/isc-form";
import { FormlyModule } from "@ngx-formly/core";
import { TranslateModule } from "@ngx-translate/core";
import { NotificationModule } from "@intersystems/notification";

@NgModule({
  imports: [
    IscFormModule,
    FormlyModule.forRoot(),
    TranslateModule.forRoot(),
    NotificationModule
  ]
})
export class AppModule {}
```

Also import in `styles.scss`:
```scss
@use '@intersystems/isc-form/assets/styles/isc-form-overlay-panel.scss';
```

### Template Usage

**Inline form:**
```html
<fr-isc-form
  [Id]="'my-form'"
  [FDN]="fdn"
  [formModel]="model"
  [mode]="'edit'"
  [buttons]="buttons"
  [formConfig]="formConfig"
  [form]="form"
></fr-isc-form>
```

**Modal form:**
```typescript
this.dialog.open(IscFormModalComponent, {
  panelClass: "isc-form-modal",
  data: {
    modalTitle: "Edit Record",
    iscFormInputs: { Id: "modal-form", FDN: fdn, formModel: model, mode: FormModes.EDIT, buttons }
  }
});
```

### Key Imports

```typescript
import {
  FDN, Section, Field, FIELD_TYPES, VALIDATORS,
  FormModes, FormConfig, FormState, FormButton,
  IscFormComponent, IscFormModule, IscFormConfigService
} from "@intersystems/isc-form";
```

## FDN Structure

```typescript
const fdn: FDN = {
  name: "Form Title",
  description: "Optional description",
  validateOn: "change",
  autosave: { trigger: "timeInterval", interval: 10000, maxIntervals: 12 },
  sectionLayout: { showSectionHeaders: true, edit: "table-of-content" },
  sections: [
    {
      name: "Section Name",
      fields: [ /* Field[] */ ],
      hideExpression: (model, formState) => model.hideSection,
      className: "optional-css-class"
    }
  ]
};
```

### Field Definition

```typescript
const field: Field = {
  key: "fieldKey",                    // model property path
  type: FIELD_TYPES.INPUT,           // or custom string name
  templateOptions: {
    label: "Field Label",
    placeholder: "Enter value",
    required: true,
    maxLength: 100
  },
  data: { /* type-specific config */ },
  hide: false,
  hideExpression: (model, formState) => !model.showField,
  disable: (field, model, formOptions, form) => model.isReadOnly,
  disableOn: { otherFieldKey: { disableIfDefined: true } },
  viewOnly: false,
  validators: {
    custom: {
      expression: (control, field) => control.value?.length > 3,
      message: (err, field) => "Must be longer than 3 characters"
    }
  },
  validation: ["registeredValidatorName"],
  className: "col-6"
};
```

## Built-in Field Types

| FIELD_TYPES enum | Component | Notes |
|---|---|---|
| INPUT | InputComponent | text, password, email via `templateOptions.type` |
| TEXTAREA | TextareaComponent | `templateOptions.rows` |
| NUMBER | NumberComponent | auto min/max validators |
| SELECT | SelectComponent | `data: SelectFieldConfig` |
| SELECT_WITH_GROUPS | SelectComponent | grouped options |
| DROPDOWN_WITH_ADD | DropdownAddItemComponent | add-new-item capability |
| RADIO | RadioComponent | `data: RadioFieldConfig` |
| BINARY_CHECKBOX | BinaryCheckboxComponent | single toggle |
| MULTI_CHECK_BOX | MultiCheckboxComponent | multiple selection |
| CHECK_BOX_TABLE | CheckboxTableComponent | checkbox rows |
| DATE_PICKER | DatePickerComponent | `data: DatePickerConfig` |
| DATE_RANGE_PICKER | DateRangePickerComponent | start/end dates |
| TIME | TimePickerComponent | `data: TimePickerConfig` |
| EMBEDDED_FORM_COLLECTION | EmbeddedFormCollectionComponent | table of forms |
| EDITABLE_TABLE | EditableTableComponent | inline editable |
| REPEAT | RepeatComponent | array field groups |
| NESTED_FORM | FormNodeComponent | lazy-loaded subforms |
| RICH_TEXT_EDITOR | RichTextEditorComponent | Quill WYSIWYG |
| INSTRUCTIONS | InstructionsComponent | display-only HTML |
| BANNER_NOTIFICATION | BannerNotificationComponent | alert messages |
| OR_DIVIDER | OrDividerComponent | visual separator |
| BUTTON | ButtonComponent | action button |

## Creating Custom Field Types

Custom fields extend `FieldType<Field>` from `@ngx-formly/core`.

### Step 1: Create the Component

```typescript
import { Component, OnInit } from "@angular/core";
import { FieldType } from "@ngx-formly/core";
import { Field } from "@intersystems/isc-form";

// Define config interface for your field's `data` property
export interface MyFieldConfig {
  customOption?: string;
  items?: { label: string; value: any }[];
}

@Component({
  selector: "app-my-custom-field",
  template: `
    <mat-form-field appearance="outline">
      <mat-label>{{ field.templateOptions?.label }}</mat-label>
      <input matInput [formControl]="$any(formControl)" />
    </mat-form-field>
    <formly-validation-message *ngIf="showError" [field]="field"></formly-validation-message>
  `
})
export class MyCustomFieldComponent extends FieldType<Field> implements OnInit {
  get fieldData(): MyFieldConfig {
    return this.field?.data as MyFieldConfig;
  }

  get formStateTyped(): any {
    return this.formState;
  }

  ngOnInit() {
    // Access: this.field, this.model, this.formControl, this.formState
    // Field config: this.field.templateOptions, this.fieldData
    // Form-level state: this.formState.Id, this.formState._mode
  }
}
```

### Step 2: Register in Module

```typescript
import { FormlyModule } from "@ngx-formly/core";

@NgModule({
  declarations: [MyCustomFieldComponent],
  imports: [
    IscFormModule,
    FormlyModule.forChild({
      types: [{ name: "my-custom-field", component: MyCustomFieldComponent }]
    })
  ]
})
export class MyFeatureModule {}
```

### Step 3: Use in FDN

```typescript
{
  key: "myField",
  type: "my-custom-field",
  templateOptions: { label: "My Custom Field" },
  data: { customOption: "value", items: [...] }
}
```

### Inherited Properties (from FieldType)

| Property | Type | Description |
|---|---|---|
| `field` | `Field` | Full field configuration |
| `model` | `any` | Form model object |
| `formControl` | `FormControl` | Angular reactive form control |
| `formState` | `FormState` | Global form state |
| `form` | `FormGroup` | Parent form group |
| `showError` | `boolean` | Whether to show validation errors |

## Creating Custom Wrappers

Wrappers extend `FieldWrapper` and wrap existing field components with additional UI.

```typescript
import { Component } from "@angular/core";
import { FieldWrapper } from "@ngx-formly/core";
import { Field } from "@intersystems/isc-form";

@Component({
  selector: "app-my-wrapper",
  template: `
    <div class="my-wrapper-container">
      <h4>{{ field.templateOptions?.label }}</h4>
      <ng-container #fieldComponent></ng-container>
      <p class="hint">{{ $any(field.data)?.hintText }}</p>
    </div>
  `
})
export class MyWrapperComponent extends FieldWrapper {
  declare field: Field;
}
```

Register:
```typescript
FormlyModule.forChild({
  wrappers: [{ name: "my-wrapper", component: MyWrapperComponent }]
})
```

Use the `wrappers` property on a field to apply:
```typescript
{ key: "wrapped", type: FIELD_TYPES.INPUT, wrappers: ["my-wrapper"], ... }
```

## Custom Validators

### Inline (one-time use)

```typescript
{
  key: "email",
  type: FIELD_TYPES.INPUT,
  validators: {
    emailFormat: {
      expression: (control, field) => /^[^@]+@[^@]+\.[^@]+$/.test(control.value),
      message: (err, field) => "Invalid email format"
    }
  }
}
```

### Registered (reusable)

```typescript
// Define
export function myValidator(control: AbstractControl, field: Field) {
  return control.value ? null : { myValidator: true };
}

// Register
FormlyModule.forChild({
  validators: [{ name: "myValidator", validation: myValidator }]
})

// Use
{ key: "field", validation: ["myValidator"] }
```

### Override built-in messages

```typescript
{
  key: "name",
  type: FIELD_TYPES.INPUT,
  templateOptions: { required: true },
  overrideValidatorMessage: {
    [VALIDATORS.ISC_REQUIRED]: "Name cannot be empty"
  }
}
```

## Recursive & Nested Forms

### REPEAT (array of field groups)

```typescript
{
  key: "contacts",
  type: FIELD_TYPES.REPEAT,
  templateOptions: { label: "Emergency Contacts" },
  data: {
    primitiveArray: false,
    buttons: { hideAddButton: false, hideRemoveButton: false },
    nestedFieldConfig: { title: "Contact", showCounter: true }
  },
  fields: [
    { key: "name", type: FIELD_TYPES.INPUT, templateOptions: { label: "Name" } },
    { key: "phone", type: FIELD_TYPES.INPUT, templateOptions: { label: "Phone" } }
  ]
}
```

### NESTED_FORM (lazy-loaded subforms)

```typescript
{
  key: "details",
  type: FIELD_TYPES.NESTED_FORM,
  templateOptions: { label: "Details" },
  nodeData: { /* arbitrary metadata */ },
  fieldGroup: [],
  nestedFormBehavior: (field, form, model) => ({
    fields: [
      { key: "sub1", type: FIELD_TYPES.INPUT, templateOptions: { label: "Sub Field" } }
    ],
    model: {}
  })
}
```

## Integration with Other @intersystems Packages

### @intersystems/table

The `EMBEDDED_FORM_COLLECTION` field type uses `@intersystems/table` internally. The editable-table field type also integrates table. For standalone table with inline editing:

```typescript
import { TableModule } from "@intersystems/table";

// In table config, provide IscFormComponent for editable cells
TableModule.forRoot({ IscFormComponent })
```

### @intersystems/notification

Required peer dependency. Add `<fr-notification></fr-notification>` to root `app.component.html`.

### @intersystems/shared-utilities

Provides `FrostButton`, `FrBreakpointObserver`, layout utilities. isc-form uses these internally.

### @intersystems/icons

Icon registry integrated into IscFormModule. `FrIconsService.registerAllIcons()` is called automatically.

### @intersystems/confirmation-dialog

Used by embedded-form-collection for delete confirmations.

## Dynamic Expressions

```typescript
// Hide based on another field
hideExpression: (model, formState, field) => !model.showAdvanced

// Disable via observable
disable: new BehaviorSubject<boolean>(false)

// Disable based on another field's state
disableOn: { parentField: { disableIfDefined: true } }

// Dynamic template options
expressionProperties: {
  "templateOptions.required": "model.makeRequired",
  "templateOptions.label": (model) => model.type === "A" ? "Label A" : "Label B"
}
```

## FormConfig & FormState

```typescript
const formConfig: FormConfig = {
  autoSave: (model, formOptions, form) => myService.save(model),
  autoSaveOnFailedValidation: false,
  additionalModels: { lookupData: {...} }
};
```

`FormState` is globally available to all fields via `this.formState`:
- `formState.Id` - form prefix ID
- `formState._mode` / `formState.getFieldMode(field)` - current mode
- `formState.sectionLayout` - layout config
- `formState.internalSavePause` - pause autosave
- Any custom properties from FormConfig

## Select Field Configuration

```typescript
{
  key: "country",
  type: FIELD_TYPES.SELECT,
  templateOptions: {
    label: "Country",
    options: [{ display: "US", value: "us" }, { display: "UK", value: "uk" }],
    // or async:
    asyncOptions: (field, formState, model) => httpService.getCountries()
  },
  data: {
    isMultiSelect: false,
    uniqueValueField: "value",    // default
    displayField: "display",      // default
    useSearch: true,
    searchConfig: {
      search: (filterText, options, field) => options.filter(o => o.display.includes(filterText)),
      debounceTime: 300
    }
  } as SelectFieldConfig
}
```

## i18n Configuration

```typescript
// In app root component
constructor(private iscFormConfigService: IscFormConfigService) {}

ngOnInit() {
  this.translate.onLangChange.subscribe(({ lang }) => {
    import(`dayjs/locale/${lang}.js`).then(() => {
      dayjs.locale(lang);
      this.iscFormConfigService.configureFormDateTime({
        defaultDateFormat: "LL",
        defaultTimeFormat: "LT"
      });
    });
  });
}
```

## Form Buttons

Buttons are passed via the `[buttons]` input (inline form) or `iscFormInputs.buttons` (modal). Each button is a `FormButton` which extends `FrostButton` from `@intersystems/shared-utilities/button`.

**Critical:** The button text property is `text` (NOT `label`), and the styling property is `buttonClass` (NOT `style` or `color`). Using wrong property names results in buttons rendering with no visible text.

```typescript
// Correct button configuration
buttons: [
  {
    text: 'Cancel',                    // ← button label (NOT "label:")
    buttonClass: 'secondary',          // ← styling (NOT "style: 'outlined'")
    noMarkAsTouchedOnClick: true,
    callback: () => { /* cancel action */ }
  },
  {
    text: 'Save',                      // ← button label
    buttonClass: 'primary',            // ← styling (NOT "style: 'raised'" + "color: 'primary'")
    disabledIfFormInvalid: true,
    callback: (_event: any, _button: any, formModel: any) => { /* save action */ }
  }
]
```

### FormButton Properties (extends FrostButton)

| Property | Type | Description |
|---|---|---|
| `text` | `string` | **Button label text** |
| `buttonClass` | `'primary' \| 'secondary' \| 'tertiary' \| 'icon'` | **Button styling** |
| `callback` | `(event, button, formModel) => any` | Click handler |
| `disabledIfFormInvalid` | `boolean` | Disable when form is invalid |
| `disabledIfFormPristine` | `boolean` | Disable when form is pristine |
| `hideInViewMode` | `boolean` | Hide when form is in VIEW mode |
| `noMarkAsTouchedOnClick` | `boolean` | Don't mark form fields as touched on click |
| `disabled` | `boolean \| Observable<boolean>` | Manual disable control |
| `hidden` | `boolean \| Observable<boolean>` | Manual hide control |

## Common Patterns

### Edit/View Mode Toggle

```typescript
// Component
mode: FormModes = FormModes.EDIT;
toggleMode() { this.mode = this.mode === FormModes.EDIT ? FormModes.VIEW : FormModes.VIEW; }

// Template
<fr-isc-form [mode]="mode" ...></fr-isc-form>
```

### Field-Level View-Only Override

```typescript
{ key: "id", type: FIELD_TYPES.INPUT, viewOnly: true } // always view-only
{ key: "name", viewOnly: (control, field) => !userHasEditPermission }
```

### Autosave

```typescript
const fdn: FDN = {
  autosave: { trigger: "timeInterval", interval: 10000, maxIntervals: 12 },
  sections: [...]
};
const formConfig: FormConfig = {
  autoSave: (model, options, form) => this.api.save(model)
};
```

### CSS Class Injection

```typescript
// Form-level (on sections)
{ name: "Section", fields: [...], className: "grid grid-cols-2 gap-4" }

// Field-level
{ key: "name", className: "col-span-1", ... }
```

## Live Documentation

Run `ng serve isc-form-harness` in the uifw2 monorepo root for interactive examples of all field types, recursive forms, and custom extensions.

## Common Mistakes

| Mistake | Fix |
|---|---|
| Using `forRoot()` in lazy module | Use `FormlyModule.forChild()` in feature modules |
| Missing `TranslateModule.forRoot()` | Must be at absolute root module |
| Forgetting `<fr-notification>` | Add to root `app.component.html` |
| Direct model mutation in custom field | Use `this.formControl.setValue()` instead |
| Missing `panelClass: "isc-form-modal"` | Required for modal styling |
| Not importing overlay SCSS | Add `@use` in styles.scss |
| Using `field.templateOptions.options` for async | Use `templateOptions.asyncOptions` callback |
| Using `label` for button text | Use `text` — `label` is not a FrostButton property |
| Using `style`/`color` for button styling | Use `buttonClass: 'primary' \| 'secondary'` |
