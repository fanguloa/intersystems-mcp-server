---
name: uifw-grid-layout
description: 'Create resizable grid layouts with collapsible panels using @intersystems/grid-layout. USE FOR: resizable split-view interfaces, collapsible panels, row/column grid layouts with min/max sizing, scroll overflow control. DO NOT USE FOR: CSS Grid utilities (use styles), data grids/tables (use uifw-table), dashboard widget grids.'
argument-hint: 'Describe the layout you need (panels, direction, resize behavior)'
---

# Quick Start

```typescript
import { GridLayoutModule } from '@intersystems/grid-layout';

@NgModule({
  imports: [GridLayoutModule]
})
```

```html
<fr-grid-cell *frGrid gridDirection="row">
  <fr-grid-cell initialSize="300px">
    Left panel
  </fr-grid-cell>
  <fr-grid-cell>
    Right panel
  </fr-grid-cell>
</fr-grid-cell>
```

## Key Concepts

- `*frGrid` marks the outermost `fr-grid-cell` and completely fills its parent element
- Nest `fr-grid-cell` components to create rows and columns
- Only one axis can be resized per parent (determined by `gridDirection`)
- Cells resize by dragging the border between siblings

## GridDirective (`*frGrid`)

Structural directive applied to the root `fr-grid-cell`. No configuration needed.

## GridCellComponent (`<fr-grid-cell>`)

### Inputs

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `gridDirection` | `'row' \| 'column' \| ''` | `''` | Layout axis for child cells: `'row'` creates columns, `'column'` creates rows |
| `collapsible` | `boolean` | `true` | Whether the cell can collapse when dragged below `minSize / 2` |
| `title` | `string` | `''` | Optional title shown at top of cell (visible when collapsed) |
| `overflow` | `string` | `'hidden'` | CSS overflow style for cell content (e.g., `'scroll'`, `'auto'`) |
| `initialSize` | `string` | `''` | Initial size as pixels (`'300px'`) or percentage (`'60%'`). Unspecified cells divide remaining space evenly |
| `minSize` | `number` | `100` | Minimum size in pixels before collapse (if collapsible) or hard stop (if not) |

### Configuration Patterns

**Two-column layout with resizable divider:**
```html
<fr-grid-cell *frGrid gridDirection="row">
  <fr-grid-cell initialSize="300px">Left</fr-grid-cell>
  <fr-grid-cell>Right</fr-grid-cell>
</fr-grid-cell>
```

**VSCode-style layout:**
```html
<fr-grid-cell *frGrid gridDirection="row">
  <fr-grid-cell initialSize="300px">File browser</fr-grid-cell>
  <fr-grid-cell gridDirection="column" [collapsible]="false" [minSize]="400">
    <fr-grid-cell overflow="scroll" [collapsible]="false" initialSize="60%">
      Editor
    </fr-grid-cell>
    <fr-grid-cell overflow="scroll" title="Terminal">Terminal</fr-grid-cell>
  </fr-grid-cell>
</fr-grid-cell>
```

**Scrollable panels:**
```html
<fr-grid-cell *frGrid gridDirection="column">
  <fr-grid-cell overflow="scroll">Top panel with scrolling</fr-grid-cell>
  <fr-grid-cell overflow="auto">Bottom panel with auto overflow</fr-grid-cell>
</fr-grid-cell>
```

## Known Limitations

On parent resize, child cells resize proportionally and may become smaller than their `minSize` without collapsing. This will be fixed in DP-412852.

## Key Imports

```typescript
import { GridLayoutModule } from '@intersystems/grid-layout';
import { GridDirective } from '@intersystems/grid-layout';
import { GridCellComponent } from '@intersystems/grid-layout';
```
