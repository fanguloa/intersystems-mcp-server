# Customizable Element Types

Per-element configuration, edit behavior, and customization options.

## HTML Elements

### Headings (h1-h5)

**DataType:** `CustomElementDataType.Heading1` through `CustomElementDataType.Heading5`

**Config:**
```typescript
const headingConfig: CustomElementConfig = {
  dataType: CustomElementDataType.Heading2,
  storePath: "page.hero.title",
  initialValue: "Welcome to Our Platform"
};
```

**Edit Behavior:**
- Click to focus → element becomes `contentEditable`
- Edit text inline with rich text (HTML tags preserved)
- Enter key blurs and saves (no multi-line support for single-line headings)
- Paste strips formatting (plain text with `<br>` for line breaks)
- Blur event saves to store via `updateKeyValue` action

**Interpolation Example:**
```typescript
const headingWithParams: CustomElementConfig = {
  dataType: CustomElementDataType.Heading1,
  storePath: "dashboard.greeting",
  initialValue: "Hello, {{userName}}!",
  interpolationParams: {
    userName: {
      tooltip: "Current logged-in user name",
      value: this.currentUser$ // Observable<string>
    }
  }
};
```

**Notes:**
- `{{param}}` placeholders are replaced in view mode only (edit mode preserves raw text)
- Supports HTML anchors (`<a>` tags set to `target="_top"` for iframe compatibility)

---

### Paragraph

**DataType:** `CustomElementDataType.Paragraph`

**Config:**
```typescript
const paragraphConfig: CustomElementConfig = {
  dataType: CustomElementDataType.Paragraph,
  storePath: "page.hero.description",
  initialValue: "This is a multi-line description.\nSupports line breaks."
};
```

**Edit Behavior:**
- Same as headings but supports multi-line content
- Enter key inserts `<br>` tag (default behavior suppressed)
- Focus converts HTML to text for editing, blur converts back to HTML

---

### Image

**DataType:** `CustomElementDataType.Image`

**Config:**
```typescript
const imageConfig: CustomImageConfig = {
  dataType: CustomElementDataType.Image,
  storePath: "page.hero.banner",
  initialValue: "https://example.com/default-banner.png",
  alt: "Hero banner image"
};
```

**Edit Behavior:**
- Double-click or Enter key (when focused) opens file picker
- File selected → FileReader converts to data URL → updates `src` attribute
- `onLoad` event dispatches `updateKeyValue` action (only after image successfully loads)
- `onError` event reverts to previous value and shows alert notification
- Images are `draggable="false"` to prevent accidental drag-drop

**Notes:**
- Hidden file input (`type="file" accept="image/*"`) is triggered programmatically
- Alt text is translatable via `translate` pipe

---

## Complex Elements

### Topbar (fr-topbar)

**DataType:** `CustomElementDataType.Topbar`

**Config:**
```typescript
const topbarConfig: CustomTopbarConfig = {
  dataType: CustomElementDataType.Topbar,
  storePath: "header.topbar",
  topbarConfig: {
    appNameConfig: { /* AppNameConfig minus appName/appNameAllCaps */ },
    headerConfig: { /* HeaderConfig minus tooltipConfig */ },
    menusConfig: { onItemClick: () => {}, items: [] }
  },
  initialValue: {
    appName: "My Application",
    companyLogoSrc: "https://example.com/logo.png"
  }
};
```

**Editable Parts:**
1. **App Name** (text content):
   - Click to edit inline
   - Single-line only (line breaks stripped on blur)
   - Stored at `storePath + ".appName"`

2. **Company Logo** (image):
   - Double-click to upload new image
   - Stored at `storePath + ".companyLogoSrc"`

**Edit Behavior:**
- Tooltip shows edit instructions when hovering in edit mode
- App name changes update `innerHTML` directly (not property binding due to Angular change detection)
- Logo changes update `topbarHeaderConfig.companyLogo.src` (property binding works)

**Template Usage:**
```html
<fr-customizable-element [config]="topbarConfig">
  <router-outlet fr-customizable-element-header-content></router-outlet>
</fr-customizable-element>
```

**Notes:**
- `appNameAllCaps` is forced to `false` (user controls casing via text)
- `tooltipConfig` is managed internally (shows edit instructions)

---

### Notification (fr-notification)

**DataType:** `CustomElementDataType.Notification`

**Config:**
```typescript
const notificationConfig: CustomNotificationConfig = {
  dataType: CustomElementDataType.Notification,
  storePath: "alerts.maintenance",
  initialValue: {
    type: "info",
    message: "System maintenance scheduled for tonight."
  }
};
```

**Editable Parts:**
1. **Message** (text content):
   - Click to edit inline (multi-line supported)
   - Stored at `storePath + ".message"`

2. **Icon Type** (notification style):
   - Double-click icon to open type selector dialog
   - Choose from: `info`, `success`, `warning`, `error`
   - Stored at `storePath + ".type"`

**Edit Behavior:**
- Message editing same as paragraph (contentEditable, paste sanitization)
- Icon double-click opens `NotificationTypeSelectComponent` Material dialog
- Type change triggers icon re-render → listeners/attributes re-added in `ngAfterViewChecked`

**Dialog Flow:**
```typescript
// Dialog returns selected Banner config
dialogRef.afterClosed().subscribe((result: Banner) => {
  if (result) {
    this.emitValue(result.type, "type");
  }
});
```

---

## Event Handling Summary

| Element Type | Event | Action |
|--------------|-------|--------|
| Text (h1-5, p) | `focus` | Convert HTML to text for editing |
| Text (h1-5, p) | `blur` | Save content, convert text to HTML |
| Text (h1-5, p) | `keypress` (Enter) | Blur to save |
| Text (h1-5, p) | `paste` | Strip formatting, insert plain text |
| Image | `dblclick` | Open file picker |
| Image | `load` | Dispatch store update |
| Image | `error` | Revert to previous value, show alert |
| Image | `keypress` (Enter) | Trigger dblclick for accessibility |
| Topbar App Name | Same as Text | |
| Topbar Logo | Same as Image | |
| Notification Message | Same as Text | |
| Notification Icon | `dblclick` | Open type selector dialog |

---

## CSS Classes

**Applied in Edit Mode:**
- `in-edit-mode` — Applied to wrapper element (e.g., `<h1>`, `<fr-topbar>`)
- `in-edit-mode-content` — Applied to editable content (e.g., text node, `<img>`)

**Styling Hooks:**
```scss
.in-edit-mode {
  // Style for element wrapper in edit mode
}

.in-edit-mode-content {
  cursor: text;
  border: 1px dashed #ccc;
}
```

**Tooltip Class:**
- `fr-customizable-element-mat-tooltip` — Applied to Angular Material tooltips showing edit instructions
