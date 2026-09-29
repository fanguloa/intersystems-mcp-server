---
name: uifw-column-view
description: 'Browse hierarchical data in a macOS Finder-style column view using @intersystems/column-view. USE FOR: hierarchical data browsing, schema navigation, tree selection with column drill-down, FHIR resource schema exploration. DO NOT USE FOR: flat lists, tree views with expand/collapse, file explorers.'
argument-hint: 'Describe the hierarchical data you want to browse (structure, selection behavior)'
---

# @intersystems/column-view

Display hierarchical data in a macOS Finder-style column view with drill-down navigation.

## Quick Start

```typescript
// app.module.ts
import { ColumnViewModule } from '@intersystems/column-view';
import { Node } from '@intersystems/column-view';

@NgModule({
  imports: [ColumnViewModule]
})
```

```typescript
// component.ts
data: Node[] = [
  {
    name: 'Patient',
    children: [
      { name: 'identifier', children: [...] },
      { name: 'name', children: [...] }
    ]
  }
];

onBaseNodeClicked(node: Node) {
  console.log('Base node:', node);
}

onChildNodeClicked(node: Node) {
  console.log('Child node:', node);
  console.log('Path:', node.data.path); // Full path array
}
```

```html
<!-- template.html -->
<fr-column-view
  [data]="data"
  [baseLevelName]="'FHIR Resources'"
  [showAddIcon]="false"
  [displayBreadCrumbs]="true"
  [leafSelectHighlight]="true"
  (baseNodeClicked)="onBaseNodeClicked($event)"
  (childNodeClicked)="onChildNodeClicked($event)">
</fr-column-view>
```

## Component API

### Inputs
- `data: Node[]` - Hierarchical data structure (required)
- `baseLevelName: string` - Header text for the first column
- `showAddIcon: boolean` - Show + icon on leaf nodes (default: true)
- `displayBreadCrumbs: boolean` - Show breadcrumb path vs current level only (default: true)
- `leafSelectHighlight: boolean` - Highlight leaf nodes when clicked (default: true)

### Outputs
- `baseNodeClicked: EventEmitter<Node>` - Emitted when first column node is clicked
- `childNodeClicked: EventEmitter<Node>` - Emitted when any other column node is clicked

## Node Structure

```typescript
interface Node {
  name: string;              // Display name (required)
  children?: Node[];         // Child nodes (defines hierarchy)
  data?: any;                // Arbitrary metadata
  selected?: boolean;        // Pre-select node on init
  parent?: Node;             // Auto-managed by component
}
```

After selection, `node.data.path` contains full path array: `['FHIR Resources', 'Patient', 'identifier']`

## Selection Events

- First column clicks emit `baseNodeClicked`
- All other column clicks emit `childNodeClicked`
- Both events pass the Node object with populated `data.path` array
- Arrow keys navigate: Up/Down (siblings), Left/Right (parent/child), Enter (select)

## Key Imports

```typescript
import { ColumnViewModule } from '@intersystems/column-view';
import { Node } from '@intersystems/column-view';
```
