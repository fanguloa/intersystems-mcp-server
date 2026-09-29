# Column Presets

Built-in cell types configured via `preset` and its corresponding config key on `cellDisplay`.

```typescript
import { EPresetOptions, EActionIcons } from '@intersystems/table';
```

---

## Checkbox (`EPresetOptions.CHECKBOX`)

Row selection with select-all header checkbox.

```typescript
{
  key: 'select',
  cellDisplay: {
    preset: EPresetOptions.CHECKBOX,
    checkbox: {
      rowsChecked: [],
      hideCheckAllBoxes: false,
      onRowsChecked: (rowsChecked, isCheck, allRowsChecked) => { /* handle */ },
      rowCheckedStatusField: 'isSelected',       // optional custom field
      rowDisabledStatusField: 'isDisabled',      // optional custom field
      tooltip: {
        header: { text: 'Select all' },
        cell: { text: 'Select row' }
      }
    }
  }
}
```

**CheckboxConfig properties:** `rowsChecked` (required, bound array), `hideCheckAllBoxes`, `onRowsChecked(rowsChecked, isCheck, allRowsChecked)`, `rowCheckedStatusField`, `rowDisabledStatusField`, `disabled(rowsChecked, isCheck)`, `tooltip`.

---

## Action Icons (`EPresetOptions.ACTIONS_ICONS`)

Row-level icon buttons. Built-in icons: `view`, `edit`, `delete`, `up`, `down`.

```typescript
{
  key: 'actions',
  title: '',
  cellDisplay: {
    preset: EPresetOptions.ACTIONS_ICONS,
    actionsIcons: {
      iconsOrder: ['view', 'edit', 'delete'],
      view: {
        callback: (event, row, col, rowIndex, paginator, refreshRow) => { /* view */ },
        tooltip: { text: 'View' }
      },
      edit: {
        callback: (event, row, col, rowIndex, paginator, refreshRow) => {
          row.name = 'Updated';
          refreshRow();
        },
        disabled: (row) => !row.active
      },
      delete: {
        callback: (event, row) => { /* delete */ },
        hidden: (row) => row.protected
      }
    }
  }
}
```

**Custom icons:** Add entries with `customIcon` (Material icon name) or `customSvgIcon` (registered SVG) and include the key in `iconsOrder`.

```typescript
actionsIcons: {
  iconsOrder: ['copy', 'view'],
  copy: { customIcon: 'content_copy', callback: (e, row) => { /* copy */ } },
  view: { callback: (e, row) => { /* view */ } }
}
```

---

## Actions Dropdown (`EPresetOptions.ACTIONS_DROPDOWN`)

Ellipsis menu with a list of actions.

```typescript
{
  key: 'menu',
  title: '',
  cellDisplay: {
    preset: EPresetOptions.ACTIONS_DROPDOWN,
    actionsDropDown: {
      actions: [
        { text: 'View',   callback: (e, row) => { /* view */ } },
        { text: 'Edit',   callback: (e, row) => { /* edit */ }, disabled: (row) => !row.active },
        { text: 'Delete', callback: (e, row) => { /* delete */ }, hidden: (row) => row.protected }
      ]
    }
  }
}
```

---

## Link (`EPresetOptions.LINK`)

Clickable link cell — supports static/dynamic text and href, or a callback.

```typescript
{
  key: 'details',
  title: 'Details',
  cellDisplay: {
    preset: EPresetOptions.LINK,
    link: {
      text: (row) => row.name,
      href: (row) => `/details/${row.id}`,
      newTab: true
    }
  }
}
```

**With callback instead of href:**
```typescript
link: {
  text: 'Open',
  callback: (event, row, col, rowIndex, paginator, refreshRow) => { /* navigate */ }
}
```

---

## Buttons (`EPresetOptions.BUTTONS`)

One or more buttons in a cell.

```typescript
{
  key: 'actions',
  title: 'Actions',
  cellDisplay: {
    preset: EPresetOptions.BUTTONS,
    buttons: {
      buttons: [
        {
          text: 'Approve',
          callback: (event, row) => { /* approve */ },
          disabled: (row) => row.status === 'approved'
        },
        {
          text: (row) => row.archived ? 'Unarchive' : 'Archive',
          callback: (event, row) => { /* toggle */ }
        }
      ]
    }
  }
}
```

---

## Editable (`EPresetOptions.EDITABLE`)

Inline-editable cell with input, textarea, or ISC form field definition.

```typescript
{
  key: 'notes',
  title: 'Notes',
  cellDisplay: {
    preset: EPresetOptions.EDITABLE,
    editable: {
      model: 'notes',
      editAs: 'textarea',
      onEdit: (row, col) => { /* save to server */ },
      disabled: (row) => row.locked
    }
  }
}
```

**Advanced FDN-based editing:** Use `editFieldFDN` or `getEditFieldFDN` for complex fields (dropdowns, validations, etc.) using ISC form field definitions.

---

## Localized Date (`EPresetOptions.LOCALIZED_DATE`)

Date display with locale-aware formatting via dayjs.

```typescript
{
  key: 'createdAt',
  title: 'Created',
  cellDisplay: {
    preset: EPresetOptions.LOCALIZED_DATE,
    localizedDate: {
      model: 'createdAt',
      dateFormat: 'D MMMM YYYY',
      displayTime: true,
      timeFormat: 'LT',
      useUTC: false
    }
  }
}
```

Defaults: `dateFormat: 'LL'`, `timeFormat: 'LT'`, `displayTime: false`.
